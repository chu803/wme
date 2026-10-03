# 阶段 9 设计文档：脚本化 API 增强（程序库 + REST 服务）

> 阶段 8 已交付 CLI（命令行批量）+ 质量评估 + 导出格式扩展。
> 阶段 9 在 **不改动任何核心修复算法** 的前提下，把「检测 + 修复」核心
> 进一步封装为「可程序调用」与「可 HTTP 调用」两套入口，让本工具既能当网页用、
> 又能当库嵌进你自己的程序、或通过 HTTP 被任何语言远程调用。

---

## 1. 为什么要做阶段 9

| 入口 | 用途 | 阶段 |
| --- | --- | --- |
| 网页界面（Gradio） | 人工点点点 | 阶段 1~6 |
| 命令行 CLI | 服务器 / 批量无人值守 | 阶段 8 |
| **程序化库 `api.py`** | 嵌进你自己的 Python 脚本 / 服务 | **阶段 9（本阶段）** |
| **REST 服务 `server.py`** | 任何语言 / 前端 / 自动化通过 HTTP 调用 | **阶段 9（本阶段）** |

四套入口**共用同一条核心逻辑**，修复行为永不分叉：

```
                ┌─────────────────────────────────────────────┐
                │       services/auto_pipeline.process_image    │
                │   （数组级原子单元：检测 + 修复，唯一真相源）   │
                └─────────────────────────────────────────────┘
                   ▲        ▲           ▲              ▲
       ┌───────────┘        │           │              └───────────┐
  网页界面              CLI 批量       程序库 api.py          REST server.py
  tab_single/auto/    cli.py          remove_watermark()     POST /api/remove
  batch/history                      remove_watermark_bytes() （base64 进出）
                                     remove_watermark_array()  （numpy 进出）
                                     batch_remove()
```

---

## 2. 核心复用点：`process_image`（数组级原子单元）

阶段 9 对 `services/auto_pipeline.py` 做了一处关键重构：把原来内联在
`process_file` 里的「智能检测 + 核心修复」抽成独立的 **`process_image(rgb, ...)`**：

```python
def process_image(rgb, *, mask=None, mode="auto", engine=None,
                  strength=None, dilate=None, feather=None, threshold=None,
                  denoise=None, sensitivity=None, on_progress=None) -> dict:
    # 返回: {"status","result_rgb","mask_pixels","regions","used_ocr","engine"}
```

- 参数回填默认值、auto 检测、空掩码跳过、核心 `inpaint()` —— 全部在这一个函数里。
- `process_file`（文件入口）改为「读图 → 调 `process_image` → 保存」，行为完全不变。
- `api.py` 的字节 / 数组接口、`server.py` 的 HTTP 接口，都直接调 `process_image`，
  **不再各自重复实现检测与修复**，杜绝行为漂移。

> 重构前后自检一致：掩码 4824 像素 / 8 区域，修复结果与原 pipeline 完全相同。

---

## 3. 程序化库 `api.py`

零 gradio 依赖，纯 Python 调用。公开函数：

### 3.1 `remove_watermark(src, *, dst=None, mode="auto", engine=None, ..., fmt=None, quality=None, evaluate=False) -> RemoveResult`
处理一张本地图片文件。`dst` 不传则落到统一 `outputs/`；传了则移动到精确路径
（已处理跨磁盘驱动器移动，见 §6 坑点）。

### 3.2 `remove_watermark_bytes(data: bytes, *, engine=None, ..., fmt=None, quality=None, evaluate=False) -> (bytes, QualityReport|None)`
处理内存里的图片字节（从网络 / 数据库读到）。返回 `(结果字节, 质量报告)`。
按 BGR 三通道解码，**透明通道会被丢弃**；需保留透明通道请用文件接口。

### 3.3 `remove_watermark_array(arr: np.ndarray, *, engine=None, ..., evaluate=False) -> (np.ndarray, QualityReport|None)`
处理内存里的 numpy 数组（BGR uint8，OpenCV 排布）。适合**视频帧 / 摄像头画面**。

### 3.4 `batch_remove(paths, out_dir, *, ...) -> list[RemoveResult]`
批量处理一组本地图片到指定目录，单张失败不影响整批。

### 3.5 `get_capabilities() -> dict`
返回能力快照：`device`(cuda/cpu)、`lama_available`、`output_formats` 等，供 REST /health 复用。

### 3.6 `RemoveResult` 数据类
`original_name / out_path / status / mask_pixels / regions / used_ocr / engine / error / quality`。

**调用示例：**
```python
from api import remove_watermark, remove_watermark_bytes

# 文件
res = remove_watermark("原图.jpg", dst="结果.png", evaluate=True)
print(res.status, res.out_path, res.quality.score)

# 字节（例如从 HTTP 收到的图片）
out_bytes, qr = remove_watermark_bytes(image_bytes, fmt="PNG")

# 数组（视频帧）
out_arr, qr = remove_watermark_array(frame_bgr)
```

---

## 4. REST 服务 `server.py`（零依赖，仅标准库）

只用 `http.server` + `threading`，**不引入 FastAPI / Flask 等任何新依赖**。
与 Gradio 网页服务端口错开：网页 7860，REST 默认 7861。

### 4.1 端点契约

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 + 能力快照 |
| POST | `/api/remove` | 单图处理，body=`{"image":<base64>, 参数...}` |
| POST | `/api/batch` | 批量，body=`{"images":[<base64>...], "params":{...}}` |
| GET | `/` | 用法说明 HTML |
| OPTIONS | `*` | CORS 预检（已放开 `Access-Control-Allow-Origin: *`） |

**POST /api/remove 请求体（JSON）：**
```json
{
  "image": "<base64 图片>",
  "engine": "opencv",     // 可选 lama/opencv/auto
  "mode": "auto",         // 可选 auto/manual
  "fmt": "PNG",           // 可选 PNG/JPG/WEBP/TIFF/AVIF
  "evaluate": true        // 可选，是否返回质量评估
}
```
**响应（JSON）：**
```json
{
  "status": "success",
  "result": "<base64 结果图>",
  "engine": "opencv",
  "quality": {"score": 100, "level": "good", "ssim": 0.99, "changed_ratio": 0.2, "detail": "..."}
}
```

### 4.2 启动方式
```bash
python cli.py --serve                 # 默认 127.0.0.1:7861
python cli.py --serve --host 0.0.0.0 --port 7861   # 全网（需自配防火墙/鉴权）
python server.py                      # 等价于上面的简写
```

### 4.3 安全说明
- 默认监听 `127.0.0.1`（仅本机），**不对外暴露**。
- 若设 `API_HOST=0.0.0.0`，服务可被局域网 / 公网访问，**务必自行加防火墙与鉴权**
  （本工具不内置鉴权，定位是个人本地 / 内网使用）。
- 单次请求体上限 50MB，防止异常大请求打垮服务。

---

## 5. 配置新增（config.py）

| 常量 | 默认 | 说明 |
| --- | --- | --- |
| `API_HOST` | `127.0.0.1` | REST 监听地址 |
| `API_PORT` | `7861` | REST 端口（与网页 7860 错开） |
| `API_ENABLED` | `True` | 是否允许启动 REST（False 时任何入口启动都会被拒） |
| `API_DEFAULT_FMT` | `PNG` | 字节 / REST 接口缺省返回格式 |

均支持 `.env`（`WME_` 前缀）覆盖，端口已做取值范围校验（1024~65535）。

---

## 6. 关键坑点（已解决）

1. **跨磁盘移动失败**：`os.replace` 不能在「不同磁盘驱动器」间移动
   （沙箱 outputs 在 D:，测试 / 用户目标常在 C:），会抛 `WinError 17`。
   已封装 `_move_file()` 用 `shutil.move`（跨盘自动降级为「复制+删除」）并支持覆盖。
2. **TIFF / AVIF 字节编码**：`cv2.imencode` 多数环境不支持这两种格式。
   `_encode_bytes()` 对 PNG/JPG/WEBP 走 `cv2.imencode`，对 TIFF/AVIF 委托
   `save_image` 落临时文件再读回字节，保证任何格式都能返回。
3. **字节模式质量评估**：REST 拿到的是 base64 字节，没有文件路径。
   `_evaluate_arrays()` 把原图与结果数组临时落盘为 PNG 再调 `evaluate_quality`，
   用完即清，避免改动 `quality_service` 的公开 API。

---

## 7. 验证结论（全部通过）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| auto_pipeline 自检 | `python -m services.auto_pipeline` | 成功（4824 像素 / 8 区域，重构前后一致） |
| api.py 自检 | `python api.py` | 5/5 用例通过（字节 / 数组 / 文件+dst / 批量 / 能力） |
| server.py 自检 | `python server.py` | health / 单图 / 批量 全过 |
| cli --serve 真实探活 | `python cli.py --serve --port 7877` + GET /api/health | 返回 `ok` |
| 界面不回归 | `python -c "import ui.app; app.build_app()"` | `build_app OK`（6 页签） |

所有测试残留（outputs/tmp*、临时目录）均已清理，历史缓存为干净态。

---

## 8. 遗留说明（后续阶段）

- **PDF / 视频帧输入**：当前 API 接收「已解码的图像」（字节 / 数组 / 文件）。
  PDF 多页、视频多帧的「拆分 → 逐帧处理 → 合并」放在阶段 10。
- **自定义模型接入**：LaMa 之外接入其他 inpaint 模型（如 LaMa-Fourier / MAT），
  作为阶段 11 的扩展点之一。
- **REST 鉴权**：本阶段不含鉴权，定位个人 / 内网；公网部署需自行在反向代理层加鉴权。
- **批量结果质量评估（GUI）**：阶段 8 已在批量页加「完成后质量评估」勾选；
  REST /api/batch 已逐张返回质量，前端可据此高亮告警项。
