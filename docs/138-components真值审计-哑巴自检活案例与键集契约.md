# 138 · ui/components 真值审计——哑巴自检活案例与键集契约

> 日期：2026-09-26 ｜ 对象：`ui/components.py`（荒漠度 4.8 = 388 代码行 ÷ 80 引用测试 / 7 文件）
> 方法：探针量化 → 分类 → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 登记

## 一、探针量化

| # | 候选 | 探针结果 | 判定 |
|---|---|---|---|
| D1 | `__main__` 自检 `except Exception → print("注意…") → exit 0` | **实锤活案例**：本机 gradio 下组件构造**真实失败**（`'Slider' object has no attribute 'page'`——`build_repair_params` 在 Blocks 外构造），自检却打印「模块本身无语法错误」并 **退出码 0**——自检自启用以来从未真正验证过组件构造 | **defect** |
| D2 | `param_values` 键集 ↔ `RepairParamHandles` 字段集 | 两头各写一份、无一致性断言（漂移只有靠 D1 修复后的自检才兜得住） | **defect（契约缺失）** |
| — | `render_scope_note`（config 派生承诺面）、`_kind_label` 回退契约、`aggregate_detection_stats` 聚合语义、`get_engine_choices` 降级 | 行为正确（scope 面已有 test_watermark_scope95 双向守卫） | by design |
| 观察 | `_SUPERRES_WIDGETS` 注册表无去重/弱引用，多轮重建界面会累积陈旧句柄 | 当前生命周期（启动构建一次 + 测试显式 clear）不触发 | 登记不修 |

## 二、最小修复（ui/components.py）

1. **D1**：自检体抽成 `_run_selfcheck()` 并包在 **`with gr.Blocks():`** 内执行（消除「某些版本要求上下文」的环境差异）；任何异常 → **`SystemExit("自检失败：…")`（非零退出）**，删除吞异常的 catch-all「注意」分支。修复后自检**首次真正通过**：20 项参数 + 导出 2 项构造成功。
2. **D2**：自检补断言 `param_values 键集 == {f.name for f in fields(handles)}`（导入 `fields as _dc_fields`）。

## 三、验证

| 门禁 | 结果 |
|---|---|
| 守卫测试 `tests/test_components_audit138.py`（4 用例：键集↔dataclass 字段契约（Blocks 内构造）/ 子进程自检 rc=0+「自检通过」/ 摘要 i18n 本地化无 None / 检测统计 Markdown 占位与 `_kind_label` 不泄漏键名） | ✅ 4/4 绿 |
| 子进程守卫登记 `test_legacy_selfcheck.py` MODULES += `("ui/components.py", [])`（纯内存构造无文件写入） | ✅ 联合回归全绿 |
| 注入式反例（`s138_inject.py`：`param_values` 删 `sharpen` 键，复现「键集漂移被哑巴自检吞掉」） | BUGGY 下守卫 **rc=1 红**；还原 sha256=`35af7a31b150cb6b` **逐字节一致**；回绿 rc=0 |
| `audit_selector.py --check` | ✅ EXIT=0，登记 37 条 |

## 四、登记

- `AUDITED_SINCE` += `"ui/components": 138`。
- 下一站（`--unaudited`）：`ui/tab_history` 4.8 → `services/quality_service` 3.6 → `utils/brand` 3.3。

## 五、经验

见 `LESSONS.md` 第 152 条：**自检的 catch-all 降级分支是吞 bug 黑洞——降级前必须证明「失败确实由预期环境原因引起」，否则任何真 bug 都被印成「注意」且退出码 0**（本阶段活案例：构造失败被吞、自检长期假绿）。
