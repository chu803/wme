# 285 · `core/image_utils` 续审 — 修复 3 处真缺陷（含 119025 块静默爆炸）

> 5j 深度复审链第 10 站（接 276–284）。
> 结论：**发现并修复 3 处真缺陷**（A 取模除零 / B 分块静默爆炸 / C NaN 净化缺失），48 例守卫全绿。
> 本模块 **1055 行 / 32 函数却无专属测试文件**（全仓仅 17 处零散引用）——覆盖率荒漠的典型样本。

## 背景

`core/image_utils`（1055 行，阶段 89 审计）是全项目**图像地基**：`load_image` 被 detector / 批量 / 报告 / 推荐器到处调用，`split_into_tiles`+`merge_tiles` 是大图分块路径的核心。
首轮审计（阶段 89）覆盖了读写真值、ICC/EXIF 方向、分块拼接往返、掩码膨胀等主干，但**纯 numpy 助手的退化输入与参数下界几乎无人看守**——因为没有专属测试文件。

## 探针（`.scratch/probe_image_utils_reaudit.py`，16 组）

逐个实测边界，发现 3 处真缺陷。

### 真缺陷 A · `pad_to_multiple(multiple<=0)` → `ZeroDivisionError`

```python
pad_h = (multiple - h % multiple) % multiple   # multiple=0 ⇒ 取模除零
```
堆栈指向取模而非真正成因（调用方只传了 `config.MODEL_SIZE_ALIGN`）。
**与阶段 161（queue_worker `retry<0`）/ 166（`records_per_page<1`）同族**：数量类参数缺下界即显式拒绝。
**修复**：`multiple < 1` → `ValueError`。

### 真缺陷 B · `split_into_tiles(overlap >= tile_size)` → 分块静默爆炸（最严重）

```python
stride = max(1, tile_size - overlap)   # overlap>=tile_size ⇒ stride 被钳到 1
```
步长退化到 1 ⇒ **逐像素切块**。探针实测 600×600 / `tile=256` / `overlap=300`：

| 参数 | 块数 |
|---|---|
| `overlap=64`（正常） | **9** |
| `overlap=300`（≥tile_size） | **119025** |

≈ **1.3 万倍**爆炸，且**无任何报错**——内存与耗时静默吞噬，用户只会看到「卡住了」。
步长钳到 1 反而**掩盖**了「overlap 相对 tile_size 过大」这一真正成因。
**修复**：`tile_size<1` / `overlap<0` / `overlap>=tile_size` 三项均显式 `ValueError`。

> 附带的额外证据（注入实验才发现）：去掉守卫后，负 `overlap` 会一路穿到 OpenCV 的
> `copyMakeBorder`，炸成 `cv2.error: top >= 0 && bottom >= 0`——比原来的静默爆炸更难排查。

### 真缺陷 C · `tensor_to_mask` 缺阶段 207 的 NaN 净化

`tensor_to_rgb` 在阶段 207 已加固（`nan_to_num` **必须先于** clamp，因 **clamp 拦不住 NaN**：`np.clip(nan,0,1)` 仍是 nan ⇒ `astype(uint8)` 触发 `invalid value encountered in cast` 并把 NaN 静默变 0）。
其**同族函数** `tensor_to_mask` 仍是旧写法，探针实测确实发出该警告。

掩码侧后果与图像侧同样严重：NaN 像素被判「无需修复」⇒ 该区域**静默跳过修复**，而调用方仍以为成功。

**修复**：与 `tensor_to_rgb` 同口径补 `nan_to_num(nan=0, posinf=1, neginf=0)`。
> 该函数当前**无生产调用方**（全仓仅定义处命中）⇒ 属**预防性加固**，防止日后接进推理链时重蹈 207 覆辙。此点已写入 docstring，避免夸大严重性。

## 注入式反向验证（`.scratch/inject285_verify.py`，独立进程）

把 3 处退回旧写法后复跑：

```
INJECTED_RC= 1
E ZeroDivisionError: integer modulo by zero        core\image_utils.py:864
E cv2.error: top >= 0 && bottom >= 0 && ...        core\image_utils.py:872   ← 额外发现
E RuntimeWarning: invalid value encountered in cast core\image_utils.py:781
FAILED test_pad_to_multiple_rejects_nonpositive[0/-1/-8]
FAILED test_split_tiles_rejects_overlap_ge_tile_size / equal_or_over / nonpositive_tile_size / negative_overlap
FAILED test_tensor_to_mask_sanitizes_nan_and_inf / all_nan_yields_zero_mask
RESTORED: True
```
⇒ 三条守卫**全部真命中**，源码已完整还原。

## 其余钉死的边界（探针实证全部正确）

| 类别 | 不变量 |
|---|---|
| `dilate_mask` | `iterations<=0` 返回**副本**不改原图；`>0` 膨胀生效 |
| `feather_mask` | `ksize<=0` 转 float32 不羽化；**偶数 ksize 自动 +1**（cv2 要求奇核） |
| `threshold_mask` | 钉死反直觉点：OpenCV `THRESH_BINARY` 取**严格大于**，`thresh==255` 会把 255 块**整块丢弃** |
| `pad_to_multiple`/`crop_padding` | 100×100 / 101×103 / 96×96 / 1×1 / 8×8 往返 `array_equal` 全真；2D 掩码保持 ndim=2；`pad=(0,0,0,0)` 恒等 |
| `split_into_tiles` | 600×600/ts=256/ov=64 ⇒ 9 块、起点 {0,192,384}、**覆盖无空洞**、首末块标记正确；ts≥图 ⇒ 1 块；overlap=0 仍完整覆盖；`skip` 随 `TILE_SKIP_EMPTY` |
| `merge_tiles` | 300×300/ts=128/ov=32 往返 `array_equal` 为真（权重窗互补、无接缝） |
| `_edge_window` | 首块起点/末块终点恒 1；仅非边界侧做余弦斜坡；极小 length 时 fade 受 `length//2` 限制 |
| 张量转换 | `tensor_to_rgb` 阶段 207 回归（NaN→0/+inf→255/-inf→0）；两者越界 clamp；`tensor_to_mask` 收 (1,1,H,W) 与 (1,H,W)；`mask_to_tensor`→(1,1,H,W) 值域{0,1}；`rgb_to_tensor` 255→1.0 |

## 验证

- 新增 `tests/test_image_utils_branches_audit.py`：**48 例守卫全绿**。
- 全回归（285 + inpainter分块260 + GPU数值守卫 + ICC + 输入归一 + 输出保真 + inpainter溢出 + 掩码内区 + compare124，`-m 'not slow'`）：**193 例全绿**、EXIT=0、无夹具泄漏 ⇒ 三处修复零连带损伤。

## 自反证伪

- 去掉 A 的守卫 → `ZeroDivisionError`（已实证）。
- 去掉 B 的守卫 → `DID NOT RAISE` + OpenCV 断言错误（已实证）。
- 去掉 C 的守卫 → `RuntimeWarning: invalid value encountered in cast`（已实证）。
- 若 `threshold_mask` 被改成 `>=` 语义，`test_threshold_mask_boundaries[255-0]` 将失败。

## 交付

- 修复：`core/image_utils.py`（3 处）
- 守卫：`tests/test_image_utils_branches_audit.py`（48 例）
- 探针：`.scratch/probe_image_utils_reaudit.py`、`.scratch/inject285_verify.py`
- 登记：`scripts/audit_selector.py` AUDITED_SINCE 注释补 285
- 记忆：`.workbuddy/memory/MEMORY.md` 进度指针 + 本日工作日志