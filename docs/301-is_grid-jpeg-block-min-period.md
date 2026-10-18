# 301 · is_grid 抗 JPEG 8px 块伪周期标定（DETECT_GRID_MIN_PERIOD 6→16）

> 2026-10-18 · 阶段结论：**ai_grid 误触发根因（JPEG 8×8 DCT 块）已修复并标定**，
> real_gate 面积 mean 8.88%→2.43%、区域数 747→42.8，红线余量 29%→80%，
> general 红线逐位不变、合成网格零漏检。

## 1. 问题

`real_gate` 面积监控中 `ai_grid` 是全预设最激进的（mean 8.88% / P90 14.73% /
max 14.96% / 平均 747 个区域，红线 ≤0.125）。doubao JPEG 图（真实资产）上
`is_grid` **10/10 误触发**。

## 2. 根因（探针定位）

`scripts/_probe_isgrid.py`（一次性）：10/10 触发且全是**强周期**（ACF 0.19~0.57，
阈值才 0.09，余量 +0.10~+0.48，非贴线抖动）——**周期几乎都是 8 像素**。
这些是 `.jpg`，**8×8 = JPEG DCT 块尺寸**：残差场（高频细节）上 JPEG 块结构
产生强 8px 周期性，ACF 误判为网格；而 `_ok` 判据最小周期只设 6，放过它。

## 3. 标定（`scripts/_probe_grid_period.py` 四类样本）

| 样本类 | period 分布 | lag8 峰 | lag56 峰 |
|---|---|---|---|
| 合成网格 PNG（正，step=56） | 9/9 = 56（或倍数） | ≤0.06 | 0.06~0.80 |
| 合成网格 JPEG q90（正+压缩） | 9/9 = 56（与无损一致） | 极小 | 0.06~0.74 |
| doubao JPEG（负） | 5/6 = 8（JPEG 块）；1 张=63（内容真周期） | 0.10~0.22 | ~0.01 |
| 合成干净底图（负，无损） | 无显著周期 | — | — |

**核心风险排除**：JPEG 压缩没有把 8px 块峰抬到埋没 56px 网格峰
（lag8 始终远小于 lag56）⇒ 抬最小周期不会漏检真实网格。

**标定值 16**：距真实网格 56 有 3.5× 余量、距 JPEG 块 8 有 2× 间距。

## 4. 实施（最小改动 + 可逆杠杆）

- `config.DETECT_GRID_MIN_PERIOD`（新增，默认 16，区间 4~128，`WME_` env 可覆盖）；
- `core/detector.py::detect_grid_watermark._ok` 最小周期由硬编码 6 改读该杠杆。

## 5. 验证

| 项 | 结果 |
|---|---|
| doubao JPEG is_grid 误触发 | **10/10 → 1/10**（残留 1 张为 63px 内容真周期=沙纹，非 JPEG 块） |
| 合成网格 seed=7（3 底图） | is_grid 全保持，mask_cov 1.86~1.90%（零漏检） |
| `tests/test_grid_precision.py` | 6 passed |
| `real_gate --strict-scope` | ✅ 通过；ai_grid 面积 mean **8.88%→2.43%**、P90 14.73%→4.40%、max 14.96%→7.80%、区域 **747→42.8**；红线余量 29%→**80%** |
| general 红线（承诺面） | **逐位不变**（mask_recall 0.5960 / precision 0.0801 / recall 0.9057 / mean_iou 0.6638 / mask_iou 0.3217 / mask_coverage 0.0129 / coverage_ratio 2.30×） |
| 其他面积监控预设 | 不变（ai_faint_tiled 2.80% / ai_text_heavy 1.25% / ai_gradient 0）——只改网格通路 |
| 守卫 `tests/test_grid_min_period.py` | 3 passed；注入反证 `WME_DETECT_GRID_MIN_PERIOD=6` → 负向守卫 FAILED（该图网格通路覆盖 8.27%） |

## 6. 附带发现（已记，待后续）

1. **ai_grid 预设是多通路并集**：is_grid=False 后 preset 级仍有 ~3% 覆盖
   （doubao 图来自半透明通路）。real_gate 的 ai_grid mean 2.43% 残留主要由此而来，
   而非网格通路。治半透明通路在非网格图上的检出是另一条线（S2 已收敛 general，
   ai_grid 未承诺预设按面积监控兜底即可）。
2. **63px 内容真周期**（03_沙漠日落，沙纹）：非 JPEG 块，最小周期治不了；
   若要继续收敛需 ACF 阈值 / 周期-覆盖率联合判据（缺真实网格真值，暂缓）。

## 7. 结论与边界

- 修复只针对「JPEG 压缩伪痕误触发」这一类，**不改变**任何真实网格水印的检出
  （56px 夹具三底图零漏检 + e2e 合成网格不受影响）。
- ai_grid 是**未承诺**预设，本次让其面积红线余量从 29% 扩到 80%，属稳健性提升，
  不影响承诺面（ai_logo）任何结论。
