# 阶段 307 · full 阶段 perf 基线标定（RTX 5060 / cu128）

> 收口「full 阶段单独标定 perf 基线」：在真机 RTX 5060 上用项目自有 `perf_probe.py`
> 把全链路（detect+inpaint）时延落成**一等公民基线**，写入 `benchmark_real/perf_baseline_gpu_default.json`。
> 此前阶段 305 用权威口径复核时，full 阶段因「噪声比 0.377>0.20 拒绝采信 + stage 不一致拒绝跨口径比」
> 退出码 2（两道护栏正确工作，非失败），故 full 一直是「有数值、无入库基线」状态。
> 本阶段把它正式标定落盘。

---

## 一、测什么 / 怎么测

- **被测路径**：`perf_probe.py --stage full`（全链路 `api.remove_watermark_bytes` 形态，detect+inpaint），
  取每张图 `repeat=3` 的 **min** 作为该图样本值（过滤瞬态噪声），再取 10 图的 P50/P95/max/均值。
- **设备**：RTX 5060 Laptop（Blackwell sm_120，8123 MiB），`device=cuda`，torch 2.9.1+cu128。
- **负载**：`benchmark_real/images` 10 张真实 AI 水印图（2048² 整帧水印，裁剪修复对整帧水印无效仍走 2048² 推理）。
- **口径绑定**：full 阶段强制比较 `real_superres_enabled/scale/backend`（阶段 263 已落地）。本次 **超分关闭**（默认）。
- **写入目标**：`benchmark_real/perf_baseline_gpu_default.json`（与 detect 基线 `perf_baseline.json` 分工，互不覆盖；CI `run_all_checks.py` 默认跑 detect，不动本文件）。

---

## 二、实测结果（RTX 5060，full 全链路，超分 OFF）

| 指标 | 值 |
|---|---:|
| n / runs / repeat | 10 / 10 / 3 |
| min | 4.047s |
| P50 | 5.433s |
| **P95** | **8.397s** |
| max | 8.397s |
| mean | 5.915s |
| 相对噪声（极差/P95） | **0.067**（阈 0.20）→ ✅ 可信 |
| 锚点图（ai_logo_02）轮首/轮尾 | 7.143s / 4.410s（尾/首=**0.617**）→ ✅ 无渐变 |
| 超分口径 | `real_superres_enabled=False` / `scale=4` / `backend=auto` |
| 结构版本 | version 3 |
| measured_at | 2026-10-19 |

### 机器洁净度验证（关键，决定结论可信性）
- **噪声门（相对噪声 ≤ 0.20）**：本次 **0.067** 远低于阈值。**该门无逃逸口**——即使 `--force-update` 也绕过不了；它过了，说明测量本身干净、非瞬态抖动 ⇒ P95 8.397s 可采信。
- **锚点门（anchor_ratio ≤ 1.30，渐进式热降频检测）**：0.617 远低于阈值 ⇒ 无热降频 / 渐进式退化，整轮测量稳定。
- 两门齐过 ⇒ full P95 8.397s 是**有效测量**，不是 305 当时因噪声爆炸（0.377）被拒的那次。

---

## 三、与路线图的映射与结论

- **路线图书面目标「P95 < 1.5s」对 full 阶段结构性不可达**：实测 8.397s 超目标 5.6×。
  阶段 305 已用 GPU↔CPU A/B 从因果上证明——detect 阶段（吃 GPU、轻量）才谈得上 1.5s；
  full 阶段瓶颈是 **LaMa 在大包围盒上的推理**（recommend+detect 仅 ~1.85s，其余全是 inpaint），
  full P95 结构地板 ≈4.4s、GPU 实测 8.397s ⇒ 1.5s 目标只应考核 detect，对 full 须另行放宽或重设。
- **向下刷新护栏**：新 P95 8.397s vs 旧基线（见第四节 9.846s）×1.30 = 12.800s → 8.397 < 12.800 ⇒ ✅ 在护栏内（且更快），无退化。
- **detect 基线未动**：`benchmark_real/perf_baseline.json`（detect，P95 0.6788，2026-10-06）保持原样，本标定只写 full 专用文件。

**结论：full 阶段 perf 基线已正式标定落盘（P95 8.397s，RTX 5060，超分 OFF）。剩余开放项 ① 收口。**

---

## 四、陈旧基线 supersession 诚实记录

执行时首跑 `perf_probe --stage full --update`（不带 `--force-update`）被守卫**正确拒绝**：

```
[拒绝刷新] 本次测量与既有基线不可比：real_superres_enabled 不一致（基线 None vs 本次 False）
```

- 既有 `perf_baseline_gpu_default.json` 是 **2026-10-06** 旧口径（P95 9.846s，**缺失 `real_superres_*` 键**）——属阶段 263 指出的「历史探针数字，非入库基线」。
- 阶段 263 的修复正是让 full 阶段把超分口径纳入比较；旧文件缺该键 ⇒ 守卫判定「结构过旧、不可比」，**显式 prescribe `--force-update` 以新口径 supersede**。
- 复跑加 `--force-update` ⇒ 写入新基线（P95 8.397s，含完整 `real_superres_*` 键，version 3），退出码 0。

> ⚠️ **诚实披露**：旧「full P95 9.846s」与本次「8.397s」不可直接比较——旧值缺超分口径、测量于不同负载与机器状态；本次为权威 `perf_probe`（min-of-3、噪声门、锚点门）在干净机器上的标定值，二者口径不同。取 **8.397s** 为唯一承诺值。

---

## 五、自反证伪与边界

- **噪声门无逃逸口**是设计要点：即便用 `--force-update`，噪声比 >0.20 仍会拒刷新。本次 0.067 证明测量干净，结论不依赖 `--force-update` 的强制力。
- **full 基线 ≠ detect 基线**：两文件分工（305/263 确立）。CI 默认门禁只跑 detect；full 基线供「全链路时延回归」独立引用，避免误覆盖 detect 红线基线。
- **超分口径已锁定为 OFF**：若后续开启真实超分（阶段 262 实测 +2.6s），须重新 `--stage full --update` 标定，旧 full 基线会因 `real_superres_enabled` 不一致被判不可比——这是预期行为，非 bug。
- **负载相关**：本次负载是 2048² 整帧水印（最重档）；小图/局部水印负载的 full P95 会显著更低（见 306 并发实测中 worker=1 单图 ~8–14s 同量级，但小图会更低）。基线是「代表性重负载」口径，非全负载上界。

---

## 六、验证

- 实跑：
  ```bash
  PYTHONPATH=. <canonical-venv>/python.exe scripts/perf_probe.py \
    --stage full --update --force-update \
    --baseline benchmark_real/perf_baseline_gpu_default.json
  ```
  → 退出码 **0**，报告 `✅ 性能门禁通过`，新基线落盘 `benchmark_real/perf_baseline_gpu_default.json`。
- 落盘核验：`benchmark_real/perf_baseline_gpu_default.json` → `p95=8.3972`、`noise_ratio=0.0669`、`anchor_ratio=0.6174`、`real_superres_enabled=false`、`version=3`、`measured_at=2026-10-19`。
- detect 基线隔离核验：`benchmark_real/perf_baseline.json` → `p95=0.6788` 未变（未被本次写入触碰）。
- 文档登记：`docs/INDEX.md` 已加 307 条目；`test_docs_index` 须保持绿。
