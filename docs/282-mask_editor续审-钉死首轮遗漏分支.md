# 282 · `core/mask_editor` 续审 — 钉死首轮遗漏的成功/边界分支

> 5j 深度复审链第 7 站（接 276–281）。本链首次进入 **core 层**（前 6 站为 ui/services/utils）。
> 方法论：**对已审模块按「契约输入类别枚举」做更深一轮**，钉死首轮遗漏分支。
> 结论：**零真缺陷**，首轮遗漏的是「未钉边界」而非代码缺陷（与 276/277/278/279/280 同构）。

## 背景

`core/mask_editor`（306 行，阶段 24 掩码代数层）提供「检测掩码 × 用户精修掩码」的组合运算（`merge_masks`）与程序化编辑原语（`apply_brush` / `apply_rect` / `count_mask_pixels`）。纯 numpy、零 gradio。
首轮审计已很密：
- **阶段 24**（`test_mask_editor.py`）：merge 并集 / manual 剥离 / None·空分支 / 尺寸对齐 / 未知模式回退 / RGB·RGBA 输入；画笔·矩形增删与夹取；服务层 `process_image` user_mask 透传。
- **阶段 111**（`test_mask_editor_audit111.py`）：RGB 亮度 / RGBA alpha 二值化；manual 空 det·空 user 边界语义；最近邻上/下采样保真；浮点圆心 / 模式大小写 / 输出纯 0/255 uint8；负半径 / 反向矩形 / 1D 抛错；并**显式记录**「target_shape 上采样面积不守恒是语义非缺陷」。

首轮聚焦「代数正确 + 若干退化输入」，但**遗漏了若干成功/边界不变量**：非 2D 输入的显式拒绝、浮点半径的舍入边界、`apply_rect` 的浮点坐标与负坐标、`target_shape` 非正维度的忽略、mode 的缺省与空白、逐维取 max 的尺寸语义、画笔圆心贴边、`_as_binary` 的 1/2 通道分支、布尔/负值掩码。

## 方法

1. **枚举契约输入类别**：对 `merge_masks` / `apply_brush` / `apply_rect` / `_as_binary` / `_canonical` / `_resize_binary` 的每个入参列出「成功/边界/异常」取值。
2. **注入式探针**（`.scratch/probe_mask_editor_reaudit.py`）：10 组共约 30 个边界取值实跑确认行为（不猜测）。
3. **守卫钉死**：把每个被确认的不变量写成机器可判定断言。

## 覆盖清单（37 例守卫）

| 类别 | 守卫 | 钉死的不变量 |
|---|---|---|
| 非 2D 输入 | `apply_brush` / `apply_rect` 喂 3D·1D·4D → `ValueError` | 显式拒绝，不静默产错 |
| 浮点半径舍入 | `0.4/0.5→0`（无操作）、`0.6→1`、`1.5→2`（银行家舍入）、负值→无操作 | `int(round(r))` 边界 |
| `apply_rect` 坐标 | 浮点 `int()` **截断** + 半开区间；负坐标夹取到 0；越界整幅覆盖；退化区间无操作 | 夹取与区间语义 |
| `target_shape` | 含 0 / 负维度 → **被忽略**，回退掩码自身尺寸 | 非正维度不被误用 |
| mode | falsy（None/""/0/False）→ merge；带空白 `"merge "` → 未知模式回退，结果仍 merge | 缺省与回退一致 |
| 尺寸语义 | det(10,50) × user(50,10) → 逐维 max 得 (50,50) | `_canonical` 逐维取 max |
| 画笔圆心贴边 | (0,0)/(h-1,w-1)/(0,mid)/(mid,0) 均在界内绘制 | 越界保护不误伤合法边缘 |
| `_as_binary` 通道 | (H,W,1)/(H,W,2) 走亮度 → (H,W)；(H,W,4) 取 **alpha**（RGB=255 但 alpha=0 判空） | 通道分支语义 |
| 布尔/负值掩码 | bool → uint8；负值（`>0` 为 False）→ 视作空 | 二值化健壮 |

## 结论 · 归因

- **零真缺陷**。首轮（24/111）已把掩码代数正确性钉得很扎实；本轮探针 10 组约 30 个边界取值**全部行为正确**。
- 值得单独指出的一条「看似问题实为正确」：`_canonical` 在无 `target_shape` 时**逐维取 max**（det(10,50)×user(50,10)→(50,50)），可能得到大于任一输入的尺寸——这是「对齐到较大尺寸」的有意语义，与 111 记录的「上采样面积不守恒」同族，本轮显式钉死以防被误当 bug「修掉」。

## 验证

- 新增 `tests/test_mask_editor_branches_audit.py`：**37 例守卫全绿**。
- 全回归（mask_editor ×3 + mask_generator + tab_auto 掩码模式 + 掩码膨胀标定）：**104 例全绿**、EXIT=0、无夹具泄漏。

## 自反证伪

- 若 `apply_brush` 去掉 `out.ndim != 2` 的 `ValueError`，`test_brush_non_2d_raises` 将失败。
- 若 `apply_rect` 的负坐标夹取被改坏（负索引回绕），`test_rect_negative_coords_clip_to_zero`（期望 25）将失败。
- 若 `_canonical` 的非正 `target_shape` 处理被改坏，`test_target_shape_nonpositive_dims_ignored`（期望回退 (20,20)）将失败。
- 若 `_as_binary` 的 RGBA 分支误取亮度而非 alpha，`test_as_binary_rgba_uses_alpha_not_luminance`（RGB=255/alpha=0 ⇒ 判空）将失败。
- 若 `merge_masks` 的 falsy-mode 归一被改坏，`test_falsy_mode_defaults_to_merge`（期望并集 34）将失败。

## 交付

- 守卫：`tests/test_mask_editor_branches_audit.py`（37 例）
- 探针：`.scratch/probe_mask_editor_reaudit.py`
- 登记：`scripts/audit_selector.py` AUDITED_SINCE 注释补 282
- 记忆：`.workbuddy/memory/MEMORY.md` 进度指针 + 本日工作日志