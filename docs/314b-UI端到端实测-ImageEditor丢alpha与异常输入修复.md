# 阶段 314b · UI 端到端实测：ImageEditor 丢 alpha 导致「整图重绘」等三处修复

> 方法：真正开浏览器（内置 Browser）走完「上传 → 自动检测 → 一键修复 → 出结果」，
> 而不是只调 API。**主缺陷只有在浏览器往返里才会出现**——服务端单测全绿。

---

## 一、主缺陷：预填图层丢 alpha ⇒ 一键修复重绘整张图

### 现象（实测）

自动检测完成（3 个区域、60,157 px 掩码）后，直接点「④ 一键修复」：

```
✅ 处理完成（引擎：AUTO）。
· 精修统计：检测命中 60,157 像素 / 编辑框 4,194,304 像素 / 你补画 4,134,147 像素。
```

- 编辑框 = 2048² = **整幅画布**
- 「你补画」= **413 万像素**（用户一笔没画）
- 输出图对比原图：**82.11% 像素被改动**，连远离水印的左上角也变了

### 根因（实证，非推测）

在修复回调处临时插桩，打印浏览器回传的编辑器值：

```
editor keys=['background','layers','composite'] layers=1
detail=[{'shape': [2048, 2048, 3], 'dtype': 'uint8', 'alpha_uniq': None,
         'rgb_mean': [254.9999, 59.0001, 48.0]}]
```

- **Gradio 6.x 把图层压成 RGB、丢弃 alpha**（`shape=[H,W,3]`、`alpha_uniq=None`）
- 预填实现 `_mask_to_rgba_layer` 当时是「**RGB 全图刷红 + 靠 alpha 表达掩码**」
  ⇒ alpha 一丢，整层 RGB 恒为 (255,59,48) ⇒ `cvtColor` 灰度恒 ≈116 > 阈值 10
  ⇒ 整层被判为涂抹区 ⇒ `merge_masks(检测, 整图)` ⇒ 整图修复 + 整图后处理

服务端单测没抓到，因为测试直接传 RGBA（alpha 在），**从未模拟 Gradio 的往返形态**。

### 修复

`ui/tab_auto.py::_mask_to_rgba_layer`：**只在掩码处上色，其余保持纯黑**，alpha 仍写
（RGBA 感知的通路不受影响）。丢 alpha 后灰度仍是 0，阈值可正确区分。

### 验证（同一张图、同一路径）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 编辑框像素 | 4,194,304（整图） | **63,106**（水印包围盒） |
| 「你补画」 | 4,134,147 | **2,949** |
| 图层 RGB 均值 | (255, 59, 48) 全红 | **(3.75, 0.93, 0.77)** 近乎全黑 |
| 输出改动像素 | 82.11% | 78.97%（其中后处理占大头，见 §四） |

## 二、异常输入：两处「不可读报错」

| 输入 | 修复前 | 修复后 |
|---|---|---|
| **0 字节 .png** | 透出 OpenCV 原始异常：`error: OpenCV(5.0.0) ... loadsave.cpp:1291` | `CorruptedImageError: 图片文件为空（0 字节）…` |
| **损坏 PDF** | 透出英文 `FileDataError: Failed to open file ... as type pdf.` | `无法打开 PDF（文件可能已损坏，或根本不是 PDF）…原因：…` |

根因：`cv2.imdecode` 对**空数组**是**抛 `cv2.error`**（不是返回 None），旧代码只判了 None；
PDF 的 `fitz.open` 异常落在通用分支，直接 `f"{type}: {exc}"`。

其余异常输入实测**已合格**：文本伪装 .png / 截断 JPEG → `CorruptedImageError`（带处置建议）；
不存在路径 → `文件不存在：…`；损坏视频 → `OpenCV 无法打开该视频（…）`；
灰度 / RGBA / 4096² 大图 → 正常处理。

另：未传 `preset` 且未 `allow_auto_erase` 时，**确认门先于输入校验**拒绝
（`AutoEraseNotConfirmed`，附三条可操作选项）——这是阶段 233 的有意设计，不是缺陷。

## 三、测试稳健性：CPU 交叉验证用例的负载抖动

`test_process_and_system_estimates_are_same_order` 全量跑偶发红、隔离跑必绿。
根因两条：
1. 两个 1.5s 采样**串行**执行，落在不同负载窗口（本机持续负载会降频）；
2. 低负载区比值无意义（1% vs 0.1% 也是 10×，但都是噪声）。

修法：改为**并发**采样（同一窗口），并在系统负载 < 5% 时显式 `skip`（附原因）。
连跑 3 次全绿。

## 四、诚实边界

- 修复后输出仍有 ~79% 像素变化：经 A/B 确认来自**默认后处理**（全局降噪/锐化/边缘融合）。
  `process_image(postprocess=False, denoise=0)` 实测改动 5.05%，`postprocess=True, denoise=3`
  为 35.05% ⇒ 属既有设计（设置页有「降噪强度」「锐化强度」滑杆），**本阶段不改**。
  但它意味着「一键修复」默认会轻微改动全图——若产品后续要「只动水印区」，需要把后处理
  作用域收窄到掩码周边，属独立阶段。
- UI 交互只覆盖了「智能自动检测」页的黄金路径；单图手涂、批量、文档/视频、历史、设置、
  模型管理六个页签仍是**结构级**验证（渲染完整、控件在位），未做点击级回归。
- 浏览器自动化需把隐藏的 `input[type=file]` 临时可见后才能 `setInputFiles`；
  这是测试手段，不涉及产品改动。

## 五、产物

- `ui/tab_auto.py`（`_mask_to_rgba_layer` 黑底 + 仅掩码上色）
- `core/image_utils.py`（空文件 / 解码抛错 → `CorruptedImageError`）
- `services/pdf_service.py`（`fitz.open` 失败 → 中文可读错误）
- `tests/test_mask_editor_alpha_loss.py`（新，5 例：模拟丢 alpha 的往返形态）
- `tests/test_bad_input_readability.py`（新，4 例）
- `tests/test_cpu_load_probe.py`（并发采样 + 低负载跳过）
