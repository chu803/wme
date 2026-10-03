# 136 · video_export 真值审计——自检临时目录泄漏与灰度帧证伪

> 日期：2026-09-26 ｜ 对象：`services/video_export.py`（未审榜榜首，荒漠度 5.4 = 118 代码行 ÷ 22 引用测试 / 2 文件）
> 方法：探针量化 → 分类（defect vs by design）→ 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 登记

## 一、探针量化（%TEMP%/s136_probe.py，项目内零残留）

| # | 候选 | 探针结果 | 判定 |
|---|---|---|---|
| D1 | `__main__` 自检 `mkdtemp(prefix="video_export_selftest_")` 从不清理 | 实证：子进程实跑 1 次 ⇒ `%TEMP%` 新增 1 目录 4 文件（2 视频 + 2 PNG），重复跑线性泄漏 | **defect** |
| D2 | `_extract_one:71` 灰度 2D 帧 `cvtColor(frame, BGR2RGB)` 崩溃 | **证伪**：OpenCV 5.0 实测 `cvtColor(2D, BGR2RGB)` **不抛异常**，自动复制为 (H,W,3) 且通道值正确（77→[77,77,77]）；返回值合 docstring 契约 | by design（撤销嫌疑） |
| D3 | `export_contact_sheet(cols=0/-3)` 除零 | 下游 `make_contact_sheet` 自带 `cols = max(1, int(cols))`（export_service:198），返回 (960,160,3) 正常 | by design |
| D4 | `idx` 越界（99 > 总帧数） | `max(0, min(idx, total-1))` 钳到末帧，正常返回 | by design |
| O1 | `export_service.make_compare_array` 直传 2D | 实证 `ValueError: could not broadcast (120,160)→(120,160,3)`——**无 `_to_rgb3` 防线**，与同文件 `make_contact_sheet`（有）不一致 | 登记不修（export_service@134 补漏；video_export 链路内被 cvtColor 兜底不可达） |
| O2 | `video_service.py:210` 灰度帧崩溃（Stage 135 登记未修） | **探针证伪**：同 D2，OpenCV 5.0 下 `cvtColor(2D,BGR2RGB)` 返回 3 通道不崩 | **撤销登记** |

## 二、归因（D1）

- 自检用 `tempfile.mkdtemp()` 建目录后**没有任何清理语句**，且产物不小（2 个 mp4 + 2 张 PNG）；跑一次泄一次，长期使用/%CI 环境下线性堆积。
- 自检本身是隔离的（mkdtemp 不碰项目目录）、rc=0、耗时 4.4s——修复清理后即满足子进程守卫登记条件（经验 144：隔离 + 非破坏）。

## 三、最小修复（services/video_export.py）

`__main__` 自检体包 `try/finally`，`finally: shutil.rmtree(tmp, ignore_errors=True)`；加注释说明阶段 136 修复泄漏。其余代码零改动。

## 四、验证

| 门禁 | 结果 |
|---|---|
| 守卫测试 `tests/test_video_export_audit136.py`（8 用例：越界钳制/灰值视频形状合规/缺文件 None/尺寸不一致对齐/cols=0 钳制/**自检零泄漏守卫**/自检不污染 outputs） | ✅ 8/8 绿 |
| 联合回归 `test_legacy_selfcheck`(19, 含新登记 video_export) + audit136(8) + audit134(5) | ✅ 32 例 EXIT=0 |
| 注入式反例（`s136_inject.py`：`shutil.rmtree` 行 → `pass`） | BUGGY 下守卫 **rc=1 红**（泄漏被捕获）；还原 sha256=`8e35ec100e2b06f3` **逐字节一致**；回绿 rc=0（首次回绿跑遇瞬时环境抖动，独立复跑全绿——已记录） |
| `audit_selector.py --check` | ✅ EXIT=0，登记 35 条 |

**测试修正记录**（初版 2 处测试自身 bug，非模块缺陷）：
1. 灰值视频「三通道逐位相等」断言过强——mp4 有损编码会产生通道间噪声；D2 契约本质是**形状合规**，改为形状断言并在 docstring 说明。
2. `cv2.imread` 吃不下中文路径（项目经典坑，image_utils 文档明载）——改用 `core.image_utils.load_image`。

## 五、登记

- `scripts/audit_selector.py` `AUDITED_SINCE` += `"services/video_export": 136`。
- `tests/test_legacy_selfcheck.py` MODULES += `("services/video_export.py", [])`（隔离 + 自清理 + rc=0 / 4.4s）。
- **O2 撤销**：Stage 135 登记的「video_service.py:210 灰度帧潜在崩溃」经 OpenCV 5.0 实测证伪，记忆与文档同步撤销。
- **O1 待办**：`export_service.make_compare_array` 缺 `_to_rgb3`（2D/4C 直传崩溃），登记为 export_service 补漏（下一站可顺带最小修）。
- 下一站（`--unaudited`）：`utils/i18n` 5.2 → `ui/components` 4.8 → `ui/tab_history` 4.8。

## 六、经验

见 `LESSONS.md` 第 150 条：**「潜在崩溃」登记也必须过探针**——同一条 API（cvtColor 对 2D 输入）在直觉里"显然会崩"，OpenCV 5.0 实测却自动升通道；未证实的登记会误导后续审计排期。
