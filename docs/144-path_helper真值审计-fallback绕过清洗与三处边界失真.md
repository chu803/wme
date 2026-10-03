# 144 · utils/path_helper 真值审计——fallback 绕过清洗管线与三处边界失真

> 日期：2026-09-27 ｜ 对象：`utils/path_helper.py`（荒漠度 2.1 = 183 代码行 / 22978 字节 ÷ 4 个引用测试文件里 89 个用例）
> 方法：探针量化 → 分类 → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 登记
> 前置：阶段 78 已重写 `sanitize_filename` 的清洗管线（N1–N5）；阶段 83 已给 `display_path` 加跨盘符 `try/except`。本轮审的是**阶段 78 未覆盖的边界**。

## 一、探针量化

探针 `%TEMP%/s144_probe.py` 覆盖 10 个候选面（零残留，只读项目 + `mkdtemp` 自清理）。

| # | 候选 | 探针结果 | 判定 |
|---|---|---|---|
| **F1** | `fallback` **绕过清洗管线** | `sanitize_filename(".", fallback="a/b")` → **`"a/b"`**（含路径分隔符）；`fallback="..\\..\\etc"` → `"..\..\etc"`；`fallback="evil*name"` → `"evil*name"`；**`fallback="   "` → 纯空格主名**；**`fallback="..."` → 全点号主名** | **defect（P1）** |
| **F2** | `max_length` 不是硬上限 | `sanitize_filename("abc.png", 4)` → **`"a.png"`（长度 5 > 4）**；`("a", 0)` → `"a"`（1 > 0） | **defect（声明失真，只改声明）** |
| **F3** | `assert_file_readable` docstring 漏列异常 | docstring「异常」段只写 `FileNotFoundError`，实际传目录抛 **`IsADirectoryError`** | **defect（P2，漏列比多列更危险）** |
| **F4** | `get_output_dir(sub_folder)` 可越出输出目录 | `"../escape"` → `<根>/escape`；`".."` → `<根>`；`"/abs"` → `D:\abs`；`"C:\\x"` → `C:\x`（**该函数会真实 `os.makedirs`**） | **defect（P2）** |
| **F5** | `make_unique_path` 的 `Path(...).name` **平台相关** | `Path("a\\b.txt").name` 在 Windows = `"b.txt"`；**`PurePosixPath("a\\b.txt").name` = `"a\\b.txt"`（不剥离）** | **defect（P2，跨平台声明失真）** |
| — | `max_length` 非整型 | `2.7` 静默截为 2；`True` → 1；`"120"` → 120（静默接受）；`"abc"`/`None` → 显式失败 | 观察项（**登记**，调用方全传 int 字面量） |
| — | `extension` 非字符串 | `None`/`123` → `AttributeError`（`.lower()`），**信息不含参数名** | 观察项（登记） |
| — | `_split_name` 边界（8 类） | 全部符合 docstring | by design ✅ |
| — | `make_unique_path` 序号算法 | `a.txt`/`a(1).txt` → `a(2).txt` ✓；`.gitignore` ✓；多重点名 ✓ | by design ✅ |
| — | `config` 三个格式白名单 | 全部小写、类型为 tuple；大小写输入经 `.lower()` 正确判定 | by design ✅ |

### F1 的两层危害（这是本阶段最重的发现）

`fallback` 在第 5 步被直接赋值 —— 而那时清洗管线的**②③ 步（控制字符 / 非法字符替换）与 ④ 步（`strip(" .")`）都已经执行完毕**，所以 fallback **两层都绕过**：

| fallback | 修复前输出 | 后果 |
|---|---|---|
| `"a/b"` | `"a/b"` | **含路径分隔符** ⇒ 调用方若直接拼进路径，会写到子目录 |
| `"..\\..\\etc"` | `"..\..\etc"` | 目录穿越形态 |
| `"evil*name"` | `"evil*name"` | 含 Windows 非法字符 ⇒ 创建文件失败 |
| `"   "` | `"   "` | **纯空格主名** |
| `"..."` | `"..."` | **全点号主名**（Windows 上创建会失败） |

而函数的契约是返回「清洗后的**安全**文件名」。

> ⚠ **一次自我修正**：我第一版修复只补了 ②③ 步（`_scrub`），遗漏了 ④ 步的 `strip` ⇒ 守卫当场变红（`fallback="   "` → `"   "`、`fallback="..."` → `"..."`），我才发现「绕过」是**两层**而不是一层。**守卫的价值正在于此**：它抓住了修复本身的不完整。

### F2 的判定：**行为对、声明错**（经验 5m 三态）

额度计算是 `budget = max(1, max_length - len(suffix))` —— **主名至少保留 1 字符**。当 `max_length < len(suffix) + 1` 时结果必然超出。

行为本身是**唯一合理的**：扩展名与 ≥1 字符主名都不该被截成空（否则文件无法被识别）。⇒ 按经验 5m 的判据（「按声明改会不会破坏下游被依赖的链路」→ 会），处置为**把声明写准**，而非改行为。

### F5 的可达性说明

`make_unique_path` 被 **28 处**调用，全部传 `sanitize_filename` / `build_output_filename` 处理过的名字（那些名字已无分隔符）⇒ **本机 Windows 上路径不可达**。但：
- `docstring` 明确承诺「剥离**任何**目录成分」—— 该承诺在 POSIX 上**不成立**；
- 修复成本极低（显式按两种分隔符取末段），收益是**兑现承诺 + 跨平台正确**。
⇒ 修复（属「补遗漏」而非「推翻裁决」）。

## 二、最小修复（utils/path_helper.py，5 处）

| # | 修复 |
|---|---|
| F1 | 抽出 `_scrub(text)` 单步（控制字符 + 非法字符替换），`fallback` 改为 `_scrub(str(fallback)).strip(" .") or "未命名"` —— 与 name 路径**同规则**；清洗后仍为空时兜底。顺带把「str(fallback)」补上（非字符串 fallback 不再让 `stem.upper()` 崩）。 |
| F2 | docstring **订正**：`max_length` 是**目标值而非硬上限**，写明极小值时的行为与理由（只改声明）。同时把「规则」小节改成与实现一致的 1→8 步顺序。 |
| F3 | docstring 的「异常」段补 `IsADirectoryError` 并说明漏列的危害方向（漏列 ⇒ 调用方漏捕获）。 |
| F4 | `get_output_dir` 只取 `sub_folder` 的**末段名**（显式按 `/` 与 `\\` 切分），并兜掉 `.` / `..` / 空串（退化为输出根目录）。与 `make_unique_path` 同款纵深防御。 |
| F5 | `make_unique_path` 改为 `str(desired_name).replace("\\", "/").rsplit("/", 1)[-1]` —— **不依赖 `Path` 的平台语义**。 |

## 三、验证

| 门禁 | 结果 |
|---|---|
| 守卫测试 `tests/test_path_helper_audit144.py`（**71 用例**：L0 内嵌旧实现存在性自检 3 / L1 fallback 清洗 7+3+2+5 / L2 max_length 行为 + 声明 7+2 / L3 异常声明 + 3 条实际行为 / L4 13 类越界输入 + 正向对照 3 / L5 双分隔符 10+1+1+2 / L6 回归 6） | ✅ **71/71 绿** |
| 关联回归（`test_filename_safety` / `test_icc_profile` / `test_validators_media` / `test_legacy_selfcheck`） | ✅ rc=0 全绿 |
| **注入式反例（逐项 5 次，`-v`）** | ✅ **5/5 精准命中，`collect_err` 全 False**：F1→**11** 失败 / F2→1 / F3→1 / F4→**8** / F5→1；还原 sha256=`444ab16b2a5bddc2…` **逐字节 + 归一化文本一致**；回绿 rc=0；**`ALL_INJECTIONS_OK=True`** |
| 子进程自检 `python utils/path_helper.py` | ✅ rc=0 |
| 干净全量回归 | 见下方（排除批量删除类环境 artifact） |
| `audit_selector.py --check` / `--unaudited` | ✅ EXIT=0，登记 **43** 条；下一站 `config` 1.0（榜末） |

### 注入明细

| 注入项 | rc | 收集期报错 | 失败数 | 命中预期 |
|---|---|---|---|---|
| F1 `fallback` 回退为直接赋值 | 1 | False | **11** | 4/4 ✓ |
| F2 `max_length` 声明回退 | 1 | False | 1 | 1/1 ✓ |
| F3 docstring 去掉 `IsADirectoryError` | 1 | False | 1 | 1/1 ✓ |
| F4 `get_output_dir` 回退为直接拼接 | 1 | False | **8** | 1/1 ✓ |
| F5 `make_unique_path` 回退为 `Path.name` | 1 | False | 1 | 1/1 ✓ |

> ⚠ **F5 的守卫局限（如实记录）**：F5 是**平台相关**缺陷，在 Windows 上 `Path(...).name`
> 的行为**恰好正确**，因此**行为用例无法在开发机变红** —— 注入 F5 后只有
> `test_L5_no_platform_dependency_in_source`（源码断言）变红。这是「平台相关缺陷」的守卫
> 固有局限：**必须保留源码层断言作为第二道防线**。守卫文件里同时保留了
> `L0` 的 `PurePosixPath` 存在性证明（用 `PurePosixPath` 显式展示 POSIX 语义差异，
> 不依赖运行平台）。

## 四、登记与边界

### 观察项（本轮**不修**，已登记）

| 项 | 现象 | 不修理由 |
|---|---|---|
| `sanitize_filename` 首点被剥离 | `.gitignore` → `"gitignore"`（`_split_name` 特意「不把首点当扩展名」，但第 ④ 步 `strip(" .")` 又把点去掉了） | 既有测试**已显式锁定并注释**该语义（`assert sanitize_filename("...") == "未命名"` 上方写着「全是点号：点号被 strip 掉后为空 → 兜底」）⇒ 属**阶段 78 有意裁决**，不改（避免推翻前人裁决）。**但登记：「不把首点当扩展名」与「strip 掉首点」两句声明并存却语义重叠，后人读 docstring 会困惑。** |
| `max_length` 类型无校验 | `2.7` → 静默截为 2；`True` → 1；`"120"` → 120 | 仓内 33 处调用全传 `int` 字面量（120/200）⇒ 路径不可达。属「类型契约」而非静默错误（危险的是 `2.7` 静默截断，但无调用方）。 |
| `build_output_filename` 的 `extension` 非字符串 | `None`/`123` → `AttributeError: 'NoneType' object has no attribute 'lower'`（**信息不含参数名**） | 是**显式失败**（不是静默错误）；20 处调用全传字符串。 |
| `extension` 超长 | `"verylongextensionname12345"` → 被截到 16 字符 ⇒ 截断后不再是合法扩展名 | 无调用方传超长扩展名；截断是第 ⑦ 步的既有设计。 |
| `make_unique_path` 保留非法字符 | `"bad*name?.png"` → 名字原样保留 `*`/`?` | docstring 只承诺「不越出 `target_dir`」，**未承诺清洗**（清洗是 `sanitize_filename` 的职责）⇒ by design。 |

### 未审榜（截至 144）

`config` 1.0 —— **榜末**（876 代码行 / 837 引用用例）。
