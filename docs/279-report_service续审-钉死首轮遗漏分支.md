# 279 · `services/report_service` 续审 — 钉死首轮遗漏的成功/边界分支

> 5j 深度复审链第 4 站（接 276 `ui/tab_auto` / 277 `ui/tab_models` / 278 `services/queue_worker`）。
> 方法论：**对已审模块按「契约输入类别枚举」做更深一轮**，钉死首轮遗漏分支。
> 结论：**零真缺陷**，首轮遗漏的是「未钉边界」而非代码缺陷（与 276/277/278 同构）。

## 背景

`services/report_service`（609 行，阶段 17 核心服务）把一批处理结果汇总成可交付的 HTML/PDF 报告。
首轮审计分三层：
- **阶段 17 基础单测**（`test_report_service.py`）：build_entries 过滤 + 单图退化、HTML 自包含、PDF 真文件、质量开关、空记录抛 `FileNotFoundError`、PDF 在 fitz 缺失回退 HTML。
- **阶段 166 契约口径**（`test_report_service_contract.py`）：`_summary_stats`/`_quality_summary` 四态（empty/none/partial/full）+ 计数对账、HTML/PDF 汇总一致（阶段 88 缺陷回归）、`records_per_page<1` 显式 `ValueError`、`include_quality` 关闭不调用 `evaluate_quality`。
- **阶段 169 i18n**（`test_report_service_i18n.py`）：报告交付物文案双语化。

首轮聚焦「出错时别崩」与「口径一致性」，但**成功路径与边界输入的返回值不变量未钉**：
字段默认值、status 过滤边界、ts 格式化、`generate_html` 空/质量/推荐分支、`export_report` fmt 归一与 `.pdf`→`.html` 改写（非 fallback 分支）。

## 方法

1. **枚举契约输入类别**：对 `build_entries` / `generate_html` / `export_report` 的每个入参列出「成功/边界/异常」取值。
2. **注入式验证**：`monkeypatch` 替换 `rs.evaluate_quality` / `rs.reason_line` / `rs.bars_html`，只验证分支路由与返回字段不变量，**不依赖 GPU/真实模型/网络**。
3. **守卫钉死**：把每个被确认的不变量写成机器可判定的断言。

## 覆盖清单（18 例守卫）

| 类别 | 守卫 | 钉死的不变量 |
|---|---|---|
| build_entries 字段默认 | `name` None/空 → `"image"` | `or "image"` 兜底 |
| | `engine`/`mode` None → `"auto"` | `or "auto"` 兜底 |
| | `params` None → `""` | `or ""` 兜底 |
| | `ts=0.0` → `"—"`；`ts>0` → 19 位格式串 | `time_str` 两分支 |
| status 过滤边界 | 仅精确 `"success"` 入列；`pending/cancelled/running/failed` 全滤 | 非 `"success"` 一律丢弃 |
| | `result_path=None` 即使 `status="success"` 也滤 | `not result_path` 短路 |
| | `original_path=""` → 退化为单图（`img_is_compare=False`） | 空串与 `None` 同路径 |
| 成功路径质量 | 原图存在 + `include_quality=True` → `quality` 被赋值 | 首轮只在失败/关闭侧验过 |
| | `recommendation` 透传至 entry | 字段级不变量 |
| generate_html 分支 | 空条目 → 不崩且含空占位 `report.empty_records` | 不内嵌任何图 |
| | `quality=None` → 渲染「未评估质量」 | 卡片两分支之一 |
| | `quality` 有值 → 渲染分数 | 卡片两分支之二 |
| | `recommendation` 存在 → 渲染理由行 + 分数条 | `reason_line`/`bars_html` 调用 |
| export_report fmt | `fmt=None` → html 路径 | `(fmt or "html").lower()` |
| | `fmt="html"` + out_path 末位 `.pdf` → 改写 `.html` | line 564 非 fallback 分支 |
| | `fmt="PDF"` 大写 → 归一为 pdf（有 fitz）/ 回退 html（无 fitz） | 大小写归一 |

## 结论 · 归因

- **零真缺陷**。首轮审计（17/166/169）已修全部真缺陷（阶段 88 口径分裂、阶段 166 `records_per_page<1` 静默退化、阶段 169 i18n）。
- 本轮补的是**成功路径字段默认值 + 边界路由**的未钉不变量——这正是「荒漠度低 ≠ 审完」的体现：首轮靠「出错路径」覆盖了鲁棒性，但成功路径的精确返回值未被机器判定守护，一旦有人改 `or "image"` 兜底逻辑不会被任何测试抓到回归。
- 18 例守卫全部落在真实执行路径上（注入点均指真实调用点），非复刻品。

## 验证

- 新增 `tests/test_report_service_branches_audit.py`：**18 例守卫全绿**。
- 与既有 `test_report_service.py`（阶段 17）/ `test_report_service_contract.py`（阶段 166）/ `test_report_service_i18n.py`（阶段 169）联跑：全部通过、**无夹具泄漏**（EXIT=0，约 53 例）。

## 自反证伪（self-falsification）

- 若有人把 `build_entries` 的 `name = _src_attr(src, "original_name", "image") or "image"` 误改为 `or "unnamed"`，`test_field_name_none_defaults_to_image` 将失败——守卫**真命中**该回归点。
- 若 `export_report` 的 `.pdf`→`.html` 改写（line 564）被误删，`test_export_report_html_out_path_pdf_rewritten` 断言「原 `.pdf` 路径不应被写出」将失败。
- 若 `generate_html` 在 `quality=None` 时改为渲染分数而非「未评估质量」，`test_generate_html_card_quality_none_branch` 将失败。
- 若 status 过滤被放宽成「非 failed 即收」，`test_only_exact_success_status_kept` 将失败。

## 交付

- 守卫：`tests/test_report_service_branches_audit.py`（18 例）
- 登记：`scripts/audit_selector.py` AUDITED_SINCE 注释补 279
- 记忆：`.workbuddy/memory/MEMORY.md` 进度指针 + 本日工作日志
