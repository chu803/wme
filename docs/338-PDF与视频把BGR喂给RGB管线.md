# 阶段 338 · PDF 与视频把 BGR 喂给了 RGB 管线：同一张图，检测像素差 2.4 倍

> 日期 2026-10-26 · 性质：**核心正确性修复**（第二轮审计 F1，本机独立实测确认）

## 一、契约在哪写的

`services/auto_pipeline.process_image` 的契约是 **RGB**：

* `core/image_utils.load_image` 返回的就是 `rgb`（单图 / 自动 / 批量三个页签都经它取图）；
* `core/detector` 内部按 `cv2.cvtColor(rgb, COLOR_RGB2GRAY)` 取灰 ⇒ 权重是 0.299R + 0.587G + 0.114B；
* 同文件 `services/video_service.extract_sampled_frames:210` 早就做了 `BGR2RGB` ——
  **说明这个约定是公认的，只是逐帧循环没遵守**。

而 PDF / 视频两个入口喂的是 BGR：

```python
# services/pdf_service.py（修复前）
return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)     # _page_to_array：PyMuPDF 给 RGB，却翻成 BGR
core = process_image(bgr, ...)                  # 直接进 RGB 管线

# services/video_service.py（修复前）
ok, frame = cap.read()                          # OpenCV 给的是 BGR
core = process_image(frame, ...)                # 原样进管线
```

顺带一提：`process_image` 的**自己的 docstring 就是矛盾的**——摘要行写「已读入内存的 RGB 数组」，
参数说明写「（BGR，OpenCV 默认排布）」。这种自相矛盾正是缺陷能长期藏身的原因。本次一并订正。

## 二、实测差多少（本机，同一张 `ui/assets/sample_watermarked.png`、同预设、同灵敏度）

| 喂进去的排布 | mask 像素 | 区域数 | 覆盖率 |
|---|---|---|---|
| **RGB**（单图/自动/批量页实际喂的） | **4292** | 6 | 1.05% |
| **BGR**（PDF 与视频以前喂的） | **10328** | 7 | 2.52% |

⇒ 掩码多 2.4 倍、区域 6→7（其中一次还触发「覆盖率超 15% 上限」的夹紧）。
用户表现：**同一个水印，单图页擦得干净，PDF 页 / 视频页要么残留、要么把不该动的地方擦了**。
颜色类先验（白底板 `V≥140/S≤80`、角标模板 NCC）也在错的通道上算，不只是灰度权重。

## 三、为什么肉眼看不出来（这条最关键）

两处错**互相抵消**：进管线时把 BGR 当 RGB（检测错），出管线时又按 BGR 交给
`cv2.imencode` / `VideoWriter`（那确实是 BGR 约定）⇒ **成品颜色是对的**，只有检测质量被悄悄拖走。
所以反例必须断言**通道顺序**本身，看图是看不出来的。
这也解释了为什么它能活这么久：所有既有测试都在看「有没有产出、状态对不对」。

## 四、修法：管线内外各只翻一次

| 位置 | 改动 |
|---|---|
| `pdf_service._page_to_array` | 返回 **RGB**（去掉那次翻转；灰度页 `GRAY2RGB`），docstring 写明契约与实测数据 |
| `pdf_service._encode_page_image` | 入参改名 `rgb`，函数开头**唯一一次** `RGB2BGR` 再给 `cv2.imencode` |
| `video_service.process_video` | 读到帧即 `frame_rgb = cv2.cvtColor(frame, BGR2RGB)`；循环内 `out_frame` 全程 RGB（原来是 `out_frame = frame`（BGR）与 `core["result_rgb"]`（RGB）**混着两种排布**）；`writer.write` 前唯一一次翻回 BGR |
| 附带修好 | `render_pdf_first_page` 的首页缩略图（`cache_service` 登记历史时用它）以前是 BGR 当 RGB 交给 Gradio ⇒ 缩略图 R/B 反色，现在正确 |

## 五、守卫与反例

`tests/test_channel_order338.py`（3 例）：

1. `test_page_to_array_returns_rgb_not_bgr` —— 现造一页「白底 + 纯红矩形」的 PDF，断言红块中心 **R 高 B 低**；
2. `test_encode_page_image_flips_back_once` —— 编码往返后按 BGR 解码仍要是红（**恰好翻一次**，不能翻两次或不翻）；
3. `test_process_video_feeds_rgb_to_pipeline` —— 造一段 3 帧纯红小视频，monkeypatch `process_image` 捕获入参，
   断言**它收到的是 RGB**，并读回输出帧断言颜色没错位。

在修复前的 HEAD 上跑（worktree，跑完即删）⇒ **2 红 / 1 绿**：
红的是 ① PDF 数组排布 和 ③ 视频入参排布；
**绿的正是 ② 编码往返**——因为它只量「颜色对不对」，而颜色本来就是两处错抵消后的结果。
这个「反例里那条该绿的绿、该红的红」恰好把 §三 的机理钉死。

写测试时踩的坑（也记下来）：读回 MJPG-AVI 的第一帧用 `cv2.imread` 返回 `None`，
换成 `cv2.VideoCapture.read()` 才稳 ⇒ **断言要按容器的真实读取方式来，别拿图片 API 量视频**。

## 六、验证

* `tests/test_pdf_service_audit131.py`、`test_video_service_audit113.py`、`test_pdf_history.py`、
  `test_video_reuse_drift_guard.py` 修后全绿（说明既有断言没写死 BGR）；
* `--full --gates` 全量门禁（含 `media_gate` 这条 PDF/视频端到端、`real_gate`、`removal_truth_gate`）
  在提交后跑一次收口，结果记于当日日志。
