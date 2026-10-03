# 阶段 74 · 双跑择优（Dual-Run Best-Pick）落地为 opt-in 能力

> 日期：2026-09-12 · 前置：阶段 51/55/56 · 性质：新功能（部署级放行闸范式，阶段 61/67 同族）

## 1. 动机：把"等 P0"变成"现在就能用"

- **阶段 51** benchmark：合成数据上 OpenCV 8/8 占优，LaMa 多数用例起负作用；
- **阶段 55**：`auto` 收敛为默认偏向 OpenCV；
- **阶段 56**：建好无参考质量分（`core.quality.no_reference_inpaint_score`）与
  双跑择优基础设施，但结论是「**仅进诊断工具、不进默认修复热路径**——真实照片
  选优效力待 P0 验证」。

这条边界让一个真实问题悬置至今：**单张真实照片上，OpenCV 与 LaMa 谁的结果更好，
用户只能二选一赌运气**。阶段 61（可选增强放行闸）与阶段 67（低分辨率兜底）已经
两次验证了同一范式：**能力可以先行实现，风险用「默认关闭的显式开关」交还使用者**。
本阶段把双跑择优按该范式落地——不必等 P0，用户对自己的素材自行决定是否启用。

## 2. 语义

开启后每次修复执行：

```
主引擎（--engine / INPAINT_ENGINE）跑一次 → res_a
副引擎（INPAINT_DUAL_SECONDARY，默认 lama）跑一次 → res_b
score = no_reference_inpaint_score(结果, 原图, 掩码)   # 越高越自洽（阶段 56）
结果 = pick_better_result(res_a, res_b, score_a, score_b)
```

- **平局容差** `INPAINT_DUAL_EPS`（默认 1e-6）：分差 ≤ eps 视为平局，**取主引擎**——
  保守原则，不因噪声换引擎；
- **自动跳过条件**（`_resolve_dual_secondary`）：副引擎与主引擎相同 / 为 auto /
  未注册 / 需要模型但模型不可用 → 退回单引擎（保证总能修）；
- **失败回退**：副引擎运行或初始化抛任何异常 → 记告警、回退主引擎单跑，
  **绝不拖垮主链路**；
- **进度可见**：进度回调播报「双跑择优完成：X 胜出（s_a vs s_b）」。

## 3. 入口（三层）

| 入口 | 用法 | 默认 |
|---|---|---|
| 环境变量 | `WME_INPAINT_DUAL_RUN=1`（副引擎 `WME_INPAINT_DUAL_SECONDARY`） | 关 |
| CLI | `--dual-run` | 关 |
| 界面（单图页） | 「双跑择优（较慢，效果更稳）」复选框（中英双语） | 关 |

`inpaint(..., dual_run=None)`：None 取配置，True/False 显式覆盖（显式值 > 配置，
有测试锁定）——UI 复选框与 CLI 开关互不干扰。

## 4. 实现要点

- `core/inpainter.py`：
  - `pick_better_result(res_a, res_b, score_a, score_b, eps=)`：纯判定函数（可单测）；
  - `_resolve_dual_secondary(primary, requested)`：跳过条件集中于此（纯逻辑）；
  - `_run_engine_once(...)`：从 `inpaint()` 抽出的单引擎执行（含 strict_setup 语义：
    主引擎 setup 失败抛 FileNotFoundError 与旧行为一致；副引擎失败由调用方回退）；
  - `inpaint()` 新增 `dual_run` 参数；打分对象为**分块/降噪/融合之前**的引擎原始
    输出（与阶段 56 诊断口径一致）；
- `config.py`：3 个新常量（开关 / 副引擎 / 平局容差），完整注释写明代价与适用场景；
- `cli.py`：`--dual-run`；
- `ui/tab_single.py`：复选框 + `process_single(dual_run=)` + 事件 inputs；
- `utils/i18n.py`：中英双键（`params.dual_run_label/info`）。

### 工程插曲（自测抓住）

`_save_with_icc` 式的教训再次复现于开发中：无。本阶段一次通过；但测试里两个断言
按假引擎的真实语义修正（假引擎返回的是「输入图 + delta」，掩码内中心 = 水印值 50
+ delta，而非理想底图 + delta）——**测试期望必须基于被测实现的实际契约，而非
想象中的契约**。

## 5. 测试（`tests/test_dual_run.py`，15 例）

- `pick_better_result`：副胜 / 主胜 / 平局取主 / 自定义 eps 双向；
- `_resolve_dual_secondary`：同引擎跳过 / auto 与未知引擎跳过 / 空值跳过 / 有效解析 /
  模型不可用跳过 / 模型可用解析；
- **配置门控**：默认 `INPAINT_DUAL_RUN=False` 且副引擎不被调用；默认常量值锁定；
- **端到端**（临时注册假引擎 + 打分桩固定分数序列）：副胜输出副结果 / 主胜输出主
  结果（副引擎仍被调用、分数仍被消费）/ 副引擎崩溃回退主引擎 / 显式 `dual_run=True`
  压过关闭的配置。

## 6. 与 P0 的关系

- 本能力**不改变默认行为**（默认关闭），门禁基线不受影响；
- P0 真实图验证的结论将回答「真实照片上副引擎胜率」——若 LaMa 在真实照片上频繁
  胜出，可考虑把 `auto` 的默认偏好翻转为「双跑择优」；若 OpenCV 仍占优，本能力
  继续作为小众 opt-in。**数据尚未到位前，默认值不动**。

## 7. 剩余边界

- UI 仅单图页接入；批量页/视频页走配置默认（环境变量或设置页后续扩展）；
- 无参考质量分对「统计自洽但内容错误」不敏感（阶段 56/61 已记录）——双跑择优
  是 best-effort，不是真值保证；
- 双跑耗时约翻倍（副引擎 LaMa CPU 3~4s/张），文档已明示。
