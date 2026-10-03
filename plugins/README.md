# 插件目录（plugins/）

本目录用于存放「自定义修复引擎插件」。把符合规范的 `.py` 文件放进这里，程序启动时会自动发现并注册，随后即可在 **界面 / 命令行 / API** 的「修复引擎」里选用。

---

## 一、一个插件解决什么问题？

原生工作台内置 `lama` 与 `opencv` 两种引擎。如果你想接入**自己的 inpaint 模型**（例如 diffusion 模型、其它 TorchScript 模型、甚至远程推理 API），就写一个插件：

- 继承 `core.plugins.InpaintEnginePlugin`
- 实现 `inpaint(image, mask, *, device=None, progress=None) -> np.ndarray`
- 模块末尾 `register_engine(你的类)`

无需改动任何核心代码，引擎列表会自动多出一项。

---

## 二、最小插件模板

```python
# my_plugin.py  —— 放进 plugins/ 目录即可
from core.plugins import InpaintEnginePlugin, register_engine

class MyEngine(InpaintEnginePlugin):
    name = "my_engine"          # 唯一键（小写），命令行/API 用它选择
    display_name = "我的引擎"     # 界面显示名
    description = "一句话简介"
    requires_model = False       # True 时框架会先调一次 setup()

    def inpaint(self, image, mask, *, device=None, progress=None):
        # image: H×W×3 uint8 RGB 原图
        # mask : H×W uint8 0/255 二值掩码（已膨胀/阈值/羽化后）
        # 返回 : H×W×3 uint8 RGB，掩码区域已被填充，其余与原图一致
        return image  # ← 在这里调用你自己的模型

register_engine(MyEngine)
```

**接口约定（务必遵守）**：`inpaint` 只负责「填充掩码区域」。掩码后处理、降噪、力度融合由框架统一完成，插件无需关心。

---

## 三、接入你自己的模型

参考 `sample_model_plugin.py`：

1. 设置环境变量 `WME_CUSTOM_MODEL_PATH` 指向你的 TorchScript 模型文件；
2. 在 `_run_custom_model()` 中按你模型的输入/输出签名改写（示例给出与 LaMa 一致的 `(img, mask) -> [-1,1]` 调用框架）；
3. 把 `requires_model` 设为 `True`，框架会在首次修复前先调 `setup()` 加载权重。

如果模型不在本地（如远程 API），直接在 `inpaint()` 里发起 HTTP 请求并返回结果即可——接口不受限制。

---

## 四、加载规则

- 启动时扫描 `config.PLUGIN_DIRS`（默认 `["plugins"]`，相对项目根目录）。
- 文件名以 `_` 开头的文件（如 `__init__.py`）会被跳过。
- 单个插件加载失败只告警、不影响其它插件与程序启动；失败原因可经
  `core.plugins.get_plugin_report()` 或 `GET /health`（`plugins.failed`）查到。
- 通过 `PLUGIN_ENABLED`（环境变量 `WME_PLUGIN_ENABLED`，逗号分隔的文件名，不含 `.py`）可限制只加载指定插件。
- 插件源码**每次直接编译执行、不走 `.pyc` 缓存**：改完文件调用
  `core.plugins.reload_plugins()` 立即生效，无需重启进程。

### 4.1 引擎命名规则（阶段 77 起强制校验）

- `name` 必须匹配 `^[a-z][a-z0-9_]{0,31}$`：小写字母开头，仅含小写字母/数字/下划线，长度 1~32。
- 大写会被自动归一化为小写（`My_Engine` → `my_engine`）并告警；
  含**空格、点号、连字符、中文、数字开头、超长**的名字一律**拒绝注册**。
  - 含空格的名字在界面下拉里会表现为空选项；含点号的名字与 i18n 键路径分隔符冲突。

### 4.2 覆盖保护（阶段 77 起）

- **插件不能顶替已注册的同名引擎**，尤其是内建 `lama` / `opencv`——
  同名注册会被拒绝并告警（此前插件叫 `lama` 就能静默替换内建引擎，
  而引擎清单仍显示「LaMa 深度学习」，**界面显示与实际执行完全脱节**）。
- 同名插件文件（两个文件都注册 `dup_engine`）**先加载者胜出**，后者被拒绝并告警。
- 确需覆盖（例如自研 LaMa 加速版顶替内建）时显式开启逃生舱：
  `WME_PLUGIN_ALLOW_OVERRIDE=1`；开启后每次覆盖仍会逐条 WARNING 记录「谁覆盖了谁」。

---

## 五、在命令行查看可用引擎

```bash
python cli.py --list-engines
```

输出示例：

```
可用修复引擎：
  auto         → 自动选择 （内建）  由配置的引擎策略自动选择（阶段 55：默认 OpenCV，LaMa 走 opt-in）
  lama         → LaMa 深度学习 （内建）  效果最好，能凭空补全纹理内容（推荐）
  opencv       → OpenCV 传统算法 （内建）  速度极快、无需模型，适合细小水印
  demo_model   → 示例·自定义模型 （插件）  ...  ← sample_model_plugin.py
```

插件引擎会附上**来源文件**，便于回答「这个 `my_engine` 到底是谁提供的」。

随后即可用 `--engine demo_model` 选择你的插件：

```bash
python cli.py -i 图.png -o 结果.png --engine demo_model
```
