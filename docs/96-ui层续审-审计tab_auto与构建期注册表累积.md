# 阶段 96 · `ui/tab_auto` 审计（异常护栏一致性 + 构建期注册表累积同源缺陷）

> 阶段 94 把「选谁审」变成可复现工具、并审了 `ui/tab_models`；阶段 95 把能力承诺面收敛为
> 「只做角标」。本阶段按 `audit_selector.py` 的实时排名，审**当前第一荒漠 `ui/tab_auto`**
> （709 代码行 / 32 引用用例 = 22.2，比第二名 `ui/tab_batch` 19.5 仅高一线）。
> 抓到 2 个真实缺陷，均为**一致性 / 同源类**，不是算法错误。

---

## §0 结论先行

| 项 | 审计前 | 阶段 96 末 |
|---|---|---|
| `on_enhance` 渲染失败 | **直接把 traceback 抛给 Gradio**（同文件 8 个 `process_*` 回调都守、唯独它裸奔） | try/except + `format_exception_for_ui` 降级为错误提示 HTML |
| `build_app()` 对 `_SUPERRES_WIDGETS` | **不清空** ⇒ 重复构建线性累积（0→5→10） | 入口 `clear_superres_widgets()`（与 U3 同构） |
| 全量测试 | 1304 例 / 87 文件 | **1308 例 / 88 文件，零失败** |
| 合成门禁 `quality_gate.py` | EXIT=0 | **EXIT=0，逐项与基线一致（零回归）** |
| 真实门禁 `real_gate.py --strict-scope` | EXIT=0 | **EXIT=0，P 0.0385 / R 1.0000 / IoU 0.7573 / 16.62× 完全一致** |

**两个缺陷都不改检测/修复算法**，只修「UI 层该一致的地方不一致」与「构建期全局注册表该清空的地方没清空」。

---

## §1 选点：为什么是 `ui/tab_auto`

`python scripts/audit_selector.py --top` 在阶段 95 收尾时给出的实时排名：

| 模块 | 代码行 | 引用用例 | 比值 |
|---|---|---|---|
| **`ui/tab_auto`** | 709 | 32 | **22.2（第一荒漠）** |
| `ui/tab_batch` | 643 | 33 | 19.5 |
| `utils/recommend_view` | 152 | 8 | 19.0 |
| `services/queue_worker` | 264 | 14 | 18.9 |
| `core/model_manager` | 495 | 29 | 17.1 |

`ui/tab_auto` 是 `ui/` 层**最后一个未审的功能页**（theme/app/tab_models 已审于 91/91/94），且比值断层第一。
审计方式：读码列候选 → 探针量化护栏覆盖 → 区分真缺陷 / 假阳性 / 边界 → 修 → 注入式反例 → 回归 → 文档。

---

## §2 探针量化：两个真缺陷

### P1 · `on_enhance` 异常护栏不一致（一致性缺陷，P1 级）

`ui/tab_auto.py` 里 9 个 `process_*` / `on_*` 回调中，**8 个**在调用 `services.compare_service.render_compare_html`
（或同族渲染）时包了 try/except + `format_exception_for_ui`；唯独 `on_enhance` 裸调用：

```python
def on_enhance(orig_arr, result_arr, mode: str) -> str:
    if orig_arr is None or result_arr is None:
        return t("compare.no_result")
    html = _cs.render_compare_html(          # ← 失败会把 traceback 直接抛给 Gradio
        orig_arr, result_arr, mode,
        before_label=t("auto.orig_label"),
        after_label=t("auto.result_label"),
        heatmap_caption=t("compare.heatmap_label"),
    )
    return html or t("compare.no_result")
```

探针（`C:/Users/HP/AppData/Local/Temp/wme96_probe.py`，写 %TEMP%）：monkeypatch `render_compare_html`
为 `_boom` → **`on_enhance` 直接 `raise RuntimeError`** ⇒ VERDICT：REAL_DEFECT（无护栏）。
这违反阶段 81 建立的「UI 唯一异常入口 `format_exception_for_ui`」契约。

### P2 · `build_app()` 不清空真实超分注册表（同源累积缺陷，P2 级）

`ui/app.py:build_app()` 在阶段 91（U3）已补 `clear_registry()`（i18n 运行时注册表），但**漏了**
`clear_superres_widgets()`（`ui/components.py` 的 `_SUPERRES_WIDGETS`）。

- 设置页「应用真实超分」遍历该注册表，刷新所有功能页的真实超分控件（见阶段 91 文档）。
- 不清空 ⇒ 每次 `build_app()` 让注册表线性累积：探针实测 **0 → 5 → 10**（每个功能页登记一组控件）。
- 累积型缺陷特征（与 U3 一致）：**单次运行完全正常**，只在重复构建时暴露；**不报错**，故门禁/测试都发现不了。

探针：先 `clear_superres_widgets()`，调 `build_app()` 两次，记录 `len(get_superres_widgets())` →
**before=0 / #1=5 / #2=10** ⇒ VERDICT：REAL_DEFECT（累积）。

> **P2 是阶段 91 U3 的同源缺陷**：前者是 i18n 注册表，本阶段是超分控件注册表，根因相同——
> 「构建期写入的进程级全局注册表，必须在构建入口清空」。修法沿用已建立的 `clear_*` 模式，低风险、高一致。

---

## §3 修复（三条原则）

| 原则 | 本阶段体现 |
|---|---|
| 单一真值来源 | `clear_superres_widgets()` 仍是唯一清口，`build_app` 只负责**调用**它（不另写一份） |
| 并集不替换 | `on_enhance` 改为「与同文件 `process_*` 回调一样的 try/except + `format_exception_for_ui`」，**补齐**而非改其它 |
| 不推翻上游裁决 | 不动 `render_compare_html` 内部；只把它的异常在 UI 层**收口**成错误提示 |

### 3.1 `ui/tab_auto.py` · P1

```python
    if orig_arr is None or result_arr is None:
        return t("compare.no_result")
    try:
        html = _cs.render_compare_html(
            orig_arr, result_arr, mode,
            before_label=t("auto.orig_label"),
            after_label=t("auto.result_label"),
            heatmap_caption=t("compare.heatmap_label"),
        )
    except Exception as exc:  # noqa: BLE001
        # 阶段 96：渲染失败必须降级为错误提示而非把 traceback 抛给 Gradio。
        # 与同文件 process_* 回调的「捕获 + format_exception_for_ui」保持一致。
        log.exception("增强对比视图生成失败")
        return (
            '<div style="color:#c0392b;padding:8px 10px;white-space:pre-wrap">'
            f"{_html.escape(t('compare.viewer.render_fail'))}<br/>"
            f"{_html.escape(format_exception_for_ui(exc))}</div>"
        )
    return html or t("compare.no_result")
```

> **i18n 锚点踩坑（顺带记一笔）**：错误文案初版误用了 `t("compare.render_fail")`，但该 key
> **不存在**——它实际在 `compare.viewer.render_fail`（阶段 78 的 `viewer` 子字典）。若把
> `utils/i18n.py` 的自检按错误 key 改，会**反过来把自检改坏**（常量图自比恒绿那类陷阱的近亲）。
> 修正：回滚 i18n 自检改动，调用点改用 `compare.viewer.render_fail`（该键中/英均已存在）。

### 3.2 `ui/app.py` · P2

```python
    clear_registry()
    # 阶段 96：同上理，`_SUPERRES_WIDGETS` 也是构建期全局注册表
    # （设置页「应用真实超分」遍历它刷新所有功能页控件）。不清空则
    # 重复构建会让注册表线性累积（与 U3 同源），使「应用」指向已废弃的组件。
    clear_superres_widgets()
```

---

## §4 注入式反例（证明守卫是「真守卫」）

编排脚本 `C:/Users/HP/AppData/Local/Temp/wme96_injection.py`（写 %TEMP%，项目内零残留），
全程**二进制读写**（CRLF 不漂移位），每轮**唯一 basetemp**，注入前落**外部备份** +
sha256，注入后断言「守卫测试失败」，还原后**与注入前外部哈希逐字节比对**（自比无效）。

| # | 注入（临时撤掉修复） | 期望 | 实测 |
|---|---|---|---|
| **I1** | 删 `on_enhance` 的 try/except（字节偏移 splice，限定在 `def on_enhance` 函数体内） | `test_on_enhance_guards_render_error` 必须 **Fail** | ✅ rc=1，`RuntimeError: injected render failure` 透出（守卫未捕获） |
| **I2** | 删 `build_app` 里的 `clear_superres_widgets()` | `test_build_app_clears_superres_registry` 必须 **Fail** | ✅ rc=1，注册表 **5 → 10** 累积 |
| **终验** | 修复齐全（还原后） | 整模块 4 例全绿 | ✅ rc=0 |

- **I1 的字节偏移技巧**：不能用「正则吞中文」去匹配 `except` 块（文件里**更早的** `except` 会被误命中，
  第一版就因此跳过了目标守卫、删错大段）。改法 = `rfind(EXC_END)` 取 `on_enhance` 末尾唯一的
  `    )\r\n    return html or t("compare.no_result")`，再 `rfind(EXC_START)` 取其之前最近的 `except`，
  **限定在 `fn_start..fn_end` 的函数体内**。且**先按原始偏移做 splice，再对缩短后的字符串做 `try:` 替换**——
  否则 `replace` 改变长度会让后续偏移全部错位（第一轮就因此把文件改成 SyntaxError，被 `assert` 拦下）。
- **区分「锚点未命中」与「守卫未捕获」**：I1 第一版注入因锚点写错导致「守卫没反应」，一开始被读成
  「守卫是哑巴」——实际是注入没写进文件。脚本对「锚点缺失/不唯一」与「注入生效但守卫没反应」分别报错
  （经验 64），避免两类反向故障同症状。
- 两个被改文件 `ui/tab_auto.py` / `ui/app.py` 还原后 sha256 **逐字节一致**。

---

## §5 验证（全部现场实测）

### 5.1 新增测试 `tests/test_ui_audit96.py`（4 例）

| 测试 | 覆盖 |
|---|---|
| `test_on_enhance_guards_render_error` | 渲染抛异常 → 降级为错误提示（含转义后的异常信息），不冒给框架 |
| `test_on_enhance_normal_html_passthrough` | 正常路径不被护栏改变：渲染返回什么就返回什么 |
| `test_on_enhance_no_result_when_inputs_missing` | 原图/结果为空仍走既有「无结果」分支（行为未被改动） |
| `test_build_app_clears_superres_registry` | 重复构建，注册表数量稳定（不累积） |

`autouse` fixture 在开头 `set_language("zh-CN") + save_language("zh-CN")`（经验 65：
全局语言是进程级且持久化到磁盘，用例必须**自己建场景**）。

### 5.2 回归与门禁（本轮实测）

| 项 | 结果 |
|---|---|
| 全量测试 | **1308 例 / 88 文件，0 失败**（7 例环境前提 skip） |
| 合成门禁 `scripts/quality_gate.py` | **EXIT=0**：误报 0.78% · P 51.8% · R 81.9% · IoU 49.7% · ΔPSNR +12.49 dB（逐项一致，零回归） |
| 真实门禁 `scripts/real_gate.py --strict-scope` | **EXIT=0**：P 0.0385 / R 1.0000 / IoU 0.7573 / mask 0.0635 / 16.62×（与阶段 86/93/95 完全一致） |
| 注入式反例 | **2/2 全部捕获**，两文件逐字节还原 + sha256 校验通过 |

---

## §6 登记不改（本轮确认过的边界）

1. **`build_auto_tab` 里 `register_superres_widget(...)` 的调用点不动**：它是正确的「写入」方，
   P2 只补「清空」方（`build_app` 入口）。写入/清空分离，单一清口，避免重复清空或漏清。
2. **不引入运行时动态清空**：设置页「应用真实超分」仍靠遍历 `_SUPERRES_WIDGETS` 刷新；
   只在**构建入口**清空（与 U3 的 `clear_registry` 同生命周期），不在每次「应用」时清空（那会误删运行中注册）。
3. **`render_compare_html` 内部不动**：P1 只改 UI 层的异常收口位置；上游渲染逻辑的阶段 38 收敛保持不变。
4. **i18n 自检不改**：`compare.viewer.render_fail` 已存在且中/英齐全，错误文案改用它即可，**不**为本次新增 key。

---

## §7 文件清单

| 文件 | 改动 |
|---|---|
| `ui/tab_auto.py` | `on_enhance` 包 try/except + `format_exception_for_ui` 降级（与同文件 `process_*` 回调一致）；补 `import html as _html` |
| `ui/app.py` | `build_app()` 在 `clear_registry()` 后补 `clear_superres_widgets()`；import 增 `clear_superres_widgets` |
| `tests/test_ui_audit96.py` | **新增 4 例** |
| `docs/96-ui层续审-审计tab_auto与构建期注册表累积.md` | 本文件 |
| `README.md` | §2.44 阶段小节；§4 计数 1304→1308 / 87→88 / 98→99 |
| `TODO.md` | 阶段 96 小节；下一阶段选点更新为 `ui/tab_batch` 第一荒漠 |
| `.workbuddy/memory/*` | STAGES/MEMORY/LESSONS/日志 同步阶段 96 |

---

## §8 新增经验

- **66（构建期全局注册表的累积缺陷是「同源类」）**：任何在 `build_app()` 期间写入的进程级全局注册表，
  必须在构建入口清空，否则重复构建线性累积（0→5→10），且单次运行完全正常、不报错、门禁测不出。
  凡新增此类注册表，直接复用 `clear_*` 模式，与既有清口同生命周期。
- **67（UI 回调异常护栏一致性）**：同一功能页里的回调，若有兄弟回调守了异常，自己就不能裸奔——
  「有的地方有 try/except、有的地方裸露」本身就是缺陷（同风险面多种处理方式）。
  审计手法：扫某页全部 `on_*` / `process_*` 回调，检查异常收口是否一致。
- **68（字节切片注入：偏移必须全部取自同一原始缓冲）**：做「删块 + 替换」式注入时，
  所有 `start/end` 偏移必须从**未修改**的原始字节算；任何会改变长度的 `.replace` 必须发生在
  splice **之后**，否则偏移错位、文件损坏（表现为 SyntaxError 而非守卫失败）。
