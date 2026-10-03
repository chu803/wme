# 阶段 200 · config 审计守卫的「假红」根因（测试污染全局单例）

> 起点：阶段 199 收尾时，`tests/test_config_audit145.py` 有 **4 条**红灯。我当时把它们标为
> 「既有失败、与本阶段无关」就放过了——**这个判断本身是错的**：仓库里有红灯，就意味着
> 「门禁在空转」，红灯会掩盖未来的真实回退。本阶段把它们收口。

## 现象：报错内容自相矛盾

第一次批量运行时，`test_N6_baseline_is_clean` 报出 11 条「配置缺陷」：

```
配置项 INPAINT_STRENGTH_MIN(1.0) 不应大于等于 INPAINT_STRENGTH_MAX(0.0)（下限/上限区间反了）
配置项 MASK_DILATE_MIN(100) 不应大于等于 MASK_DILATE_MAX(0)
配置项 DENOISE_STRENGTH_MIN(30) 不应大于等于 DENOISE_STRENGTH_MAX(0)
...
```

但 `config.py` 里的**干净值完全正常**：

| 配置项 | 干净值 | 测试报的「值」 |
|---|---|---|
| `INPAINT_STRENGTH_MIN / MAX` | `0.0 / 1.0` ✅ | `1.0 / 0.0` ❌ |
| `MASK_DILATE_MIN / MAX` | `0 / 100` ✅ | `100 / 0` ❌ |
| `DENOISE_STRENGTH_MIN / MAX` | `0 / 30` ✅ | `30 / 0` ❌ |

而且**两轮运行的值不一样**（`DENOISE_STRENGTH_DEFAULT` 第一轮 20、第二轮 3），
`test_N2_every_reversed_pair_is_caught` 第一轮 FAIL、第二轮 PASS。
「MIN/MAX 互换」+「值会变」+「同用例时红时绿」——这是**状态污染**的三个典型特征。

## 归因：测试把全局单例留在脏态

`test_config_audit145.py` 的每个用例都要**临时改写 `config` 模块属性**来驱动
`validate_config`。它有一个模块级 autouse fixture 兜底：

```python
_UPPER = {n: getattr(config, n) for n in dir(config) if n.isupper()}   # 导入时快照

@pytest.fixture(autouse=True)
def _restore_config():
    yield
    for _n, _v in _UPPER.items():
        setattr(config, _n, _v)
```

**问题**：fixture 只在**测试结束时**兜底。而**同一个测试内部**的循环会互相污染——
`test_N3_default_out_of_range_is_caught` 遍历 5 组三元组，每组末尾都执行：

```python
_set(**{dname: getattr(config, mn) - 10})   # 把 DEFAULT 留在区间外，且不还原
```

`DENOISE_STRENGTH_DEFAULT` 于是残留为 `-10`（干净值 `3`）。后续用例读到脏值，
报出**根本不存在的配置缺陷**。

### 决定性证据（注入式）

| 实验 | 结果 |
|---|---|
| 单独跑 `test_N6_baseline_is_clean`（干净态） | **PASS** |
| 先跑 `test_N3_default_out_of_range_is_caught`，**不还原**，再跑 `N3_defaults_are_currently_in_range` | **FAIL**：`DENOISE_STRENGTH_DEFAULT(-10) 不在 [0, 30] 内` |
| 手工注入 `DETECT_PLATE_MIN/MAX = 0.05/0.01`（模拟残留） | `N6_baseline_is_clean` 立刻 FAIL，报错与线上一致 |

## 为什么这是「假红」而非普通红灯（危害评级）

`test_N6_baseline_is_clean` 是「**元检查不得误报**」的那道闸门——它守的是
「默认配置零告警」。它一假红，整套 `config` 审计的可信度归零：
人会去「修」根本没坏的配置，或者（更糟）因为「反正一直在红」而整体忽略该文件。

## 修复：改全局单例必须自包含

1. `_set()` 改为**返回还原函数**（记录旧值，支持回滚）；
2. 全部 **17 处** `_set` 调用改为 `_undo = _set(...)` + `try/finally: _undo()`；
3. `test_N3_default_out_of_range_is_caught` 里 `MIN/MAX/DEFAULT` 的「干净值」
   在**任何改动之前**取好（原实现用 `getattr(config, mn)` 现取，会读到上一组残留）；
4. 循环内加断言：每组结束时 `DEFAULT` 必须回到原值，否则立刻失败并指名键名。

## 新增守卫 N8（把不变式钉死）

| 用例 | 作用 |
|---|---|
| `test_N8_every_set_call_is_self_contained` | **源码层**扫描：任何未接住还原函数的裸 `_set(` 立即失败（否定式断言，同 209） |
| `test_N8_set_returns_a_working_undo` | 还原函数**自身**可证伪：`_set` 生效 → `undo()` 真的改回去 |
| `test_N8_no_residue_after_all_cases` | 跑完全部用例（**刻意不调 fixture 还原**）后 config 必须**零残留**，且 `validate_config() == []` |

`test_N8_no_residue_after_all_cases` 是核心：它证明修复的是**根因**（用例自净），
而不是靠 fixture 掩盖症状。

### 注入式反例有效性（已验证）

把 N7 的一处改回裸 `_set`，**三条守卫同时变红**：

```
CAUGHT test_N8_every_set_call_is_self_contained :: [(505, '_set(MASK_THRESHOLD_DEFAULT=999)')]
CAUGHT test_N8_no_residue_after_all_cases       :: [('MASK_THRESHOLD_DEFAULT', (127, 999))]
CAUGHT test_N6_baseline_is_clean                :: 默认配置出现告警：默认值 MASK_THRESHOLD_DEFAULT(999) 落在其合法区间 [0, 255] 之外…
```

## 验证

| 项 | 结果 |
|---|---|
| `test_config_audit145.py` 全文件（33 次调用） | **33/33 通过**（修复前 4 条失败） |
| 跑完全部用例后 config 残留 | **无**（19 个关键项逐一比对） |
| 残留态下 `validate_config()` 告警数 | **0** |
| **N8 反例有效性** | 一处裸 `_set` ⇒ 3 条守卫同时变红 ✅ |
| `tests/test_config.py` + `test_config_parse.py` | 7/7 通过 |
| `test_legacy_selfcheck`（undef_scan + 自检登记 5 条） | 5/5 通过 |

## 波及面排查

用探针扫了 `tests/` 下所有 `config.XXX = ` 裸赋值（27 处疑似），逐个核实**函数体内
是否有 `finally` / `monkeypatch` 保护**：

- 绝大多数写法正确（含 `test_corner_plate.py:182-189` 的 `try/finally`、
  `test_tiled_precision.py` / `test_detector_v3.py` / `test_perf2.py` 的 `finally`、
  `test_mask_dilate_calibration.py` 的 `restore_dilate` fixture）；
- **只有 `test_config_audit145.py` 存在「同一测试内循环」这一特定隐患**——
  fixture 兜底对单测试安全，唯独在循环内不安全。

⇒ 修复范围收敛到这一个文件，未扩散。

## 教训 → LESSONS 213

**改全局单例状态必须自包含，不能依赖「测试级 fixture 兜底」**。判据：① fixture /
`try-finally` 的还原只在**当前测试结束**时发生，而**同一个测试内的循环**会让脏值
在循环体内传播——本例 `test_N3_default_out_of_range_is_caught` 遍历 5 组三元组，
每组末尾把 `DEFAULT` 留在区间外，后续用例读到的全是**不存在的配置缺陷**；②
这类「假红」比漏检更危险：它让守卫本身失去可信度，人会去「修」没坏的配置，
或因「一直红着」而整体忽略该文件；③ 判别特征：**同一用例时红时绿 + 报错值与
`config.py` 实际值矛盾**（MIN/MAX 互换、数值每轮都变）⇒ 不是配置缺陷而是状态污染；
④ 修复要落在根因：把 `_set` 改成返回还原函数（`try/finally` 自包含），
而不是给守卫加「忽略假红」的跳过逻辑；⑤ **验证根因的标准动作**是「**关掉 fixture
兜底再跑一遍**」——若仍零残留，才证明是用例自净而非掩盖（同 191 验证装置须可证伪）；
⑥ 排查同类问题用**静态扫描 + 逐个核实函数体保护**（27 处疑似里只有 1 处真有问题），
不要凭窗口启发式下结论。
