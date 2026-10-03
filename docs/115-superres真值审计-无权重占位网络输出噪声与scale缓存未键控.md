# 阶段 115 · `core/superres` 真值审计

> 选点：由 `scripts/audit_selector.py --unaudited` 给出（榜首 `core/superres` 10.5，168 代码行 / 16 测试引用 / 2 个测试文件）。
> 审计口径：核对「模块声明（docstring / note）」与「实际推理行为」是否一致；对「疑似缺陷」先复测判定条件再定夺（经验 119）。

## 1. 模块概览

`core/superres.py`（core 层，零 gradio / 零 ui）提供**真·超分（AI 模型真实放大）**后端，是阶段 29「轻量超分（同分辨率细节重建）」的补充/替代：

- `SuperResBackend` 基类：`upscale(arr, scale)` 负责数组校验与异常兜底，返回 `(out|None, used_real, note)`；`_infer` 由子类实现。
- `_TorchESRGANBackend`（`esrgan`）：纯 torch RRDB 风格生成器，lazy import torch。
- `_RealESRGANBackend`（`realesrgan`）：lazy import 第三方官方后端。
- `get_superres_backend(kind, ...)` 工厂：缺依赖/包→None（调用方回退轻量超分）。
- `superres_upscale(arr, backend, scale)` 便捷入口。

**该模块长期未被真实执行**：项目早期无 torch，`esrgan` 后端从未真实推理过（阶段 48 才因装好 torch 首次运行暴露出 `_RRDBMini` 缺 `relu`）。本节审计正是在 torch 2.9.1+cpu 就绪后对「真实推理行为」的首次逐条核对。

## 2. 探针与发现

探针（torch 可用）构造结构化测试图（渐变 + 圆块），对比「后端输出」与「cv2 干净双三次」，并检查跨 scale 复用：

| 探针 | 结果 |
|---|---|
| 无权重 `esrgan` 输出 vs 干净双三次 | ❌ `mean_abs_diff≈102`、`out.mean=1.45~45` vs `源=138`（**近黑噪声**） |
| `note` 措辞 | ❌ 声称「双三次放大的真实 torch 推理」，与实际不符 |
| 同一实例先 2× 再 4× | ❌ 4× 返回 `(96,128,3)`（应为 `(192,256,3)`）——scale 被忽略 |
| 未知 kind / 非 BGR 输入 / 无后端的优雅降级 | ✅ 正确 |

## 3. C2（真缺陷，高严重）：无权重「占位」网络输出的是噪声，不是双三次

### 3.1 症状

`_TorchESRGANBackend` 的「无权重」分支此前 `self._model = model`（一个**随机初始化的** `_RRDBMini`），并设：

```python
self.note = ("ESRGAN 后端已加载（无训练权重，{scale}× 双三次放大的真实 torch 推理；"
             "配置 REAL_SUPERRES_MODEL_PATH 可启用学习式增强）")
```

但随机初始化的卷积网络（head/body/tail/out 全是 `nn.Conv2d` 默认随机初始化）对输入的映射**不是恒等**，输出是**噪声**。实测（`arr` 均值 138.0）：

```
无权重 esrgan 输出 vs 干净双三次：mean_abs_diff = 102.06，out.mean = 45.1（甚至 1.45），bicubic.mean = 137.9
```

而模块 docstring（第 17-19 行）声称缺权重时是「**双三次上采样 + 恒等卷积**」——**声明 ≠ 实现**；`tests/test_superres.py` 的注释（第 34-36 行）也把该分支当作「退化的双三次 torch 推理」，同样基于错误假设。

### 3.2 用户可见后果（为何是高严重）

`services/postprocess_service.postprocess`（第 481-482 行）：

```python
out_sr, _used_real, real_sr_note = superres_upscale(out, backend, _scale)
if out_sr is None:
    ...  # 回退轻量超分
```

只要 `out_sr` **非 None 就直接采用**，并**忽略** `used_real` 旗标。而 `config.REAL_SUPERRES_MODEL_PATH` 默认为 `""` ⇒ 无权重 ⇒ 工厂仍返回 esrgan 后端 ⇒ 非 None ⇒ **不触发回退**。于是：**用户开启真·超分（默认 auto 后端、无权重）即得到一张近黑噪声图**，却被告知是「真实超分」。这与 `postprocess_service` docstring「后端不可用（缺 torch/权重）时自动回退轻量超分」的承诺也矛盾。

### 3.3 修复

无权重（或权重加载失败）时，改为**真正的纯双三次上采样**（`nn.Upsample(mode="bicubic")`，仍走 torch 张量管线，放大生效），并如实标注 note：

```python
has_weights = bool(self.model_path) and _os.path.exists(self.model_path)
if not has_weights:
    self._model = nn.Upsample(scale_factor=scale, mode="bicubic", align_corners=False)
    self.used_real = False
    self.note = f"ESRGAN 后端无训练权重，已退化为纯双三次放大（{scale}×）；配置 REAL_SUPERRES_MODEL_PATH 可启用学习式增强"
    ...return
```

权重文件存在但 `load_state_dict` 失败时同样退化为双三次（不保留随机网络）。同步订正模块 docstring 与类 docstring。

**修复后实测**：`mean_abs_diff = 0.44`、`out.mean = 137.4` ≈ 源 138.0；note 如实标注「纯双三次放大」。

## 4. C9（真缺陷，潜伏）：模型缓存未按 scale 键控

`_ensure_model` 用 `self._built` 缓存模型，但**不区分 scale**：

```python
if self._built:
    return
```

于是同一后端实例先请求 2×、再请求 4× 时，第二次直接复用**按 2× 构建**的模型 ⇒ 返回 `(96,128,3)` 而非 `(192,256,3)`。当前调用方多以固定 scale 创建实例，故潜伏；但这是 `scale` 契约的真实破坏。

修复：另存 `self._built_scale`，缓存按 `(built, built_scale==scale)` 键控；并在**全部成功之后**才置位（同时消除「先置位后干活」的惰性初始化隐患——技能候选 #8）：

```python
if self._built and self._built_scale == scale:
    return
...
self._built = True
self._built_scale = scale
```

修复后实测：2×→`(96,128,3)`、4×→`(192,256,3)`，逐次正确。

## 5. 守卫与注入式反例

新增 `tests/test_superres_audit115.py`（**5 例**，torch 缺失时自动跳过 3 例）：

- `test_esrgan_no_weights_produces_clean_bicubic`：无权重输出亮度须接近源、与 cv2 双三次 `mean_abs_diff < 8`、note 含「双三次」；
- `test_esrgan_scale_cache_keyed_by_scale`：同一实例 2×/4× 各得正确尺寸；
- `test_upscale_rejects_non_bgr_input`：2D / 4 通道 / 非 ndarray → `(None, False, note)`；
- `test_get_superres_backend_unknown_kind_none`：未知 kind → None（工厂降级契约）。

注入式反例（回退 C2+C9）：

```
注入前 sha256 = 197c5ac9…
临时回退 → pytest EXIT=1：
  FAILED test_esrgan_no_weights_produces_clean_bicubic（out.mean=1.45 vs 源 137.2）
  FAILED test_esrgan_scale_cache_keyed_by_scale（4× 请求得 (96,128,3)）
还原 → sha256 逐字节一致（197c5ac9…）
```

## 6. 验证

受影响面（superres 相关 + 注册表）：

```
pytest tests/test_superres_audit115.py tests/test_superres.py tests/test_video_superres.py \
       tests/test_cli_real_superres.py tests/test_cli_audit114.py \
       tests/test_postprocess.py tests/test_postprocess_fidelity.py tests/test_audit_selector.py
→ EXIT=0（全绿；4 例「无 torch」用例在 torch 就绪时预期跳过）
```

## 7. 注册与交付

- `scripts/audit_selector.py`：`AUDITED_SINCE["core/superres"] = 115`，并补 provenance 注释。
- 新增 `tests/test_superres_audit115.py`（5 例）。
- `core/superres.py`：无权重路径改纯双三次 + scale 键控缓存 + 两处 docstring 订正。
- README §2.62 / TODO P1.63 更新；LESSONS 追加 124；工作记忆追加本阶段条目。

## 8. 跨模块观察（登记，不在本阶段修）

- `services/postprocess_service` 采用「非 None 即用」并忽略 `used_real`；其 docstring 又称「缺权重…回退轻量超分」。本阶段修复后，「缺权重」得到的是**干净的 4× 双三次放大**（非回退、非噪声）。两模块对「缺权重」的措辞仍不完全一致（core 说「放大生效」，postprocess 说「回退」），已登记；因 postprocess 不在本阶段审计范围，暂不改其行为。

## 9. 下一步（未审榜）

`services/postprocess_service` 10.5 → `core/mask_generator` 10.5 → `services/compare_service` 10.3 → `api` 9.6。
