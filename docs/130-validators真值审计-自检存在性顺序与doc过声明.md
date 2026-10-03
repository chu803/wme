# Stage 130 — services/validators.py 真值审计：自检输入违反前置顺序 + doc 过声明

> 审计方法：探针量化 → 分类（缺陷 vs by-design） → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 文档 → 记忆同步。
> 审计对象：`services/validators.py`（输入校验服务层：图片 / PDF / 视频预校验闸门）。
> 荒漠度：`audit_selector.py --unaudited` 榜首 **6.9**（code 243 / refs 35 / files 2）。

## 1. 探针量化

探针 `s130_probe.py`（写 `%TEMP%`，项目零残留）实测：

| 项 | 修复前 | 修复后 |
|---|---|---|
| `python services/validators.py` 退出码 | **1（用例4 崩于 FileNotFoundError）** | 0（全部用例通过） |
| `validate_numpy_image(float32 数组)` | 放行（仅 WARNING），docstring 却称抛 ValueError | 放行（docstring 已订正） |

## 2. 两处真缺陷

### D1（真缺陷）：`__main__` 自检用例 4/5 违反被校验函数的前置顺序 → 自检崩溃
- **症状**：自检用例 4/5 用**不存在的** `"note.txt"` / `"clip.mp3"` 调 `validate_pdf_file` / `validate_video_file`，期望 `UnsupportedMediaError`。但这两个函数**先查存在性（`os.path.exists` → FileNotFoundError）、再查格式白名单** ⇒ 抛出的 `FileNotFoundError` 不被 `except UnsupportedMediaError` 捕获 ⇒ 自检在用例 4 崩溃、**exit 1**。
- **为何长期无感**：`services/validators.py` **不在** `tests/test_legacy_selfcheck.py` 的 `MODULES` 中 ⇒ 无子进程守卫；而既有 pytest（`test_validators_media.py`）用的是**真实存在**的 `note.doc` / `clip.mp3`，恰好绕开了这个坑，反而证明**函数行为正确、只有自检错**。
- **定性**：真缺陷（自检是项目铁律要求必须通过的交付物；属「自检与实现同构性」家族——经验 126/133）。
- **最小修复**：自检用例 4/5 改用**真实存在**的 `.txt` / `.mp3` 临时文件（与 pytest 覆盖一致）；并在 `test_legacy_selfcheck.py` 的 `MODULES` 登记 `services/validators.py`，补上子进程守卫。

### D2（真缺陷）：`validate_numpy_image` docstring 过声明
- **症状**：docstring 异常段写「`ValueError`：数组形态非法（**不是 2D/3D uint8**）」，但实现只对 `ndim ∉ {2,3}` / 非 ndarray 抛错；`dtype != uint8` **仅 warning**（不抛）。float32 的 H×W×3 数组被放行，与 docstring 暗示的「非 uint8 即非法」矛盾。
- **定性**：真缺陷（声明 ≠ 实现）。实现的宽容是合理的（下游 inpaint 会转换），故**修文档**而非收紧行为。
- **最小修复**：docstring 订正为「dtype 非 uint8 时仅告警不拒绝」+ 异常行改为「非 numpy 数组，或 ndim 不在 {2,3}」；并以守卫测试锁定「非 uint8 放行」的真实契约。

### 已排除（by-design，不修）
- 「存在性优先于格式白名单」的顺序：docstring 的异常列表亦以 `FileNotFoundError` 打头，实现与文档一致；对不存在路径报「找不到文件」比对 UI 更有用。

## 3. 守卫与注入式反例

- 回归守卫：`tests/test_validators_audit130.py`（5 用例：缺失文件抛 FileNotFound / 存在坏扩展名抛 Unsupported / 非 uint8 放行 / 真非法 ndim 仍抛）。
- 子进程守卫：`tests/test_legacy_selfcheck.py` 的 `MODULES` 新增 `("services/validators.py", [])`（Stage 122 铁律）。
- 注入式反例：`s130_inject.py`（写 `%TEMP%`）——FIXED 守卫通过 → BUGGY（自检用例 4/5 恢复传不存在文件）→ 子进程守卫捕获回归（自检 exit≠0）→ 还原 FIXED，`sha256=8fb63cf15cc88d243751f530e56ece044aa6e49eb77e0d861d724e1adbb97e77` 字节一致。

## 4. 影响面与回归
- D1 只改 `__main__` 自检与测试登记，不改函数行为；D2 只改 docstring。合法输入路径行为逐字节不变。
- 本阶段全测：`test_validators_audit130.py`(5) + `test_validators_media.py` + `test_input_normalization.py` + 子进程自检 = **63 例 EXIT=0**。
