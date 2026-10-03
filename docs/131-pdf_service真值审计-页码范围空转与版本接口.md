# Stage 131 — services/pdf_service.py 真值审计：页码范围空转 + 版本接口名实不符

> 审计方法：探针量化 → 分类（缺陷 vs by-design） → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 文档 → 记忆同步。
> 审计对象：`services/pdf_service.py`（PDF 文档去水印服务，阶段 10 新增，460 代码行）。
> 荒漠度：`audit_selector.py --unaudited` 榜首 **6.5**（code 460 / refs 71 / files 6）。

## 1. 探针量化

探针 `s131_probe.py`（写 `%TEMP%`，项目零残留）实测：

| 项 | 修复前 | 修复后 |
|---|---|---|
| `_parse_pages("1-5000000", 3)` 用时 | **186 ms**（3 页文档） | **0.0 ms** |
| `get_pdf_version()` | **100 字符多行文档串**（含 Python 版本/平台） | `'1.28.0'` |
| `get_pdf_info(...)["page_size"]` | `'595 × 842 点'`（docstring 举例含「（约 A4）」） | 同（docstring 已订正） |

## 2. 三处真缺陷

### D1（真缺陷，行为级）：页码范围解析在循环体内才钳制 → 超范围输入空转
- **症状**：`_parse_pages` 对范围写法执行 `for p in range(start, end+1): if 1 <= p <= total: picked.add(p-1)`——**钳制发生在循环内部**。于是用户把页码写成 `"1-99999999"` 之类，循环体执行近亿次（每次只做一次越界判断后丢弃），界面**卡住数十秒**。实测 `"1-5000000"` 在**3 页**文档上耗时 **186 ms**，时间只随 `(end-start)` 增长、与 `total` 无关。
- **定性**：真缺陷（用户输入可达的 O(输入范围) 空转；属「边界未在循环前收敛」家族）。
- **最小修复**：遍历前先把范围与 `[1, total]` **求交**（`start=max(1,start); end=min(end,total)`）。

### D2（真缺陷）：`get_pdf_version` 名实不符——返回模块文档串而非版本号
- **症状**：docstring 声明「返回 PyMuPDF 版本号字符串」，实现却是 `str(fitz.__doc__).strip() or "已安装"` ⇒ 返回 `'PyMuPDF 1.28.0: Python bindings for the MuPDF 1.29.0 library.\nPython 3.13 running on win32 (64-bit).'`（100 字符、含换行与平台信息）。
- **定性**：真缺陷（声明 ≠ 实现）。正确 API 是 `fitz.__version__`（`'1.28.0'`）。该函数**当前无调用点**（死函数），但死函数也要对齐——否则是等着被误用的陷阱。
- **最小修复**：优先取 `fitz.__version__`，回退 `fitz.version[0]`，再回退 `"已安装"`。

### D3（真缺陷，文档级）：`get_pdf_info` docstring 示例多出实现不产出的后缀
- **症状**：docstring 举例 `page_size` 为 `"595 × 842 点（约 A4）"`，实现只产出 `"595 × 842 点"`（自检实测印证）。
- **定性**：真缺陷（文档示例 ≠ 实现产出）。修**文档**（不擅自改行为，避免影响既有消费方）。
- **最小修复**：docstring 示例订正为 `"595 × 842 点"`。

### 已排除（by-design，不修）
- `_page_to_array` 的 `pix.n == 4` 分支在 `alpha=False` 下为防御性死分支（无害）。
- 「未选中的页原样复制 / 失败页按 `PDF_KEEP_FAILED_PAGE` 处理」等行为与文档一致。

## 3. 守卫与注入式反例

- 回归守卫：`tests/test_pdf_service_audit131.py`（5 用例：超大范围有界 / 常规范围结果不变 / 版本号为短串 / page_size doc 一致 / page_size 实产形态）。
- 子进程守卫：`tests/test_legacy_selfcheck.py` 的 `MODULES` 新增 `("services/pdf_service.py", [])`（Stage 122 铁律）。
- 注入式反例：`s131_inject.py`（写 `%TEMP%`）——FIXED 守卫通过 → BUGGY（去掉钳制 + 版本函数改回返回 `fitz.__doc__`）→ 守卫捕获回归 → 还原 FIXED，`sha256=43fb54e5c95eac9d700f108f19db9baf39de0765812f6a9344e0ee26d31f240a` 字节一致。

## 4. 影响面与回归
- D1 只收敛遍历范围，常规输入结果逐字节不变；D2 改死函数实现；D3 改 docstring。合法路径行为不变。
- 本阶段全测：`test_pdf_service_audit131.py`(5) + `test_pdf_history.py` + `test_media_recommend.py` + 子进程自检 = **27 例 EXIT=0**。
