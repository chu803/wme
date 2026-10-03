# 137 · i18n 真值审计——语言包对称性守卫、dict 节点泄漏与 save 校验

> 日期：2026-09-26 ｜ 对象：`utils/i18n.py`（未审榜榜首，荒漠度 5.2 = 1793 代码行 ÷ 347 引用测试 / 28 文件）
> 附带：Stage 137a（O1 补漏，`export_service.make_compare_array` 形状防线）
> 方法：探针量化 → 分类 → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 登记

## 一、探针量化（%TEMP%/s137_probe.py）

| # | 候选 | 探针结果 | 判定 |
|---|---|---|---|
| A | zh/en 结构对称性 | **562 叶子两侧完全对称**：仅单侧存在 0 个、类型不一致 0 个——数据健康，但**无机器校验**（靠零散断言人肉维持） | **defect D1（守卫缺失）** |
| B | {占位符} 一致性 | 不一致 0 个（同 D1，无守卫） | 并入 D1 |
| C | `t('app.tab')`（命中中间层 dict） | 返回 **137 字符 dict repr**（`"{'single': '单图处理（手动涂抹）'…"`）泄漏给界面 | **defect D2** |
| D | `save_language('fr-FR')` | **不校验直接落盘**垃圾 `{"lang": "fr-FR"}`（load 有守卫不生效，但文件永久残留且无告警） | **defect D3** |
| — | 缺失键回退链 / format 守卫 / env 优先级 | 行为正确 | by design |

## 二、最小修复（utils/i18n.py）

1. **D1**：新增公开函数 `validate_i18n_parity()`——把语言包展平为叶子表，三重校验：① 仅单侧存在的叶子键 ② 叶子类型不一致 ③ `{占位符}` 集合不一致；返回问题列表（空=通过）。新增语言自动纳入差集校验。**接入 `__main__` 自检**（562 叶子全量扫，构建期守卫）。
2. **D2**：`t_in` 命中 dict 节点 → 按缺失处理返回 key（不把 repr 泄漏给界面；int/float 等标量仍 `str(val)`）。
3. **D3**：`save_language` 对不在 `TRANSLATIONS` 的代码 **告警 + 拒绝落盘**。
4. 自检补断言：parity 通过、`t("app.tab")=="app.tab"`、非法代码不落盘（临时状态文件 mkdtemp 即删，不碰真实偏好）。

## 三、验证

| 门禁 | 结果 |
|---|---|
| 守卫测试 `tests/test_i18n_audit137.py`（7 用例：parity 干净 / **注入合成漂移证明校验器有效**——缺失叶子、占位符改名、标量类型不一致、dict 替换四种全被检出 / dict 节点返回 key / save 拒绝非法代码） | ✅ 7/7 绿 |
| 137a 守卫 `test_export_service_audit137a.py`(4) + audit134(5) | ✅ 9/9 绿 |
| 联合回归 selfcheck(20，含新登记 i18n) + 137 + 137a + 134 | ✅ 全绿 EXIT=0 |
| 注入式反例（`s137_inject.py`：回退 t_in dict 节点修复） | BUGGY 下守卫 **rc=1 红**；还原 sha256=`9fa2f19b9f47a7a1` **逐字节一致**；回绿 rc=0 |
| 137a 注入（`s137a_inject.py`：回退 `_to_rgb3` 为 `np.asarray`） | BUGGY **rc=1 红**；还原 sha256=`c28c834c698d4942` 一致；回绿 |
| `audit_selector.py --check` | ✅ EXIT=0，登记 36 条 |

## 四、Stage 137a（O1 补漏）摘要

`export_service.make_compare_array` 直传 2D 灰度在画布粘贴处 broadcast 崩溃（docs/136 观察项 O1）。修复：`_to_rgb3` 补 **2D→三通道分支**（此前只丢 alpha，2D 直通），并接入 `make_compare_array`——`make_contact_sheet` 与 `_array_to_data_uri` 一并受益。docstring 同步「接受灰度/RGB/RGBA」。守卫 4 用例（对比图灰度/RGBA、contact sheet 灰度帧、data URI 灰度）。

## 五、登记

- `AUDITED_SINCE` += `"utils/i18n": 137`；`test_legacy_selfcheck.py` MODULES += `("utils/i18n.py", [])`（纯内存断言 + 隔离临时状态文件）。
- **O1 待办已闭环**（137a）；无新增登记未修项。
- 下一站（`--unaudited`）：`ui/components` 4.8 → `ui/tab_history` 4.8 → `services/quality_service` 3.6。

## 六、经验

见 `LESSONS.md` 第 151 条：**「数据当前干净」≠「契约有守卫」——契约型承诺（一一对应/占位符同名）必须配机器可判定校验，并注入合成漂移证明校验器本身有效**。
