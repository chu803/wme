# 阶段 40 设计文档：PDF 结果历史记录与管理

> 版本：1.0.0 ｜ 日期：2026-08-10 ｜ 归属模块：`services` / `ui`
> 合规定位：仅处理用户**本人生成的 AI 图片/文档水印**；严禁清除他人著作权作品水印/署名（依据《著作权法》第五十三条）。

---

## 1. 背景与目标

阶段 37 已补齐「视频处理结果」的历史管理：处理后自动登记进 `cache_service`，历史页可回访
缩略图、元信息、预览与下载。但 `ui/tab_media.run_pdf` 的成功分支**从不调用**缓存登记，
导致：

- PDF 处理完成后结果 PDF 散落在 `outputs/` 目录；
- 用户无法在历史页「浏览 / 下载 / 回访」PDF 处理结果；
- 与视频记录形成体验不对称（视频能回访、PDF 不能）。

**阶段 40 目标**：镜像阶段 37 的视频记录管理路径，新增 **PDF 结果历史记录与管理**，
使 PDF 与视频、图片一样，处理后自动归档、可在历史页回访与下载。

设计原则（严守项目铁律）：

- 分层 `ui → services → core → utils → config`，无反向依赖；`core/services/utils` 零 gradio。
- 复用以 `add_video_record` 为蓝本，字段与展示逻辑对称，降低维护成本。
- 零 torch 可测：所有新增逻辑均不依赖深度学习权重，单元测试覆盖契约与回退分支。

---

## 2. 整体方案

```
pdf_service.render_pdf_first_page(pdf, *, dpi)
        │  渲染 PDF 首页为 BGR 数组（复用既有 _page_to_array）
        ▼
cache_service.add_pdf_record(src, out, *, original_name, params,
                              page_count, file_size, upscaled)
        │  生成首页缩略图（失败回退占位）→ HistoryRecord(kind="pdf") 追加 + 持久化
        ▼
ui.tab_media._store_pdf_record(res, src_path)   ← run_pdf 成功分支调用
        │
        ▼
ui.tab_history  加载徽章 / 详情 / 选中 / 重新处理 的 pdf 分支
```

---

## 3. 各模块改动

### 3.1 `services/pdf_service.py` —— 新增 `render_pdf_first_page`

- 公共函数：`render_pdf_first_page(pdf_path, *, dpi=None) -> Optional[np.ndarray]`
- 行为：校验文件存在且为 PDF → `_import_fitz()`（缺库回退 None）→ 打开文档、取第 0 页 →
  复用 `_page_to_array(page, dpi)` 渲染为 BGR 数组。
- 回退契约（**绝不抛异常**，统一返回 None 并记 warning）：
  - PyMuPDF 未安装；
  - 文件不存在 / 不是 PDF（扩展名不在 `config.SUPPORTED_DOC_FORMATS`）；
  - 文档无任何页面；
  - 渲染或解码过程出现异常。
- 调用方（缓存层）拿到 `None` 时回退「占位缩略图」，保证记录始终可注册。

### 3.2 `services/cache_service.py` —— 扩展 `HistoryRecord` + 新增 `add_pdf_record`

`HistoryRecord` 新增字段（旧数据 `from_dict` 回溯为默认值，向后兼容）：

| 字段         | 类型 | 说明                          |
|--------------|------|-------------------------------|
| `kind`       | str  | 取值集合扩为 `"image"\|"video"\|"pdf"` |
| `page_count` | int  | 文档总页数                    |
| `file_size`  | int  | 结果 PDF 字节数               |

`to_dict` / `from_dict` 同步扩展（保证 JSON 索引序列化往返一致）。

新增公共方法：

```python
def add_pdf_record(self, src_path, out_path, *, original_name, params="",
                   page_count=0, file_size=0, upscaled=False) -> Optional[HistoryRecord]:
    ...
    # 1) 渲染首页缩略图；失败则回退占位图（避免记录因缩略图缺失而流产）。
    first_arr = render_pdf_first_page(str(out_path))
    thumb_path = (save_thumbnail(make_thumbnail(first_arr)) if first_arr is not None
                  else save_thumbnail(placeholder))
    rec = HistoryRecord(..., kind="pdf", page_count=..., file_size=..., upscaled=...)
    with self._lock:
        self._records.append(rec); self._persist()
```

- `render_pdf_first_page` 采用函数内惰性导入（`from services.pdf_service import ...`），
  避免模块加载期耦合。
- 缓存关闭（`enabled=False`）时返回 `None`，与既有 `add_*` 一致。

### 3.3 `ui/tab_media.py` —— 成功后登记 PDF 历史

- 新增 `_store_pdf_record(res, src_path)`，镜像 `_store_video_record`：
  - 校验 `res.out_path` 有效且存在；
  - 调用 `_CACHE.add_pdf_record(src, res.out_path, original_name=..., params=res.summary(),
    page_count=res.total_pages, file_size=os.path.getsize(res.out_path), upscaled=False)`。
  - PDF 媒体页当前未暴露真实超分开关，`upscaled` 记 `False`（字段保留便于后续扩展）。
- 在 `run_pdf` 成功返回**之前**调用 `_store_pdf_record(res, path)`，使「处理完即入库」。

### 3.4 `ui/tab_history.py` —— 支持 PDF 记录展示与交互

沿用阶段 37 视频记录的分支结构：

| 位置               | PDF 分支行为                                                                 |
|--------------------|------------------------------------------------------------------------------|
| `load_gallery`     | 徽章 `📄 名称｜PDF · N页`；缩略图用 `thumb_path`（首页图）；结果对比视图仅对 `image` 生效，PDF 不走 `load_image` 拼图。 |
| `_record_detail`   | 追加 `history.detail.pdf`（页数 / 体积 MB）+ `history.detail.pdf_hint` 引导。  |
| `on_select`        | `is_pdf` 时返回 `(详情, result_path, video_path=None, pair=None, "", pdf_hint)` —— 不走图片对比查看器，引导到 PDF 页。 |
| `reprocess`        | `kind=="pdf"` 直接返回 `history.detail.pdf_hint`，不尝试重处理（无原图）。   |
| `reprocess_and_refresh` | 复位判定条件扩为 `kind in ("video", "pdf")`，PDF 记录复位对比查看器。     |

### 3.5 `utils/i18n.py` —— 新增 PDF 历史键

中英双语各新增：

- `history.detail.pdf`：`**PDF 信息**：共 {pages} 页 · 结果体积 {size_mb} MB`
- `history.detail.pdf_hint`：PDF 记录仅支持预览与下载；如需重新处理请回到「📄 PDF 文档」页重新上传。
- `compare.viewer.pdf_hint`：📄 PDF 记录请到「媒体与文档」页的「📄 PDF 文档」子页预览与下载。
- 自检键列表（`compare.viewer` 17 → 18 键）补 `pdf_hint`，缺失即断言失败。

---

## 4. 测试（`tests/test_pdf_history.py`，14 例，零 torch）

| 用例 | 覆盖 |
|------|------|
| `render_pdf_first_page_*` | 缺文件 / 非 PDF / 缺 fitz → None；fitz 可用时正常渲染（skipif 未装） |
| `history_record_pdf_roundtrip` | `page_count`/`file_size` 序列化往返；旧数据回溯默认值 |
| `add_pdf_record_registers` | 登记成功、字段正确、缩略图落地 |
| `add_pdf_record_render_fallback_placeholder` | 首页渲染失败回退占位，记录照常登记 |
| `tab_media_stores_pdf_record` | 成功后调用 `add_pdf_record`，参数正确 |
| `tab_media_stores_pdf_record_skips_invalid` | 无效结果路径跳过登记 |
| `tab_history_on_select_pdf` | 返回结果路径、无视频预览、pair=None、给出 PDF 引导 |
| `tab_history_reprocess_pdf_hint` | PDF 记录重处理返回提示 |
| `tab_history_load_gallery_pdf_badge` | 徽章 `📄 名称｜PDF · N页` |
| `tab_history_record_detail_pdf` | 详情含页数/体积与引导 |
| `i18n_pdf_history_keys` | 三键中英双语存在性 |

---

## 5. 验证结果

- `py_compile` 全量通过（新增/改动模块 `pdf_service` / `cache_service` / `tab_media` / `tab_history` / `i18n`）。
- 全量 `pytest`：基线 278 passed / 1 skipped → **292 passed / 1 skipped**（新增 14 例，无回归）。
- `build_app()` 经 `ui/app.py` 构建通过（应用装配无缺引用）。

---

## 6. 已知边界与后续

- 阶段 40 与阶段 37 一致：**PDF / 视频记录均不支持「重新处理」**（无原图载体），历史页引导回对应媒体子页。
- `upscaled` 字段对 PDF 恒为 `False`（PDF 媒体页未暴露真实超分开关）；若后续在 PDF 页接入真实超分，`_store_pdf_record` 已预留该参数传递位。
- 沙箱无 torch：真实超分/模型推理全程回退轻量路径，代码真实，待装 torch+权重生效（与全局边界一致）。
