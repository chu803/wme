# 阶段 50 · 环境攻坚与潜伏 Bug 修复设计文档

> 关联工作：2026-08-30 的「LaMa 真实跑通」与「真实超分首次执行」环境攻坚。
> 本文把当时散落在提交里的 3 个潜伏 Bug 修复 + 网络策略沉淀为正式设计文档，
> 便于后续维护者理解与复现。

---

## 1. 背景与动机

项目前 48 个阶段里，**核心擦除链路（detector + inpainter + mask）从未被真实验证过**——
所有测试只验证「不报错、流程通」，LaMa 深度学习引擎与真实超分（Real-ESRGAN）一直停留在
「torch 缺失 → 自动降级 OpenCV」的假执行状态。这导致三个潜伏 Bug 长期潜伏、无人发现：

- 一旦装上 torch + 权重，LaMa 加载即抛 `AttributeError`，根本跑不起来；
- 真实超分在 torch ≥ 2.9 下因负步长视图直接崩溃；
- `_RRDBMini` 前向用了一个 `__init__` 从未定义的属性，真实超分权重加载即崩。

2026-08-30 通过阿里云镜像装通 `torch 2.9.1+cpu` + 断点续传拉齐 `big-lama.pt`（205MB），
首次真实执行了两条引擎链路，三个 Bug 才暴露并被修复。本文记录其根因与修复。

---

## 2. 网络环境策略（复现前提）

实测各官方源在本环境均不可达或极慢：

| 源 | 状态 |
|---|---|
| 官方 `download.pytorch.org` | 卡死（21 分钟无进展） |
| GitHub 直连 | 约 30KB/s（仅 `download_model.py` 断点续传可用） |
| HuggingFace | 不可达 |
| 清华镜像 | 极慢/不稳定 |
| **阿里云 `mirrors.aliyun.com/pytorch-wheels/cpu/`** | **约 160KB/s，唯一可用高速源** |

**torch 安装命令（已写入 README 快速开始）**：

```bash
# 1) 先装纯 Python 依赖（阿里云镜像对 sympy/networkx 偶发超时，先手动装）
pip install sympy networkx
# 2) 用 wheel 直链 + --no-deps 装 torch（双 index 写法会解析失败，必须用直链）
pip install "https://mirrors.aliyun.com/pytorch-wheels/cpu/torch-2.9.1%2Bcpu-cp313-cp313-win_amd64.whl" --no-deps
# 3) 模型权重：走 download_model.py 的 GitHub 断点续传（约 1h17m）
python download_model.py
```

---

## 3. 三个潜伏 Bug

### 3.1 `_DeviceHandle` 缺 `is_gpu` 属性（LaMa 加载即崩）

- **现象**：装上 torch 后，`detect_use_lama` 走到模型加载即抛
  `AttributeError: '_DeviceHandle' object has no attribute 'is_gpu'`。
- **根因**：LaMa 包装层通过 `_DeviceHandle.is_gpu` 判断部署设备，但该类只持有
  `DeviceInfo`，**从未把 `is_gpu` 暴露为自身 property**，于是属性查找失败。
- **修复**（`core/device_manager.py`，约第 82 行）：补 property 委托底层。

  ```python
  @property
  def is_gpu(self) -> bool:
      """委托给底层 DeviceInfo.is_gpu。阶段 48 修复：此前缺此属性，
      导致 LaMa 模型在 torch 可用时加载即抛 AttributeError。"""
      return self.info.is_gpu
  ```
- **验证**：修复后 LaMa 在纯色底图上跑通，PSNR 65.21dB，未掩码区 100% 保留。

### 3.2 真实超分负步长视图（torch ≥ 2.9 崩溃）

- **现象**：`core/superres.py` 真实超分路径抛
  `RuntimeError: negative strides not supported`（或 `from_numpy` 相关报错）。
- **根因**：`arr[:, :, ::-1]` 是 **负步长（non-contiguous）视图**；torch ≥ 2.9 的
  `torch.from_numpy` 不再接受负步长输入，而旧版本静默拷贝、不报错。
- **修复**（约第 190 行）：强制连续化后再转 tensor。

  ```python
  # 注意：arr[:, :, ::-1] 是负步长视图，torch>=2.9 的 from_numpy 不支持
  rgb = np.ascontiguousarray(arr[:, :, ::-1])
  t = torch.from_numpy(rgb).permute(2, 0, 1).float().div(255.0).unsqueeze(0)
  ```
- **验证**：修复后真实超分 64→256（4×）在本机首次成功执行。

### 3.3 `_RRDBMini` 前向使用未定义属性 `self.relu`

- **现象**：加载真实超分权重走到 `_RRDBMini.forward` 抛
  `AttributeError: '_RRDBMini' object has no attribute 'relu'`。
- **根因**：`forward` 中调用 `self.relu(...)`，但 `__init__` 里**从未定义 `self.relu`**，
  属于典型「写 forward 时顺手用、漏写初始化」的笔误。
- **修复**（约第 149 行）：补 `nn.ReLU(True)`。

  ```python
  # 阶段 48 修复：forward 使用 self.relu，但此前 __init__ 从未定义它。
  self.relu = nn.ReLU(True)
  ```
- **验证**：同 3.2，真实超分全链路跑通。

---

## 4. 由此沉淀的测试资产

- **环境前提类测试（6 例）**：原本断言「资源缺失时优雅降级」，装上 torch/权重后行为反转，
  改为 `@pytest.mark.skipif(资源存在)` 跳过——资源缺失时仍验证降级路径，存在时交给真实链路。
  涉及 `tests/test_api.py` / `test_cli.py` / `test_superres.py` / `test_video_superres.py` /
  `test_postprocess.py`。
- **`test_perf2.py` flaky 修复**：`os.utime(p, None)` 在低精度文件系统上 mtime 可能与首次写入
  相同导致时序断言偶败；改为显式推后 10 秒。

---

## 5. 结论

三个 Bug 的共性是：**「降级路径永远走不到」的代码分支，在真实引擎接入前无法触发**。
这正是「核心链路从未被真实验证」的具体代价。阶段 50 的价值不在新增功能，而在
**把唯一能改变产品性质的能力（LaMa / 真实超分）从「假执行」变为「真执行」，并固化复现路径**。

后续若要纳入常规 CI，建议把 torch 装上后的「真实引擎冒烟」作为可选项（环境前提类），
而非阻塞项——避免 CI 机器因体积/网络拒绝 torch 时整轮失败。
