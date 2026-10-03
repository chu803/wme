# 阶段 112 · `ui/tab_media` 真值审计

> 选点：`scripts/audit_selector.py --unaudited` 榜首 **`ui/tab_media` 12.8**
> （702 代码行 / 55 引用；5 个测试文件：`test_media_recommend` / `test_pdf_history` / `test_validators_media` / `test_video_management` / `test_video_superres`）
> 审计日期：2026-09-24
> 结论：**接线契约全绿，仅一处文档误写（F1），无控件≠形参错位（阶段 110 P0 未复现）**。

---

## 0. 审计目标与方法

`ui/tab_media` 是「文档与视频」标签页，承载 PDF / 视频去水印的全部 Gradio 回调装配
（`run_pdf` / `run_video` / `run_video_compare` / `on_frame_gallery_select` / `export_frame_file` / `export_sheet_file`）。
它天然是**阶段 110 P0 模式（控件数 ≠ 形参数 = 静默错位）**的高危候选，且 `run_video`
文档曾写「四元组」，疑似阶段 110 F5（文档/返回数不符）同类。

方法：
1. 读全文件（1–1009 行），逐一定位回调装配段（`*.click` / `*.select`）。
2. 写 AST 探针 `%TEMP%/s112_probe.py`，实测每个回调的「控件数 / 形参数 / 输出组件数 / 真实返回元数」四元组对账。
3. 修正探针计数口径（剔除 Gradio 注入的 `progress` / `evt`），复测。
4. 修复真实缺陷（F1），把接线契约钉成静态回归守卫。
5. 受影响面定向跑 + 登记 `AUDITED_SINCE`。

---

## 1. 探针结论（误报 → 修正）

首版探针把 `progress`（`.click`/`.change` 末位自动注入）与 `evt`（`.select` 首位的
`gr.SelectData`）算进"形参数"，误报：

```
run_pdf:  params==inputs==12 ? False (pos=10, inputs=12)
run_video: params==inputs==16 ? False (pos=11, inputs=16)
```

修正计数口径——**接线形参数 = 全部形参 − Gradio 自动注入项**——后重测，全部对齐：

| 接线 | 控件数 | 形参数(去注入) | 输出组件 | 真实返回 | 结论 |
|---|---|---|---|---|---|
| `pdf_btn.click → run_pdf` | 12 | 12 | 3 | 3 元组 | ✅ |
| `video_btn.click → run_video` | 16 | 16 | 6 | 6 元组 | ✅ |
| `v_compare_btn.click → run_video_compare` | 4 | 4 | 1 | 单值 | ✅ |
| `v_frame_gallery.select → on_frame_gallery_select` | 3 | 3(evt注入) | 2 | 2 元组 | ✅ |
| `v_export_frame_btn.click → export_frame_file` | 4 | 4 | 1 | 单值 | ✅ |
| `v_export_sheet_btn.click → export_sheet_file` | 1 | 1 | 1 | 单值 | ✅ |

**阶段 110 P0 在本模块未复现**：所有回调的控件数 == 形参数 == 输出组件数，Gradio 按位置传参不会错位。
（这正是经验 119 要提醒的——"看起来像已知缺陷"先用计数口径复测，别直接套修复。）

---

## 2. F1 · 真实缺陷：`run_video` 文档「四元组」误写

`run_video` 在 **5 个 return 点均返回 6 元组**（无文件 / 校验失败 / 处理失败 / 成功 / 异常），
但 docstring 写的是「返回值（四元组，对应四个输出组件）」（`ui/tab_media.py:446`），
少算了第 5 元素（结果帧浏览画廊值 `v_frame_gallery`）与第 6 元素（导出文件占位 `v_export_file`，恒 `None`）。

**性质**：纯文档缺陷，绑定与输出完全正确（6 控件参数 ↔ 6 输出 ↔ 6 返回）。**只修文档，不动接线**
——否则会把一个干净的模块改坏。

修复（`ui/tab_media.py:446-448`）：

```python
返回值（六元组，对应六个输出组件）：
    (日志文本, 视频组件更新对象, ffmpeg 合并命令文本, 检测命中率统计 Markdown,
     浏览画廊值(各检测帧抽样图，供阶段 37 结果浏览), 导出文件组件占位(None))
```

对照：`run_pdf` 文档「三元组」与其 3 元组返回一致，无需改。

---

## 3. F2 · 携带项：`sensitivity` 透传（非本模块缺陷）

`run_pdf`(`:248`)、`run_video`(`:488`) 仍把 `sensitivity=sensitivity` 透传给
`process_pdf` / `process_video`。核查：

- `services/pdf_service.py:321` 与 `services/video_service.py:415` 的 `process_*` **形参仍接受** `sensitivity: Optional[float] = None` ⇒ 不会 `TypeError`。
- `services/auto_pipeline.py:198/227-231/459` 中 `sensitivity` **实际驱动** `recommend_preset` / `resolve_preset`（智能推荐预设的敏感度输入）。

⇒ 阶段 109 判的"dead"在调用点被跨模块引用复活：**接线层正确，不改动**。若在此删掉透传，反而会
让"智能推荐"失去敏感度输入。登记为经验 120（"死参数"断言须调用点复验）。

---

## 4. F3 · 回归守卫（静态 AST）

新增 `tests/test_tab_media_audit112.py`，把上述接线契约钉成机器可判定守卫（不导入 `ui/tab_media`，
避免拉起 Gradio 全栈）：

- `test_wiring_contract`：6 个回调的「控件数 == 形参数(去注入) == 输出数」逐一断言。
- `test_return_arity_matches_outputs`：`run_pdf` 恒 3 元组、`run_video` 恒 6 元组。
- `test_docstring_arity_claims`：`run_pdf` 文档含「三元组」、`run_video` 文档含「六元组」且不含「四元组」。

> 契约表是**回归护栏**：将来若有人"增删控件却不同步函数形参 / 改了返回却不同步 outputs"，
> 测试立即红。若发生合理契约变更，需同步更新 `EXPECTED` 表与文档。

---

## 5. 验证

定向受影响面（避开沙箱批量删除护栏：用唯一非 Temp basetemp，未纳入 4 个删文件重的测试文件）：

```
tests/test_tab_media_audit112.py        +3
tests/test_audit_selector.py           (改了 AUDITED_SINCE，自身测试必进，经验 115)
tests/test_media_recommend.py
tests/test_pdf_history.py
tests/test_validators_media.py
tests/test_video_management.py
tests/test_video_superres.py
→ 88 passed, 1 skipped (torch 后端存在时跳过"无后端回退"用例), EXIT=0
```

全量跑受沙箱批量删除护栏限制无法在本环境跑完（与本轮零删除改动无关，属环境 artifact，见阶段 111 §5 同款说明）。

---

## 6. 登记与交付

- `scripts/audit_selector.py`：`AUDITED_SINCE` 追加 `"ui/tab_media": 112,`。
- `tests/test_tab_media_audit112.py`：新建（3 项守卫）。
- `ui/tab_media.py`：仅 `:446` 文档修正，逻辑零改动。
- 经验 117/118/119/120 落 `LESSONS.md`；README §2.59、TODO P1.60 同步。

---

## 7. 下一站

未审榜下一站：`services/video_service` 11.3 → `cli` 10.5 → `core/superres` 10.5 → `services/postprocess_service` 10.5。
（"交图"仍是无真实图数据的唯一 P0，见 P1.56。）
