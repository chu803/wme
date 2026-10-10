# 阶段 276 · `ui/tab_auto` 续审：钉死首轮（96/163）遗漏的回调分支

> 日期：2026-10-10
> 方法论：legacy-module-audit 1c/5j（已审模块按「契约 docstring 输入类别」枚举做深度复审）
> 结论：**零真缺陷**，22 个首轮未覆盖分支全部 by-design 正确 → 仅钉边界、不引入修复。

---

## 1. 背景：为什么续审

模块级审计链已闭合（`scripts/audit_selector.py --unaudited` 返回空，45 个模块全在
`AUDITED_SINCE`）。按 5j「荒漠度低 ≠ 审完」，已审模块可按**契约输入类别枚举**做更深
一轮复审，找首轮没覆盖的分支。

全量排名里比值最高（测试最薄）的已审模块是 **`ui/tab_auto`（ratio 14.4，阶段 96 首审、
阶段 163 补了掩码精修矩阵）**。阶段 96/163 都聚焦「核心逻辑」，但自动页**其余回调的
错误路由 + 工具按钮 + 历史登记 + 真超分放大后的对比拼接**从未被专属测试钉死。

---

## 2. 契约输入类别枚举

| 回调 | 输入类别 | 分支数 |
|------|---------|-------|
| `process_auto_detect` | None / 分辨率过大 / 坏数据 / 检测核心异常 / 可视化失败 / 未检测到水印 | 6 |
| `process_auto_repair` | inpaint `EmptyMaskError`/`FileNotFoundError`/generic / build_mask 异常 / `record=True` 历史 / 真超分放大+对比拼接 | 6 |
| `process_recommend_preset` | None / 坏图 / 推荐异常 / 正常 | 4 |
| `_reload_detection_mask` | bg=None / det 非 ndarray / 尺寸不符 / det 空 / 正常 | 5 |
| `_clear_editor` | bg=None / 正常 | 2 |

**合计 23 类**（探针按可独立触达的子分支拆成 22 项断言）。

---

## 3. 首轮覆盖现状（对照）

| 已守（96/163） | 未守（本轮补） |
|----------------|----------------|
| `on_enhance` 渲染护栏 + 降级 HTML | `process_auto_detect` 全部错误路由（4 项） |
| `build_app` 注册表清零 | `process_auto_repair` 全部错误路由（3 项） |
| 掩码精修矩阵 8 例（merge/manual/bogus） | `record=True` 历史登记路径 |
| 返回 5 元组不变量（矩阵内） | 真超分放大 + `UI_COMPARE_VIEW` 对比拼接 |
| `process_auto_detect` None-upload + State 掩码透传 | `_reload_detection_mask` / `_clear_editor` 全部分支 |
| | `process_recommend_preset` 全部分支 |

---

## 4. 探针量化（`.scratch/probe_tab_auto_reaudit.py`）

22 项分支逐一注入式触发，断言返回元组长度 / 关键分量：

| 分支 | 实测结果 | 定性 |
|------|---------|------|
| repair.inpaint.EmptyMaskError / FileNotFoundError / generic | 5 元组，原图保留 / 结果空 / 无下载 | by-design ✓ |
| repair.build_mask_fail | 5 元组，结果空 | by-design ✓ |
| repair.record_true | `add_success`+`update_result_path` 各 1 次 | by-design ✓ |
| repair.upscale+compare | 对比图 ndarray（80×264×3），状态栏提示分辨率提升 | by-design ✓ |
| detect.too_large / bad_data / core_fail | 5 元组，原图保留 / 编辑框空 | by-design ✓ |
| detect.vis_fail_fallback | 预览退回原图 | by-design ✓ |
| detect.no_detect_prefill | 空图层预填 + 本地化引导 + State 存零掩码 | by-design ✓ |
| recommend.none / bad / exception / normal | 错误→默认预设；正常→(preset, md) | by-design ✓ |
| _reload 5 分支 | 非法→None；合法→预填红层（alpha=检测掩码） | by-design ✓ |
| _clear 2 分支 | 非法→None；合法→空白画布 | by-design ✓ |

**总计 22 项，疑似缺陷 0 项。**

> 探针初版曾把 `detect.no_detect_prefill` 误报为 DEF——根因是断言用 i18n *键名*
> `"no_detect"` 当子串去匹配*本地化消息值*（中文「未检测到…」）。更正后确认为正确。
> 这是探针自身的断言 bug，非被测缺陷（已独立复验：5 元组 / 空图层 / 零掩码 / 本地化文案）。

---

## 5. 结论 · 归因 · 验证

### 结论
`ui/tab_auto` 的回调错误路由、工具按钮、历史登记、真超分对比拼接**全部符合契约**，
首轮审计留下的只是「未钉边界」的覆盖缺口，不是代码缺陷。**本轮零修复**，只新增
22 例注入式守卫把现状钉死。

### 归因（为何首轮漏了这些分支）
- 阶段 96/163 的审计焦点是「最不直观的核心业务」（掩码矩阵、渲染护栏），按「荒漠榜
  比值」驱动，倾向先打最高风险面；自动页其余回调属「常规错误路由」，被合理地排在
  了首轮之外，而非被遗漏忽视。
- 这些分支没有「已知失败史」，单靠覆盖率指标不会把它们推上待办——这正是 5j「按
  契约类别枚举」要补的盲区。

### 验证（self-falsification 反证）
- 若某条错误路由被后人改坏（如 repair.inpaint 异常时误返回 4 元组或泄漏 traceback），
  `test_repair_inpaint_error_routes_to_5tuple` 必红（已覆盖 3 类异常）。
- 若真超分放大后对比拼接崩溃，`test_repair_upscaled_result_compare_ok` 必红
  （实际 `make_compare_array` 对 40×60 vs 80×120 的差异尺寸**稳健**）。
- 若历史登记被误删 `record=True` 分支，`test_repair_record_true_registers_history` 必红。

---

## 6. 守卫落点

`tests/test_tab_auto_branches_audit.py`（新增，22 例，全绿）。
桩策略 mirror 阶段 163 的 `env` fixture：把 `detect_watermark_mask` / `inpaint` /
`pp_postprocess` / `build_mask_from_editor` / `validate_numpy_image` /
`recommend_preset` / `save_result` / `_CACHE` 全部注入式替换，只验证分支路由与
返回不变量，不依赖 GPU / 真实模型。

`scripts/audit_selector.py` 的 `AUDITED_SINCE["ui/tab_auto"]` 维持 `96`（首审阶段），
新增注释登记本轮 276 续审，避免日后误判该模块「从未被复审」。

---

## 7. 决策

- **不修复**：无真缺陷。
- **只钉边界**：22 例守卫已落地，防止回归。
- **下一步候选**：按 5j 继续枚举下一个已审模块（`ui/tab_models` ratio 14.05 /
  `services/queue_worker` 10.04）的契约输入类别，或转入用户侧阻塞项
  （真实水印图 / GPU 实测 P95 / GitHub push 需联网机器）。
