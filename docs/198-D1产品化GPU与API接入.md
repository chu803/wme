# 阶段 198 · D1 产品化路线 —— GPU 与 API 接入

> 用户于 2026-10-03 在「下一阶段」选择 **D1 产品化路线**（原清单定义：GPU + API + Docker，P95<1.5s）。
> 本阶段完成 D1 的「接入与硬化」部分；Docker（C5/197）此前已完成，本阶段把 GPU 自动选设备链路钉成契约、并把已有的 REST 服务真正接入启动入口与容器。

## 关键发现（避免重复造轮子）

启动前先 grep 现有实现，发现「API 产品化」的**骨架早已就绪**，并非从零缺失：

- `api.py` —— 完整程序化库（`remove_watermark` / `remove_watermark_bytes` / `_array` / `_pdf` / `_video` / `batch_remove` / `get_capabilities` / 模型管理），复用 `services.auto_pipeline`，GUI/CLI/库/REST 四入口行为统一。
- `server.py` —— **零依赖** REST 服务（仅用标准库 `http.server` + `ThreadingHTTPServer`），端点 `/api/health`、`/api/remove`、`/api/batch`、`/`，带 CORS、请求体 50MB 上限、自检测试；复用 `api.remove_watermark_bytes`。
- `config` 已有 `API_HOST=127.0.0.1` / `API_PORT=7861` / `API_ENABLED=True` / `API_DEFAULT_FMT=PNG`。
- `core/device_manager.py` 早已实现 `select_device("auto"/"cuda"/"cpu")` + 显存安全水位降级 + `use_fp16()` 仅 GPU 开启；`core/inpainter.py` 的 `inpaint` 已按设备加载模型。
- `requirements-gpu.txt` 已备好 CUDA 版 torch 覆盖清单。

**结论**：所谓「API 产品化」差的不是「造一个 API」，而是① GPU 分支从未被 CI 覆盖（死代码路径风险）、② REST 服务没进启动入口与容器、③ Docker GPU 配方仍是注释、④ P95 目标需真实 GPU 实测。本阶段只做前两点可验证项。

## D1-Stage 1 · GPU 设备选择契约测试 + 推理张量设备跟随

- **新增 `tests/test_device_manager_gpu.py`（6 例）**：用 monkeypatch 把 `torch.cuda` 伪装成「8GB 显卡」，对 auto/cuda/cpu 三模式、显存安全水位降级、半精度门控逐一断言。覆盖此前**从未被 CI 验证**的 CUDA 分支。
- **硬化 `core/inpainter.py::_run_lama_tile`**：原代码写死 `device_str="cpu"` 再 `.to(dev)`，GPU 上每张 tile 多一次 host→device 拷贝。改为由 `next(model.parameters()).device` 推导 `device_str`（`cuda:0` / `cpu`），直接在模型设备构造张量——直接服务 **P95<1.5s** 时延目标，CPU 行为完全不变。
- **验证**：独立脚本伪造 CUDA 跑通 **9/9**；pytest 6 例全绿；`core/inpainter.py` 自检（OpenCV 路径）通过无回归。

## D1-Stage 2 · REST 服务接入启动入口与容器

- **`main.py` 新增 `--api` / `--api-host` / `--api-port`**：`--api` 时延迟导入 `server.start_server` 并启动 REST 服务（不启动 Gradio）；host/port 缺省交 `config.API_*` 兜底。原 Gradio 启动行为默认不变。
- **`Dockerfile` 暴露 7861**：`EXPOSE 7860`（Gradio）+ `EXPOSE 7861`（REST）；GPU 配方注释补「`python main.py --api` 仅起 REST」的运行示例。
- **契约测试**：`tests/test_main_api_entry.py`（3 例，验证 `--api` 解析与默认值） + `tests/test_dockerfile_contract.py` 增 `test_expose_api_port`（断言 EXPOSE 7861）。
- **验证**：pytest 聚焦 18 例（6+3+9）全绿；`main.parse_args()` 独立验证 `--api --api-host 0.0.0.0 --api-port 9000` 解析正确。

## D1-Stage 3 · Docker GPU 配方落地（Dockerfile.gpu）

- **新增 `Dockerfile.gpu`**：基于 `nvidia/cuda:12.1.1-runtime-ubuntu22.04`（自带 CUDA 运行库），多阶段构建；torch/torchvision 从 PyTorch **cu121** 源安装（CUDA 版，覆盖默认 CPU 版）——这是 GPU 镜像的「灵魂」：`DEVICE_MODE=auto` 检测到 CUDA 才能启用显卡。沿用默认 Dockerfile 的全部契约：多阶段、`/install` 前缀、非 root uid 10001、`WME_` 前缀 ENV、`HEALTHCHECK`、EXPOSE 7860+7861、`ENTRYPOINT main.py`。
- **契约测试 `tests/test_dockerfile_gpu_contract.py`（9 例）**：断言 ① 文件存在 ② 基础镜像 nvidia/cuda ③ torch 来自 cu121 源 ④ ENV 带 `WME_` 前缀（禁裸 `SERVER_HOST`）⑤ EXPOSE 7860+7861 ⑥ 非 root ⑦ ENTRYPOINT main.py ⑧ 有 HEALTHCHECK ⑨ 依赖来自 requirements.txt。
- **验证**：pytest **9 例全绿**。
- **用法**：`docker build -f Dockerfile.gpu -t watermark-eraser-gpu .` → `docker run --gpus all -p 7860:7860 -p 7861:7861 -e WME_SERVER_HOST=0.0.0.0 watermark-eraser-gpu`（仅 REST 时命令覆盖为 `python main.py --api`）。前置：宿主机装 NVIDIA 驱动 + nvidia-container-toolkit。

## D1-Stage 4 · REST 细粒度端点（/api/detect + /api/inpaint）

- **动机**：`/api/remove` 把「检测 + 修复」一步打包，但调用方无法先看掩码、也无法用自己的掩码（外部 OCR / 分割模型 / 人工圈定）。补两个「粗粒度」端点，组成两段式链路：**先 detect 预览掩码 → 再 inpaint 用该掩码修复**。
- **`api.py` 新增两个薄封装（与四入口共用同一核心，同源 LESSONS 184）**：
  - `detect_watermark_bytes(data, *, preset=None, auto_preset=True, sensitivity=None) -> (mask_png_bytes, stats_dict)`：解码字节 → `core.detector.detect_watermark_mask` → 返回单通道灰度 PNG 掩码 + 统计（mask_pixels / coverage_ratio / region_count / by_kind / used_ocr / regions 包围盒列表）。
  - `inpaint_watermark_bytes(data, mask_data, *, fmt, quality, strength, dilate, feather, threshold, denoise, engine, dual_run) -> bytes`：解码原图 + 掩码（4 通道取 alpha、3 通道转灰度、否则用单通道）→ `core.inpainter.inpaint` → 结果字节；跳过自动检测，直接用调用方掩码。
- **`server.py` 接入**：新增 `_collect_inpaint_params` + `_detect_one_b64` + `_inpaint_one_b64`；`do_POST` 加 `/api/detect`、`/api/inpaint` 路由（含 image/mask 字段校验）；顶部端点契约注释、`GET /` 用法页（④⑤ 两节）、`start_server` 启动横幅同步更新。
- **契约测试 `tests/test_server_endpoints.py`（5 例）**：随机端口起 `_Handler`，真 HTTP 跑 `/api/health`·`/api/remove`·`/api/detect`·`/api/inpaint`·`/api/batch`，断言掩码尺寸与原图一致、修复结果可解码、批量全成功。
- **自检覆盖（LESSONS 208）**：`api.py` 自检扩 用例9（detect）/ 用例10（inpaint）；`server.py` 自检扩 用例4（detect 命中 6263px/5 区域）/ 用例5（inpaint）；`test_legacy_selfcheck` 的 undef_scan 守卫发现并修复了一处 `Dict` 未导入（`api.py` 改用内置小写 `dict`）。
- **验证**：pytest **5/5** 通过；`api.py` 自检通过；`server.py` 自检 5/5 通过；`test_legacy_selfcheck`（含 undef_scan + selfcheck 覆盖）**72/72** 通过。

## D1-Stage 5 · perf_probe 全链路计时（--stage full）与真机验证口径

- **动机（诚实边界）**：原 `perf_probe` 只测**检测阶段**（CPU 绑定的 OpenCV），**永不触及 GPU**；而 D1 的 GPU 收益在**修复(inpaint/LaMa)** 阶段。路线图目标「P95<1.5s」是**全链路**生产表现，原脚本**无法验证**该目标——只会测出「检测」基线。
- **扩展**：`scripts/perf_probe.py` 增 `--stage {detect,full}`（默认 detect，向后兼容）：
  - `detect`：仅检测，与原口径一致（CPU，不涉 GPU），用于「检测器本身」回归。
  - `full`：经 `api.remove_watermark_bytes` 跑**检测+修复全链路**，在 `WME_DEVICE_MODE=auto` 下吃到 GPU/LaMa 收益；默认引擎 `lama`（用 `--engine` 可改）；单图无检出有效掩码时跳过该图，全无检出则给出明确报错（提示放入含水印真实图）。
  - 基线可比性按 `stage` 字段区分：`detect` 基线与 `full` 基线**互不可比**（判跳过），避免拿检测基线判全链路。
- **契约测试**：`tests/test_perf_probe.py` 增 `test_baseline_comparable_rejects_detect_vs_full` + `test_measure_full_stage_wires_api_and_records_stage`（stub 验证 full 走 `api.remove_watermark_bytes` 且默认引擎 lama）。
- **真机验证口径（你在 RTX 5060 上跑）**：
  1. 装 CUDA 版 torch：`pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121`（或 `requirements-gpu.txt`）；验证 `python -c "import torch;print(torch.cuda.is_available(),torch.cuda.get_device_name(0))"` → `True` + `RTX 5060`。
  2. 放若干张**含水印的真实 AI 图**到 `benchmark_real/images/`（或 `--images-dir <dir>`）。
  3. 写全链路基线：`python scripts/perf_probe.py --stage full --update`（含向下刷新护栏）。
  4. 判定目标：`python scripts/perf_probe.py --stage full --enforce-target --target 1.5`。
     - 退出码 **0** = 通过（P95 ≤ 基线×(1+30%) 且 ≤ 1.5s）/ **1** = 未通过 / **2** = 跳过（无基线或 stage/source 不可比）。
     - 仅 `--stage full` 能体现 GPU 加速；`detect` 阶段在 GPU 机器上数字不会变（CPU 工作）。
- **验证（沙箱 CPU 冒烟）**：`--stage full --engine opencv --images-dir <含水印图>` 跑通，打印 `min=0.390s` 且因 stage 与 detect 基线不可比而正确判「跳过（退出码 2）」；stub 校验 full 默认走 lama 引擎。
- **诚实边界**：pytest 因沙箱删除护栏被拦截未能在沙箱跑（见上）；已用独立脚本 + 真实管线冒烟验证等价逻辑，待你机器上跑真实 GPU 数据后我接管判定。

## D1-Stage 6 · 性能基线的硬件一致性守卫（设备指纹）

- **动机（诚实边界）**：D1-Stage 5 让 `--stage full` 吃到 GPU/LaMa 收益后，出现一个**比对陷阱**：原 `baseline_comparable` 只校验 `source/stage/repeat`，**不校验硬件**。于是用户用「沙箱 CPU 基线」去判「RTX 5060 全链路基线」时——要么把 CPU 数字误当 GPU 标杆，要么把 GPU 真实成绩与 CPU 数字比出虚假回退。两种结果都会让护栏「看起来通过、实则毫无约束力」（正是 LESSONS 209「基础设施一致性守卫」要堵的坑）。
- **修复**：`scripts/perf_probe.py`
  - 新增 `device_fingerprint()`（懒加载 torch，避免拖慢纯检测路径）：CUDA 可用 ⇒ `device="cuda"` + `gpu_name=torch.cuda.get_device_name(0)`；不可用 ⇒ `device="cpu"` + `gpu_name=None`（与「无 torch 环境」保持同一指纹，保证可读可比）。
  - `measure()` 在 `detect` 与 `full` 两分支的 `meta` 都写入 `device` + `gpu_name`；`main()` 的基线 payload 由 `dict(meta)` 自动带出，持久化到 `perf_baseline.json`。
  - `baseline_comparable` 的校验循环新增 `device` 与 `gpu_name`：**跨设备（cpu↔cuda）或跨 GPU 型号（如 RTX 4090 vs RTX 5060）一律判不可比 → 跳过（退出码 2）**，绝不假装通过。
- **契约测试 `tests/test_perf_probe.py`**：
  - 更新 `_base`/`_meta` 助手带 `device`/`gpu_name` 字段。
  - 新增 `test_baseline_comparable_rejects_device_mismatch`：断言 ① cpu 基线 vs cuda 本次 ⇒ 不可比（device）② 同 cpu/gpu_name 均为空 ⇒ 可比 ③ 不同 GPU 型号 ⇒ 不可比（gpu_name）。
  - 新增 `test_measure_records_device_fingerprint`（monkeypatch `device_fingerprint` 返回 cuda）：断言 `measure(stage=full)` 的 `meta` 确实带 `device=cuda` + `gpu_name`。
- **验证（沙箱）**：独立脚本 + 直接调用测试函数（pytest 因沙箱删除护栏被拦截，沿用 D1-Stage 5 的诚实边界做法）跑通全部 perf_probe 守卫：设备指纹沙箱返回 `cpu/None`；cpu↔cuda、不同 gpu_name 均正确判不可比；`measure` 两分支 meta 均带设备字段；一次真实 `full` 管线在 CPU 上也正确写入 `cpu/None`。
- **真机收口口径**：你在 RTX 5060 上写基线时，`perf_baseline.json` 会记 `device="cuda"` + 真实 `gpu_name`；日后任何 `detect`（cpu）或别张卡（gpu_name 不同）的本次测量都会被判「不可比 → 跳过」，强制你用同硬件重测/刷新，杜绝跨硬件假绿灯。

## 验证汇总

| 项 | 结果 |
|---|---|
| `tests/test_device_manager_gpu.py` | 6 例 pytest 绿 |
| 独立脚本伪造 CUDA | 9/9 通过 |
| `tests/test_main_api_entry.py` | 3 例 pytest 绿 |
| `tests/test_dockerfile_contract.py` | 9 例 pytest 绿（含新增 7861） |
| `core/inpainter.py` 自检 | 通过，无回归 |
| `main --api` 解析 | 正确 |
| `tests/test_server_endpoints.py` | 5 例 pytest 绿（含 /api/detect + /api/inpaint） |
| `api.py` 自检 | 通过（含 用例9 detect / 用例10 inpaint） |
| `server.py` 自检 | 5/5 通过（含 detect 命中 6263px/5 区域 + inpaint） |
| `test_legacy_selfcheck`（undef_scan+覆盖） | 72/72 通过 |
| `tests/test_perf_probe.py`（D1-Stage 6 + 既有） | 设备指纹守卫 + 全链路守卫全绿（独立脚本/直接调用验证；沙箱 pytest 受删除护栏拦截） |

## 剩余 D1 项（需用户真机 / 决策）

1. **用户真机启用 GPU**：在本机装 CUDA 版 torch（`requirements-gpu.txt` 或 `pip install torch torchvision --index-url .../cu121`），设 `WME_DEVICE_MODE=auto` 即自动启用；当前沙箱为 `torch 2.9.1+cpu` 且 `torch.cuda.is_available()=False`，故 GPU 实测须在你 RTX 5060 上做。
2. ~~**Docker GPU 配方落地**~~ ✅ **已完成（D1-Stage 3）**：新增 `Dockerfile.gpu`（nvidia/cuda:12.1.1-runtime + cu121 torch，沿用全部契约）+ `tests/test_dockerfile_gpu_contract.py`（9 例契约，pytest 全绿）。
3. **P95<1.5s 实测**：`scripts/perf_probe.py` 当前 CPU 基线 P50 1.427s / P95 3.209s；GPU 目标须在真机重跑 perf_probe 校验是否达标（当前沙箱无法测）。D1-Stage 6 已保证「跨硬件基线不可比」——你 RTX 5060 的基线会和沙箱 CPU 基线互相判跳过，必须同硬件重测。
4. ~~**可选细化端点**~~ ✅ **已完成（D1-Stage 4）**：`server.py` 已增 `/api/detect`、`/api/inpaint` 粗粒度端点（复用 `api.detect_watermark_bytes` + `api.inpaint_watermark_bytes`），`tests/test_server_endpoints.py` 5 例契约守卫。
5. ~~**性能基线硬件一致性守卫**~~ ✅ **已完成（D1-Stage 6）**：`perf_probe` 新增 `device_fingerprint()` + `baseline_comparable` 校验 `device`/`gpu_name`，跨硬件（cpu↔cuda）或跨 GPU 型号基线一律判不可比→跳过；`tests/test_perf_probe.py` 增 2 例守卫。纯代码类清单项已全部收口，余下仅剩真机实测（#1/#3）。

## 教训 → LESSONS 210

探查既有实现再动手：D1 启动前若直接「新建 FastAPI 服务」会重复造轮子——`server.py` 已是零依赖 REST 服务且符合「全本地离线、不引新依赖」基调。重大功能前务必先 `grep`/读现有模块，确认缺口是「硬化/接入」还是「从零构建」。
