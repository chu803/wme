# 阶段 314 · 产品入口输出编码加固 + 非 ASCII 路径告警（缺陷修复）

> 起因：环境重建后的**功能实测审计**（逐条跑 API/CLI/REST，13 项）暴露两处**产品自身**缺陷。
> 两处都在进入产品前被当成「环境问题」排查过一轮，实测证明是产品该负责的健壮性缺口。

---

## 一、为什么要做（发现路径）

按「不读文档、逐条真跑」的方式审计产品能力，13 项中 11 项一次通过；把 2 个「失败」逐个复核后，
发现是**我的调用姿势**问题（`evaluate` 默认 False 返回 None；批量漏传 `allow_auto_erase`），
产品行为正确。但审计过程中撞到两个**真正的产品缺陷**，且都能用最小用例复现。

## 二、缺陷一：三个入口在 GBK 管道下崩溃（输出内容决定退出码）

**病症**：`python cli.py -i ... > 日志.txt`（或任何管道/重定向）在**任务已经完成之后**崩溃：

```
UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'
```

退出码变成 1 ⇒ 「成功」被读成「失败」；且日志最后一段（汇总）丢失。

**最小复现**：

```bash
python -c "print('✅ 完成')" > out.txt 2>&1; echo $?   # EXIT=1
```

**根因**：Windows 上 stdout 被重定向时按 ANSI 代码页（简体中文 = GBK）编码，而
`main.py`（4 处）、`cli.py`（11 处）、`server.py`（10 处）都会 print `✅/❌/⚠️/📄/🎬`。

**为什么之前没被发现**：阶段 146 修了 3 个门禁脚本，阶段 170 抽成共用件并用元守卫
机械覆盖了 `scripts/*.py`——但**元守卫的扫描范围就是 `scripts/`**，产品三个入口
天然在射程之外。门禁越做越全，反而给出了「编码问题已全族修完」的错觉。

**修复**：
1. 把实现从 `scripts/console_encoding.py` **迁到 `utils/console_encoding.py`**。
   理由：`pyproject.toml` 的 `packages.find` 刻意不含 `scripts/`（不随分发包发布），
   入口若从 scripts 导入，`pip install` 后必 `ImportError`。
   `scripts/console_encoding.py` 保留为一行转出，30 个门禁脚本的导入路径不变。
2. `main.main()` / `cli.main()` / `server.py __main__` 的**第一条语句**调用
   `force_utf8_stdio()`（`errors="replace"` 兜底，绝不因编码中断判定）。

## 三、缺陷二：非 ASCII 安装路径 ⇒ 模型加载报「文件不存在」

**病症**：项目放在含中文的路径下（本机实测：`D:\qoder\归档整理\01-我的项目-...\`），
`import torch` 正常、CUDA 正常，但加载权重时报：

```
OSError: [WinError 127] ... c10_cuda.dll ... （或） open file failed ... big-lama.pt
```

而 `models/big-lama.pt` **明明存在**（205,669,692 字节）。一次拖垮 **46 个用例**，
失败原因散布在 report/export/history 各模块，极易误判为「项目坏了」。

**根因**：Windows 上 libtorch 用 **ANSI 代码页**打开文件，含中文的路径在非 UTF-8 locale
下无法解析。**不是文件缺失，是路径编码**。

**为什么之前没被发现**：`utils/path_helper.py` 的文件头早已写着「中文路径：很多库直接
open 会报文件不存在」，但那只覆盖了 OpenCV 的读图路径（用「读字节 → imdecode」绕过）；
**模型加载走的是 torch 自己的文件打开**，从未纳管。

**修复**：
1. `utils/path_helper.py` 新增 `non_ascii_path_warning(path)`（纯函数，可单测）：
   含非 ASCII ⇒ 返回面向用户的告警（点出 WinError 127 的真实症状 + 处置建议）。
2. `main.run_environment_check()` 增开「2.2b 安装路径编码」一项：
   非 ASCII 打 `[WARN]`（不阻断启动——有些用户就是要在中文目录跑，只是要让他知道风险）；
   纯 ASCII 打 `[OK] 安装路径为纯 ASCII`。
3. `core/model_manager.py` 的 `ModelLoadError` 在路径含非 ASCII 时**追加**该提示——
   把「长得像文件缺失」的失败变成能直接照做的排查指引。

## 四、刻意**不**改的两项（附理由）

| 现象 | 为什么不算缺陷 |
|---|---|
| `batch_remove` 未传 `allow_auto_erase` 时整批返回 `status="failed"` 而不是抛异常 | **有意设计**：阶段 233 的裁决是「单张失败不中断整批」，并有守卫 `test_batch_remove_reports_rejection_as_failed_not_silent` 锁着「如实报 failed 且原因指向确认门」。改成 fail-fast 会破坏该契约。审计时的「失败」是我漏传参数。 |
| `perf_probe` P95 1.7s 超护栏 | **环境问题**，非代码：纯 numpy FFT 基准尺在检测负载后从 0.092s 变 0.197s（2.1×），GPU 压测时钟/功耗/显存均稳定。详见 `2026-10-22.md`。未重锚性能基线。 |

## 五、守卫测试（`tests/test_entry_encoding_and_path_guard.py`，13 例）

| 用例 | 钉住的行为 |
|---|---|
| `test_force_utf8_stdio_switches_gbk_stream` | 把 gbk 流换进 stdout，加固后编码 = utf-8 且能 print `✅` |
| `test_force_utf8_stdio_is_idempotent` | 重复调用不抛（三个入口可能各调一次） |
| `test_entry_point_hardens_encoding[main/cli/server]` ×3 | 三个入口都必须**从 utils 导入**并**实际调用** |
| `test_cli_survives_gbk_redirect` | **行为级反例**：`PYTHONIOENCODING=gbk` 下跑 `cli.py --list-models`，断言 EXIT=0 且 stderr 无 `UnicodeEncodeError`。修复前此例会 EXIT=1 |
| `test_non_ascii_path_warning_ascii_is_silent` | 纯 ASCII 路径不得误报 |
| `test_non_ascii_path_warning_flags_cjk` ×3 | 中文路径必须报，且点出 `WinError 127` 与「纯英文路径」处置 |
| `test_environment_check_uses_path_guard` | 启动自检必须检查 `PROJECT_ROOT` 编码 |
| `test_model_load_error_includes_path_hint` | ModelLoadError 必须附带路径提示 |
| `test_warning_text_is_printable_under_utf8` | 告警文本自身不得再引入编码问题 |

同时修了 `tests/test_real_gate.py::test_main_reconfigures_streams_to_utf8`：
它原来按**文件路径**读 `scripts/console_encoding.py` 断言实现，共用件搬家后会误红。
改为 `inspect.getsource(force_utf8_stdio)` —— 按**函数对象**断言，实现搬家不再影响守卫
（同族教训：阶段 313 的 Docker 反例因硬编码常量而静默空转）。

## 六、验证

- 新增守卫 **13 passed**；编码相关三文件 **53 passed**
- 首轮 `run_all_checks.py`：3187 passed / 2 failed，两个失败复核后：
  - `test_real_gate::test_main_reconfigures_streams_to_utf8` —— 真回归（守卫钉了旧文件路径），**已修**
  - `test_cpu_load_probe::test_process_and_system_estimates_are_same_order` —— 隔离跑通过，
    全量时受本机 CPU 降频影响抖动（该用例比较两路 CPU 负载估计的数量级）
- **修复后复跑全量：`✅ 全部通过（退出码 0）`**，pytest / undef_scan / docker_static 三步全绿

**效果实拍**（模拟中文路径）：

```
[WARN] 安装路径包含非 ASCII 字符「工具」：D:\工具\watermark-workbench
  Windows 上 PyTorch 用 ANSI 编码打开文件，含中文/日文等字符的路径会让模型加载失败
  （WinError 127，报「文件不存在」，但文件其实在）。
  建议把项目移到纯英文路径（例如 D:\watermark-workbench）后重试。
```

## 七、诚实边界

- 路径告警**只警告不阻断**：中文目录下用 OpenCV 引擎仍可正常工作（修复引擎 auto 默认偏好
  OpenCV，不加载 LaMa 就不会触发该问题）。强行拒绝启动会误伤这类用户。
- 只覆盖 `PROJECT_ROOT`（安装路径）。**输入图片/输出目录**含中文不受影响——那两条路径走
  `path_helper` 的字节读入与自行拼接，不经过 libtorch 的文件打开。
- `test_cli_survives_gbk_redirect` 依赖 `--list-models` 会打印 `✅/❌`；若将来该输出被改写，
  用例会失去反例效力（提醒：改那行输出时需同步本用例）。

## 八、产物

- `utils/console_encoding.py`（新，唯一实现）
- `scripts/console_encoding.py`（改为转出，30 个脚本导入路径不变）
- `main.py`（`force_utf8_stdio()` + 2.2b 路径编码自检）
- `cli.py` / `server.py`（`force_utf8_stdio()` 前置）
- `utils/path_helper.py`（`non_ascii_path_warning`）
- `core/model_manager.py`（ModelLoadError 附带路径提示）
- `tests/test_entry_encoding_and_path_guard.py`（新，13 例）
- `tests/test_real_gate.py`（守卫改为按函数对象断言）
