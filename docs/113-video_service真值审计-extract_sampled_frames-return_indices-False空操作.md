# 阶段 113 · `services/video_service` 真值审计

> 选点：由 `scripts/audit_selector.py --unaudited` 给出（榜首 `services/video_service` 11.3，599 代码行 / 53 测试引用 / 6 个测试文件）。
> 审计口径：逐条核对模块公开 API 与文档承诺是否一致；对「疑似已知缺陷」先复测判定条件再定夺（经验 119）。

## 1. 模块概览

`services/video_service.py`（917 行，含 `__main__` 自检）是视频「拆→修→拼」主链路：

- `is_video_available` / `is_video_file` / `get_video_info` —— 能力与信息探测
- `extract_sampled_frames` —— 均匀抽样帧（用于浏览画廊 / 拼图）
- `VideoResult` dataclass —— 汇总结果
- `_open_writer` / `_default_out_path` / `_mask_is_usable` / `_mask_iou` / `_resolve_video_backend` —— 内部工具
- `process_video` —— 主流程（逐帧去水印、复用掩码 + 漂移护栏、真实超分、尺寸兜底、截断、单帧异常隔离）
- `build_audio_merge_command` —— ffmpeg 合并音轨命令生成

## 2. 探针与发现

| 探针对象 | 结果 |
|---|---|
| `process_video` 帧循环（检测帧 / 中间帧分支） | 正确；复用掩码下中间帧走 `manual`，检测帧走 `auto` |
| 漂移护栏（`_mask_iou < VIDEO_REUSE_MIN_IOU` → 自动改逐帧） | 正确；仅 `success` 时更新 `cached_mask` |
| 尺寸兜底（`out_frame` ≠ `(out_h,out_w)` → `cv2.resize`） | 正确；`shape[0]/[1]` 与 `out_h/out_w` 对应 H/W |
| 截断（`idx >= limit` → 置 `truncated` 并 break） | 正确；EOF 先触发 `not ok` 退出、不误标 truncated |
| 单帧异常隔离（`except` 内 `failed_frames++` 原样输出） | 正确；不中断整段 |
| `fps` 异常兜底、编码器候选回退 | 正确 |
| `extract_sampled_frames` `return_indices=False` 分支 | **F1：空操作（见下）** |

## 3. F1 · `extract_sampled_frames` 的 `return_indices=False` 是空操作

### 3.1 症状

```python
# 修复前
if not return_indices:
    out = [{"idx": d["idx"], "image": d["image"]} for d in out]   # ← 重建出完全相同的 dict
return out
```

`return_indices=False` 分支把每条 `{"idx","image"}` **原样重建一遍**，等价于什么都没做。结果是：

- `return_indices` 这个参数**永远不产生任何效果**——无论传 `True` 还是 `False`，返回项始终是 `{"idx","image"}`。
- 这与文档承诺的契约矛盾：参数名 `return_indices` 与 param docstring「`True 时每项的 'idx' 字段为帧序号`」强烈暗示「该标志控制 idx 是否被返回」；返回 docstring 也写「每项 `{"idx": int, "image": ...}`」，两处自相矛盾。
- 属于**误导性 API + 死代码**：未来调用方若传 `return_indices=False` 期望拿到不带 `idx` 的纯画面列表（或纯 `image` 列表），会**静默地**继续拿到 `idx`，埋下隐患。

### 3.2 复测判定条件（经验 119 应用）

先确认「是否真的违反契约」，而非直接下手：

- 所有 6 个调用点（`cache_service.py:392`、`video_export.py:143`、`ui/tab_media.py:369`、以及测试）**全部传 `return_indices=True`**（或默认 `True`）。
- 因此当前运行时 `False` 分支**从未被任何真实路径执行**，属于「既错又无害」的潜伏期缺陷。
- 判定结论：**确为真实缺陷（契约被破坏 + 死代码）**，不是误报，应当修复使其真正履约。

### 3.3 根因

作者本意是「`False` 时剥离 `idx`」，但列表推导的左侧仍写成了 `{"idx": d["idx"], "image": d["image"]}`，与原始项逐字段相同 → 重建恒等。

### 3.4 修复

```python
# 修复后
if not return_indices:
    # 仅返回画面、不暴露帧序号（用于不需要定位对比帧的纯展示场景）。
    out = [{"image": d["image"]} for d in out]
return out
```

并同步修正两处 docstring，使其与代码契约一致：

- param：`return_indices : 是否返回帧序号。True（默认）时每项含 "idx" 字段……；False 时每项仅返回画面（{"image"}），不暴露帧序号。`
- return：`return_indices=True 时每项 {"idx": int, "image": …}；return_indices=False 时每项仅 {"image": …}（无 "idx" 字段）。`

### 3.5 守卫

新增 `tests/test_video_service_audit113.py`，把契约钉死：

- `test_esf_return_indices_true_keeps_idx`：`True` 时每项 `keys() == {"idx","image"}`
- `test_esf_return_indices_false_strips_idx`：`False` 时每项 `keys() == {"image"}` 且**不含 `idx`**（此条会直接抓回「空操作」回归）
- `test_esf_default_keeps_idx`：默认取值与显式 `True` 一致
- `test_esf_missing_file_returns_empty`：文件缺失安全返回 `[]`，与取值无关

## 4. 非缺陷澄清

- 所有 `return_indices=False` 的真实/测试调用方都不存在 → 修复**对现有调用零行为变化**（仅改变"若有人传 False"的语义，使其符合文档）。
- `process_video` / `VideoResult` / `_mask_*` 等核心路径经逐条核对**无真缺陷**，属健康模块；本次审计交付为「修复 1 处契约缺陷 + 钉死不变量」。

## 5. 验证

受影响面（视频相关 + 注册表）跑测：

```
pytest tests/test_video_service_audit113.py tests/test_video_management.py \
       tests/test_video_reuse_drift_guard.py tests/test_video_superres.py \
       tests/test_audit_selector.py
→ EXIT=0（1 个 skip：torch 后端存在的「无后端回退」用例，预期跳过）
```

## 6. 注册与交付

- `scripts/audit_selector.py`：`AUDITED_SINCE["services/video_service"] = 113`，并补 provenance 注释。
- 新增 `tests/test_video_service_audit113.py`（4 例）。
- README §2.60 / TODO P1.61 更新。
- LESSONS 追加 121（`return_indices` 类「名称暗示可控、实现恒等」的死参数陷阱）。
- 工作记忆 `2026-09-25.md` 追加本阶段条目。

## 7. 下一步（未审榜）

`cli` 10.5 → `core/superres` 10.5 → `services/postprocess_service` 10.5 → `core/mask_generator` 10.5 → `services/compare_service` 10.3。
