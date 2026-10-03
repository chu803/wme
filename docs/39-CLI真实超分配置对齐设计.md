# 阶段 39 · CLI 真实超分配置对齐设计

## 1. 背景与目标

阶段 35 在「设置页」暴露了真实超分三旋钮（总开关 / 放大倍数 / 后端），并把用户覆盖
持久化到 `config.user_overrides.json`（白名单仅 `REAL_SUPERRES_ENABLED` /
`REAL_SUPERRES_SCALE` / `REAL_SUPERRES_BACKEND`，环境变量 `WME_*` 优先）。

`config.py` 在**模块导入时**即调用 `_apply_user_overrides()`（第 1411 行），因此任何
import `config` 的入口（含 `cli.py`）都会自动继承设置页保存的默认值——CLI 其实**已经**
在读 `user_overrides.json`，只是有两个语义缺口：

1. **缺 `--real-backend` 开关**：CLI 有 `--real-superres` / `--real-scale`，但没有任何
   办法覆盖「真实超分后端」（`auto` / `esrgan` / `realesrgan`），只能沿用 config 默认值。
2. **`--real-superres` 是裸 `store_true`，不继承保存的默认值**：无论用户在设置页是否开启
   真·超分，CLI 默认都是 `False`，与界面行为（UI 复选框默认取 `config.REAL_SUPERRES_ENABLED`）
   不一致。
3. **参数未透传到 PDF / 视频路径**：图像路径 `_run_one` 已透传 `real_superres` / `real_scale`
   但漏了 `real_backend`；`_run_pdf` / `_run_video` 则**完全未透传**三者，导致命令行批量处理
   PDF / 视频时真实超分根本不生效。

**阶段 39 目标**：补齐 `--real-backend`，让三个真实超分参数默认继承 `user_overrides.json`
（与设置页一致），并把三者完整透传到图像 / PDF / 视频三条处理链路。

## 2. 改动清单

### 2.1 `cli.py` — 参数定义（后处理深化参数组）

| 参数 | 改动前 | 改动后 |
|------|--------|--------|
| `--real-superres` | `store_true`（默认 `False`） | `BooleanOptionalAction`，默认 `config.REAL_SUPERRES_ENABLED` |
| `--real-scale` | 默认 `config.REAL_SUPERRES_SCALE` | 不变（本就继承 config） |
| `--real-backend` | 不存在 | **新增** `choices=["auto","esrgan","realesrgan"]`，默认 `config.REAL_SUPERRES_BACKEND` |

语义效果：
- 不传任何开关 → 三个值取自「应用 `user_overrides.json` 之后」的 config 运行值（与设置页一致）。
- `--real-superres` / `--no-real-superres` 分别强制开 / 关。
- `--real-backend realesrgan` 可显式指定后端；非法取值被 argparse 拒绝（退出码 2）。

模块 docstring 顶部示例新增一行真·超分用法。

### 2.2 `cli.py` — 三处理器透传

- `_run_one`（图像）：在 `process_file(...)` 入参补 `real_backend=args.real_backend`
  （`real_superres` / `real_scale` 此前已有）。
- `_run_pdf`（PDF）：在 `process_pdf(...)` 入参补 `real_superres` / `real_scale` / `real_backend`
  （此前三者全缺）。
- `_run_video`（视频）：在 `process_video(...)` 入参补 `real_superres` / `real_scale` / `real_backend`
  （此前三者全缺）。
- 运行摘要：开启真·超分时打印 `(真·超分：开启 | 后端=… | 倍数=…×)`，与界面状态一致。

下游 `services/auto_pipeline.process_file` / `video_service.process_video` /
`pdf_service.process_pdf` 早已接受 `real_backend: Optional[str]`（阶段 33/34 接入），
本次仅补齐 CLI 侧的透传，未改动服务层。

### 2.3 `tests/test_cli_real_superres.py`（新增，7 例，零 torch）

覆盖三层：
- **解析层**：`--help` 含 `--real-backend` 与 `--no-real-superres`；`--real-backend` 选项受约束；
  非法取值退出码 2；三参数默认值等于 `config` 运行值（验证继承 `user_overrides`）。
- **透传层**（monkeypatch 下游函数捕获入参，不真正处理文件）：
  - `_run_one` 透传 `real_backend`（此前缺失的关键点）；
  - `_run_pdf` / `_run_video` 透传三者（此前全缺）；
  - `--no-real-superres` 时 `real_superres=False` 正确透传。

### 2.4 `tests/test_perf2.py` — 修复一处既有 flaky 测试

`test_decode_cache_invalidation_on_overwrite` 假设 `cv2.imwrite` 覆盖同一路径后 `mtime_ns`
必然前进，使缓存键 `(abspath, mtime_ns, size)` 失效。当新内容尺寸相同、写入落在同一
mtime 精度内（沙箱 / NTFS 偶发），缓存键不变 → 偶发返回旧值 → 测试偶发失败。

修复：覆盖后显式 `os.utime(p, None)` 把 mtime 推后，确保键必然失效。该测试与阶段 39 功能
无关，属既有稳定性缺陷，修复后 3/3 稳定通过。

## 3. 验证

- `python cli.py --help` 正确列出 `--real-superres | --no-real-superres`、
  `--real-backend {auto,esrgan,realesrgan}`、`--real-scale`。
- 解析器默认值校验：本环境 `user_overrides.json` 为
  `REAL_SUPERRES_ENABLED=True` / `REAL_SUPERRES_SCALE=2` / `REAL_SUPERRES_BACKEND=esrgan`，
  CLI 三参数默认值与之完全一致，证明确实继承了保存的覆盖。
- 全量 pytest：**278 passed / 1 skipped / 0 failed**（torch 缺失跳过；较阶段 38 的 270
  新增 7 例 `test_cli_real_superres` 全绿，并顺带修复 1 例 flaky，无回归）。

## 4. 边界与说明

- `config.py` 导入即应用 `user_overrides`，故 CLI 无需单独读 JSON；环境变量 `WME_REAL_*`
  仍优先级最高（与界面一致）。
- `--real-backend` 仅决定后端选择；缺 torch / 权重时下游自动回退轻量超分（与界面同口径）。
- 未新增 `--real-model-path` / `--real-model-dir` 开关——二者有 config 默认值，
  透传 `real_backend` 后下游自动取 config 的 `REAL_SUPERRES_MODEL_PATH` / `MODEL_DIR`。
