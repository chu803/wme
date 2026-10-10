# 283 · `ui/tab_single` 续审 — 钉死首轮遗漏的返回分支不变量 + 修声明漂移

> 5j 深度复审链第 8 站（接 276–282）。
> 结论：**零真缺陷**，另修复一处**声明漂移**（docstring 引用已合并掉的测试文件）。
> 与 276–282 同构：首轮遗漏的是「未钉边界」而非代码缺陷。

## 背景

`ui/tab_single`（613 行，阶段 5 单图主标签页）是覆盖度荒漠榜首位模块。首轮审计分两层，已把**接线**与**耗时**钉得很密：
- **阶段 110**（`test_ui_wiring_contract110.py`）：`len(inputs) == len(形参) − 内部尾部形参` 的**全 ui 层通解守卫**；`record` 必须是后缀（**P0 回归**：原把内部开关排在 `dual_run` 前 ⇒ 21 个控件整体右移，「双跑择优」失效 + 默认路径丢历史）；`_SINGLE_WIDGET_PARAMS` 与形参表前缀一致；五元组返回与 docstring 声明一致。
- **阶段 190**（`test_single_elapsed_display.py`）：`single.msg.elapsed` 双语同形、耗时机检起止点顺序断言、`_compose_status` 三段拼接。

首轮聚焦「接线契约 + 耗时口径」，但**完全遗漏了 `process_single` 九条返回分支的返回值不变量**（每条分支回传什么图 / download 是否为空 / status 是否对应文案）与 `_extract_image` 的输入形态矩阵。

## 探针（`.scratch/probe_tab_single_reaudit.py`）

> 注：探针首版有桩错误——`build_mask_from_editor` 返回全零掩码 ⇒ 所有「成功」用例都误触 `no_mask` 早退，输出了一片 `False`。修正为**非空掩码**后重跑（LESSON 252/269：先拿 traceback / 过程量，别信一次可疑结果）。

探针实证 11 类 `_extract_image` 形态 + 九条返回分支 + record/缓存/超分/on_enhance，**行为全部正确**。

## 覆盖清单（39 例守卫）

| 类别 | 守卫 | 钉死的不变量 |
|---|---|---|
| `_extract_image` 形态 | None / dict(有·无 background) / 空 dict / tuple / **空 tuple** / list / **空 list** / ndarray | 非法与空形态一律 None（不 IndexError） |
| | str·int·float·object 等未知标量 | 垃圾不当图片传出 |
| `process_single` 返回 | `no_image` → **全 None** 五元组 | 唯一连原图都不回传的分支 |
| | 七条失败分支（too_large·bad_data·mask_fail·no_mask·EmptyMaskError·no_model·generic_fail） | **恒五元组 + 原图仍回传 + result/download 均为 None + status 逐条对账** |
| | **save_fail（成功分支）** | 修复成功仅落盘失败 ⇒ **仍回传结果图**，download 为 None |
| | 成功路径 | 五元组：原图 / 结果数组 / compare / status / out_path |
| `UI_COMPARE_VIEW` | 开 ⇒ 第三元组非 None；关 ⇒ None | 开关直接决定 compare |
| `record` 开关 | False ⇒ 不调 `add_success`；True ⇒ 恰好一次 | 登记次数 |
| 缓存容错 | `add_success` 返回 None / 抛错 ⇒ **主流程不受影响**（仍回传结果与 download） | 历史登记不得反噬主流程 |
| 超分放大 | 后处理改 shape ⇒ 追加 `auto.msg.upscaled` 且 factor 正确；形状未变 ⇒ 不追加 | 防误报 |
| `on_enhance` | 四类模式均返回非空 HTML；None 原图/结果 ⇒ `compare.no_result` | 成功路径与空态 |

## 附带修复 · 声明漂移（docstring）

`_SINGLE_WIDGET_PARAMS` 的 docstring 引用 `tests/test_tab_single_audit110.py`——该文件**已被合并**进 `tests/test_ui_wiring_contract110.py`，引用指向不存在的文件。
阶段 283 更正为现行文件名，并点明组 D 的前缀守卫；另加 `test_docstring_references_existing_test_file` 防同类漂移复发。

> 这正是项目「组 D 契约声明与实现一致」精神的延伸：声明里指向的守卫必须真的存在，否则后人照着改会找不到文件。

## 结论 · 归因

- **零真缺陷**。首轮（110/190）已修全部真缺陷（含 110 的 P0 接线错位）。
- 本轮补的是**九条返回分支的返回值不变量**——「每条失败分支都回传原图、都不给下载、status 都对应专属文案」这套对账此前完全没被机器判定，一旦有人把某分支误改成返回结果图（或漏掉 `download=None`），不会有任何测试报警。

## 验证

- 新增 `tests/test_tab_single_branches_audit.py`：**39 例守卫全绿**。
- 全回归（283 + 110接线 + 190耗时 + 导出对比 + 历史登记 + 处理偏好）：**107 例全绿**、EXIT=0、无夹具泄漏。

## 自反证伪

- 若某失败分支被改成回传结果图，`test_failure_branches_return_orig_but_no_result` 将失败。
- 若 `save_fail` 分支把结果图也吞掉（`return image, None, ...`），`test_save_failure_still_returns_result_image` 将失败。
- 若 `_extract_image` 的空 tuple/list 分支去掉长度守卫，`test_extract_image_shapes[()]` / `([])` 将抛 IndexError。
- 若 `record` 开关被反向接线，`test_record_false_skips_cache` / `test_record_true_registers_once` 将失败。
- 若缓存容错被改成「登记失败即中断主流程」，`test_cache_failure_does_not_break_main_flow` 将失败。
- 若 docstring 回退引用已删除的文件，`test_docstring_references_existing_test_file` 将失败。

## 交付

- 守卫：`tests/test_tab_single_branches_audit.py`（39 例）
- 修复：`ui/tab_single.py`（docstring 声明漂移）
- 探针：`.scratch/probe_tab_single_reaudit.py`
- 登记：`scripts/audit_selector.py` AUDITED_SINCE 注释补 283
- 记忆：`.workbuddy/memory/MEMORY.md` 进度指针 + 本日工作日志