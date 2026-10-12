# 安全策略 / SECURITY

> 本项目为**专有软件**（见 LICENSE）。本文件说明其安全 posture 与漏洞报告途径。

## 安全 posture（本地离线为默认）

- **默认全本地离线**：推理在本机完成，图片/PDF/视频**不上传云端**；无遥测（Gradle 遥测已关，见 Dockerfile GRADIO_ANALYTICS_ENABLED=False）。
- **网络默认关闭**：除用户主动下载模型权重（download_model.py）外，程序不发起外网请求。
- **数据落点**：运行产物写入 outputs/ cache/ temp/ logs/ exports/；运行期用户覆盖写 config.user_overrides.json（已被 .gitignore，严禁入库）。
- **模型权重**：big-lama.pt 本地加载，不入库、不入镜像（.dockerignore 排除 models/），经卷挂载或下载获得。
- **REST 服务**：零依赖内置服务（server.py，端口 7861）；当前面向本机/受信网络，**无鉴权与限流**——暴露到公网前须自行加固（路线图 P1）。

## 依赖与供应链

- 版本单一真值来源：requirements.txt / requirements-gpu.txt；torch 统一 2.9.1+cu128（RTX 50 系 Blackwell 下限）。
- scripts/docker_consistency_check.py 静态核对 Dockerfile/依赖一致性（解释器错配、CUDA 版本、体积），接入 CI。

## 合规边界

- 仅处理**用户自有权处理的 AI 生成图片可见水印**（「去除本人生成的 AI 图片水印」，《著作权法》53 条）。工具不鼓励、也不应用于去除他人版权内容的水印。

## 报告漏洞

- 专有项目，请通过著作权人提供的内部渠道报告，勿公开披露。报告请附：复现步骤、影响面、环境（OS/Python/torch/是否 GPU）。
