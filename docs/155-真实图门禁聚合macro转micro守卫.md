# 阶段 155 · 真实图门禁聚合 macro→micro 守卫

> 审计对象：`scripts/run_real_benchmark.py` 的 `_aggregate` / `_match_regions`，及消费其输出的 `scripts/real_gate.py`。
> 触发：未审榜已空（截至 145），顺延审 `scripts/` 入口；`_aggregate` 仅被 smoke 的 `_repair_one` 接线覆盖，聚合口径（macro vs micro）零测试。

## 结论

`_aggregate` 把 **per-image 均值（macro）** precision/recall 当作头条输出，但文件头对 recall 的定义就是 **micro**（TP 区域数 / 真值区域总数）。两者不等价：macro 把「少数干净小图」与「多数重残大图」等权，会**系统化高估**聚合指标，并直接被 `real_gate` 喂给红线 → **可能误绿**。

修复：头条口径改为 micro（按汇总 TP/FP/FN），macro 降级为次级参考；`real_gate` 红线改取 micro。

| 项 | 修复前（macro） | 修复后（micro） | 偏差 |
|---|---|---|---|
| 1 完美 + 1 重残（50TP/50FP/50FN） | precision 0.75 / recall 0.75 | **0.5050** / **0.5050** | +0.2450 |
| 9 完美 + 1 重残 | 0.95 / 0.95 | **0.5413** / **0.5413** | +0.4087 |
| 红线 precision≥0.6 | **PASS（假绿）** | **FAIL** | 翻转 |

## 归因

1. **口径写错**：`_aggregate` 对 per-image 的 `precision`/`recall` 列表取 `mean()`（lines 239–240），本质是 macro；
   而文件头第 10–11 行定义 recall = "TP 区域数 / 真值区域总数"（micro）。实现与契约定义不一致。
2. **门禁直接消费 macro**：`real_gate.py:703-704` 把 `a["precision_mean"]`/`a["recall_mean"]`（macro）作为
   `per_preset` 的 `precision`/`recall` 喂给红线判定，于是「被高估的 macro」直接驱动门禁。
3. **`_match_regions` 不是缺陷**：经探针验证——`two-preds-one-truth → (tp1,fp1,fn0)`、`one-pred-two-truths → (tp1,fp0,fn1)`、
   `empty/empty → (0,0,0)` 不崩溃、不漏配。IoU 本身对称 + 贪心一对一双向遍历，无方向不对称。故**只修聚合口径**。
4. **退化输入**：全 error 行被 `_aggregate` 静默跳过（filter `"error" not in ...`）；若全部 error → `n=0`、
   `precision_mean/micro = 0.0`（误导但安全，非崩溃）。已在守卫中固化该行为，避免后续改动引入崩溃。

## 验证（探针）

用托管 Python 复刻并调用真实 `_aggregate` / `_match_regions`（代码见 `.probe_155.py`，已清理）：

```
SCENARIO 1: 1 perfect small image + 1 heavily-flawed big image
  macro  precision=0.75  recall=0.75
  micro  precision=0.5050  recall=0.5050
  -> precision inflation (macro-micro) = +0.2450
  -> recall    inflation (macro-micro) = +0.2450
  gate(precision>=0.6): macro=PASS  micro=FAIL

SCENARIO 2: 9 perfect small images + 1 big flawed (10% complex)
  macro  precision=0.95  recall=0.95
  micro  precision=0.5413  recall=0.5413
  gate(precision>=0.6): macro=PASS  micro=FAIL

MATCH REGIONS symmetry / robustness
  equal boxes          : (1, 0, 0, [1.0])
  two preds one truth  : (1, 1, 0, [1.0])
  one pred two truths  : (1, 0, 1, [1.0])
  empty/empty          : (0, 0, 0, [])
  empty preds,1 truth  : (0, 0, 1, [])

DEGENERATE aggregate inputs
  one error + one ok   : n=1, precision_mean=1.0 (error filtered)
  all error (n field)  : n=0, precision_mean=0.0  (micro 同样落 0.0，无崩溃)
```

## 修复

`scripts/run_real_benchmark.py`：

- `_aggregate`：每预设增 `tp_total/fp_total/fn_total` 汇总（跳过 error 行），输出
  `precision_micro = tp/(tp+fp)`、`recall_micro = tp/(tp+fn)`（分母为 0 时落 0.0），**置顶**；
  原 `precision_mean`/`recall_mean` 保留为 macro 次级参考。
- `_print_markdown`：表头改为 `precision(micro) | recall(micro) | precision(mean) | recall(mean)`，micro 列前置。
- 函数 docstring 补 macro/micro 口径说明。

`scripts/real_gate.py`：

- `run_real_gate` 构建 `per_preset` 时：`"precision": a.get("precision_micro", a["precision_mean"])`、
  `"recall": a.get("recall_micro", a["recall_mean"])`——红线改取 micro，旧结果缺 micro 字段时回退 macro（兼容）。

## 守卫

新增 `tests/test_real_benchmark_aggregate.py`（4 例）：

1. `test_aggregate_reports_micro_correctly`：1 完美+1 重残，`precision_micro/recall_micro == 0.5050` 且与 macro(0.75) 不同；`tp_total/fp_total/fn_total == 51/50/50`。
2. `test_aggregate_macro_inflates_vs_micro`：9 完美+1 重残，`precision_mean - precision_micro > 0.2` 且 `precision_micro < 0.6`（红线条约可翻转）。
3. `test_aggregate_all_error_no_crash_and_micro_zero`：全 error → `n=0`、`precision_micro == 0.0`、`recall_micro == 0.0`，无 `KeyError`/崩溃。
4. `test_real_gate_uses_micro_not_macro`：隔离 `WME_BENCH_DIR` 到 tmp + `importlib.reload(real_gate)` + `monkeypatch.run_all` 返回 macro(0.95)≠micro(0.5413) 的合成结果，断言 `per_preset["general"]["precision/recall"] == 0.5413`（走 micro 而非 macro）。

## 注入式反例（守卫有牙）

`.ce_155.py`：移除 `_aggregate` 的 micro 计算（accumulation 三行 + 结果三行）→ 守卫 `pytest` exit=1（红灯，
`KeyError` / 值不符）；还原源文件 → sha256 逐字节一致（`0bd4f6b24995c34f814faaa2a80b7b74d4612a78ddfdd3da564b72efbf580e58`）；
还原后 exit=0（绿灯）。

## 回归

- 新增 4 例 + `test_real_benchmark_smoke.py` 5 例 + `test_real_gate.py` 37 例 = **46 例全绿**。
- `_print_markdown` 渲染验证：micro 列（0.5050）前置、macro 列（0.7500）次级，无 `KeyError`。

## 诚实边界

- 本阶段只修正「聚合口径被读错」这一缺陷，不改变任何真实数据集的结论——当前 `benchmark_real/ground_truth.json`
  仍是 `entries:[]`（阶段 149/150 移除模板化伪造标注），真实红线因缺证持续红，`ai_logo` 仍 `committed_missing=['ai_logo']`。
- micro 字段在空真值/全 error 下落 0.0（与 macro 一致），属「误导但安全」；已在守卫固化非崩溃，避免后续改动引入崩溃。
- `_match_regions` 经探针验证无需改动。
