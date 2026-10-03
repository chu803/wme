# 阶段 197（C5）· Docker 容器化（多阶段 + HEALTHCHECK + 非 root）

> 关联清单项：C5（Docker HEALTHCHECK + 多阶段构建）。本文档为权威记录；容器行为以 `Dockerfile` 为准。

## 结论

- 把既有**单阶段、root、无健康检查**且**存在静默缺陷**的 `Dockerfile` 改造为：
  **多阶段构建**（builder 装依赖到 `/install` → runtime 只拷已装包）、**HEALTHCHECK**（轮询 Gradio 首页）、**非 root（uid 10001）**运行。
- 修掉一个**真实的静默缺陷**：旧文件设 `SERVER_HOST=0.0.0.0`，而 `config` 只认带前缀的 **`WME_SERVER_HOST`**（`config.ENV_PREFIX = "WME_"`）⇒ 容器会**退回默认 `127.0.0.1`，容器外访问不了**，且无任何报错。
- 新增契约守卫 `tests/test_dockerfile_contract.py`（8 例），把上述契约（多阶段 / HEALTHCHECK / `WME_` 前缀 / EXPOSE 7860 / 非 root / ENTRYPOINT main.py / 依赖单一真值来源）钉死。

## 背景与发现

优化清单 C5 要求「HEALTHCHECK + 多阶段构建」。审计既有 `Dockerfile` 时发现三类问题：

| # | 问题 | 后果 | 归因 |
|---|---|---|---|
| ① | `ENV SERVER_HOST=0.0.0.0`（无 `WME_` 前缀） | 容器退回 `127.0.0.1`，**外部访问不了**且无报错 | `config._env_str("SERVER_HOST", ...)` 实际读 `WME_SERVER_HOST`（`ENV_PREFIX="WME_"`） |
| ② | 无 `HEALTHCHECK` | 编排器（compose/k8s）无法判活、无法滚动升级 | 缺失 |
| ③ | 单阶段 + root 运行 | 镜像含编译工具链、体积偏大；root 权限过大 | 缺失多阶段与最小权限 |

> ①属「配置名存实亡」一类（同源经验：LESSONS 91「配置项名存实亡」）——写了一个看似生效、实则被忽略的环境变量。本阶段用守卫把它机械化拦住。

## 设计

### 多阶段构建

- **builder 阶段**：`python:3.11-slim` + `build-essential`；`pip install --prefix=/install -r requirements.txt`（依赖清单真值来源仍是 `requirements.txt`，**不复制第二份**，同源 LESSONS 187/195）。
- **runtime 阶段**：`python:3.11-slim` + 运行期系统库（`libgl1`/`libglib2.0-0`/`libsm6`/`libxext6`/`libxrender1`/`ffmpeg`）；`COPY --from=builder /install /usr/local` **只拷已装好的包**，不带编译器、wheel 缓存与构建中间产物。

### HEALTHCHECK

```
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('WME_SERVER_PORT','7860')+'/', timeout=4)"
```

- 用容器自带 `python` 探测 Gradio 首页，**免装 curl**；成功即退出码 0。
- `--start-period=120s` 给足模型加载时间，避免启动期被误判为 unhealthy。
- 探测端口读运行期环境变量 `WME_SERVER_PORT`（默认 7860），与 `EXPOSE` 一致。

### 非 root 与可写目录

- `useradd --create-home --uid 10001 appuser`；`USER appuser` 收尾。
- `COPY --chown=appuser:appuser . .` + `mkdir -p outputs cache temp logs exports models && chown -R appuser:appuser /app`，保证运行期（`config.ensure_directories()`）可写。
- ⚠ 卷挂载目录需对 uid 10001 可写；Windows Docker Desktop 的 bind mount 通常无感，Linux 上如遇权限问题用 `--user $(id -u)` 或先在宿主 `chown`。

### 环境变量（一律 `WME_` 前缀）

```
ENV WME_SERVER_HOST=0.0.0.0 WME_SERVER_PORT=7860 WME_SERVER_OPEN_BROWSER=False \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 GRADIO_ANALYTICS_ENABLED=False
```

## 验证

- `tests/test_dockerfile_contract.py`（**8 例**）：文件存在 / 多阶段（≥2 FROM 且命名阶段）/ HEALTHCHECK（含 7860）/ **`WME_` 前缀（并禁止裸 `SERVER_HOST=`）** / EXPOSE 7860 / 非 root / ENTRYPOINT 运行 main.py / 依赖来自 requirements.txt。
- 只读复现（离线跑守卫的核心断言，不启 docker、不跑 pytest）：`froms=2`、`named=True`、`healthcheck=True`、`bare SERVER_HOST/PORT/OPEN_BROWSER=False`、`expose=7860`、`user=appuser`、`entry=["python","main.py"]`、`req=True` —— **全绿**。

## 用法

```bash
# 构建
docker build -t watermark-eraser .

# 运行（注意 WME_ 前缀！挂载 outputs 与 models）
docker run -p 7860:7860 \
  -e WME_SERVER_HOST=0.0.0.0 \
  -v "$PWD/outputs:/app/outputs" \
  -v "$PWD/models:/app/models" \
  watermark-eraser

# 健康状态
docker inspect --format '{{.State.Health.Status}}' <container>
```

GPU：见 `Dockerfile` 文末「GPU 镜像参考配方」（换 `nvidia/cuda` 基础镜像 + CUDA 版 torch，运行加 `--gpus all`）。

## 诚实边界与待办

- **未实际执行 `docker build`**：本沙箱网络受限、亦无 docker daemon ⇒ 本阶段交付的是**经静态契约守卫验证**的 Dockerfile，未做真实镜像构建/启动验证。首次真实构建时请留意基础镜像拉取与 `requirements.txt` 的 wheel 可得性。
- **CI 是否纳入 `docker build`**：待决策。当前 CI（`.github/workflows/ci.yml` → `run_all_checks.py`）不构建镜像；若纳入需 CI 环境支持 docker 且可拉取依赖（网络/缓存策略待定），故**本阶段不改 CI**。
- **GPU 配方为注释参考、未验证**：CUDA 基础镜像与 torch CUDA 版本需在目标机实测。
- 与清单其余项的关系：C5 完成后，产品化路线 P2（GPU + API + Docker）的 **Docker 一环闭合**；GPU 性能与 API 对外仍属 D1。
