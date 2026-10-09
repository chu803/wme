# 阶段 248 · 补扫描器盲点（`setLevel`）· 运行期 config 泄漏检测 · 结构性防护

> 承接 247：247 发现的「级别泄漏」暴露出 **246 的扫描器漏了 `logger.setLevel`**——
> 即「排查工具本身有盲点」。本阶段补盲点，并把「全局状态污染」的排查
> **从静态扫描升级为运行期检测**，最后加**结构性防护**防复发。

---

## 1. 结论先行

| 项 | 结论 |
|---|---|
| 扫描器 v2 | 补 `setLevel` 等模式后命中 261 处；**logger 相关全部落在已修+有守卫的文件** |
| 2 个误报类型 | `evaluate_quality(...).level == "good"`（**QualityReport.level** 不是 logger 级别）；`warnings.simplefilter`（在 `catch_warnings` 上下文内，**自动还原**） |
| **运行期检测器**（本阶段核心工具） | 分批快照→跑 pytest→再快照，**确定性**回答「跑完是否残留变化」 |
| 检出真实泄漏 | **`config.REAL_SUPERRES_SCALE` / `REAL_SUPERRES_BACKEND`** 被测试污染且不还原 |
| 泄漏点数量 | **3 处**（我预期 1 处 —— 见 §3「一修再修」） |
| 结构性防护 | conftest 新增 autouse `_isolate_config_globals`（快照→还原，与 `_isolate_i18n_language` 同款） |

---

## 2. 为什么静态扫描不够：升级为运行期检测

248 的 G6 组（config 直改）报了 **184 处**。但其中：
- 绝大多数是 `monkeypatch.setattr(config, ...)` → **自动还原**，不是缺陷；
- 少数是直接 `config.X = Y` → 需判断「是否在所有路径还原」。

**静态分析判不了**这类问题，尤其「还原写在函数末尾而非 `finally`」时——抛异常即漏还原，
而静态扫描只看到「有还原语句」就放行。

⇒ 本阶段写了**运行期检测器**（`.scratch/stage248_config_leak.py`）：分批起**独立子进程**，
子进程内 `deepcopy` 快照 config 全部大写属性 → 跑 pytest → 再快照 → 逐项对比 → 经 stdout
回传差异。**这才是能确定性回答「有没有泄漏」的方法。**

### 检测器自身的三个 bug（迭代过程，值得记）

| 版 | 症状 | 真因 |
|---|---|---|
| v1 | 全批「泄漏项=?」 | 我在 `if isfile(diffpath)` 内先 `os.remove(diffpath)`，**再**在 print 里 `isfile` 检查 ⇒ 必然打 `?`（探针逻辑 bug） |
| v2 | 只跑完批 1 就停 | 批间清 `.pytest_cfg`（121 文件）触发 **safe-delete** 批量删除护栏 ⇒ `SystemExit` 杀掉脚本（`ignore_errors` 拦不住 SystemExit） |
| v3（采用） | — | 每批**唯一 basetemp** + 全程不删 + stdout 回传（绕开文件清理） |

（同 244 教训：**「快得反常」本身就是探针 bug 的信号**——v2 用 1m3s「跑完」192 文件，正是被杀的征兆。）

---

## 3. 一修再修：同一泄漏有 **3 处**

检出 `REAL_SUPERRES_SCALE`/`BACKEND` 泄漏后，我修复了 **2 处**预期中的位置，
**再跑检测器仍泄漏** ⇒ 逐文件二分（`.scratch/bisect248.py`）才定位到第三处：

| # | 位置 | 缺陷形态 |
|---|---|---|
| 1 | `test_tab_settings_audit129::test_d2_save_failure_is_observable` | 调 `apply_superres_config()`（**该函数会写 config**），`finally` 只还原 logger |
| 2 | `test_tab_settings_audit129::test_d2_save_success_no_warning` | 同上 |
| 3 | `test_tab_settings_audit129::test_d2_no_save_when_checkbox_off` | 同样调 `apply_superres_config()`，**完全没有 try/finally** |
| （4） | `test_settings_superres::test_register_and_refresh_superres_widgets` | 直接写 3 个 config 全局量，`finally` 只 `clear_superres_widgets()`，**不还原 config** |

**教训**：**「同一根因往往不止一处」**——我按「预期 2 处」修完就以为好了，
**是检测器二次运行 + 逐文件二分**才逼出第三处。**修完必须重跑检测器**，不能凭「我改完了」下结论。

### 隐蔽性来源

`apply_superres_config` 是**被调函数**，它写的是 `config` 全局量——测试作者看
`finally` 只写了「还原 logger」，**不会意识到被调函数偷偷改了 config**。
这类「隐式副作用」是跨用例污染最难看的一类。

---

## 4. 结构性防护：conftest autouse 夹具

逐点修复治标；**结构性防护**治本。参照既有的 `_isolate_i18n_language`
（阶段 78 为 i18n 全局语言状态所做的同类防护），新增：

```python
@pytest.fixture(autouse=True)
def _isolate_config_globals():
    # 每个用例前后快照/还原 config 的全部大写属性
    # · 不可变标量：按身份判断是否被替换 → 只回写真正改过的
    # · 可变容器：deepcopy 快照、按值比较 → 捕捉「原地 append/更新」
```

**为什么这样设计**：
- 已有的 `monkeypatch.setattr(config, ...)` **不受影响**（monkeypatch 先还原，
  本夹具再还原到同一初值，幂等）；
- 忘记还原的直接赋值会被**自动纠正** ⇒ 从根上消除该类污染；
- 只覆盖 `config` 模块；logger / i18n / model_manager 各有专项夹具（245/246/247 / `restore_active_model`）。

---

## 5. 验证

| 项 | 结果 |
|---|---|
| 逐文件二分（**conftest 改动前**，证修复本身有效） | 批14 **12/12 文件 `ok`** |
| 全库运行期检测（192 文件 / 16 批） | **零 config 泄漏** |
| 全量分块回归（验 autouse 夹具不破坏任何测试） | 见 `_s248.log` |

⚠ **诚实说明**：§5 的全库检测 run 与 conftest 改动**时间上重叠**（后几批子进程已加载新夹具），
故「零泄漏」这一结果**无法完全区分**「修复生效」与「夹具兜底」。
判定「修复本身有效」的**干净证据**是 §5 第一行（二分在 conftest 改动**之前**完成）。

---

## 6. 遗留

1. 运行期检测器只抓**净泄漏**（批次级）：若 A 用例泄漏、B 用例又改回默认，则看不出。
   要抓这类需**逐用例**快照（更重的 infra）——而 autouse 夹具（§4）恰好从**机制上**覆盖了这种情况。
2. 检测器只覆盖 `config`；其它全局单例未纳入（各有专项守卫）。
3. `logger.setLevel` 已加入扫描模式（v2），但**其它全局单例的盲点**仍可能有——
   本阶段的结论是「**运行期检测优于静态扫描**」，后续排查应优先用前者。