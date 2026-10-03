# 142 · core/inpainter 真值审计——掩码值域越界回绕导致静默空操作

> 日期：2026-09-27 ｜ 对象：`core/inpainter.py`（荒漠度 3.3 = 579 代码行 / 26276 字节 ÷ 12 个引用测试文件里 91 个用例）
> 方法：探针量化 → 分类 → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 登记
> 前置：阶段 102 已修「bool / 0-1 掩码被阈值清零」的静默空操作；阶段 105 已修「羽化冲淡掩码内部」。本轮为**同族续审**——同一句 docstring 承诺的「任意值域」还有第三类没有被覆盖。

## 一、探针量化

探针脚本 `%TEMP%/s142_probe.py` 对 7 个候选面逐条量化（零残留，只读项目）。

| # | 候选 | 探针结果 | 判定 |
|---|---|---|---|
| **D1** | `mask.astype(np.uint8)` 对 **>255 浮点掩码**静默回绕 | `float32` 全片 `300.0` → 回绕成 **44** → 低于阈值 127 → 整片清零 → **改动 0 px，不抛异常** | **defect（P0，「静默空操作」）** |
| **D2** | 同上，回绕到 0 的分支 | 全片 `256.0` → **0** → 抛 `EmptyMaskError`，理由是「掩码为空」 | **defect（P1，错误信息误导）** |
| — | `strength` 越界（1.5 / 3.0 / 0.0 / −1.0）无范围校验 | 全部正常工作：`s=0.0` 逐字节等于原图（合理）；`s=1.5/3.0` 产生外插过冲但被 `np.clip(0,255)` 兜住，输出仍是合法 uint8 | by design（**有兜底**，且调用方全传 0~1；登记不上纲上线） |
| — | `engine` 非法值 / 非字符串 | 未知名 → `ValueError` 且列出可用引擎；非字符串 → `AttributeError: 'int' object has no attribute 'lower'` | by design（错误类型可接受，信息可定位） |
| — | `engine` 大小写 | `"OpenCV"` 正常工作（入口 `.lower()`） | by design |
| — | `_denoise` 掩码内融合取整口径 | `denoise=0` 恒等；`only_masked` True/False 差异 0；掩码内均值一致 | by design（无截断偏差） |
| — | `dilate=1.5` / `feather=2.5` 非整型 | 由 OpenCV 底层抛 `error(-5:Bad argument)` | by design（**显式失败**，非静默） |
| — | `threshold` 越界（−5 / 256） | 分别静默变成「全不修」/「全修」 | 观察项（**登记未修**，见第五节） |
| — | mask 形状不匹配 / 图像 float | OpenCV 底层报 `Sizes of input arguments do not match` / `Unsupported depth` | by design（**显式失败**） |
| — | `(H,W,4)` 掩码取第 0 通道 | 正常修复 | by design（与阶段 102 I3 一致） |
| — | `__main__` 里 `import cv2` + `cv2 = __import__("cv2")` 冗余 | 同值重复绑定，纯代码卫生 | 顺手清理（非缺陷） |

### D1/D2 危害：把「整幅都是水印」读成「没有水印」

`inpaint()` 的 docstring 明写：

> `mask : 掩码 H×W（bool / 0-1 / 0-255 任意值域，语义为「0=保留、非0=修复」）`

阶段 102 覆盖了 `bool` / `{0,1}`，阶段 105 覆盖了「羽化不得冲淡内部」，但**>255 这一类零覆盖**。而它恰恰是最容易踩的一类——调用方从归一化张量（`mask` 是 `0.0~255.0` 的 `float32`，但某些预处理会乘一个 `gain`，或直接给出 `1.0` 表示「全片」×255 以上标度）拿到的掩码常常带越界值：

| 输入（float32） | 修复前 | 真实意图 | 后果 |
|---|---|---|---|
| 全片 `300.0` | 回绕 44 → 清零 → **0 px** | 「整幅都是水印」 | **静默空操作**（最重） |
| 全片 `256.0` | 回绕 0 → `EmptyMaskError` | 同上 | 误报「掩码为空」，把调用方引向错误方向 |
| 全片 `511.0` | 回绕 255 → 正常工作 | 同上 | 巧合正确（更危险：掩盖问题，让人以为没问题） |
| 全片 `512.0` | 回绕 0 → 抛异常 | 同上 | 同上 |

**危险的不只是错，还有「有时对有时错」**——`511` 对、`300` 静默、`256` 报错，同一份代码按数值不同给出三种行为，无任何一致性。

### 探针实测（修前）

```
P6: {255.0: 400 px,  256.0: 抛 EmptyMaskError,  300.0: 0 px}      ← 300.0 是静默空操作
P4: threshold_mask 底层：256/300/512 → 全零；511 → 全 255        ← 同一回绕机制
```

### 危害窗口与「无守卫」判据

`tests/test_inpainter_mask_range.py` 的 I1 只测 `bool` / `{0,1}` / `[0,1]` 三种值域 —— **>255 零覆盖**；`test_inpainter.py::test_empty_mask_raises` 用 `pytest.raises(Exception)` 过宽，连 `256.0` 误抛也照收。即阶段 83 判据成立：改坏它没有任何自动化会失败。

## 二、最小修复（core/inpainter.py 入口掩码规整段）

在 `astype(np.uint8)` **之前**先看原始值域上界，一旦存在 `>255` 的值即按「非零即修复」抬到 0/255，并留可见告警：

```python
raw_max = int(mask.max()) if mask.size else 0
mask_u8 = mask.astype(np.uint8)
if raw_max > 255:
    log.warning(
        "掩码存在超过 255 的值（原始最大值 %d，疑似浮点越界或自定义标度）——"
        "已按「非零即修复」统一抬升到 0/255 值域，避免 uint8 回绕导致修复被静默跳过。"
        "若期望灰度软掩码，请把输入归一化到 0~255。",
        raw_max,
    )
    mask_u8 = (mask > 0).astype(np.uint8) * np.uint8(255)
```

**落点选择理由**：`threshold_mask` / `dilate_mask` / `feather_mask` 是 `core/image_utils.py` 的**公共底层函数**（检测侧也在用），在那里加值域判定会波及无关调用方；`inpaint()` 入口才是这条 docstring 契约的承诺方，且此处已有阶段 102 的同类归一化先例（最小侵入、语义一致）。

**不误伤证明**：真正落在 `0~255` 的灰度软掩码仍由 `threshold` 决定二值化边界（J2 守住），阶段 102 的 `{0,1}` 分支语义不变（`raw_max > 255` 先行归一，`mask_u8.max() <= 1` 分支只会命中真 `bool` / `{0,1}`）。

**顺带清理**：`__main__` 的 `import cv2  # noqa` + `cv2 = __import__("cv2")` 同值重复绑定 → 改为单行 `__import__("cv2")`（语义等价，子进程自检仍 rc=0 2/2）。

## 三、探针复测（修后）

```
P6: {255.0: 400 px,  256.0: 400 px,  300.0: 400 px,  -10.0: 400 px}   ← D1/D2 全消
```

| 输入 | 修前 | 修后 |
|---|---|---|
| float32 `255.0` | 400 px | 400 px（不变） |
| float32 `256.0` | 抛 `EmptyMaskError` | **400 px** |
| float32 `300.0` | **0 px（静默）** | **400 px** |
| float32 `-10.0` | 400 px | 400 px（不变） |

## 四、验证

| 门禁 | 结果 |
|---|---|
| 守卫测试 `tests/test_inpainter_mask_overflow.py`（13 用例：J0 回绕存在性自检 / J1 四种越界值等价 + 非静默空操作 + 负值 / J2 灰度软掩码双向 / J3 告警可见 + 正常掩码不刷 / J4 256 边界 + 真空掩码仍抛） | ✅ 13/13 绿 |
| 相关回归（`test_inpainter.py` 8 + `test_inpainter_mask_range.py` 10 + `test_dual_run.py` 15 + `test_auto_engine.py` 4 + `test_mask_interior_full_strength.py` 7 = 44） | ✅ 44/44 绿 |
| 注入式反例（`s142_counter2.py`：精确锚点还原旧行为 `mask_u8 = mask.astype(np.uint8)`） | BUGGY **rc=1 红**（**6 条精准失败**：J1×4 + J3 + J4）；还原 sha256=`9c1eefc01cbcf5fd…` **逐字节一致 + 归一化文本一致**；回绿 rc=0 |
| 子进程自检 `python core/inpainter.py` | ✅ rc=0，2/2 通过 |
| 连带修复：`tests/test_processing_prefs.py` 全局 config 隔离（见第六节） | ✅ 26/26 绿（修前与其连跑的 `test_input_normalization.py` 21 条假红） |
| 连带修复：`tests/test_audit_selector.py` 榜首快照更新 | ✅ 30/30 绿 |
| `audit_selector.py --check` | ✅ EXIT=0，登记 41 条 |
| `audit_selector.py --unaudited` | `core/inpainter` 已剔除；下一站 `core/detection_presets` 3.1 |

### 注入失败清单（证明守卫真的在守）

```
FAILED test_J1_overflow_float_mask_equals_0_255_mask[256.0]
FAILED test_J1_overflow_float_mask_equals_0_255_mask[300.0]
FAILED test_J1_overflow_float_mask_equals_0_255_mask[1000000.0]
FAILED test_J1_overflow_mask_is_not_silent_noop
FAILED test_J3_overflow_mask_emits_warning
FAILED test_J4_overflow_256_no_longer_raises_empty_mask
```

> ⚠ 首次注入脚本的两点教训（已修正）：① 备份基准必须取「修复后」版本，我第一次误取了修复前旧版，导致第三次校验出现虚假「哈希不一致」（实为还原到了修复前）；② 注入锚点必须精确到目标分支内，第一次锚点跨进了 `EmptyMaskError(...)` 的字符串字面量中部，产生 `SyntaxError`（收集期报错）而非失败断言 —— 收集期红灯是**假绿灯**，会掩盖「守卫是否真的覆盖了该行为」。

## 五、登记与边界

### 第六节：全量回归暴露的两项连带缺陷（同轮修复）

全量回归时发现两个**与 inpainter 无关但由本轮改动触发/暴露**的问题，按项目铁律同轮修复。

#### 连带 A（真实缺陷）：`test_processing_prefs.py` 的全局 config 泄漏

**症状**：`test_processing_prefs.py` 与 `test_input_normalization.py` **同进程连跑**时，后者 **21 条假红**（EXIF 方向校正全失效、位深不归一化、`read_image_flags` 恒返回 `(1,1)`）；**单独跑**后者 41/41 绿。

**根因**：`ui/tab_settings.apply_processing_prefs()` 直接改写 `config.IMAGE_APPLY_EXIF_ORIENTATION` / `config.IMAGE_NORMALIZE_BIT_DEPTH` / `config.INPAINT_DUAL_RUN` / `config.PNG_KEEP_ALPHA` 四个**模块属性**，**且无 teardown 还原**。测试文件里的 `test_apply_processing_prefs_restore_defaults` 调用 `apply_processing_prefs(False, False, False, False)` ⇒ 全局开关被永久关掉 ⇒ 后续所有依赖默认行为的用例全错。

**这是经验 155（i18n 写盘污染）的同族**，只是载体从**文件**换成**模块属性**：单点 `monkeypatch` 无法覆盖「被测函数自己直接赋值」的路径，必须用 `autouse` fixture 在**整个文件**级别快照 / 还原。

**修法**：`test_processing_prefs.py` 加 `@pytest.fixture(autouse=True) restore_pref_config`，对 `_PREF_ATTRS` 四个属性快照 → `yield` → 还原；并补 3 条守卫用例（基线探针 / apply 后属性确实被改 / **紧随其后的用例必须读到默认值**）。

**注入验证**：把 `autouse=True` 去掉 ⇒ `test_config_isolation_guard_baseline` + `test_config_restored_for_subsequent_tests` **2 条红**；还原 sha256 一致 → 26/26 绿。

#### 连带 B（正当快照失效）：`test_audit_selector.py` 榜首变化

本阶段新增 `tests/test_inpainter_mask_overflow.py`（13 例）与 `test_processing_prefs.py` +3 例 ⇒ `ui/tab_auto` / `ui/tab_batch` 的引用例数增加 ⇒ 荒漠度比值下降（`tab_auto` 720/39 = 18.46）⇒ **第一荒漠从 `ui/tab_auto` 变为 `services/queue_worker`（264/14 = 18.86）**。

该断言的原 docstring 已自述「**本条是"结论编码"，不是不变量**——下次审计后应更新」，故按既有约定更新，`test_cli_json_roundtrip` 改为**只断言「与 `collect()` 首位一致」**（不再写死名字，避免再次过时）。30/30 绿。

#### 环境 artifact（**不计入**，已登记）

`sitecustomize.py` 的 `SAFE_DELETE_BULK_REJECTED` 护栏：一旦**单轮累计删除数 ≥ 50**，后续所有 `_os.remove` **连锁被拦**（`SystemExit(1)`），表现为 `test_orphan_cleanup` / `test_cache_*` / `test_history_batch_delete` / `test_legacy_selfcheck[api.py, export_service.py]` 等**大面积同时红**（本轮 11 条）。判据：stderr 出现 `[safe-delete][SAFE_DELETE_BULK_REJECTED]` + `count:50`。这是沙箱环境行为，非产品缺陷。

### 观察项（本轮**不修**，已登记）

| 项 | 说明 | 不修理由 |
|---|---|---|
| `threshold` 参数越界 | `threshold=-5` 静默「全不修」；`threshold=256` 静默「全修」（含掩码外？不，仍受 `proc_mask` 限制，实为「全掩码区按硬边界修」）。无范围校验。 | 调用方（`config.MASK_THRESHOLD_DEFAULT`）与 UI 滑杆均为 `0~255`；且越界方向是**保守**（更少修）或**同义**（硬边界），不产生静默空操作。若日后 UI 暴露自由输入再上纲。 |
| `strength` 无范围校验 | `>1` 外插过冲 / `<0` 反向注入，均被 `np.clip` 兜住 | 有兜底、无静默、调用方恒传 0~1。**登记为 by design**。 |
| `_SUPERRES_WIDGETS` 注册表 | 无去重 / 弱引用（阶段 139 登记，当前生命周期不触发） | 与本轮无关，维持原登记。 |
| `scripts/real_benchmark_decision.py:159` | 传 `compute_no_params=True` 形参幻觉，被 `except` 吞成 `None` | 与本轮无关，维持原登记。 |

### 未审榜（截至 142）

`core/detection_presets` 3.1 → `utils/path_helper` 2.1 → `config` 1.1。
