# -*- coding: utf-8 -*-
"""
==============================================================================
 文件名称 : scripts/undef_scan.py
 文件说明 : **静态未定义名扫描** —— 找出「引用了但从未定义」的名字，阶段 146 新增。

 【为什么需要它】
 这类缺陷**编译期不报**（Python 只在真正执行到那一行时才 `NameError`），
 因此它最常见的藏身处是**分支 / 模式 / 配置路径**里「从未被跑到」的代码。
 实测（跨阶段累计踩中至少三次）：
   · 阶段 125 `api.remove_watermark_pdf` 转发未声明的形参 `preset`；
   · 阶段 127 `auto_pipeline.process_file` 的 manual 分支用了从未 import 的
     `load_image_with_alpha` ⇒ **manual 模式整个不可用**；
   · 阶段 145 `config.py` 的元检查里写了 `re.match` —— 而本模块只 import 了
     `json/os/sys/pathlib/typing` ⇒ 一跑就 `NameError`；
   · 阶段 146 `real_gate.py` 在模块级引用**下方才定义**的 `AREA_DRIFT_KEYS`。

 【原理】
 `ast` 遍历所有 `Name(Load)`，减去：
   ① 模块级绑定（赋值目标 / import / def / class / 形参 / 循环变量 /
      `with ... as` / `except ... as` / `global` / `nonlocal`）
   ② `builtins`
 差集即候选。**零依赖**（只用标准库 `ast`），比装 pyflakes 更轻。

 【两个已知误报源（本工具首版都踩过，已处理）】
 · **f-string 格式说明符**：`f"{x:.1f}"` 的格式说明符子树里会出现 `Name('f')`，
   不剔除会把 `f` 报成未定义；
 · **`with ... as` 的绑定位置**：在 `node.items[i].optional_vars`，
   **不是** `node.optional_vars` —— 漏了会把 `_f` 报成未定义。

 【用法】
   python scripts/undef_scan.py                      # 扫默认清单
   python scripts/undef_scan.py a.py b.py            # 扫指定文件
   python scripts/undef_scan.py --all                # 扫全仓 .py（排除 tests/ 与临时目录）
  退出码：0 = 无可疑名；1 = 有可疑名（可用于 CI / 门禁）。
==============================================================================
"""

from __future__ import annotations

import ast
import builtins
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 阶段 183：`main()` 会 `from scripts.console_encoding import force_utf8_stdio`
# （阶段 170 统一编码加固）。以 `python scripts/undef_scan.py` **直接运行**时
# `sys.path[0]` 是 `scripts/` 而非项目根 ⇒ `import scripts.*` 抛
# `ModuleNotFoundError: No module named 'scripts'`（14 个同族脚本里唯一漏了引导的），
# 使 `tests/test_legacy_selfcheck.py::test_module_selfcheck[scripts/undef_scan.py]`
# 自阶段 170 起**常红**。此处补上项目根引导，与其余脚本同一写法。
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 默认扫描清单：核心算法层 + 三条门禁 + 本阶段新增的工具。
#: 刻意**不**默认扫全仓：全仓扫描会把 `scripts/` 下的一次性诊断脚本（历史上允许
#: 有实验性写法）一起卷进来，噪音掩盖信号。需要时用 `--all`。
DEFAULT_TARGETS = (
    "config.py",
    "core/inpainter.py",
    "core/detector.py",
    "core/detection_presets.py",
    "utils/path_helper.py",
    "utils/i18n.py",
    "services/auto_pipeline.py",
    "scripts/quality_gate.py",
    "scripts/real_gate.py",
    "scripts/e2e_real_probe.py",
    "scripts/baseline_guard.py",
    "scripts/mask_param_audit.py",
    "scripts/real_asset_preflight.py",
)

#: 枚举 / 全仓扫描时跳过的目录名（**唯一真值来源**）。
#: `tests/test_legacy_selfcheck.py` 直接导入本集合使用——此前两边各写一份、
#: 注释却声称「同一口径」，实测已经漂移（一边有 temp/logs/assets/plugins、
#: 另一边有 uploads/benchmark_real）。阶段 190 收口为单一来源。
SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".workbuddy", "models",
    "cache", "outputs", "exports", "uploads", "temp", "logs",
    "assets", "plugins", "benchmark_real",
    # 阶段 190：构建 / 归档 / 报告类**产物目录**。它们内部含 `.py` 副本
    # （`build/lib/**` 是 setuptools 复制出来的源码），若不跳过，
    # 「自检登记守卫」会把它们误报成「未登记模块」，而它们本不该被审计。
    "build", "dist", ".scratch", "htmlcov",
})

#: 需要按**前缀**跳过的目录名（数量会随会话增长，无法逐个列举）。
SKIP_DIR_PREFIXES = (".pytest_",)

#: 兼容旧名（本文件内部历史引用）。
_SKIP_DIRS = SKIP_DIRS

_BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__builtins__", "__spec__",
}


def _add_target(target, names: set) -> None:
    """把一个赋值目标里出现的所有 `Name` 记进 `names`（递归解包元组/列表/星号）。"""
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for e in target.elts:
            _add_target(e, names)
    elif isinstance(target, ast.Starred):
        _add_target(target.value, names)


def _add_params(args, names: set) -> None:
    for a in (list(getattr(args, "posonlyargs", [])) + list(args.args)
              + list(args.kwonlyargs)):
        names.add(a.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)


def module_bindings(tree: ast.AST) -> set:
    """收集「能绑定名字」的全部构造（不区分作用域 —— 本工具宁可漏报不误报）。"""
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            _add_params(node.args, names)
        elif isinstance(node, ast.Lambda):
            _add_params(node.args, names)
        elif isinstance(node, ast.comprehension):
            _add_target(node.target, names)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                _add_target(t, names)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            # ⚠ 绑定在 `items[i].optional_vars`，不是 `node.optional_vars`。
            for item in node.items:
                if item.optional_vars is not None:
                    _add_target(item.optional_vars, names)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor,
                               ast.NamedExpr)):
            tgt = getattr(node, "target", None)
            if tgt is not None:
                _add_target(tgt, names)
    return names


def collect_used(tree: ast.AST) -> set:
    """收集 `Name(Load)`，剔除 **f-string 格式说明符**子树（已知误报源）。"""
    skip: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FormattedValue) and node.format_spec is not None:
            skip |= {id(s) for s in ast.walk(node.format_spec)}
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and id(n) not in skip}


def scan_file(path: str) -> "tuple[list, str | None]":
    """扫描单个文件。返回 (可疑名列表, 错误信息)。"""
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except OSError as exc:
        return [], f"读取失败：{exc}"
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [], f"语法错误：{exc}"
    missing = sorted(collect_used(tree) - module_bindings(tree) - _BUILTINS)
    return missing, None


def _all_py(root: str) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS
                       and not d.startswith(SKIP_DIR_PREFIXES)]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(out)


def main(argv: "list | None" = None) -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--all" in argv:
        targets = _all_py(_ROOT)
    elif argv:
        targets = [a for a in argv if not a.startswith("-")]
    else:
        targets = list(DEFAULT_TARGETS)

    bad = 0
    print("=" * 72)
    for rel in targets:
        p = rel if os.path.isabs(rel) else os.path.join(_ROOT, rel)
        if not os.path.exists(p):
            print(f"[跳过] {rel}（不存在）")
            continue
        missing, err = scan_file(p)
        if err:
            print(f"{rel:34} ⚠ {err}")
            bad += 1
            continue
        if missing:
            bad += 1
            print(f"{rel:34} ⚠ {len(missing)} 个可疑名")
            for m in missing:
                print(f"      · {m}")
        else:
            print(f"{rel:34} ✅ 无未定义名")
    print("=" * 72)
    if bad:
        print(f"发现 {bad} 个文件含可疑名（请人工确认：可能是真缺陷，也可能是本工具的误报）。")
        return 1
    print("全部通过：未发现「引用了但从未定义」的名字。")
    return 0


__all__ = ["DEFAULT_TARGETS", "collect_used", "module_bindings", "scan_file", "main"]


def _selftest() -> None:
    """工具自检（纯内存）：确认扫描器**有区分力** —— 能抓真正例、且不误报两个已知模式。

    为什么必须自检：扫描器自身首版就踩过两个误报源（f-string 格式说明符、`with ... as`
    的绑定位置）。**一个不会误报也不会漏报的扫描器才值得信任**，而这两点只能靠用例固定。
    """
    probe = (
        "import os\n"
        "def f(a):\n"
        "    b = a + 1\n"
        "    with open('x') as fh:\n"
        "        y = fh.read()\n"
        "    print(f'{b:.1f}', fh, os, y)\n"
        "    return never_defined_here\n"
    )
    tree = ast.parse(probe)
    miss = sorted(collect_used(tree) - module_bindings(tree) - _BUILTINS)
    assert miss == ["never_defined_here"], f"自检失败：{miss}"
    print("undef_scan.py 自检通过：能抓出真正例，且不误报 f-string 与 with-as。")


if __name__ == "__main__":
    # ⚠ 阶段 147 订正：**不要再写 `raise SystemExit(0)`**。
    #   首版在自检后直接 `raise SystemExit(0)` ⇒ `main()` 成为**不可达的死代码**，
    #   命令行参数（`--all` / 指定文件）**完全失效**、却没有任何症状
    #   （因为它照样打印「自检通过」并以 0 退出）—— 属「死代码 + 静默失效」族。
    #   现改为：先自检，再**真正运行** `main()`。
    #   于是 `python scripts/undef_scan.py` 本身就是一次**真实自检**
    #   （扫描默认清单，发现未定义名即非零退出），而不只是打印一句话。
    _selftest()
    raise SystemExit(main())
