# 阶段 191 · UI 两个最弱标签页的接缝补测 + gr.File 归一化收敛

> 承接 `docs/187`：全库覆盖率里 **`ui/tab_batch.py` 55.52%**、**`ui/tab_media.py` 56.43%**
> 是全仓最低的两块**产品代码**（正是「批量」「视频/PDF」两条最重链路），也是「产品化 D」
> 的具体落点。本阶段补测其**纯函数接缝**，并顺手消除一处**双实现分歧**。

## 1. 发现：`gr.File` 返回值被两处各归一化一次，且**都已踩同一个坑**

| 文件 | 函数 | 形状 | 关键差异 |
|---|---|---|---|
| `ui/tab_batch.py` | `_normalize_file_value` → `list[str]` | 多文件 | **列表内**未知元素兜底 `str(item)`；**顶层**未知标量丢弃（返回 `[]`） |
| `ui/tab_media.py` | `_normalize_file_value` → `str` | 单文件 | 未知类型兜底 `obj.name`（**该分支会截断 `pathlib.Path`）** |

- **同一输入、两处结果不同**：带 `.name` 的对象在批量页得到对象 repr、在媒体页得到 `.name`
  ⇒ Gradio 一旦改返回形态，**只会有一个页面坏**（另一个照旧）。
- **两处都踩的坑**：`pathlib.Path` **也有 `.name`**，而 `.name` 只是**末段文件名**
  ⇒ 旧实现会把 `/tmp/dir/a.png` 悄悄截成 `a.png`（丢目录）。批量页因为先落到 `str(obj)`
  反而「侥幸正确」；媒体页则是**真错**。

## 2. 交付

### 2.1 归一化收敛为唯一实现
- 新增 **`ui.components.normalize_file_paths(value) -> list[str]`**（组件库＝复用件的正确归处，
  且此前**没有**第三份实现）。优先级显式钉死：
  `None → str → os.PathLike → dict(path/name) → list/tuple（递归 + 去重保序）→ .name → str()`
  —— **`os.PathLike` 必须排在 `.name` 之前**（否则 Path 丢目录）。
- `tab_batch._normalize_file_value` 与 `tab_media._normalize_file_value` 改为**只做转发**
  （各自保留自己需要的形状：list / 「取第一个」）。
  **行为变更**：媒体页对 `pathlib.Path` 不再截断目录（修缺陷）。

> ⚠ **阶段 192 改正**：本节原文写「其余形状逐条对齐旧行为」——**不准确**。实际情况：
> 批量的旧实现是**不对称**的（**列表内**未知元素 → `str(item)`；**顶层**未知标量 → 丢弃 `[]`），
> 而我 191 的首版把**顶层**也写成 `str(value)`（`3.14 → ["3.14"]`），**打破了这条既有契约**
> （由 `tests/test_tab_batch_helpers.py` 守着，直到全库跑才暴露）。
> 已在 192 修复：末位兜底改回 `[]`、列表分支单独保留 `str(item)`，并把这段不对称约定的由来
> 写进 `normalize_file_paths` 的文档字符串。详见 `docs/192` §3.2 与 LESSONS 205。

### 2.2 纯函数接缝补测（`tests/test_ui_tab_seams.py`，34 例全绿）
| 文件 | 被锁住的接缝 |
|---|---|
| `ui/components.py` | `normalize_file_paths` 的 12 种形状 + 优先级（Path vs `.name`）+ 去重保序 + 递归 |
| `ui/tab_batch.py` | `_preset_display`（已知/未知）· `_build_editor_value`（结构 + alpha==掩码 + 固定涂抹色）· `cancel_batch`（有/无队列）· `on_gallery_select` / `_with_mode`（越界 hint / `(row,col)` 索引规整 / **沿用用户所选模式**）· `_mask_path_for`（同名掩码 / 缺目录 / 空路径）· `_resolve_batch_preset`（**四条分支**：关推荐 / 推荐到具体预设 / 推荐回默认视为无强信号 / 推荐抛异常回退） |
| `ui/tab_media.py` | `_human_seconds`（0 / <60 / 60 / 90 / 3600 / **负数夹 0**）· `_preset_choice_label`（有键用译文、缺键用兜底显示名、**绝不返回裸键名**）· `export_frame_file` / `export_sheet_file`（路径缺失一律 `None`，不抛）· `on_frame_gallery_select`（无索引 / 视频缺失的提示分支） |

### 2.3 测试侧教训（已写入本文件）
① **路径断言不得写死 POSIX 分隔符**：`Path("/tmp/a")` 在 Windows 上是 `\tmp\a` ⇒
   期望值一律用 `os.fspath(Path(...))` 生成（首轮 4 条用例因此变红）。
② **比较对象 repr 必须用同一实例**：`str(_Fake(""))` 两次调用地址不同 ⇒ 断言必红。

## 3. 覆盖率证据（诚实口径）

- **单文件隔离测量**（仅本测试文件驱动）：`tab_batch 30.65%` / `tab_media 18.26%`
  —— 这个数字**不能**与 187 的全库数字（55.52% / 56.43%）直接比较，因为那两个数字
  是**整套测试**共同贡献的。
- **可精确声明的部分**：187 报告中这两个文件「未覆盖」的行块里，
  `tab_batch` 的 `L92–231`（`_preset_display` / `_normalize_file_value` / `_mask_path_for` /
  `_build_editor_value` / `_resolve_batch_preset`）与 `L649–690`（画廊点选）**已不在**本次
  隔离测量的 missing 列表中 ⇒ **这些接缝确实被覆盖了**（其余大块是 `run_batch` /
  `build_*_tab` 的 Gradio 编排体，按设计需要真服务，不在本阶段范围）。
- **全库复测**：`.scratch/cov191full`（本阶段收口跑），结果见下节。

## 4. 连带发现：`pytest-timeout` **内外层超时错配**（把全库覆盖率跑整场中断）

**事故**：本阶段收口跑全库覆盖率时，在 **71%** 被掐断——`pytest-timeout` 报
`test_prompted_area_gate.py::test_gate_end_to_end_exit0` 超时，**整场 session 中断、16 分钟的覆盖率数据全部丢失**（数据文件都没写出）。

**根因（枚举三处，非记忆）**：`pytest.ini` 设了全局 `--timeout=300`，它作用于**测试函数本身**；
而某些用例给它 spawn 的**子进程**传了更长的 `timeout=` ⇒ **外层比内层还小**：

| 用例 | 子进程允许 | 外层（测试函数） | 后果 |
|---|---|---|---|
| `test_prompted_area_gate::test_gate_end_to_end_exit0` | **600s** | 全局 300s | **实测炸掉**（门禁真跑 20 图×4 预设 ≈171s，插桩/负载下超 300s） |
| `test_quality_service_audit140::test_selfcheck_runs_clean_and_no_leak` | **600s** | 全局 300s | 同类定时炸弹 |
| `test_legacy_selfcheck::test_module_selfcheck` | 300s | 全局 300s | **相等即竞态**（谁先到点不定） |
| `test_compare_service_audit124::test_module_selfcheck_subprocess` | 300s | 全局 300s | 同上 |

**修复**：四处显式加 `@pytest.mark.timeout(900/420)`，使**外层严格大于内层**；
并把该不变式**机械化**为 `tests/test_timeout_contract.py`：扫描 `tests/`，
凡函数体内出现 `timeout=N`（N ≥ 300）者，必须带 `@pytest.mark.timeout(M)` 且 `M > N`。

**守卫自身的教训**（写在守卫代码里）：首版扫描器**把注释里的 `timeout=600` 当成代码**，
于是「解释该契约的注释」反把守卫判红 ⇒ 扫描源码的守卫**必须先剔除注释**
（同族：189 的顺序断言曾被文档字符串干扰）。
顺带修正 `test_prompted_area_gate` 的注释估值（原文「≈40s」，与 `docs/185` 实测 **≈171s** 不符）。

## 5. 边界与后续

- 本阶段**不**覆盖 `run_batch` / `run_pdf` / `run_video` / `build_*_tab` 的编排体：
  它们需要真实 Gradio 服务、队列线程与媒体文件；**不建议**为刷覆盖率写「假装测试」。
  若要覆盖，正确的做法是**容器内冒烟**（启服务 + 一条真实小样本走通），与 `docs/189` 的判断一致。
- `ui/components.py` 覆盖率仍低（本次隔离测量仅 36.82%）——但它的**未覆盖主体**是
  `build_repair_params` / `build_export_format_selector` 等 Gradio 组件构造器，
  属同类边界（需要真服务才能验）。已登记，不硬抓。
