# 阶段 362 · CI 第一次红在「用例级」：Windows 假设 + 诊断自己看不见剩下的失败

> 日期 2026-10-31 · 性质：**OS 便携性修复 + 诊断可见性收口**

## 一、这次红的位置本身就是一条信息

推送 `16c0ac7` 后 Actions run #39：**5m36s 才红**（此前四次的红都发生在 1~2 分钟的装依赖/收集段）。
自动诊断 issue #30 的步骤状态表：

```
checkout: success   python: success   system: success   deps: success
probe_collect: success   entry: failure
```

⇒ 347 / 351 / 358 / 360 四条都真的修好了；剩下的是**我一直声明"未覆盖"的那一维：OS**
（本机 Windows，runner Ubuntu）。而且入口开头打印了
`判定 : [ci_equivalent]`——阶段 361 的环境画像第一次在 runner 上自证了"这次量的就是 CI 环境"。

## 二、三条失败，全是平台假设，没有一条是产品逻辑回归

| 用例 | 症状 | 根因 | 修法 |
|---|---|---|---|
| `test_preset_recommender.py::test_process_file_auto_preset_threading` | `FileNotFoundError: 'powershell'` | 清理临时文件时调 `powershell Remove-Item`——**本机沙箱的 safe-delete 闩锁被写进了产品测试的可执行文件依赖** | 文件放 pytest 的 `tmp_path`，用例**自己不做删除**（两个 OS 都不需要删除工具） |
| `test_real_gate.py::test_display_path_survives_cross_drive` | `../Z:\somewhere\baseline.json` != `Z:\somewhere\baseline.json` | POSIX 上 `os.path.relpath` **不为"另一个挂载点"抛 ValueError**，而是给出逃逸出 base 的 `../…` | `utils/path_helper.py::display_path` 按它**自己 docstring 的契约**（表达不了相对就退回绝对）把「逃逸」也算表达不了 ⇒ 两个 OS 同答案 |
| `test_repo_hygiene.py::test_root_dir_has_no_unexpected_files` | 根目录散落 `gate_output.txt` | 阶段 356 给入口步加了 `tee gate_output.txt`，写到了**仓库根** | 改 tee 到 `logs/entry.log`（目录不算散落文件），诊断脚本同步改读该路径；`publish.yml` 同改 |

`display_path` 为什么修产品而不是给测试加 `skipif`：
它的两个真实调用方（`scripts/quality_gate.py:623`、`scripts/real_gate.py:1236`）都是**打印给用户看的确认信息**，
Linux 上印 `../Z:\…` 本来就是错的表现；`skipif` 只会把这个真缺陷永久藏进"CI 不测"的暗处。
消费者核查：两处 print + `test_path_helper_audit144.py`（只断"不抛异常"）⇒ 无依赖 `..` 形态的调用方。

## 三、诊断的洞：6 条失败只看得见 3 条

issue #30 写的是 `6 failed, 3360 passed`，但**只列出 3 条 FAILED**——
因为 `run_all_checks` 对失败步骤只回显子进程输出的**末尾 12 行**，短摘要被截断了。
剩下 3 条只能靠猜 ⇒ 这正是本仓库整条弧线反对的事（"说不清的失败不是可用的失败"，与 331/352 同族）。

修法（[scripts/run_all_checks.py:321](file:///D:/qoder/watermark-workbench/scripts/run_all_checks.py)）：失败时**先把短摘要里所有 `FAILED`/`ERROR` 行全部接出来**（上限 80 条，超出说明还有多少），
末尾 12 行仍保留作上下文；短摘要里没有失败行时**明说**"去看末尾输出"，不留空头清单。只改显示，不改判定。

守卫 `tests/test_run_all_checks.py`：
`test_failure_surfaces_the_whole_failed_list`（注入 20 条 FAILED ⇒ 逐条都要在输出里）+
在既有 `test_failure_prints_tail_output` 上补"没有失败行要明说"。

## 四、防复发：OS 便携性守卫

新守卫 `tests/test_os_portability362.py`（5 例）：
① AST 扫 `tests/test_*.py`，凡**真的会起进程**的调用（`subprocess.run/Popen/call/check_*`、`os.system/popen`）
   的第一参数落在 Windows 专属程序表（`powershell/cmd/where.exe/del/rd/reg/schtasks/pwsh`…）⇒ 红；
② **反例 + 反-误报**双测：注入 `powershell` 必须被抓；`re.search('CMD python3 -c …')`（Docker 守卫里的正则文本）
   必须不被抓——第一版判据不限定函数名，结果把这条正则**误报成命令调用**，
   差点让守卫自己去改一个不存在的问题（判据要按"它真会执行什么"写，同 #316/#337）；
③ `display_path` 的「逃逸 ⇒ 退回绝对」在**任何 OS** 上成立（不 `skipif`），比较时统一分隔符：
   Windows 上 pathlib 会把 `/a/b` 规范化成 `\a\b`，那是分隔符行为、不是本条要钉的点。

## 五、验证与诚实边界

- 本地复验（CI 等价环境 `D:\qoder\ci311venv`，3.11 + 只装 min/dev，跑与 CI 同一条命令）：
  **`rc=0`，`3377 passed / 36 skipped / 6 deselected`（13 分 44 秒）**，开头打印 `[ci_equivalent]`。
- ⚠ **同一条命令在本环境此前实测 6 分 47 秒，这次 13 分 44 秒**（+100%）。
  用例数只多了 6 条（本阶段新增守卫），**原因未查明**——本轮与其它验证进程时间重叠过，
  先记为待测现象，下次干净重跑再判；不把它解释成"必然无害"。
- **仍有 3 条失败未露面**（被截断的那 3 条）。本轮修的是**已经看得见**的 3 条；
  §三 的修法（回显完整失败清单）让下一次 CI 自己把剩下的说清楚，而不是靠猜。
- OS 维度的等价验证在本地做不到（没有 Linux）。可选补强：容器里跑 `python:3.11-slim` 复现（未做）。
