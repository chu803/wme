# 阶段 358 · CI 连红的**第三个**原因：我自己那个测试在模块顶层 import 了 PyMuPDF

> 日期 2026-10-27 · 性质：**CI 阻断级修复 + 一条防复发守卫**

## 一、抓到它的路径（没有靠截图）

仓库里有个**专为此造的探针**（阶段 184 的 `scripts/ci_env_probe.py`，「CI 环境等价性审计」）：
它在**导入层**屏蔽 CI 提供不了的模块（torch / PyMuPDF / 权重），本机复现 CI 的依赖版图。
跑它 ⇒

```
ERROR tests/test_pdf_page_abort341.py
tests\test_pdf_page_abort341.py:20: in <module>
    import fitz
scripts\ci_env_probe.py:72: in find_spec
    raise ModuleNotFoundError(
E   ModuleNotFoundError: [CI 环境模拟] fitz 在 CI（requirements-min.txt + requirements-dev.txt）中不存在
```

⇒ **`tests/test_pdf_page_abort341.py`（阶段 341 我自己写的）在模块顶层 `import fitz`**，
而 CI 只装 `requirements-min/dev`（**不含 PyMuPDF**）⇒ **收集期 ImportError**
⇒ pytest 退出码 **2** ⇒ 阶段 330 之后「2 = 收集期报错 = 失败」（不再当"跳过"）⇒ **入口判失败 ⇒ CI 红**。

**时长也对得上**：收集期就中止 ⇒ 不跑任何用例 ⇒ 1 分多钟（截图里每次都 1m10s~1m49s）。

## 二、三个原因的区别（这才是这段最值钱的部分）

| # | 原因 | 症状 | 修法 |
|---|---|---|---|
| 1 | 依赖 pin 钉了**没有 cp311 轮子**的版本 | 装依赖时失败 | 改区间（347） |
| 2 | 系统依赖写死了**镜像代际**的包名（Ubuntu 24.04 改名 `libglib2.0-0t64`） | apt 找不到包 | 按镜像探测（351） |
| 3 | **测试模块顶层 import 了 CI 没有的可选依赖** ⇒ 收集期报错 ⇒ 退出码 2 | 收集即中止（最快的那种红） | `pytest.importorskip`（本阶段） |

三个症状**都是"红得飞快"**，光看截图分不出来 ⇒ 必须**逐个用探针验证**，而不是"改一个看看"。

## 三、为什么我的守卫没抓到

阶段 331 给所有**脚本**加了 UTF-8 加固的守卫，但**测试文件**没有同类守卫；
而「本地全量入口 rc=0」也不能发现它（本地有 PyMuPDF）。
⇒ 真正的护栏是 `ci_env_probe.py`——它早就在仓库里，只是**没人跑**。

## 四、防复发

1. **修**：`tests/test_pdf_page_abort341.py` 改用 `fitz = pytest.importorskip("fitz")`
   ⇒ CI 上**跳过这条用例**，而不是让整个子集收集失败。
2. **新守卫** `tests/test_ci_optional_imports358.py`：AST 扫 `tests/test_*.py` 的**模块顶层** import，
   命中「CI 环境没有的可选依赖」（fitz / pymupdf / torch / torchvision / easyocr / basicsr / realesrgan）⇒ 红。
   ⚠ `cv2` / `gradio` **在** requirements-min.txt 里（opencv-python-headless / gradio）⇒ 不算 offender
   （第一版把它们也列进去 ⇒ 误报 14 个文件，已收窄）。
3. **建议（未做）**：把 `ci_env_probe.py` 变成 CI 的**第二个 job**（约 10 分钟），
   让"本地绿、CI 红"在**推之前**就被发现，而不是等 CI 红了再回来查。

## 五、续：修完收集期，还有两条**运行时**失败

收集期修好后重跑探针，子集能跑到用例级了，又露出两条**不是 import 问题**的失败：

- `test_broken_pdf_reports_readable_error`
- `test_process_pdf_reaches_process_image`

它们**没有**在顶层 import fitz，而是调用「需要 fitz 才有意义」的 PDF 路径 ⇒ 在 CI 上会真的跑、真的失败
（这就是「修一条不绿」的又一次实例：同一症状下藏着不同层级的成因）。
⇒ 给这两条加 `skipif(not _HAS_FITZ, reason="需要 PyMuPDF（CI 最小依赖不含）")`，与 341 的口径一致。

### 但「跳过判据」第一版自己又炸了

第一版写的是 `importlib.util.find_spec("fitz")` —— 而 `ci_env_probe.py` **钩住的正是 `find_spec`**
（它靠这个模拟"CI 上没有此模块"）⇒ 判据表达式在**收集期**抛出 `ModuleNotFoundError` ⇒ 两个文件再次 ERROR。
**模拟缺失的机制，与最常用的探测方式撞车。**

改法 `tests/_optional_deps.py`：**真的去 `__import__`**，`except ImportError`
（`ModuleNotFoundError` 是其子类 ⇒ 探针的模拟同样被捕获），且 import 在**函数体内** ⇒ 不违反本阶段的顶层 import 守卫。

**教训（#334）**：凡是被「模拟 / 注入」的那一层，判定代码必须走**另一条路径**。
否则「探测器」和「被测行为」共用同一个假点，模拟越像真的，探测器越先死。
