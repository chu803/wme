# 阶段 163 · `ui/tab_auto` 掩码精修模式矩阵守卫

## 动机

覆盖度荒漠榜第 3 席 = `ui/tab_auto`（720 代码 / 39 引用测试 / 比值 18.5）。既有覆盖只钉了
「历史登记接线」「`dual_run` 签名」「回调接线契约」；而自动页**最核心、最不直观的业务逻辑——
「掩码精修模式矩阵」（merge / manual / 自动兜底 / 未知模式 × 编辑框状态 × det 状态）——此前零专属测试**。

## 探针（`probe_163.py`：桩 detector/inpaint/postprocess/save/cache，用真实 `build_mask_from_editor` / `merge_masks`）

| # | mode | 编辑框图层 | det(State) | 交给修复引擎的掩码 | 判定 |
|---|------|-----------|-----------|------------------|------|
| 1 | merge | det+补画 | det | 编辑框所见即所得（= det ∪ 补画） | ✓ |
| 2 | merge | 空 | det | det | ✓ |
| 3 | merge | 空 | None | **自动兜底检测**（状态栏 ℹ️ 提示） | ✓ |
| 4 | merge | 仅补画 | det | **仅补画**（det 丢弃） | **边界，钉死** |
| 5 | manual | det+重叠补画 | det | 补画 ∩ ¬det（剥离预填层） | ✓ |
| 6 | bogus | det+补画 | det | 回退 merge + 告警日志 | ✓ |
| 7 | merge | 空 | 3 维数组 | 视为无效 det → 自动兜底 | ✓ |
| 8 | merge | 空 | None（兜底也空） | 空掩码 → **不调引擎**，提示先标记 | ✓ |

量化确认：`merge_masks(..., "manual") == user ∩ ¬det`、`merge_masks(..., "merge") == det ∪ user`；
`process_auto_detect` 无上传 / 正常两条路径均返回 5 元组，检测掩码原样存入 State、预填图层 alpha == 掩码。
**8 条路径 + 2 个检测回调全部 by design 正确**——但矩阵整体无测试保护。

## #4 的语义辨析（本阶段最重要的「没动手」）

mode_info 承诺「合并 = 检测结果 ∪ 你的精修（**补漏 + 擦除**都生效）」。若把 merge 实现成严格
`det ∪ 编辑框`，字面上更贴近「∪」，但**橡皮擦会静默失效**——用户擦掉误检后，`det ∪ 编辑框`
会把误检加回来。故「编辑框所见即所得」是**擦除可用的前提**；det 只在编辑框**为空**时兜底
（`#2/#3`）。`#4`（清空编辑框后只补画）因此是**设计使然**，本轮不改，而是**显式钉死**，
防止后人以「修复 merge 语义」为名改成严格并集。

## 守卫

`tests/test_tab_auto_mask_modes.py`（**15 例**）：矩阵 8 条 + 返回形状参数化 5 条 + `process_auto_detect`
2 条。**观察点选「交给修复引擎的实参」**——掩码不出现在 `process_auto_repair` 的返回值里，
故桩掉 `inpaint` 捕获其实参掩码来断言，而非看返回值（返回值只有图像/状态/下载路径）。

## 注入式反例（守卫有牙）

`inject_163.py`（系统 temp + `retention=all`，零删除）：把 merge 分支改成严格并集
`merge_masks(det, user_mask, MASK_MODE_MERGE, ...)` → pytest **exit=1（红，
`test_merge_strokes_only_drops_det` 命中）**；还原字节级一致 sha=`2294337a9ea0b2c1…`
→ **exit=0（绿）**。守卫有牙：YES。

## 回归

新 15 + `test_image_history_record` + `test_processing_prefs` + `test_ui_wiring_contract110`
+ `test_legacy_selfcheck` = **135 例全绿**。

## 诚实边界

- 只钉「掩码模式矩阵」契约；`process_auto_repair` 的后处理/超分/历史登记分支由其它阶段的测试覆盖。
- `#4` 的语义是**设计裁决**而非缺陷：若产品上希望「清空后仍合并 det」，应改 UI 文案或加独立开关，
  而不是改实现——本轮不改行为。
- 探针/守卫用桩引擎，不验证 `inpaint` 真实修复质量（那由三条质量门禁负责）。

## 产物

`docs/163-tab_auto掩码精修模式矩阵守卫.md`
