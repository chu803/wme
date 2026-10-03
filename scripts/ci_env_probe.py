# -*- coding: utf-8 -*-
"""阶段 206 · CI 环境等价性探针：在本地**模拟 CI 的依赖缺失**，提前发现「本地绿、CI 红」。

## 为什么需要

`.github/workflows/ci.yml` 只装 `requirements-min.txt` + `requirements-dev.txt`
（**刻意不含 torch / PyMuPDF**），而开发机/沙箱是**完整环境**。
两者差异会掩盖一批真实缺陷：测试在本地绿、在 CI 上前提不同（缺可选依赖）⇒ 直接失败。
**而 CI 只有在仓库推到远端后才会跑** ⇒ 这个差异可能长期无人发现。

本探针在**导入层**屏蔽 CI 提供不了的模块，并把模型目录指向空目录
（真实 CI 是全新 clone，权重不入库 ⇒ `is_model_available()` 为 False），
从而在本地复现 CI 的依赖版图。

## 用法

    python scripts/ci_env_probe.py                 # 跑 CI 等价子集（含真实执行，约 10 分钟）
    python scripts/ci_env_probe.py --check         # 只自检探针本身（毫秒级，零副作用）
    python scripts/ci_env_probe.py --list-blocked  # 打印被屏蔽的模块

## 退出码（与门禁家族一致）

    0 = CI 等价环境下全部通过
    1 = 有失败（**这些失败就是推远端后 CI 会红的那些**）
    2 = 跳过（依赖缺失/环境不满足，如 pytest 不可用）

## 诚实边界

本探针**只**模拟「依赖缺失」这一个维度。CI 与本地仍可能因
**操作系统（ubuntu vs windows）**、**Python 版本（3.11 vs 3.13）**、
**路径分隔符**、**文件系统大小写敏感**等差异而不同。它把「依赖版图」这一
最常被忽视的维度变成可本地验证，但不声称等价于真实 CI。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: CI（`requirements-min` + `requirements-dev`）**真正提供不了**的模块。
#:
#: ⚠ 判据：只列「CI 明确不装、且**不是**任何已装包的传递依赖」的模块。
#: 反例教训（本阶段实测）：首版把 `yaml` / `pandas` 也列入，结果 25 个测试文件收集失败
#: ——因为 `yaml` 是 gradio→huggingface_hub 的**传递依赖**，`pip install gradio`
#: 会自动装上。**屏蔽传递依赖 = 模拟错了环境**，会制造假红。
BLOCKED_MODULES = (
    "torch", "torchvision",     # min 清单刻意排除；gradio 不依赖它们
    "fitz", "pymupdf",          # 文档声明为可选增强
    "easyocr",                  # min 不装（走 OpenCV 降级）
    "onnxruntime",              # 未在任何清单中
)

#: 与 CI 一致：CI 只安装以下两个清单（供 `--check` 自省与文档一致性核对）。
CI_REQUIREMENTS = ("requirements-min.txt", "requirements-dev.txt")


def is_blocked(module: str) -> bool:
    """该顶层模块是否应被屏蔽（纯函数，便于测试）。"""
    return module.split(".")[0] in BLOCKED_MODULES


class _CiEnvBlocker:
    """`sys.meta_path` finder：对被屏蔽模块抛 ModuleNotFoundError。"""

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        if is_blocked(fullname):
            raise ModuleNotFoundError(
                f"[CI 环境模拟] {fullname.split('.')[0]} 在 CI"
                f"（{' + '.join(CI_REQUIREMENTS)}）中不存在",
                name=fullname,
            )
        return None


def install_blocker() -> None:
    """装上屏蔽器，并把已加载的被屏蔽模块从 sys.modules 移除（保证后续 import 会命中）。"""
    for name in [m for m in list(sys.modules) if is_blocked(m)]:
        del sys.modules[name]
    if not any(isinstance(f, _CiEnvBlocker) for f in sys.meta_path):
        sys.meta_path.insert(0, _CiEnvBlocker())


def point_model_dir_to_empty() -> str:
    """把 `WME_MODEL_DIR` 指向空目录（模拟全新 clone：权重不入库）。

    必须在 `import config` **之前**调用（config 在导入期读环境变量）。
    返回该目录路径。
    """
    d = os.path.join(tempfile.gettempdir(), "wme_ci_env_probe_empty_models")
    os.makedirs(d, exist_ok=True)
    os.environ["WME_MODEL_DIR"] = d
    return d


def build_pytest_args(extra=None):
    """构造 pytest 参数（纯函数，便于测试）：CI 等价子集 + 唯一 basetemp。"""
    import time
    args = [
        "-p", "no:cacheprovider",
        "--basetemp", os.path.join(_ROOT, f".pytest_ciprobe_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"),
        "-m", "not torch_required and not network",
        "-q", "--no-header", "--tb=short", "-rfE",
    ]
    if extra:
        args += list(extra)
    return args


def main(argv=None) -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。本脚本打印 `⇒` 等非
    # GBK 字符，若不加固，在 GBK 管道下会抛 UnicodeEncodeError 使退出码变 1（假红）——
    # 对**门禁类脚本**而言，「输出内容」决定「退出码」是致命的（`tests/test_scripts_console_encoding.py` 元守卫会拦）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    ap = argparse.ArgumentParser(description="CI 环境等价性探针（模拟 CI 的依赖缺失）")
    ap.add_argument("--check", action="store_true", help="只自检探针本身，不跑测试")
    ap.add_argument("--list-blocked", action="store_true", help="打印被屏蔽的模块")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, help="透传给 pytest 的额外参数")
    ns = ap.parse_args(argv)

    if ns.list_blocked:
        print("被屏蔽的模块（CI 提供不了）：")
        for m in BLOCKED_MODULES:
            print(f"  - {m}")
        return 0

    if ns.check:
        # 零副作用自检：屏蔽器装得上、纯函数判定正确、清单与 CI 一致。
        install_blocker()
        assert is_blocked("torch") and is_blocked("torch.cuda") and is_blocked("fitz")
        assert not is_blocked("numpy") and not is_blocked("gradio")
        assert "torch" not in sys.modules, "已加载的 torch 应被移出 sys.modules"
        missing = [r for r in CI_REQUIREMENTS if not os.path.isfile(os.path.join(_ROOT, r))]
        assert not missing, f"CI 清单缺失：{missing}"
        print("自检通过：屏蔽器可用、判定正确、CI 清单存在。")
        return 0

    # 正式模式：先改模型目录（config 导入前），再装屏蔽器，最后跑 pytest。
    empty = point_model_dir_to_empty()
    install_blocker()
    print("=" * 74)
    print("CI 环境等价性探针")
    print(f"  屏蔽模块   : {', '.join(BLOCKED_MODULES)}")
    print(f"  模型目录   : {empty}（空 ⇒ 模拟全新 clone）")
    print(f"  marker 子集: not torch_required and not network")
    print("=" * 74)
    try:
        import pytest
    except ImportError:
        print("pytest 不可用 ⇒ 跳过（退出码 2）")
        return 2
    rc = pytest.main(build_pytest_args(ns.extra))
    print("=" * 74)
    if rc == 0:
        print("结论：CI 等价环境下**全部通过** ⇒ 推远端后 CI 应绿（依赖维度）。")
    else:
        print("结论：CI 等价环境下**有失败** ⇒ 多半是推远端后 CI 会红的那些。")
        print("      处置：给缺可选依赖的用例加 `pytest.importorskip(...)` 或 `skipif`，")
        print("            而不是给 CI 加依赖（CI 刻意保持轻量，见 ci.yml 说明）。")
        print("      ⚠ 但先排除一种**沙箱假红**：本机受限环境对删除操作有「会话级闩锁」")
        print("        （一旦触发，此后任何删除都 `SystemExit(1)`）⇒ 涉及删除/日志轮转的")
        print("        用例会以 `SystemExit: 1` 失败，这是**本机限制、非 CI 问题**。")
        print("        判据：报错里出现 `SystemExit: 1` 而行号指向删除/rotate 逻辑。")
    print("=" * 74)
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
