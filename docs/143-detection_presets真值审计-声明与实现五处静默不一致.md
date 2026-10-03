# 143 · core/detection_presets 真值审计——「声明 ≠ 实现」的五处静默不一致

> 日期：2026-09-27 ｜ 对象：`core/detection_presets.py`（荒漠度 3.1 = 271 代码行 / 17786 字节 ÷ 4 个引用测试文件里 88 个用例）
> 方法：探针量化 → 分类 → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 登记
> 引用测试文件：`test_detection_presets.py` / `test_detector_v3.py` / `test_preset_recommend_accuracy.py` / `test_watermark_scope.py`

## 一、探针量化

三轮探针（`%TEMP%/s143_probe.py` / `s143_probe2.py` / `s143_probe3.py`，零残留）覆盖 8 个候选面。

| # | 候选 | 探针结果 | 判定 |
|---|---|---|---|
| **D1** | `resolve()` 声明「只输出非 None 字段」，但 `sensitivity` **无条件**输出 | `get_preset("general").resolve(0.99)` → `{"sensitivity": 0.99}`（general 全字段 None，按声明应返回 `{}`） | **defect（P1，声明≠实现）** |
| **D2** | `register_preset` 把类型错**伪装成值错** | `(None)`/`({})` → `ValueError("预设必须提供非空 name。")`（**指错原因**）；`("abc")`/`(42)` → `AttributeError` ⇒ **同一输入族两种异常类型** | **defect（P1，经验 157 反向违反）** |
| **D3** | `__main__` 自检硬编码 9 项清单 + `in` 判定 | 内置 **10** 个（漏 `ai_multi`）而自检**静默通过**；`in` 对「多出来的」不敏感 ⇒ 未来新增预设永远绿 | **defect（P1，哑巴自检）** |
| **D4** | 模块 docstring 的预设清单漏列 | docstring 8 项 / `_BUILTIN_PRESETS` 10 项 ⇒ 漏 `ai_logo`、`ai_multi` | **defect（P2，文档失真）** |
| **D5** | `resolve_preset` 空白口径不一致 | `""` 走 `not name` **静默**；`"   "` 走 `get_preset` → None → **多发一条告警** | **defect（P2，经验 158 同族）** |
| — | `preset_display` 的 i18n 缺键探测 | zh-CN→「AI 平铺水印」、en-US→「AI Tiled Watermark」；缺键确返回 key 本身 ⇒ 探测逻辑**成立** | by design ✅ |
| — | `resolve_preset(name, None)` 抛 `TypeError` | `base_sensitivity` 类型标注为 `float`（必填）；`process_image:182` 与 `process_file:425` **都在入口回填 sensitivity** ⇒ 崩路径**不可达** | by design（**登记**） |
| — | `base_sensitivity` 值域（nan / −1 / 2.0 / inf） | 静默通过；但该图（text/corner 通路）对 sensitivity **无区分力**（8 个值恒 465 px）⇒ 越界消费属 `detector` 职责 | 观察项（**登记**） |
| — | `list_presets()` / `get_preset()` 返回可变共享引用 | `mutation_leaks=True`；但**全仓扫描无任何代码改预设字段**（`P10` → `(none)`） | 看似缺陷实为无害 → **登记 + 守卫钉住** |

### ⚠ 探针自身的两次翻车（如实记录）

1. **P3 首版误判**：探针里变量取名 `display_known_zh`，但赋值前循环已把语言切到 `en-US` ⇒ 读到英文，一度看起来像「中文界面漏出英文」的缺陷。**修正后证明 `preset_display` 完全正确**——这是**探针的 bug，不是产品的 bug**。
2. **P2 首版无区分力**：用**纯色图**测「`sensitivity` 越界后果」，得到全部 `mask_nonzero=0`，看似「越界必然导致零检出」，实为该图本来就无水印。改用带水印图后 8 个值恒 465 px ⇒ 该图不走 sensitivity 通路。**⇒ 「探针结果为零」必须先问「夹具本身有没有区分力」。**

### 全量回归暴露的两次探针路径误判（重要方法论收获）

我一度据 `resolve_preset('general', None)` 抛 `TypeError` 判定「**P0：默认参数组合会崩**」，并据此写了「可达路径」分析。**读调用链后证伪**：

```
process_image:182   sensitivity = config.DETECT_TRANSLUCENT_SENSITIVITY if sensitivity is None else sensitivity
process_file:425    同上
```

**两个入口都在最前面把 `sensitivity=None` 回填成 config 默认** ⇒ 传给 `resolve_preset` 的永远非 None。⇒ 崩溃路径**当前不可达**，属「违反 `base_sensitivity: float` 类型契约」的显式失败，**不是缺陷**。

**教训**：判定「可达性」必须**读完整调用链**（含每一层的参数回填），不能只凭「签名默认值是 None」推断。

## 二、最小修复（core/detection_presets.py，5 处）

| # | 修复 |
|---|---|
| D1 | `resolve` docstring **订正**：明确「除 `sensitivity` 外只输出非 None 字段」+ 说明 `sensitivity` 是**唯一例外**（预设未设时用 base 兜底）及其理由（下游 `sensitivity=None` 会去读 config，预设解析的职责正是替调用方决定敏感度）。**只改声明不改行为**——行为是对的。 |
| D2 | `register_preset` 校验拆两层：`isinstance(preset, DetectionPreset)` → 否则 `TypeError`（**信息含真实类型**）；再查 `name` → `ValueError`。删除死条件 `not preset`（dataclass 无 `__bool__`/`__len__`，恒为真）。 |
| D3 | `__main__` 自检改为与 `_BUILTIN_PRESETS` **集合对账** + `choices` 数量对账 + 每个内置预设「取回同一对象」+ 补 `ai_multi` 并集断言 + 补注册校验断言。 |
| D4 | 模块 docstring 清单补齐 `ai_logo` / `ai_multi`，并注明「真值来源永远是 `_BUILTIN_PRESETS`，本清单只作导览，完整性由守卫对账」。 |
| D5 | `resolve_preset` 判「未指定」统一为 `name is None or not str(name).strip()` ⇒ 空串与各类空白串同语义（都静默回退），**真拼错的非空名仍告警**。 |

## 三、验证

| 门禁 | 结果 |
|---|---|
| 守卫测试 `tests/test_detection_presets_audit143.py`（**48 用例**：K0 内嵌旧实现存在性自检 / K1 类型错值错分离 6+1+3 / K2 死条件存在性 / K3 sensitivity 例外 + docstring 契约 + 未设键不外泄 / K4 空白口径 7+3+未知名仍告警 / K5 四方清单对账 + 键名与下游形参一致 / K6 ai_multi 并集 + ai_gradient 隔离 / K7 可变引用边界钉住） | ✅ **48/48 绿** |
| 关联回归（`test_detection_presets` 23 + `test_detector_v3` + `test_preset_recommend_accuracy` + `test_watermark_scope` + `test_preset_recommender` + `test_auto_pipeline_audit127` + 子进程自检守卫 = 45 项） | ✅ rc=0 全绿 |
| **注入式反例（逐项 5 次）** | 见下表，**5/5 精准命中，`collect_err=False`**（无收集期假绿灯） |
| 子进程自检 `python core/detection_presets.py` | ✅ rc=0（新增 `core/detection_presets.py` 进 `test_legacy_selfcheck.MODULES`） |
| `audit_selector.py --check` / `--unaudited` | ✅ EXIT=0，登记 **42** 条；下一站 `utils/path_helper` 2.1 |

### 注入式反例明细（`s143_counter.py`）

| 注入项 | rc | 收集期报错 | 失败数 | 命中预期用例 |
|---|---|---|---|---|
| D2 `register_preset` 回退为旧校验 | 1 | False | **16** | 4/4 ✓ |
| D5 空白口径回退为 `if not name:` | 1 | False | 2 | 1/1 ✓ |
| D1 `resolve` docstring 回退 | 1 | False | 1 | 1/1 ✓ |
| D4 模块 docstring 清单回退 | 1 | False | 1 | 1/1 ✓ |
| D3 自检对账回退为硬编码 | 1 | False | 1 | 1/1 ✓ |

还原：sha256 = `8f6efde9d3750a18…` **逐字节一致 + 归一化文本一致**，回绿 rc=0。**`ALL_INJECTIONS_OK = True`**。

### 守卫自检的两处翻车（同样如实记录）

1. **K0 首版**：用 `monkeypatch` 改 `dp.get_preset` 来观测「旧实现是否查注册表」，但 `_legacy_resolve_preset` 里引用的是测试文件顶部 `from … import get_preset` 绑定的**局部名** ⇒ patch 打不到。改为**显式注入 `lookup` 参数**。
2. **K1 首版**：用 `assert "not preset or" not in _SRC` 做文本断言，但修复后的 **docstring 里引用了旧写法作说明** ⇒ 文本扫描把「说明」当「代码」，**误报**。改为 **AST 扫 `register_preset` 函数体内的 `if` 测试节点**，并正向断言源码含 `isinstance(preset, DetectionPreset)`。

## 四、登记与边界

### 观察项（本轮**不修**，已登记）

| 项 | 现象 | 不修理由 |
|---|---|---|
| `resolve_preset(name, None)` | 抛 `TypeError`（`float(None)`），信息**不含参数名** | `base_sensitivity` 类型标注为 `float`（必填），传 None 是**违反契约的显式失败**；且两个消费入口都在最前面回填了 sensitivity ⇒ **路径不可达**。属「可选性不对称」（`name` 可空而 `base_sensitivity` 不可空）的文档问题，已在 `resolve`/`resolve_preset` 的 docstring 写明。 |
| `base_sensitivity` 值域无校验 | nan / −1 / 2.0 / inf 静默通过 | 越界值的**消费方是 `detector`**，不是本模块；且实测该参数在目标通路上无区分力差异 ⇒ 属 `detector` 职责边界。 |
| 返回对象是可变共享引用 | `get_preset("x").field = v` 会污染进程内注册表 | 全仓扫描确认**无任何代码修改预设字段**（`P10` → `(none)`）⇒ 无用户可见后果。改 `frozen=True` 会破坏「覆盖式注册」既有语义 ⇒ 按技能判据归「看似缺陷实为无害」，**用 K7 守卫钉住当前语义**（将来有人开始改字段时会被提醒那是污染而非改副本）。 |
| `ui/tab_batch.py:89` 的 `_PRESET_DISPLAY` | **构建期快照**（`import` 时求值 `list_presets()`），不用 `preset_display()` | 属 `ui/tab_batch` 而非本模块；与阶段 141 的 `HEX_TOKENS` 同族（模块级快照）。**登记**为跨模块观察项。 |

### 未审榜（截至 143）

`utils/path_helper` 2.1 → `config` 1.1。
