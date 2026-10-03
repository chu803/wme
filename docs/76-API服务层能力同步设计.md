# 阶段 76 · API / 服务层能力同步设计

> 日期：2026-09-13 · 前置：阶段 70~75 · 性质：模块深化（外部集成入口收口）

## 1. 动机：能力先进了核心，但集成入口没跟上

阶段 70~75 连续交付了六项能力，但**外部集成入口（REST / 库 API）始终停在阶段 21 的形态**：

| 能力 | 阶段 | REST/库 API 是否可用（修复前） |
|---|---|---|
| ICC 色彩配置保留 | 73 | ✅（自动生效，无需参数） |
| 输入形态规范化（EXIF/位深） | 70 | ✅（开关只能走环境变量） |
| 透明通道策略 | 71 | ✅（自动生效） |
| 低分辨率兜底 | 67 | ⚠️ 仅环境变量，REST 无法按请求开启 |
| 双跑择优 | 74/75 | ❌ `remove_watermark*` **不接受 `dual_run`** |
| 可选增强（Poisson/纹理/伪影） | 21/61 | ❌ **REST 参数白名单未收录**（发出即被静默丢弃） |

其中第三类最隐蔽：`server._collect_params` 的白名单只覆盖基础参数，客户端发
`dual_run` / `texture_transfer` 等键会被**静默丢弃**——既不报错也不生效。
`get_capabilities()` 同样停留在阶段 21 字段，客户端无法「先问后调」。

## 2. 实现

### 2.1 库 API（api.py）

- `remove_watermark` / `remove_watermark_bytes` / `remove_watermark_array` /
  `batch_remove` 新增 `dual_run: Optional[bool] = None`，逐层透传至
  `process_file` → `process_image` → `inpaint(dual_run=)`；
- `services/auto_pipeline.process_file` 同步新增 `dual_run` 参数（此前只有
  `process_image` 接受它）。

**边界（刻意不暴露）**：`remove_watermark_pdf` 与 `remove_watermark_video` 不接收
`dual_run`——逐页 / 逐帧双跑会让成本翻倍，需求场景（单张精修）与批量场景不同；
这两个入口仍受进程配置（环境变量 / 设置页）全局影响。该边界有测试锁定。

### 2.2 能力快照（api.get_capabilities）

新增 16 个字段，全部**与进程实际配置同源**（`getattr(config, ...)`，不硬编码）：

```
mask_dilate_default / mask_feather_default / inpaint_auto_prefer
dual_run_default / dual_run_secondary
output_alpha_formats / output_flatten_bg_color / keep_alpha_default
exif_orientation_applied / bit_depth_normalized / icc_preserved_on_save
lowres_rescue / lowres_rescue_side / detect_min_area_ref
optional_enhance_gates{poisson_blend, texture_transfer, artifact_reduce}
user_overrides_path
```

其中 `optional_enhance_gates` 是「默认开关 ∧ 部署放行闸」的**合取**结果（阶段 61
双闸语义）——客户端拿到的 `false` 意味着该增强当前不可用，而不是「参数没传」。

### 2.3 REST 参数白名单（server._collect_params）

- 白名单由 13 键扩至 **25 键**：补全 `poisson_*` / `texture_*` / `artifact_*` /
  `superres*` / `real_*`（与 `api.remove_watermark_bytes` 签名对齐）与 `dual_run`；
- 新增**布尔归一**：字符串 `"1"/"true"/"yes"/"on"` → `True`，便于表单类客户端；
- 响应新增 `dual_run` 回显（请求未传时回显进程默认值），客户端可确认「这一张到底
  有没有双跑」。

## 3. 过程中发现的真实缺陷（已修）

**接线时的一次误插入**：把 `dual_run=dual_run` 加到 `services/auto_pipeline` 的
`pp_postprocess(...)` 调用上（该调用与 `process_image(...)` 的参数列表高度相似，
且都含 `real_backend=real_backend,`），导致数组接口一开双跑就抛
`TypeError: postprocess() got an unexpected keyword argument 'dual_run'`。
**新写的端到端测试当场抓住**——这正是「测试要覆盖真实调用链」的价值：仅做签名
断言（`inspect.signature`）无法发现此类误插。

**第二次接线事故（被既有测试抓住）**：同一区域的参数列表高度相似，编辑时还**误删了
`process_image` 内 `pp_postprocess(...)` 的 `real_backend=real_backend,`**——服务路径
（视频 / CLI / 文件）因此丢失后端实例，改用配置里的 ESRGAN，阶段 33 的
`test_process_video_real_backend_instance_reused`（「传入实例应被复用 6 次」）当场断言
`calls == 0` 失败。**教训**：在参数列表雷同的调用点附近做批量文本替换，必须逐处核对
差异；既有回归测试是这类「静默能力降级」的唯一防线（若无该测试，视频超分只是悄悄换了
后端，功能表面正常）。

**测试自身的两个坑（已记录）**：
① 用伪 PNG 字节（`b"\x89PNG\r\n\x1a\n"`）做转发测试 → 解码即失败、假引擎从未被调用，
断言恒假；改用真实编码字节。
② 端到端用「平坦色块」当水印 → 检测器按设计判「未检测到水印区域」；且本机
`config.user_overrides.json` 已开真实超分，输出尺寸翻倍 → 改用合成用例并显式
`real_superres=False, superres=False`（与阶段 60 记录的同族坑一致）。

## 4. 测试（`tests/test_api_layer_sync.py`，34 例）

- 能力快照：16 字段存在性（参数化）+ 与 config 同源 + 双闸合取语义；
- API 层：4 个函数签名含 `dual_run`（参数化）/ PDF·视频不含（边界锁定）/
  `remove_watermark` 与 `remove_watermark_bytes` 的实际转发（monkeypatch 捕获）/
  `batch_remove` 转发 / 数组接口端到端可跑通；
- REST 参数白名单：`dual_run` 透传 / 布尔字符串归一 / 增强键全量透传 /
  未知键丢弃 / 空 body；
- 响应回显：未传时回显进程默认、显式传入时回显显式值。

## 5. 剩余边界

- 视频（逐帧）不暴露 `dual_run`（成本翻倍，走进程配置）；
- ICC「保留」是自动行为，无参数（上传-下载字节接口不带 ICC，因 `remove_watermark_bytes`
  经 `cv2.imdecode` 解码，ICC 在字节接口上天然不可得——需 ICC 请走文件接口）；
- 能力快照字段随新阶段增长，未做版本号语义（`app_version` 仍为应用版本）。

## 6. 交付清单

- `api.py`：4 个函数加 `dual_run`、`get_capabilities` 扩 16 字段；
- `services/auto_pipeline.py`：`process_file` 加 `dual_run` 并透传；
- `server.py`：参数白名单扩至 25 键 + 布尔归一 + 响应回显 `dual_run`；
- `tests/test_api_layer_sync.py`（34 例）。
