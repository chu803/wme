# 阶段 122 · `services/postprocess_service` 真值审计

> 选点：`scripts/audit_selector.py --unaudited` 榜首 `services/postprocess_service` 10.5（357 代码行 / 34 引用 / 4 个测试文件）。
> 审计口径：① 声明（docstring）与实现是否一致；② 自检/守卫是否**真能在本环境通过**；
> ③ 「疑似缺陷」先复测判定条件（经验 119）再定夺。

---

## 1. 模块概览

`services/postprocess_service.py` 在 inpaint 之后叠加画质增强（零 gradio）：
**① 色彩还原 → ② 边缘融合 → ③ 伪影抑制 → ④ 纹理重建 → ⑤ Poisson 融合 → ⑥ 超分（真实优先·轻量回退）→ ⑦ 锐化**，
并给 ③④⑤⑥ 套「质量分下滑即自动回退」的安全网（`_revert_if_worse`），另与 `quality_service` 联动做优化前后对比。

---

## 2. 三个候选：逐一复测后**判定为非缺陷**（经验 119）

| 候选 | 探针实测 | 判定 |
|---|---|---|
| C1：`_edge_blend(result, mask, original, band)` 的 `original` 是否为死参 | 传入全黑 vs 全白 `original`，输出**逐字节相同** ⇒ 确实未使用 | **非缺陷**：阶段 60 刻意改为「只用 result 自身模糊、绝不混回原图水印像素」，docstring 已明写 |
| C2：`real_superres=True` 无 mask/original 时静默不放大 | 有 geom → 画布放大；无 geom → 尺寸不变 | **非缺陷（设计如此）**：`docs/32` 明确 `if real_superres 且具备原图+掩码`。但**docstring 未写** ⇒ 记为 F3 文档缺口 |
| C3：`POSTPROCESS_TEXTURE_ALLOW/POSTPROCESS_POISSON_ALLOW` 默认 False，导致用户开关静默无效 | `texture_transfer=True` 与 `False` 输出**相同** | **非缺陷（设计如此）**：阶段 61 的部署级放行闸（这两项实测净负收益），且会打 INFO 日志。属**已知边界**，此处仅登记 |

> 这三条正是经验 119 的又一次应用：**看起来像缺陷，实测发现设计文档里写了**——改它反而是破坏。

---

## 3. F1（真缺陷）：模块自检**在本环境必然失败**

### 3.1 症状（实跑）

```
$ python services/postprocess_service.py
[INFO] 真实超分已应用：ESRGAN 后端无训练权重，已退化为纯双三次放大（2×）
Traceback ... line 589, in <module>
    assert improved.shape == raw.shape
AssertionError            ← EXIT=1
```

### 3.2 根因（两条，缺一不可）

1. **早期断言用的是「默认调用」**：`postprocess(raw, original_rgb=orig, mask=mask)` 未指定
   `real_superres` ⇒ `postprocess` 读 `config.REAL_SUPERRES_ENABLED`。而
   **`config.user_overrides.json` 正是 `{"REAL_SUPERRES_ENABLED": true, "REAL_SUPERRES_SCALE": 2,
   "REAL_SUPERRES_BACKEND": "esrgan"}`**（用户在设置页保存的偏好，机器本地文件）⇒ 每条默认
   调用都把画布**放大 2×** ⇒ `improved.shape == raw.shape` 崩。
2. **用例 7 硬编码「不应改变尺寸」**：`assert rs.shape == raw.shape, "真实超分后端不可用时不应改变尺寸"`。
   但**装了 torch 时 esrgan 后端可用**（缺权重则退化为纯双三次放大）⇒ 该断言在装 torch 的环境上同样必崩。

⇒ **该自检在「装了 torch」或「用户开过真·超分」的机器上无法通过**。这正是它长期没被发现的原因：
`tests/test_superres.py` / `test_postprocess.py` **都有 `skipif(_TORCH_PRESENT)` 跳过同类断言**，
而模块 `__main__` **没有**这层跳过。

### 3.3 修复

- 自检开头**显式关闭** `config.REAL_SUPERRES_ENABLED`（`postprocess` 在 `real_superres=None` 时
  读该属性，故一处即可解耦全篇）；用例 7 仍用**显式入参**单独测真·超分，不受影响。
- 用例 7 断言改为**环境无关**：`形状 ∈ {原尺寸, 恰好 4×}`，并打印实际走向。

## 4. F2（声明 ≠ 实现）：docstring 的「缺权重」措辞已过时

docstring 原文：「后端不可用（**缺 torch/权重**）时自动回退轻量超分（同分辨率）」。
但**阶段 115 起**，`esrgan` 在缺权重时退化为**纯双三次放大**（`nn.Upsample`）——**仍会改变画布**，
既不回退、也不同分辨率。实测日志：`ESRGAN 后端无训练权重，已退化为纯双三次放大（4×）`。
⇒ 修正为「后端不可用＝**缺 torch / 缺 realesrgan 包**」，并补 ⚠ 说明缺权重仍会放大。

## 5. F3（文档缺口）：`real_superres` 需 geom，未写

补注：`real_superres` 需**原图 + 掩码**才执行（设计见 `docs/32`）⇒ 缺二者时该开关**静默无效**。

---

## 6. 守卫与注入式反例

新增 `tests/test_postprocess_audit122.py`（6 例）：

| 守卫 | 抓什么 |
|---|---|
| `test_module_selftest_passes_in_this_env` | **子进程实跑模块自检 → 退出码 0**（能永久抓住 F1） |
| `test_module_selftest_independent_of_real_superres_env` | 用 `WME_REAL_SUPERRES_ENABLED=1` 强制打开真·超分，自检仍须通过（证明已解耦） |
| `test_docstring_documents_no_weight_bicubic_upscale` | F2：docstring 必须写「缺训练权重→双三次」，且旧错误表述已消失 |
| `test_light_and_real_superres_are_mutually_exclusive` | 钉住「真实/轻量超分互斥」的 docstring 承诺 |
| `test_real_superres_needs_geom_and_is_documented` | F3：无 geom 静默无效 + docstring 有说明 |
| `test_explicit_real_off_keeps_size_unchanged` | 显式关闭时尺寸不变（与环境无关） |

注入式反例：注释掉自检里的 `config.REAL_SUPERRES_ENABLED = False` ⇒ 见 §7 判定表。

## 7. 验证

| 项 | 结果 |
|---|---|
| 修复前自检 | **EXIT=1**，`AssertionError @ line 589` |
| 修复后自检 | **EXIT=0**，用例 7 正确报告「后端可用，已放大 → (1200,1600,3)」 |
| **注入式反例** | 去掉自检里的 `config.REAL_SUPERRES_ENABLED = False` ⇒ 守卫 `test_module_selftest_passes_in_this_env` **FAIL**（复现同一条 `AssertionError`）；还原后 sha256 逐字节一致（`8ae04058…`） |
| 受影响面 | `test_postprocess_audit122`(6) + `test_postprocess` + `test_postprocess_fidelity` + `test_superres` + `test_optional_enhance_gate` + `test_audit_selector` = **71 通过 / 3 跳过**（torch 就绪时预期跳过「无后端」用例），**EXIT=0** |

## 8. 注册与交付

- `scripts/audit_selector.py`：`AUDITED_SINCE["services/postprocess_service"] = 122`（+ provenance 注释）。
- 新增 `tests/test_postprocess_audit122.py`（6 例）。
- `services/postprocess_service.py`：自检环境解耦 + 用例 7 环境无关断言 + 两处 docstring 勘误。
- README / TODO / LESSONS / 工作记忆同步。

## 9. 边界与观察（登记，不改）

1. `POSTPROCESS_TEXTURE_ALLOW` / `POSTPROCESS_POISSON_ALLOW` 默认 False ⇒ 用户/CLI 打开
   `texture_transfer` / `poisson_blend` 会被部署闸**静默吃掉**（仅 INFO 日志）。属阶段 61 的
   刻意设计（两项实测净负收益），但**CLI 帮助与 `postprocess` 参数说明均未提及此闸**——
   登记为后续可改进的文档缺口（本阶段不动行为）。
2. `config.user_overrides.json` 是**机器本地用户偏好**（阶段 116 已加入 `.gitignore`），
   但它的存在使「默认行为」随机器而变。任何写「默认/形状」断言的代码都必须**显式入参**，
   不能依赖 config 默认值——本阶段的 F1 就是这条的反例。
