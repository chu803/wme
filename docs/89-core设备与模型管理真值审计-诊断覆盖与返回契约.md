# 阶段 89 · core 设备 / 模型管理真值审计：诊断覆盖与返回契约

> 审计对象：`core/model_manager.py`（725 行）、`core/device_manager.py`（383 行）
> 结论：**4 项确认缺陷**（含 1 项本机实测的用户可见重复项）+ **3 项已验证为非缺陷** + **1 项边界**
> 守卫：`tests/test_core_audit89.py`（19 例，含 5 处注入式反例）
> 变更：`core/model_manager.py`、`core/device_manager.py`、`api.py`

---

## 0. 一句话结论

`model_manager` 与 `device_manager` 处在 **LaMa / 真实超分的模型加载路径**上，
但它们的测试密度极低。本阶段抓到的不是「算法算错」，而是**三类"看起来在工作"的缺陷**：

1. **精确原因被泛化结论覆盖** —— 文件读不出来（被占用/权限）被报成「**文件可能被篡改/损坏**」；
2. **返回字典 schema 与分支有关** —— 同一个函数，失败分支少 4~9 个键，而对外 API 明写「含 size_mb 等」；
3. **判重判据与注释声明不一致** —— 注释说「已注册的文件会跳过」，代码比的是「槽 key」，
   于是内置权重被再注册成一个新槽，**本机实测 `lama` 与 `big-lama` 指向同一份 `big-lama.pt`**。

外加一处被静默容错藏起来的资源泄漏（NVML 未成对关闭）。

---

## 1. 选点：一次必须公开的**指标修正**

阶段 87/88 用的选点指标是 **模块行数 ÷ 测试引用次数**（对测试文件做字符串计数）。
本阶段一扫就发现它**在测试使用别名导入时严重低估覆盖率**：

| 模块 | 字面计数（旧指标） | 实际测试函数数 | 修正后比值 |
|---|---|---|---|
| `core/device_manager` | 1 次引用（比值 383） | **6** | 63.8 |
| `core/model_manager` | 2 次引用（比值 362） | **13** | 55.8 |
| `utils/logger.py` | **0** 次引用 | **0** | ∞（仍为唯一真零覆盖） |

原因是测试普遍写成 `from core import model_manager as mm` —— 模块名只出现 **1 次**，
但该文件里 12 个测试函数都在跑它。旧指标把「引用一次、测得很勤」误判成了荒漠。

**修正后的分母**：统计「导入了该模块的测试文件中的测试函数总数」。
重新排序后本阶段目标依然是 `core/` 内最荒漠的两个（63.8 / 55.8），方向选择成立；
但**同时暴露出一件新事实**：`utils/logger.py`（237 行 / **0** 个测试函数）
是全仓唯一真正的零覆盖模块，且被所有层引用——列为下一阶段首选候选。

> 这条修正已写进经验 61：**选点指标本身也要接受审计**，否则会在错的数据上做正确的推理。

---

## 2. 缺陷 A：精确原因被泛化结论覆盖（"读不了" → "被篡改"）

### 2.1 代码位置

`core/model_manager.py::verify_model` 修复前：

```python
    if slot.sha256:
        try:
            actual = _sha256_of_file(p)
            info["sha_ok"] = actual.lower() == slot.sha256.lower()
        except Exception as exc:
            info["sha_ok"] = False
            info["reason"] = f"SHA256 计算失败：{exc}"     # ① 先写下精确原因
    info["ok"] = ...
    if info["ok"]:
        info["reason"] = "校验通过"
    elif not info["size_ok"]:
        info["reason"] = f"体积异常：..."
    elif not info["sha_ok"]:
        info["reason"] = "SHA256 校验失败（文件可能被篡改/损坏）"   # ② 无条件覆盖掉 ①
```

结尾的分支**只按 `sha_ok` 真假判断**，于是 ① 写的精确原因被 ② 覆盖。

### 2.2 探针量化

```
实际抛出的异常：PermissionError('拒绝访问：文件被其他进程占用')
verify_model 返回 reason = 'SHA256 校验失败（文件可能被篡改/损坏）'
```

**危害**：把「**读不了文件**」（被其他进程占用、权限不足、网络盘掉线）
断言成「**文件被改过**」。用户拿到这条提示的正常反应是删掉重新下载 —— 而真实问题是
另一个进程占着这个文件，重下也会遇到同样情况，属于**把用户引向错误处置方向**的诊断。

### 2.3 修复：区分「算不出来」与「算出来不匹配」

```python
        except Exception as exc:
            info["sha_ok"] = False
            info["sha_error"] = f"{type(exc).__name__}: {exc}"        # 新增独立字段
    ...
    elif info["sha_error"]:
        info["reason"] = f"SHA256 计算失败（无法读取文件，并非校验不匹配）：{info['sha_error']}"
    else:
        info["reason"] = "SHA256 校验失败（文件可能被篡改/损坏）"
```

修复后：
```
reason = 'SHA256 计算失败（无法读取文件，并非校验不匹配）：PermissionError: 拒绝访问：文件被其他进程占用'
```

**语义边界由两条测试共同界定**（缺一条就会矫枉过正）：
- `test_sha_computation_failure_keeps_precise_reason`：计算抛错 → 说「无法读取」，**不得**出现「篡改」；
- `test_sha_mismatch_still_reports_tamper`：计算成功但值不同 → **仍然**保留「可能被篡改/损坏」，且 `sha_error` 为空。

> 这直接复用阶段 88 的经验 60：**「缺数据」必须与「不通过」在数据结构上可区分**。
> `sha_ok=False` 同时代表「算出来不匹配」和「根本没算出来」，所以必须再加 `sha_error`。

---

## 3. 缺陷 B：返回字典的 schema 与分支有关

### 3.1 探针量化（修复前）

| 调用 | 键数 | 相对正常分支缺少 |
|---|---|---|
| `verify_model(槽不存在)` | **4** | `expected_mb` `size_mb` `size_ok` `sha_ok` |
| `verify_model(校验通过)` | 8 | — |
| `slot_detail(槽不存在)` | **4** | `display_name` `expected_mb` `loaded` `mtime` `path` `sha_computed` `sha_ok` `size_mb` `size_ok` |
| `slot_detail(存在)` | 13 | — |
| `api.verify_model(槽不存在)` | **4** | 同 verify_model |

而 `api.verify_model` 的 docstring 明写「校验结果字典（**含 ok / reason / size_mb 等**）」——
**对外承诺的键，恰好在最常见的失败路径上不存在**。

### 3.2 修复：键集合唯一来源

- 新增 `_VERIFY_KEYS` 常量与 `blank_verify_result(name, reason)`，
  `verify_model` 的三个分支统一由它起手；
- `slot_detail` 的缺失槽分支补齐 UI 会读取的全部键；
- `api.verify_model` 的异常兜底分支复用 `model_manager.blank_verify_result`
  （仅当 model_manager 本身导不进来时才退回字面量，并保持同一键集合）。

### 3.3 为什么现在没炸：一次「有防护」的核对

诚实记录：**当前所有调用方碰巧都没踩到**——`ui/tab_models.on_detail` 先 `if not d.get("exists"): return`，
`on_verify` 只读 `ok/name/reason`（三个键在两个分支都在）。所以这是**潜伏缺陷**，
不是正在崩的 bug。它值得修的理由是：`api.py` 是**公开接口**，
外部消费者按 docstring 读 `size_mb` 会直接 `KeyError`；而「潜伏」只是运气。

> 这类「目前恰好没人踩」的缺陷正需要**对账断言**守卫（见 §6），
> 而不是等它变成线上报错。

---

## 4. 缺陷 C：自动发现把内置权重重复注册成一个新槽（用户可见）

### 4.1 代码与注释的矛盾

```python
def _init_discover() -> None:
    """扫描 MODEL_DIR 下的 .pt/.onnx 文件，自动注册为槽（不覆盖已有槽）。"""
    ...
        stem = _os.path.splitext(fname)[0].lower()
        # 内置槽的文件名已注册，跳过。
        if stem in _SLOTS:          # ← 比的是「槽 key」，不是「文件名」
            continue
```

内置槽 `key="lama"`、`filename=config.MODEL_FILENAME="big-lama.pt"`。
于是 `stem="big-lama"` ∉ `{"lama"}` → **又注册一个 `big-lama` 槽**。

### 4.2 探针量化（本机真实注册表）

```
槽清单： lama   big-lama.pt   builtin=True
        big-lama  big-lama.pt   builtin=False
指向同一路径的重复槽：{'.../models/big-lama.pt': ['lama', 'big-lama']}
```

**用户可见**：模型管理页的模型列表里出现两个条目，指向同一份权重；
对其中任意一个执行下载/回滚，实际都会改写同一个文件，
而 `set_active_model("big-lama")` 也会被接受 —— 用户以为在切换两份模型，
其实一直是同一份。这属于「**声明 ≠ 实现**」这一类（注释声称的跳过并没有发生）。

### 4.3 修复：按「槽 key」**与**「已登记文件名」双重判重

```python
    taken_keys = set(_SLOTS.keys())
    taken_files = {str(s.filename).lower() for s in _SLOTS.values() if s.filename}
    for fname in sorted(_os.listdir(str(d))):
        ...
        if stem in taken_keys or fname.lower() in taken_files:
            continue
```

修复后探针输出：**指向同一路径的重复槽：无**。

---

## 5. 缺陷 E：NVML 只在成功路径关闭（静默容错藏资源泄漏）

```python
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(index)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        pynvml.nvmlShutdown()        # ← 只有走完前面三行才会执行
        return int(mem.free / (1024 * 1024))
    except Exception:
        return None                   # ← 拿不到句柄/读不到显存时，NVML 停在"已初始化未关闭"
```

异常被 `except Exception: return None` 吞掉，**泄漏被"容错"藏起来了**
（正是经验 12 描述的模式）。改为 `try/finally`，并保证 `nvmlInit` 自身失败时不误关：

```python
    pynvml = None
    try:
        import pynvml
        pynvml.nvmlInit()
        ...
    except Exception:
        return None
    finally:
        try:
            if pynvml is not None:
                pynvml.nvmlShutdown()
        except Exception:
            pass
```

**可测性说明**：本机未安装 `pynvml`，无法用真实 NVML 复现。
因此测试**注入一个假 `pynvml` 模块**（记录 `nvmlInit` / `nvmlShutdown` 调用次数，
并可在 `handle` / `mem` 两处定点抛错），参数化覆盖「成功 / 拿不到句柄 / 读不到显存」
三种路径，断言 **`inits == 1` 且 `shutdowns == 1`**。
这让一个「环境不可复现」的静态判断变成了确定性断言。

---

## 6. 注入式反例：5 处回退，9 个用例失败

`%TEMP%/s89_probe/inject_check89.py` 逐个把修复回退，跑守卫，`finally` 还原并做逐字节校验：

| 注入的回退 | 失败用例 |
|---|---|
| A 原因覆盖顺序复原 | **2**（`test_sha_computation_failure_keeps_precise_reason`、`test_old_reason_logic_would_misdiagnose`） |
| C 判重退回「只比槽 key」 | **2**（`test_discover_skips_already_registered_filename`、`test_real_registry_has_no_duplicate_paths`） |
| B `slot_detail` 缺失槽退回 4 键 | **1** |
| B `api` 兜底退回 3 键 | **1** |
| E NVML 退回「只在成功路径关闭」 | **3**（`[handle]`、`[mem]`、`test_old_vram_query_leaks_nvml_on_error`） |

还原后复跑：**19 例全绿，0 失败**。

### 6.1 本阶段新增的两种守卫手法

1. **对账断言（reconciliation）**：不比较字符串，而是断言
   「`verify_model` 三个分支的键集合**相等**」、
   「`blank_verify_result` 的键集合 == `_VERIFY_KEYS`」、
   「真实注册表里指向同一路径的槽**至多一个**」。
   这类断言在新增分支却忘了补键时会立刻失败 —— 是**穷举式**的，不依赖我想到哪个分支。
2. **假可选依赖注入**：把 `sys.modules["pynvml"]` 换成记录调用次数的假模块，
   使「资源是否成对释放」这种原本只能在有硬件时观察的性质，变成纯逻辑断言。

---

## 7. 已验证为非缺陷（各写成测试钉住）

| # | 候选 | 结论与依据 |
|---|---|---|
| V1 | `load_model` 内联 `device.is_gpu and config.DEVICE_USE_FP16`，与 `device_manager.use_fp16()` 「重复实现」 | **非缺陷，且不可合并**：`use_fp16()` 读的是**全局 active 设备**，`load_model` 读的是**调用方传入的 device**。合并会让「显式传 CPU 句柄」在配置开启时按 fp16 加载。`test_load_model_fp16_judgment_uses_passed_device` 用**源码结构断言**钉住（改成 `use_fp16()` 即失败，逼后人先读这条理由） |
| V2 | `select_device("auto")` 直接复用已缓存的设备（不重新探测） | **非缺陷**：docstring 明确声明「auto 模式下，若之前已经定下来（无论 GPU/CPU）就复用」。`test_select_device_auto_reuses_settled_choice` 钉住 |
| V3 | `DEVICE_MODE=cuda` 无显卡时抛 `RuntimeError`，而 `get_device_info()` 是 UI 展示路径 | **非缺陷**：①「不默默降级，避免用户误以为在用显卡」是设计意图；② 展示路径**已有护栏**——`ui/tab_models._render_status_md` 用 `try/except` 包住 `get_model_info()` 并降级为友好文案（经验 20「展示路径不得有能力让功能失败」已满足）。`test_forced_cuda_does_not_silently_degrade` 钉住「必须抛错」 |
| V4 | `select_device("gpu")`（拼写错误）会静默走 auto | **非缺陷**：`config.validate_config()` 已对 `DEVICE_MODE` 做白名单校验并明确提示「程序将按 auto 处理」，行为与声明一致（阶段 80 的配置链路审计已覆盖） |

## 8. 登记的边界（本阶段不改）

1. **`get_model_slot("")` 等价于 `get_model_slot(None)`**：空字符串被当成「未指定」，
   静默返回当前 active 槽（docstring 只声明了 `None` 的行为）。
   调用方一律写 `name or None`，当前不可达；改语义需先评估调用方，故只登记 + 用测试钉住现状。
2. **`_cache` 相关**：`clear_cache()` 采用重新绑定（`_CACHE = {}`）而非 `clear()`；
   对外只经 `cache_size()` / `load_model()` 访问，无外部持有引用，不改。
3. **`device_manager` 本阶段只有 1 项确认缺陷**：其余候选（`empty_cache` 节流、
   `apply_cpu_threads` 的 `set_num_interop_threads` 二次调用抛错被吞、`os.environ.setdefault`
   不覆盖已有环境变量）经核对均为**声明过的行为**或影响可忽略，未做改动。

---

## 9. 回归与门禁

| 门禁 | 结果 |
|---|---|
| 合成 `scripts/quality_gate.py` | ✅ 通过（exit 0）—— 误报 **0.78%** · 精确率 **51.8%** · 召回 **81.9%** · IoU **49.7%** · ΔPSNR **+12.49 dB**（与阶段 86/87/88 基线逐位一致） |
| 真实 `scripts/real_gate.py` | ✅ 通过（exit 0）—— general **P 0.0385 / R 1.0000 / IoU 0.7573 / mask IoU 0.0635** |
| 全量 pytest | ✅ 新增 `tests/test_core_audit89.py`（19 例）；总数 1132 → **1151** |

> 设备/模型管理不在检测或修复链路上（门禁走 OpenCV 引擎、不加载 torch 权重），
> 因此门禁**本就不可能**因本次改动变化 —— 跑它们是为了确认「零回归」这个断言本身成立，
> 而不是因为预期会变。

---

## 10. 新增经验

**经验 61 —— 选点指标本身也要接受审计。**
「模块行数 ÷ 测试引用次数」在测试使用**别名导入**（`from core import X as mm`）时会把
「引用一次、测得很勤」误判成荒漠：`model_manager` 字面引用 2 次、实际 13 个测试函数在跑它。
**判据**：算完指标后，至少抽查两个高分项，数一遍「导入了该模块的测试文件里有多少 `def test_`」。
指标错了，后面所有推理都建立在错的前提上。

**经验 62 —— 失败分支的「精确原因」不得被结尾的泛化分支覆盖。**
按状态位（`sha_ok=False`）统一决定结论，会把两种不同事实（**没算出来** / **算出来不匹配**）
合并成一句更泛化、且**指向错误处置方向**的话（"读不了文件" → "文件被篡改"，用户会去重下载）。
正确做法：为「过程失败」单独留字段（`sha_error`），结尾按
「过程失败 → 结果不匹配 → 通过」的**优先级**决定文案，而不是按单个布尔位。

**经验 63 —— 用「假可选依赖」把环境不可复现的资源问题变成确定性断言。**
NVML 未成对关闭在无显卡的机器上根本跑不到。把 `sys.modules["pynvml"]` 换成记录
`init` / `shutdown` 次数的假模块、并在内部定点抛错，就能断言「**无论走哪条路径，
inits == shutdowns**」——把「资源释放」从只能靠肉眼审代码，变成可以失败的红线。

---

## 11. 本阶段产出

| 类型 | 文件 | 说明 |
|---|---|---|
| 修复 | `core/model_manager.py` | A 保留精确原因 + `sha_error`；B `_VERIFY_KEYS` / `blank_verify_result` 统一键集合（`verify_model` 三分支 + `slot_detail` 缺失槽分支）；C 自动发现双判重；顺带 `note` 参数规整 |
| 修复 | `core/device_manager.py` | E `_query_free_vram_mb` 用 `try/finally` 保证 NVML 成对关闭 |
| 修复 | `api.py` | B `verify_model` 兜底分支复用 `blank_verify_result`，键集合与 model_manager 对齐 |
| 测试 | `tests/test_core_audit89.py` | 19 例：对账断言 ×4、缺陷回归 ×6、注入反例内嵌 ×3、非缺陷钉住 ×4、边界 ×1、参数化资源断言 ×1 |
| 探针 | `%TEMP%/s89_probe/` | `probe_mm_dev.py`、`inject_check89.py`、`run_all_checks89.py`（项目内零残留） |
| 文档 | 本文件 | — |

## 12. 下一步候选（按修正后的指标）

1. **`utils/logger.py`（237 行 / 0 个测试函数）** —— 全仓唯一真零覆盖，且被所有层引用（最高优先）；
2. `ui/theme.py`（164 / 1）、`ui/tab_models.py`（356 / 4）—— 首次进入 `ui/` 层；
3. `services/` 剩余：`postprocess_service`（655 / 34）、`auto_pipeline`（未进前 12，需单独核算）。
