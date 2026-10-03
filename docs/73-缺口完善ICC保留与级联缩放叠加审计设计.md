# 阶段 73 · 缺口完善：ICC 色彩配置保留、级联×缩放叠加审计、浮点 TIFF 真值验证

> 日期：2026-09-12 · 前置：阶段 70/71/72 · 性质：把此前显式记录的「未测/不保留」缺口收口

## 0. 动机：缺口清单（阶段 70/71/72 文档里的「剩余边界」）

| 缺口 | 原状态 | 本阶段处置 |
|---|---|---|
| ICC 色彩配置不保留 | 「宽色域输入导出可能偏色，留待真实样本评估」 | **已修复**：加载端读出 ICC、保存端随结果写回（§1） |
| 级联与缩放叠加未测 | 阶段 67 单测缩放、阶段 72 单测级联 | **已审计**：`--with-scale` 维度 + 基线（§2） |
| 32 位浮点 TIFF 无真值 | 「仅做合理映射、未做真值验证」 | **已验证**：逐位还原测试锁定（§3） |
| 混合质量组合未测 | 未覆盖 | **部分覆盖**：级联本身即 JPEG75/WebP75 混合序列；任意质量组合空间无限，记录为不测（§4） |

## 1. ICC 色彩配置端到端保留（修复宽色域偏色）

### 1.1 缺陷机理

- `cv2.imdecode` **丢弃**内嵌 ICC；`cv2.imencode` **无法嵌入** ICC。
- 后果：Display-P3 / AdobeRGB 等宽色域输入经「读 → 修 → 写」后色彩配置丢失，
  在不做色彩管理的查看器里**偏色**（通常是饱和度/色相整体漂移）。
- 探测实证：Pillow 的 `icc_profile` 元数据在 PNG/JPEG/WEBP/TIFF 四格式均可
  **逐字节往返**（588 字节 sRGB profile 读写一致；PNG/TIFF 像素逐位无损）。

### 1.2 实现（`core/image_utils.py`）

- `read_icc_profile(p) -> Optional[bytes]`：Pillow 只读元数据（不解码像素），软失败
  返回 None——**绝不因读色彩配置让加载失败**；
- `load_image_full(p) -> (rgb, alpha, icc)`：既有 `load_image_with_alpha` 的超集；
- `save_image(..., icc: Optional[bytes] = None)`：带 ICC 时改走 Pillow 写出
  （`_save_with_icc`，PNG/JPEG/WEBP/TIFF 均支持 `icc_profile`）；失败回退 cv2 路径
  并告警（sRGB 图本就无 ICC，不受影响）；
- AVIF 分支：cv2 编码路径不带 ICC → 有 ICC 时**优先 Pillow**（其 AVIF 插件支持
  `icc_profile`），两级回退不暴露隐藏 RGB 的语义保持（阶段 71）；
- 服务层贯通：`auto_pipeline`（CLI/批量主链路）与 `export_service.reencode_image`
  均改为 `load_image_full` → `save_image(..., icc=icc)`；`save_result` 增加 `icc`
  透传参数（UI 层暂不传，见 §5 边界）。

### 1.3 工程插曲（值得记录）

`_save_with_icc` 首版直接引用模块级 `cv2`——但本模块的 cv2 是 `_require_cv2()`
延迟导入，NameError 被 `except Exception` 吞掉 → ICC 静默丢失、测试当场抓住。
教训与阶段 61（死代码）同族：**「回退兜底」类代码必须为失败路径写反例测试**，
否则异常吞掉后功能等于没实现。

## 2. 级联 × 缩放叠加审计（`--with-scale`）

### 2.1 口径

真实链路是「平台先下采样 → 反复转码」。维度：scale ∈ {1.0, 0.75, 0.5}（先缩放）×
轮数 0/1/2/3/5/8（每轮 JPEG75→WebP75→JPEG75）；真值取无损配对后**随图同步最近邻
缩放**（阶段 63/67 口径延续）。

### 2.2 实测（三底图范围）

| scale | n | corner | edge | grid | clean avg(最差底图) | general(最差) |
|---|---|---|---|---|---|---|
| 0.75 | 0 | 100% | 86~99% | 83~100% | 1.09% | 1.45% |
| 0.75 | 3 | 100% | 86~100% | 68~97% | 2.31% | **3.01%** |
| 0.75 | 8 | 100% | 88~100% | **23~94%** | 3.58% | 2.18% |
| 0.5 | 1 | **0~100%** | 66~100% | 50~94% | 0.74% | 0.98% |
| 0.5 | 3 | 84~100% | 97~100% | **2~94%** | 0.53% | 0.68% |

### 2.3 归档裁决

scale < 1 的行落入**阶段 67 已建立的低分辨率区间**（长边 <640：黑屏由 opt-in 兜底
`DETECT_LOWRES_RESCUE` 处置、精度保证放宽）——本审计不重复设立契约，只做两件事：

- **黑屏红线**：scale=0.75 时非豁免用例召回 0 仍判失败（0.5 为阶段 67 记录区间）；
- **记录性数据**：低分辨率行的召回/误报如实进基线 JSON，供 P0 真实图验证对照。

scale=1.0 行完整保留阶段 72 的四条红线（不得被新维度稀释，有专门测试锁定）。

## 3. 32 位浮点 TIFF 真值验证

- 实测：cv2 对 float32 TIFF 的 imencode/imdecode **数值无损**（float 原样往返）；
- `[0,1]` 归一化输入：`_normalize_bit_depth` 的「max≤1 → ×255」映射把视觉真值
  **逐位还原**（最大差 0）；
- `[0,255]` 全量程输入：按原值截断映射，同样逐位还原；
- 阶段 70 的映射实现本就正确——本阶段补上的是**真值测试**（注释从「合理映射」升级为
  「逐位验证」）。

## 4. 混合质量组合：显式「不测」的理由

级联每轮本身已是 JPEG75/WebP75 **混合质量**序列；任意 (格式×质量×轮数×缩放) 组合空间
无限，逐一覆盖无边际收益。真实场景的主导变量（次数、缩放）已分别与联合审计。记录为
**显式不测**，非遗漏。

## 5. 测试与剩余边界

- `tests/test_icc_profile.py` **17 例**：四格式 ICC 写出 / 未提供不写 / PNG+TIFF 像素
  逐位 / alpha+ICC 共存 / JPG 合成不显形 / `load_image_full` 语义 / 无 ICC 返回 None /
  `save_result` 透传 / AVIF 分支；
- `tests/test_cascade_robustness.py` 13 → **19 例**：x0.75 黑屏红线 / x0.5 只记录 /
  低分辨率行不套召回下限与误报契约 / scale=1 契约不被稀释 / markdown 缩放标签 /
  `rescale` 语义；
- `tests/test_input_normalization.py` 39 → **41 例**：浮点 TIFF 两种量程逐位还原。

**剩余边界（更新后）**：UI 界面的自动保存路径（`save_result` 默认调用点）暂不传 ICC
（界面请求流需先透传 ICC 元数据，属 UI 改造，另行安排）；ICC 只做「原样保留」，不做
跨色彩空间转换（如 P3→sRGB 压缩色域）；显式不测任意混合质量组合。

## 6. 交付清单

- `core/image_utils.py`：`read_icc_profile` / `load_image_full` / `save_image(icc=)` /
  `_save_with_icc` / AVIF 分支 ICC 感知；
- `services/auto_pipeline.py`、`services/export_service.py`、`services/file_service.py`：贯通；
- `scripts/cascade_robustness.py`：`rescale` + scale 维度 + 分区红线判定 + `--with-scale`；
- `docs/assets/cascade_baseline.json`：含缩放维度的新基线；
- 测试：`tests/test_icc_profile.py`（17）、`test_cascade_robustness.py`（+6）、
  `test_input_normalization.py`（+2）。
