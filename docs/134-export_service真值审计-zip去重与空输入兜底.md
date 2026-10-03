# 阶段 134 · `services/export_service` 真值审计

> 审计对象：`services/export_service.py`（未审榜榜首，荒漠度 5.4，321 代码 / 59 引用 / 5 文件）
> 方法：探针量化 → 分类（defect vs by design）→ 最小修复 → 回归守卫 → 注入式反例（sha256 字节级还原）→ 文档 → 记忆同步
> 结论：**命中 5 处真缺陷**（D1 zip 去重缺失 / D2 空联系表颜色 / D3 零高度除零 / D4 大写 ext mime / D5 自检桩契约）

---

## 1. 探针实测（量化）

探针 `s134_probe.py`（落 `%TEMP%`，不污染项目）：

| 项 | 实测 |
|---|---|
| **D1** | `export_paths_as_zip([p1,p2], names=None)` 两图同名 `photo.png` → 包内出现 **两条同名 `photo.png`**（Python 发 `UserWarning: Duplicate name`），读回时**首条不可达**；设计文档 `docs/06` 明确承诺「**自动去重文件名**」，代码注释也声称去重——**双重承诺落空，实为静默丢数据** |
| **D2** | `make_contact_sheet([])` 返回 `(160,160,3)` 但颜色为 `[[30,30,30]]`（单通道 `bg[0]` 平铺），而非 `bg=(30,41,59)`；docstring 称「返回 1×1 灰块」，形状与颜色都错 |
| **D3** | `make_compare_array` 传入零高度左图 + 非零右图 → **`ZeroDivisionError`**（缩放系数 `wa*target_h/ha`，`ha==0`） |
| **D4** | `_array_to_data_uri(arr, ext=".JPG")` → uri 前缀 `data:image/png`（标错 mime，字节实为 JPEG） |
| **D5** | 自检用例 4 调用 `export_history_as_zip(_StubCache(), fmt="WEBP")` → `TypeError: list_records() got unexpected keyword 'kind'`，**该用例从未真正验证历史导出** |

---

## 2. 分类与处置

| 缺陷 | 类型 | 修复 |
|---|---|---|
| **D1** zip 去重缺失 | defect（违反文档契约 + 静默数据丢失） | 写包前用 `used` 字典记录已用名，冲突时追加 `_N` 序号（`photo.png`→`photo_1.png`） |
| **D2** 空联系表颜色 | defect（颜色错 + doc 不符） | `np.full(..., bg, ...)` 取代 `bg[0]`；docstring 改为「返回 thumb_size×thumb_size 的 bg 色块」 |
| **D3** 零高度除零 | defect（边缘输入崩溃） | 拼接前 `if ha==0 or hb==0: return 最小空白画布`，短路避免除零 |
| **D4** 大写 ext mime | defect（mime 错标） | 归一化 `ext_l = ext.lower()` 后再判 mime/参数 |
| **D5** 自检桩契约 | defect（自检盲区） | `_StubCache.list_records` 加 `kind=None, query=None`，对齐 `CacheService.list_records` 真实契约 |

**D1 修复后实测**（重跑探针）：包内 `['photo.png', 'photo_1.png']`，首条可独立读回（不再重名告警）。

---

## 3. 守卫与回归

- 新增 `tests/test_export_service_audit134.py`（5 例）：去重/D2 空块颜色/D3 零高度/D4 大写 ext/D5 桩契约。
- 既有测试不受影响：`test_export_compare_view.py` / `test_video_management.py` / `test_history_filter.py` / `test_history_search.py` / `test_ui_audit91.py`。
- `services/export_service.py` 登记进 `tests/test_legacy_selfcheck.py` 的 `MODULES`（自检非破坏性：用 tempfile 造输入、写 EXPORT_DIR 临时产物后清理，不动用户文件）。
- 本轮运行：守卫(5) + 既有 5 文件 + 子进程自检 = **65 例 EXIT=0**。

### 注入式反例（`s134_inject.py`，`%TEMP%`，CRLF 安全 + 二进制 sha）
- 记录 FIXED sha256 = `bcff949335d49d0e`
- 注入 BUGGY（D1 去重回退 / D2 颜色回退 / D3 守卫回退 / D4 ext 回退 / D5 桩回退）→ 守卫 **returncode=2**（回归被捕获）
- 二进制写回复原 → RESTORED sha256 = `bcff949335d49d0e`（**字节级一致**）

---

## 4. 沉淀（经验 147 / 148 落 `LESSONS.md`）

- **147**：**「自动去重」是契约——凡文档/注释承诺「去重」，实现就必须真的重命名；直接 `zf.write(arcname=同名的)` 只是写出**重名条目**，读回时首条被末条覆盖而不可达（Python 仅告警不报错），等于静默丢数据**。判据：凡打包/导出带「去重」字样，写一条断言「同名输入在包内变为 `name`/`name_1`」；**zip 的「Duplicate name」告警是数据丢失的前兆，不是无害提示**。
- **148**：**自检桩必须对齐被测函数实际调用的接口形状**——`export_history_as_zip` 调 `cache.list_records(kind=, query=)`，自检桩却写 `list_records(self)`（无 kwargs），于是用例 4 永远 `TypeError`、从未验证过历史导出；且因自检未登记进子进程守卫，这个坏用例长期无感。**桩不是装饰：它的签名就是被测契约的一部分，错一个参数就是一处盲区**（同族：经验 139 自检输入要满足前置检查、133 哑巴自检）。

---

## 5. 下一步（未审榜，ratio 5.4 三连余二）
`services/file_service` 5.4 → `services/video_export` 5.4 → `utils/path_helper` 5.4。
