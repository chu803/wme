# 278 · `services/queue_worker` 续审（5j 深度复审）

## 背景
`services/queue_worker` 已被两轮审计：
- **阶段 88 首审**（`tests/test_services_audit.py` B1/B2）：取消要 yield / `TaskCancelledError` 不重试。
- **阶段 161 契约深挖**（`tests/test_queue_worker_contract.py`）：发现并修复 `or` 静默覆盖 + `retry<0` 永久 RUNNING 两处实质缺陷，钉死构造参数契约 / 终态不变量 / 满空队列 / 重置 / 重试耗尽 / summary / to_dict。

两轮都聚焦「**出错与边界参数**」，漏钉一批**成功路径与字段级不变量**——正常使用时决定上层 UI 能否正确渲染，却**零测试**。本轮按 5j「已审模块按契约输入类别枚举」做更深一层，专补这批首轮遗漏。

## 覆盖清单（首轮遗漏的成功/边界分支）
| 类别 | 守卫 | 验证点 |
|---|---|---|
| 进度夹紧 | `test_progress_clamped_to_unit_range` | `_on_progress(-0.5)→0.0`、`(2.0)→1.0`、回调确实写入 `task.progress` |
| DONE 文案 | `test_done_message_from_dict_status` / `test_done_message_non_dict_result` | `result["status"]` 透传；非 dict 结果回退「完成」 |
| DONE 字段 | `test_done_field_invariants` | `result` 设、`error=None`、`progress=1.0` |
| FAILED 字段 | `test_failed_field_invariants` | `error="boom"`、`result=None`、`progress=0.0` |
| on_update | `test_on_update_called_once_per_task` | 每产出任务触发一次，回传同一对象 |
| cancel 置位 | `test_cancel_sets_cancelled_flag` | `cancel()` → `is_cancelled()==True` |
| 取消字段 | `test_cancelled_fields_error_none_result_none` | CANCELLED 任务 `error/result` 为 None |
| tasks 拷贝 | `test_tasks_returns_copy` | 改返回值不污染内部状态 |
| submit 字段 | `test_submit_returns_task_fields` | `id(8位hex)/name/path/status` 正确 |
| 惰性生成器 | `test_run_is_lazy_generator` | 不消费 `run()` 则不执行（防「调了 run 却没动」隐性 bug） |
| 显式合法值 | `test_explicit_valid_values_honored` | 显式值被采纳，不回退 config |
| 计数辅助 | `test_count_helpers_after_mixed` | 混合结果 `count_done/failed/cancelled` 正确 |

## 结论 · 归因
- **零真缺陷**：所有分支路由与字段不变量均符合预期，首轮遗漏的是「未钉边界」而非代码缺陷（与 276/277 同构）。
- 与阶段 88/161 套件**无重复**：161 已覆盖参数契约与终态计数，本文件只补字段级与行为级遗漏。
- 新增 `tests/test_queue_worker_branches_audit.py` **14 例守卫**；与 `test_queue_worker_contract.py`(14) + `test_services_audit.py`(queue 部分) 联跑 **47 例全绿**，无夹具泄漏。

## 验证
```
pytest tests/test_queue_worker_branches_audit.py tests/test_queue_worker_contract.py tests/test_services_audit.py -q
# 47 passed
```

## 决策
- 仅钉边界，不改动源码（无缺陷可修）。
- `scripts/audit_selector.py` AUDITED_SINCE 注释登记本轮 278。
