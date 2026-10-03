# 阶段 87 · services 层真值审计 —— SSIM 值域 P0 与取消契约

> 日期：2026-09-14
> 范围：`services/quality_service.py`（277 行）、`services/queue_worker.py`（429 行）
> 结论：**3 项确认缺陷（含 1 项 P0）+ 1 项已验证为非缺陷 + 1 项登记为边界**

---

## 1. 动机：services 层是唯一的审计盲区

阶段 77–86 的「真值审计」路线已覆盖 `core` / `utils` / `config` / `scripts`：

| 阶段 | 审计对象 |
|---|---|
| 77 | `core/plugins.py` |
| 78 | `utils/path_helper.py` |
| 79 | `utils/i18n.py` |
| 80 | `config.py` 解析语义 |
| 81 | `utils/exceptions.py` |
| 83 | `scripts/real_gate.py` |
| 85 | `core/quality.py` |

而 **`services/`（6,950 行）与 `ui/`（6,796 行）一次都没进过审计范围**。
阶段 87 启动前的覆盖度探查（tests/ 78 个文件中模块名出现次数）：

| 模块 | 行数 | 测试引用 | 判读 |
|---|---|---|---|
| cache_service | 998 | 33 | 尚可（孤儿清理阶段覆盖过） |
| video_service | 917 | 25 | 尚可（视频阶段覆盖过） |
| **queue_worker** | 429 | **2** | ⚠️ 并发 + 取消 + 宽泛 except |
| **quality_service** | 277 | **1** | ⚠️ 近乎零覆盖 |
| **report_service** | 526 | **3** | ⚠️ 近乎零覆盖 |
| **video_export** | 183 | **1** | ⚠️ 近乎零覆盖 |

本阶段取「高风险 + 低覆盖」的两个模块作为首期：`queue_worker`（并发/取消/异常三重叠加）
与 `quality_service`（用户直接可见的质量评分）。

---

## 2. P0 · `_ssim_channel` 在 uint8 上做乘法导致溢出

### 2.1 现象

探针在评估「底图 vs 噪声」时得到 **SSIM = 1.7770**——SSIM 的理论值域是 **[-1, 1]**，
该值不可能存在。

### 2.2 根因

`_ssim_channel` 的 docstring 声明 `x, y : 同为 H×W 的 float64 数组`，
但唯一调用方 `_ssim` 传入的是 `img1[:, :, c]`，即 **uint8**。

实现里包含 `x * x` 与 `x * y`，在 uint8 上会**按 256 取模溢出**：

```
x = 200 (uint8)
x*x (uint8) = 64        正确值应为 40000
40000 mod 256 = 64
```

于是 `sigma_x2 = E[x²] − μx²` 与 `sigma_xy = E[xy] − μx·μy` 全部基于垃圾值计算，
SSIM 公式失效。

### 2.3 量化

同一对图（底图 vs 有一块改动的图）：

| 口径 | SSIM |
|---|---|
| float64（docstring 契约口径） | **0.858488** |
| uint8（修复前实际口径） | **1.183706** |

值域扫描（两张常量图 `v` 与 `v−30`，v ∈ {5,50,100,150,200,240,255}）：

| | 修复前 | 修复后 |
|---|---|---|
| 取值范围 | **[0.2064, 3.9175]** | [0.2064, **0.9922**] |
| 是否越界 | **是** | 否 |

**偏差方向随图像内容变化、不可预测**——同一「底图 vs 噪声」场景，实测既出现过
0.3079 也出现过 1.7770。

### 2.4 用户可见危害（注入验证时意外暴露）

回退修复后跑测试，`test_good_level_detail_untouched` 失败：

```
期望 good，实际 warn  (cr=0.0977)
```

即：**一张正常修好的图会被误判为「修复过头」**。SSIM 虚高时漏报过处理、
偏低时误报——两个方向的错误都会发生。

### 2.5 为什么长期没被发现

**常量图自比时，溢出项恰好相互抵消，结果仍为 1.0。**
而模块 `__main__` 自检用的正是平滑常量块图，且三个用例分别断言
`good`（靠 changed_ratio）、`review`（靠 changed_ratio=0）、`review`（靠 changed_ratio=0）——
**自检从未断言 SSIM 的值域**。

> 这正是经验 26/27 的复现：「推理契约（值域）必须用探针直接断言」，
> 「不报错 + 形状对」抓不住值域错误。

### 2.6 修复

在 `_ssim_channel` 唯一实现处统一规整 dtype（经验 44：dtype 决定语义，
且只在单一实现里做一次）：

```python
x = np.asarray(x, dtype=np.float64)
y = np.asarray(y, dtype=np.float64)
```

---

## 3. 缺陷 2 · 改写 level 时不改写 detail（状态与观测矛盾）

`evaluate_quality_arrays` 末尾：

```python
if score < config.QUALITY_SCORE_REVIEW:
    level = "review"        # 只改 level，detail 仍是原分支的措辞
```

实测（SSIM 修复后才稳定复现）：

```
level  = review
score  = 55
detail = '改动区域过大且结构变化明显，可能修复过头（误伤原图）。'
```

`review` 的标准诊断方向是「水印可能没修掉」，而这里的 detail 说的是「修过头」——
**两种诊断方向相反**。用户按 review 去查漏修，看到的是相反结论。

修复：改写 level 时同步在 detail 开头声明复核原因，并保留原有诊断信息。

```python
if score < config.QUALITY_SCORE_REVIEW:
    level = "review"
    detail = (f"综合质量分 {score} 低于复核线 {int(config.QUALITY_SCORE_REVIEW)}，"
              f"建议人工复核；{detail}")
```

---

## 4. 缺陷 3 · 被取消的任务不被 yield

`run()` 的契约是「逐个 yield 完成**（或状态变更）**的 Task」，但两处取消路径
用 `return` / `continue` 绕过了 `yield`：

```python
if cancelled:
    for t in tasks:
        ...标记 CANCELLED...
    return                 # ← 不 yield

for t in tasks:
    if self.is_cancelled():
        ...标记 CANCELLED...
        continue           # ← 不 yield
```

实测：**入队 5 个全部取消 → yield 出来 0 个**，而 `summary()` 显示 `cancelled=5`。
上层通过 `for t in q.run(processor)` 一个对象都收不到，
用户在界面上的观感是「点了取消，进度条与日志毫无反应」。

> 模块 `__main__` 自检的断言是 `done_count + failed_count == 7`，
> **不统计 CANCELLED**，所以自检同样漏掉了这个缺陷。

修复：两处都改为收集后逐个 `yield`（并触发 `on_update` 回调）。

---

## 5. 缺陷 4 · `TaskCancelledError` 被当作故障重试

`_execute` 只有 `except _CancelSignal` 与宽泛 `except Exception` 两个分支。
`TaskCancelledError → TaskError → WatermarkEraserError → Exception`，
因此**必然**落入宽泛分支：

```
processor 抛 TaskCancelledError("用户主动取消")
  → 重试 3 次（retry=2）
  → status = failed
  → error = '【任务已取消】原因：用户主动取消 建议：1. 这是您主动取消的操作，无需处理。'
```

既白白重试（processor 可能有副作用），又给出自相矛盾的报告。

**与阶段 81 的关系**：阶段 81 已把「`_CancelSignal` 与 `TaskCancelledError` 双实现」
登记为 E6，并裁决「二者语义不等价、不合并」（前者是生成器内部的流程控制信号，
后者是业务状态，合并需重设状态机）。
**该裁决本身正确**，但遗漏了一环：没考虑 processor 真的抛出 `TaskCancelledError` 会怎样。

本阶段**不推翻**该裁决（不合并两个类），只补上遗漏——让二者在 `_execute` 中
得到相同处理：

```python
except (_CancelSignal, TaskCancelledError):
    ...标记 CANCELLED...
```

---

## 6. 已验证为「非缺陷」的候选（防止后人改坏）

### 降采样不改变分级结论 ✅ 声明成立

`_downscale_for_eval` 的 docstring 声明「启发式分级结论基本不变」。
这是一个**未经量化验证的性能优化**（阶段 16 引入），本阶段实测：

| 场景 | 不降采样 | 降采样到 1024 |
|---|---|---|
| 2048² 水印 200×200 | cr=0.00960 good | cr=0.00970 good |
| 2048² 水印 120×120 | cr=0.00350 good | cr=0.00350 good |
| 2048² 水印 60×60 | cr=0.00090 good | cr=0.00090 good |

分级结论一致、changed_ratio 差异 <2% → **声明成立，不改**。
已加测试 `test_downscale_preserves_level_and_changed_ratio` 钉住该结论，
防止后来者把它当缺陷「修复」反而引入回归。

---

## 7. 登记为边界（不改）

| 边界 | 说明 |
|---|---|
| `summary()['current']` 在并发 >1 时只取第一个 RUNNING | 声明「正在处理：xxx」在并发下不完整。属轻度「声明≠实现」，改动需同步 UI 语义，本阶段只登记 |
| 灰度 / 非 3 通道输入会 `IndexError` | `_ssim` 硬编码 `range(3)`。`evaluate_quality`（路径版）经 `load_image_with_alpha` 已保证 3 通道；数组版入口的契约已写入 docstring |
| `_CancelSignal` 与 `TaskCancelledError` 双轨 | 阶段 81 裁决不合并（语义不等价），本阶段只统一处理、不合并类 |

---

## 8. 验证

### 8.1 新增测试（21 例，`tests/test_services_audit.py`）

| 层 | 用例 |
|---|---|
| SSIM 值域 | 4 组常量图对、5 组自比、uint8/float64 一致性、单调性 |
| **注入式反例** | 内嵌旧实现复现，断言其**确实越界**（若它变合法，说明值域断言已退化为空守卫） |
| level/detail | review 场景 detail 必须同步；good 场景不得误加「复核」 |
| 抗脆化 | 降采样不得改变分级结论 |
| 取消契约 | 执行前取消 / 执行中取消 / on_update 回调 |
| 取消语义 | TaskCancelledError 不重试；**普通故障仍须重试**（防修复扩大化）；内部检查点取消仍有效 |

### 8.2 注入式反例（改坏代码，验证测试真的会失败）

| 注入 | 结果 |
|---|---|
| 回退 SSIM dtype 规整 | **8 个测试失败**：SSIM=3.92/5.99/2.66 越界；且 `good` 被误判为 `warn` |
| 回退 `TaskCancelledError` 分支 | **1 个测试失败**：`cancelled → failed`，日志复现「第 1/2 次失败，准备重试，【任务已取消】」 |

两次注入后均已恢复，重跑全绿。

### 8.3 门禁与全量回归

| 门禁 | 结果 |
|---|---|
| 合成 `quality_gate.py` | ✅ 通过（EXIT=0）：干净图误报 **0.78%**、精确率 **51.8%**、召回 **81.9%**、IoU **49.7%**、ΔPSNR **+12.49 dB**（交付链 +11.31，掩码区最差劣化 −0.01 dB）——与阶段 86 基线**逐位一致** |
| 真实 `real_gate.py` | ✅ 通过（EXIT=0）：general P **0.0385** / R **1.0000** / 区域 IoU **0.7573** / 掩码 IoU **0.0635**——逐位一致，耗时 16.9s |
| 全量 pytest | ✅ **1119 例（1113 pass + 6 env-skip），零失败**，耗时 3m16s（阶段 86 为 1098 例 / 3m29s，本阶段新增 21 例） |

**说明**：SSIM 修复会改变门禁报告里 `quality_score` 展示列的数值（它此前基于错误的
SSIM 计算）。该列按阶段 85 的裁决**只作展示、不入红线**，故不影响门禁判定——
本阶段两条门禁均通过即为验证。

---

## 9. 新增经验（54~58）

54. **「测试引用密度」是一个有效的选点指标**：模块行数 ÷ 测试引用次数，
    能直接暴露「高风险 + 零覆盖」的盲区（queue_worker 429 行仅 2 次引用）。
    审计不必从「哪里看起来复杂」开始，从**覆盖度荒漠**开始命中率更高。
55. **性能优化引入的精度损失必须量化，不能靠 docstring 声明**：
    「分级结论基本不变」是一句断言，不是证据。本阶段实测它**确实成立**，
    但成立这件事本身需要一次探针来证明——否则它就是下一个待爆的雷。
    （本阶段把它写成测试，防止后人以「优化」名义改坏它。）
56. **dtype 决定语义（44 的同族扩展）**：不只是索引数组，
    **任何含乘法/累加的公式都要先确认 dtype**。uint8 上的 `x*x` 是取模，
    不是溢出报错——它静默给出确定但错误的结果。
57. **「常量输入下正确」是最危险的假绿**：常量图自比时溢出项恰好抵消，
    SSIM 恒为 1.0。构造测试时必须**让目标统计量有信号**（经验 39），
    本例中即：输入必须有非零方差且亮度足够高。
58. **改写状态时清点所有与之配对的观测字段**：改 `level` 就要看 `detail`、
    `message`、`error` 是否有同级字段（经验 21 的具体化）。
    状态与观测是**一对多**关系，改一处就要扫一遍同级字段。

---

## 10. 剩余边界

- 本阶段只审了 `services/` 13 个模块中的 2 个；
  **report_service（526 行 / 3 次引用）、video_export（183 行 / 1 次）、
  postprocess_service（655 行 / 8 次）、auto_pipeline（575 行 / 10 次）** 仍未审计。
- `ui/` 层（6,796 行）**完全未审计**，是下一个更大的盲区。
- 未接入 CI：两条门禁仍需手工触发。
