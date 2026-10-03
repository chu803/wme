# 145 · config 真值审计——NaN 穿透钳制与 validate_config 的成对约束盲区

> 日期：2026-09-27 ｜ 对象：`config.py`（荒漠度 **1.0** = 876 代码行 / 191117 字节 ÷ 837 引用用例）
> 方法：探针量化 → 分类 → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 登记
> **本模块是未审榜末位，也是全仓测试密度最高者** —— 按技能 1c 的判据「荒漠榜只答『哪里没测过』，
> 不答『测过的是否测对』」，高密度 ≠ 无缺陷；而它的影响面是**所有模块的配置真值**。
> 本轮是「审 validate_config 这个**元守卫自身**」的元审计。

## 一、探针量化

三轮探针（`s145_probe.py` / `s145_probe2.py`，零残留）。**探针 v1 有两处误判，已在 v2 修正**（见第五节）。

| # | 候选 | 探针结果 | 判定 |
|---|---|---|---|
| **F1** | `_env_float` 的 **NaN 穿透钳制** | `WME_X=nan` → 返回 **`nan`**（不是 default，也不是任何钳制值）。根因：`nan < min` 与 `nan > max` **都返回 False** ⇒ 两段钳制对 NaN 完全失效 | **defect（P0）** |
| **F2** | `validate_config` 的**成对约束覆盖不全** | 命名空间**自动枚举**出 **11 对**约束，**只有 2 对**被覆盖（检查 8 通用面积、检查 16 白牌面积）⇒ **9 对漏检** | **defect（P1，元审计）** |
| — | 5 组滑杆三元组 `X_DEFAULT/X_MIN/X_MAX` | DEFAULT 全部落在区间内（现状自洽）；但**三处独立字面量**（`_env_*` 钳制参数 + MIN/MAX 常量）无任何对账 | **defect（P2，防漂移）** |
| — | `parse_bool` 的 21 种输入 | 全部符合 docstring（含中文字面量、`"  true  "`、`nan`、`2`、`{}`、`object()`） | by design ✅ |
| — | `_env_int` 对 nan/inf/`"1.9"` | 全部安全回退（`int("nan")` 抛 ValueError） | by design ✅ |
| — | `UI_LANGUAGE="xx-YY"` | `set_language` **安全回退** `zh-CN` | by design ✅ |
| — | `_env_int` / `_env_float` 的「钳制 vs 回退」双口径 | 超范围**钳制**、转换失败**回退**、且**均无告警** | 观察项（**登记**） |

### F2 的细节：9 对漏检清单（**同族漏检**是本阶段的核心洞察）

| 漏检的约束对 | 与已覆盖者的关系 |
|---|---|
| `DETECT_GRID_MIN_COVERAGE` / `MAX_COVERAGE` | **与检查 8（通用面积）、检查 16（白牌面积）完全同族** —— 都是「区间反了 ⇒ 判据恒不产出 ⇒ 静默漏检」 |
| `DETECT_LOGO_MIN_AREA_RATIO` / `MAX_AREA_RATIO` | 同上（LOGO 族） |
| `RECOMMEND_GRID_MIN_COVERAGE` / `MAX_COVERAGE` | 同上（推荐器侧的同一对） |
| `DETECT_LOGO_CANNY_LO` / `CANNY_HI` | 双阈值族（`LO >= HI` ⇒ Canny 行为异常） |
| `MASK_DILATE_MIN/MAX`、`MASK_FEATHER_MIN/MAX`、`MASK_THRESHOLD_MIN/MAX`、`DENOISE_STRENGTH_MIN/MAX`、`INPAINT_STRENGTH_MIN/MAX` | UI 滑杆区间族（反转后滑杆行为未定义） |

> **检查 16 的注释已经把这条道理写明了** ——「面积区间反了会让判据恒不产出（静默失效）」——
> 却**没有推广到同族**。这与阶段 142「同一句契约含多个值域类别、只覆盖一类」、
> 阶段 143「硬编码清单漏项」是**同一族的元缺陷：意识到了一条判据，却没有让它机械化地覆盖整族**。
> 这正是**该用元检查而不是逐条补**的理由。

### F1 的危害链（可直接复现）

```
WME_DETECT_TRANSLUCENT_SENSITIVITY=nan
  → config.DETECT_TRANSLUCENT_SENSITIVITY is nan
  → detect_watermark_mask(sensitivity=nan, ...)
  → 返回零掩码（阶段 142 实测：mask_nonzero == 0）
  → 静默零检出
而 validate_config() 报绿、无任何告警
```

即：**用户能通过一个环境变量让检测永久检不到东西，且所有自检都说「配置正常」**。
`inf` / `-inf` **不在此列**（它们能被正常钳制，实测 `inf → max`、`-inf → min`）。

## 二、最小修复（config.py，2 处）

| # | 修复 |
|---|---|
| F1 | `_env_float` 在钳制**之前**加单点拦截：`if value != value: value = default`（NaN 是唯一不等于自身的值）。**只拦 NaN** —— `inf`/`-inf` 保持既有钳制行为，故对非 NaN 路径**零行为变更**。 |
| F2 | `validate_config` 新增**检查 18/19（元检查）**：从**本模块命名空间自动枚举** ① 全部 `X_MIN/X_MAX`、`X_MIN_Y/X_MAX_Y`、`X_LO/X_HI` 成对约束；② 全部 `X_DEFAULT/X_MIN/X_MAX` 三元组的 DEFAULT 越界。**不逐条补、不写死清单** ⇒ 未来新增任何同族配置项**自动纳入**（经验 167 判据：期望值必须从真值来源派生）。 |

**为什么不逐条补 9 个检查**：逐条补只解决「今天这 9 对」，明天新增第 12 对时**照样漏**——
这与本阶段要修的缺陷是同一个病。元检查把「意识到一条判据」变成「机械覆盖整族」。

**落点与依赖约束**：元检查用 `globals()` + 字符串切分实现，**刻意不用正则** ——
`config.py` 只 import 了 `json/os/sys/pathlib/typing`，为一条自检引入 `re` 属不必要的依赖面扩大。
（**注**：我第一版写成了 `re.match(...)` —— 运行即 `NameError: name 're' is not defined`，
正是本技能第 2 步家族 15「引用了从未导入的名字」；已就地改为字符串切分。）

## 三、验证

| 门禁 | 结果 |
|---|---|
| 守卫测试 `tests/test_config_audit145.py`（**46 用例**：N0 内嵌旧实现存在性自检 2 / N1 NaN 族 6+8+2+1 / N2 成对约束动态枚举 3 / N3 三元组 3 / N4 元检查派生性 4 / N5 原有检查回归 11 / N6 无误报 3 / N7 消息质量 2） | ✅ **46/46 绿** |
| 探针复测 | ✅ NaN→default（`nan`/`NaN`/`NAN`/`-nan`）；`inf`→1.0、`-inf`→0.0（**与修前一致**）；**11/11 对全捕获**（修前 2/11）；**5/5 三元组全捕获** |
| **注入式反例（逐项 2 次，`-v`）** | ✅ **2/2 精准命中，`collect_err` 全 False**：F1→**8** 失败（3/3 命中）/ F2→**9** 失败（6/6 命中）；还原 sha256=`7b1a55b3334eaac3…` **逐字节 + 归一化文本一致**；回绿 rc=0；**`ALL_INJECTIONS_OK=True`** |
| 子进程自检 `python config.py` | ✅ rc=0，输出「**通过。未发现配置问题。**」（元检查**无误报**） |
| 干净全量回归 | 见下方 |
| `audit_selector.py --check` / `--unaudited` | ✅ EXIT=0，登记 **44** 条；`--unaudited` **已空**（此前榜末的 `config` 亦已审计） |

### 注入明细

| 注入项 | rc | 收集期报错 | 失败数 | 命中预期 |
|---|---|---|---|---|
| F1 删掉 NaN 拦截 | 1 | False | **8** | 3/3 ✓ |
| F2 删掉元检查段 | 1 | False | **9** | 6/6 ✓ |

## 四、登记与边界

### 观察项（本轮**不修**，已登记）

| 项 | 现象 | 不修理由 |
|---|---|---|
| `_env_int`/`_env_float` 的**双口径且无告警** | 超范围 → **钳到边界**（`WME_SERVER_PORT=999999` → 65535）；转换失败 → **回退默认**（`abc` → 7860）。两者都**静默** | 「钳制」是 docstring 明确的设计意图（「保证程序永远能启动」），且 `SERVER_AUTO_PORT` 等下游有兜底。⇒ **登记**：可考虑加一条「配置值被调整」的启动日志，但这属产品决策而非缺陷。 |
| `MASK_FEATHER_STEP=2` 不整除区间跨度 51 | `51 % 2 == 1` ⇒ 滑杆最后一段刻度不落在 MAX 上 | Gradio 自行处理刻度计算；无用户可见缺陷。 |
| `get_config_snapshot` 是否漏列 `API_PORT` / `DETECT_TRANSLUCENT_SENSITIVITY` | 探针 v2 按**值**比对时未命中，但快照键是**中文说明**，值可能与他项重合 ⇒ **判定不可靠** | 需按中文键逐条核对才能定性；本轮**不下结论**，登记为待确认项（避免把探针局限当成缺陷）。 |
| `_env_float` 的 `inf → max` 语义 | `WME_X=inf` 得到 `max` 而非 default | 属既有设计（钳制），且无危害（`max` 是合法值）。F1 刻意**不碰**它以保证零行为变更。 |

### 未审榜（截至 145）

**`--unaudited` 已空** —— 全部模块（44 个）均已登记 `AUDITED_SINCE`。
