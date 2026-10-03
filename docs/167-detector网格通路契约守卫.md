# 阶段 167 · detector 网格通路契约守卫（覆盖度荒漠收口后的定向前置审计）

## 选点背景
`scripts/audit_selector.py --unaudited` 已返回 **0 个模块**：全量排名共 44 个模块
（ui/services/core/utils + 顶层 ≥300 行，阈值 ≥80 行）**全部**已登记进 `AUDITED_SINCE`，
口径自检通过（无退化告警）。驱动 Stage 94→166 的「荒漠榜」主引擎耗尽。

用户以 AskUserQuestion 选定方向 **① detector 非角标通路审计**。经 grep 厘清调用链：
`_periodic_strength` / `_fold_template` / `_tile_out`（二维 FFT 自相关 → 周期折叠模板 →
平铺回原图）实际服务于 `detect_grid_watermark`，而非早先预测的 `detect_tiled_translucent`，
故把本轮精确锁定到算法最复杂的 **网格状平铺水印检测通路**。

## 探针量化（`.workbuddy/scratch/probe_167.py`，10 项）
合成图构造均先 2D 再 `np.stack([..,..,..],-1)` 扩 3 通道，避免广播错误。

| 探针项 | 结果 |
|---|---|
| 输出契约（mask∈{0,255}、regions 字段齐全、kind==KIND_GRID） | ✅ |
| 干净图 / 纯随机噪声不误报（ACF 重写消灭 13~14% 误报回归） | ✅ |
| 细线网格真命中（period×period tile 四边暗线，cov=0.1978、regions=3） | ✅ |
| 大图网格不被 min_area 上限夹紧滤空（1024²，stage 58 回归） | ✅ |
| 退化输入不崩（1×1 / 8×8 / 常值图） | ✅ |
| 证据相交不变式（AND 开时结果 ⊆ 单图证据、覆盖不增） | ✅ |
| hline_256（单轴横线、占空比 ~6%） | ⚠ 不崩但 0 检出 |

**结论**：`detect_grid_watermark` **by design 正确**，本轮 **core/detector.py 零改动**。
唯一非全绿项 `hline_256` 是**稀疏单轴细线**（占空比低于 ACF 周期峰阈值），此类图案归
`detect_edge_text` / `detect_tiled_translucent` 管辖，属设计取舍而非契约违反（不崩、
不误报，行为符合预期）。

## 最小修复
- **无检测器源码改动**（逻辑经探针确认正确）。
- 修正 `scripts/audit_selector.py` 的 `AUDIT_SCOPE_NOTES` 中**过时注记**：
  旧注记称「非角标通路从未逐条审」已不成立——角标/MSER 确系阶段 100 逐条审，且其余子通路
  后续各有专门套件：网格 `detect_grid_watermark`（49/53/106）、边缘文字 `detect_edge_text`
  （52）、半透明 `detect_translucent` / 平铺 `detect_tiled_translucent`（54/82/84/104）。
- 连带刷新 `tests/test_audit_selector.py::test_real_repo_current_top_desert_snapshot`
  的过期快照：阶段 161/163/164 批量补测后 `services/queue_worker` 比值由 18.86 降至 10.04，
  第一荒漠回摆为 `ui/tab_auto`（14.40）。更新预期值 + 历史注记（该测试自述「结论编码，非不变量」）。

## 守卫（新增 `tests/test_detect_grid_watermark_guard.py`，6 例全绿）
与既有质量/精度测试（test_detector_v2 / test_grid_precision / test_grid_resolution /
test_tiled_path_guard 等）**互补不重复**，锁定**契约与不变量**：
1. `test_output_contract_on_detected_grid` — 输出 mask∈{0,255}、regions 字段齐全、kind==KIND_GRID；
2. `test_clean_image_not_false_positive` — 干净图零误报；
3. `test_random_noise_not_false_positive` — 纯噪声零误报（ACF 重写收益回归护栏）；
4. `test_large_grid_detected_min_area_clamp` — 大图真网格不被 min_area 夹紧滤空；
5. `test_degenerate_inputs_no_crash` — 1×1 / 8×8 / 常值图不崩且返回合法契约；
6. `test_evidence_and_subset_invariant` — `DETECT_GRID_EVIDENCE_AND=True` 时结果 ⊆ 单图证据、覆盖不增。

## 验证
- 守卫 6 例全绿（`pytest tests/test_detect_grid_watermark_guard.py`）。
- `python scripts/audit_selector.py --check` ✓ 选点口径自检通过。
- `tests/test_audit_selector.py` 30 例全绿（含刷新后的快照）。
- 行尾检查：`audit_selector.py` 517 LF/0 CRLF、`test_detect_grid_watermark_guard.py`
  144 LF/0 CRLF、`core/detector.py` 3136 CRLF/0 LF（未动）均符合预期（经验 196 行尾纪律）。

## 诚实边界
- **无注入式反例**：本阶段 `core/detector.py` 零改动，注入式反例仅适用于「有源码修复」的情形；
  守卫本身已固化契约与不变量，回归全绿即足够。
- `hline_256` 单轴细线归其他通路管辖，不构成缺陷。
- 非角标其余三条通路（edge_text / translucent / tiled_translucent）早有专门套件，
  本阶段价值落在「补契约/不变量守卫 + 修正过时选点注记」，而非从零审计。
- 选点工具的注记/排除列表会随审计推进而过时，须反向同步（本次即纠正了误导后续选点的陈旧注记）。
