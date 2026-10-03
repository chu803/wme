# 阶段 97 · `ui/tab_batch` 审计（生成器 yield 元组长度 + 渲染回调兜底根因）

> 阶段 96 审了 `ui/tab_auto`（异常护栏一致性 + 构建期注册表累积同源缺陷）。本阶段按 `audit_selector.py` 实时排名，审**未审计模块第一荒漠 `ui/tab_batch`**（643 代码行 / 33 引用用例 = 19.5）。
> 抓到 2 个真实缺陷：一个**可触发的运行时崩溃**（生成器早退 `yield` 元组长度与 `outputs` 不匹配），一个**渲染回调兜底根因**（`render_pair` 文档承诺「不抛错只返回提示」但实现未守卫外部调用，异常会穿透到画廊 `.select` 回调泄露 traceback）。两处均为 UI 层健壮性缺陷，**不改检测/修复算法**。

---

## §0 结论先行

| 项 | 审计前 | 阶段 97 末 |
|---|---|---|
| 手动掩码模式未选「掩码文件夹」时 `run_batch` 早退 | **`yield` 只给 4 个值**，与 `click` 绑定的 5 个 `outputs` 不匹配 ⇒ 该路径触发 Gradio 元组长度崩溃 | 补第 5 个值 `build_detection_stats_markdown(None)`，与兄弟早退分支（无文件/无可处理）一致 |
| `render_pair` 渲染引擎调用 | **未加保护**：`_cs.render_pair_html` 一旦抛错（图片损坏/PIL 异常），异常穿透到 `on_gallery_select[_with_mode]` 泄露完整 traceback（与文档「各类异常返回可读提示」承诺不符） | `render_pair` 内部加 try/except 兜底，渲染失败统一返回 `compare.viewer.render_fail`；tab_batch / tab_history 所有调用方同步受益 |
| 全量测试 | 1308 例 / 88 文件 | **1312 例 / 89 文件，零失败** |
| 合成门禁 `quality_gate.py` | EXIT=0 | **EXIT=0，逐项与基线一致（零回归）** |
| 真实门禁 `real_gate.py --strict-scope` | EXIT=0 | **EXIT=0，P 0.0385 / R 1.0000 / IoU 0.7573 / 16.62× 完全一致** |

**两个缺陷都不改检测/修复算法**，只修「生成器回调的 yield 必须与 outputs 严格对齐」与「承诺不抛错的渲染回调要在实现层真正兜底」。

---

## §1 选点：为什么是 `ui/tab_batch`

`python scripts/audit_selector.py --md`（阶段 96 收尾后实时排名，剔除已审 `tab_auto`）：

| 模块 | 代码行 | 引用用例 | 比值 |
|---|---|---|---|
| **`ui/tab_batch`** | 643 | 33 | **19.5（未审计第一荒漠）** |
| `utils/recommend_view` | 152 | 8 | 19.0 |
| `services/queue_worker` | 264 | 14 | 18.9 |
| `ui/tab_models` | 281 | 15 | 18.7（已审于 94） |
| `ui/tab_single` | 437 | 32 | 13.7 |

`ui/tab_batch` 是 `ui/` 层**最后一个比值断层靠前的未审功能页**（theme/app/tab_models/tab_auto 已审于 91/91/94/96）。
审计方式：读码列候选 → 探针量化护栏覆盖 → 区分真缺陷 / 假阳性 / 边界 → 修 → 注入式反例 → 回归 → 文档。

---

## §2 探针量化：两个真缺陷

### P1 · 生成器早退 `yield` 元组长度不匹配（崩溃级缺陷）

`run_batch` 是 Gradio 生成器回调，`click` 绑定 `outputs=[batch_bar, batch_log, batch_gallery, batch_status, batch_detect_stats]`（**5 个**）。
逐分支核对 6 处 `yield`：

| 分支 | 行号 | yield 值数 | 判定 |
|---|---|---|---|
| 无图片文件 | 474 | 5 | ✅ |
| **手动模式无掩码文件夹** | **479** | **4** | ❌ **漏了第 5 个 `batch_detect_stats`** |
| 无可处理任务 | 495–496 | 5 | ✅ |
| 入队后首 yield | 609 | 5 | ✅ |
| 处理中 yield | 615 | 5 | ✅ |
| 收尾 yield | 630 | 5 | ✅ |

唯独 479 行早退只给了 4 个值——当用户在**手动掩码模式**下未指定「掩码文件夹」就点运行，该分支被触发，Gradio 抛 `ValueError: Expected 5 output values, got 4`，整个批量页该次运行崩溃。这是**可达路径上的真实崩溃**，不是理论风险。

### P2 · `render_pair` 渲染调用未兜底（与文档承诺不符 + 穿透到 UI 回调）

`ui/compare_view.py` 的 `render_pair` docstring 明确承诺（第 130 行）：「各类异常情况都返回可读提示而非抛错」。但实现里 `_cs.render_pair_html(...)` 调用（第 146 行）**没有任何 try/except**——文件存在性、空结果等前置检查虽已守卫，唯独渲染引擎本身的异常（损坏图片、PIL/numpy 异常）会直接上抛。

`render_pair` 被三处裸调（无上层捕获）：
- `ui/tab_batch.py:661` / `:682`（`on_gallery_select` / `on_gallery_select_with_mode`，画廊 `.select` 回调）
- `ui/tab_history.py:277` / `:471`（历史页同类回调）

→ 一次损坏结果图的对比查看，就会把 traceback 泄露给用户。这与阶段 96 抓到的「UI 回调渲染调用裸奔」是**同一类问题**，但根因在 `render_pair` 自身未履约，而非每个调用方各自不守。

---

## §3 修复

### P1 修复（`ui/tab_batch.py` 第 479 行）
早退 `yield` 补上第 5 个值，与行 474/496 的兄弟分支保持一致：
```python
    if mode == "manual" and not mask_folder:
        yield 0.0, t("batch.msg.need_mask"), [], t("batch.msg.need_mask"), \
            build_detection_stats_markdown(None)
        return
```
不动上游逻辑、不新增分支，纯补齐缺字段。

### P2 修复（根因，落在 `ui/compare_view.py` 的 `render_pair`）
把渲染引擎调用包进 try/except，让 `render_pair` 真正履约「不抛错只返回提示」：
```python
    try:
        html = _cs.render_pair_html(
            orig_path, result_path, mode,
            before_label=t("single.orig_label"),
            after_label=t("single.result_label"),
            heatmap_caption=t("compare.heatmap_label"),
        )
    except Exception as exc:  # noqa: BLE001 - 渲染异常不向上泄漏（与 docstring 承诺一致）
        log.warning("对比视图渲染失败（%s）：%s", mode, exc)
        return "", t("compare.viewer.render_fail")
    if not html:
        return "", t("compare.viewer.render_fail")
```
**根因修复优于逐调用方包 try/except**：一处兜底即护住 tab_batch + tab_history 全部画廊点选回调，且消除了「有的调用方守、有的不守」的同类不一致（经验 67 的镜像）。

> 修复前置核验：`build_app()` 在 `app.py:374` / `:378` 已分别调用 `clear_registry()` / `clear_superres_widgets()`（阶段 91 U3 / 96 P2 修复），故 `build_batch_tab` 重复构建不会累积注册表——本阶段 tab_batch 自身**无**新的构建期累积缺陷，确认系阶段 96 同源修复已落地。

---

## §4 验证

- 新增 `tests/test_ui_audit97.py` **4 例**；全量 **1312 例 / 89 文件零失败**（7 env-skip 与基线一致）。
- 合成门禁 EXIT=0、逐项与基线一致（零回归）；真实门禁 `--strict-scope` EXIT=0、P 0.0385 / R 1.0000 / IoU 0.7573 / 16.62× 一致。
- **注入式反例 2/2 捕获**：
  - **I1** 构造「手动模式 + 无掩码文件夹 + 图片路径」使 `run_batch` 走早退分支 → 断言 `next(gen)` 的元组**长度 == 5**（还原为 4 即断言失败）。
  - **I2** `patch` 掉 `_cs.render_pair_html` 使其抛 `RuntimeError` → 断言 `render_pair` 返回 `("", t("compare.viewer.render_fail"))` 而非上抛；并端到端验证 `on_gallery_select_with_mode` 在该异常下返回三元组而非泄露 traceback。

---

## §5 新增经验（69~71）

- **69（生成器式 Gradio 回调的 yield 必须与 outputs 严格等长）**：凡 `def fn() -> Generator` 且绑定多 `outputs` 的回调，**逐早退分支**核对 `yield` 元组长度——「无输入/无可处理/参数缺失」等早退分支最易漏字段，漏一个就在该路径触发运行时崩溃，且单元测试很难覆盖（需构造走到早退的输入）。修复即补齐缺字段，与兄弟早退分支对齐。
- **70（承诺「不抛错只返回提示」的回调必须在实现层兜底）**：若一个函数的文档/契约承诺「异常时返回可读提示而非抛错」，其实现必须真正 guard 住**所有**外部调用（含渲染引擎、IO），否则契约与实现不符；且异常会穿透到所有裸调它的上层 UI 回调泄露 traceback。**根因修复（在契约函数内部兜底）优于在每个调用方各自包 try/except**——一处护全部，并消除「调用方守与不守不一致」的同类缺陷。
- **71（页面回调若只被测试引用、未绑定事件，是死代码）**：`on_gallery_select`（无 mode 版）在 `ui/tab_batch.py` 定义但只被 `tests/test_compare_viewer.py` 引用、未绑到任何 Gradio 事件（线上实际走 `on_gallery_select_with_mode`）。审计时**登记为边界、不盲目删除**——删除会牵动测试，且它作为「默认模式」文档化 API 仍有价值。

---

## §6 产出文件

- `ui/tab_batch.py`（P1 修复：第 479 行早退 yield 补第 5 值）
- `ui/compare_view.py`（P2 根因修复：`render_pair` 渲染调用 try/except 兜底）
- `tests/test_ui_audit97.py`（4 例：I1 yield 长度 / I2 render 兜底 + 端到端）
- `docs/97-ui层续审-审计tab_batch与渲染回调兜底根因.md`

---

## §7 边界与剩余

- **边界（已登记，未修）**：`on_gallery_select`（无 mode 版）是死代码（仅测试引用、未绑定事件）。保留作默认模式文档化 API；不删以免牵动 `test_compare_viewer.py`。
- **i18n 完整**：`batch.*` 与 `compare.viewer.*` 键在 `utils/i18n.py` 的 zh-CN / en 两段均已定义，`t()` 缺失键仅降级为键名而非崩溃——非缺陷。
- **构建期注册表**：`build_app()` 入口已 `clear_registry()` + `clear_superres_widgets()`，tab_batch 重复构建无累积——阶段 91/96 同源修复已生效。
- **剩余荒漠**：`ui/tab_batch` 移出荒漠榜后，未审计第一荒漠顺延为 `utils/recommend_view`（152/8＝19.0），其后 `services/queue_worker`（264/14＝18.9）、`ui/tab_single`（437/32＝13.7）；另 `core/detector`（2664 行，自 89 起未独立审）仍是高价值审计对象。
