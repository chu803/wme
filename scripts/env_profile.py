# -*- coding: utf-8 -*-
"""阶段 361 · 环境画像：这个解释器量的到底是「哪个环境」。

## 为什么需要（CI 红 8 天里最贵的一课）

门禁家族有条隐含规则：**用哪个解释器启动入口，门禁量的就是哪个环境**
（`run_all_checks` 的步骤一律用 `sys.executable`）。但入口**从不说出这是哪个环境**
⇒ 本地跑的是「装了 torch / PyMuPDF / easyocr 的完整开发环境」，
CI 跑的是「只装 requirements-min + dev 的精简环境」——
两份代码路径不一样，于是「本地 3400 项全绿」和「CI 红」可以**同时为真**（阶段 347/351/358/360 四次都是这样）。

⇒ 把「这是哪个环境」从**读者的脑补**变成入口**开头就打印的事实**，
并给出可执行的下一步（去跑 CI 等价探针 / 建等价 venv）。

## 判据（只认两份清单，不认回忆）

- `CI_REQUIRED`：CI 那两份清单合起来**必须**能给到的东西 ⇒ 缺一个就说明
  这个解释器**根本不是 CI 那种环境**（连"能不能跑"都谈不上）。
- `CI_FORBIDDEN`：CI **刻意不装**的可选增强（torch / PyMuPDF / easyocr）⇒ 装了就意味着
  本地会执行 CI 永远不会执行的代码分支。**这一侧才是「本地绿、CI 红」的成因。**

## 探测方式：走元数据，**不走 `find_spec`、也不真的 import**

`scripts/ci_env_probe.py` 靠钩 `find_spec` 模拟「模块不存在」，而本脚本要在被它包着的运行里工作；
用 `find_spec` 判定 = 与假点共用一层，会在收集期自己炸（阶段 358 的续，LESSONS #334）。
`importlib.metadata.packages_distributions()` 只读已装包的元数据 ⇒ 不导入被测模块、不碰 `find_spec`、
毫秒级，且对「本机装了但探针模拟成缺失」这种情况**如实报告本机**。

## 用法与退出码（与门禁家族一致）

    python scripts/env_profile.py                  # 画像当前解释器
    python scripts/env_profile.py --as-if cv2,gradio,numpy,PIL,pytest   # 演示/自检：按给定集合判定
    python scripts/env_profile.py --json

    0 = 与 CI 等价（必装齐、可选增强都不在）⇒ 这个环境的入口结果**就是** CI 的结果
    1 = CI 必装的都缺 ⇒ 这个解释器不该用来验 CI 关心的事（质量失败）
    2 = 完整开发环境（多了 CI 不装的包）⇒ 结果不能直接外推给 CI（**告知**，不是失败）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from importlib import metadata

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    # 直接 `python scripts/env_profile.py` 时 sys.path[0] 是 scripts/ ⇒ `import scripts.x` 会炸；
    # 与 ci_env_probe.py 同款处理（入口 `python -m` 调用则本来就对）。
    sys.path.insert(0, _ROOT)

#: CI（`requirements-min.txt` + `requirements-dev.txt`）**应当**能提供的顶层模块。
#: 用 import 名（不是分发名）——判的是「代码能不能 import 到它」，分发名在各家镜像/历史里改名频繁。
CI_REQUIRED = ("cv2", "gradio", "numpy", "PIL", "pytest")

#: CI **刻意不装**的可选增强：import 名 → 分发名（人话里要说分发名，用户是照它 `pip install` 的）。
CI_FORBIDDEN = {"torch": "torch", "fitz": "PyMuPDF", "easyocr": "easyocr"}


def installed_modules() -> set:
    """当前解释器里**已装包**暴露的顶层模块名集合（纯元数据，不 import、不用 `find_spec`）。"""
    try:
        return set(metadata.packages_distributions())
    except Exception:  # 元数据损坏/极老的解释器：宁可说「读不出来」，也不要装作「什么都装了」
        return set()


def classify(present: set) -> dict:
    """按「CI 必装 / CI 不装」两套名单给环境定性（纯函数，便于断言）。

    返回 `{"tag": "ci_equivalent" | "full_dev" | "not_ci_env", "missing": [...], "extra": [...]}`。
    """
    missing = [m for m in CI_REQUIRED if m not in present]
    extra = sorted(m for m in CI_FORBIDDEN if m in present)
    if missing:
        tag = "not_ci_env"
    elif extra:
        tag = "full_dev"
    else:
        tag = "ci_equivalent"
    return {"tag": tag, "missing": missing, "extra": extra}


#: tag → (人话结论, 下一步该干什么)。文案必须带「所以……」，光说"是什么"不 actionable。
_VERDICT = {
    "ci_equivalent": (
        "与 CI 等价（必装齐，且 CI 不装的可选增强都不在）"
        " ⇒ 这个解释器跑出来的入口结果**就是** runner 会看到的结果。",
        "",
    ),
    "full_dev": (
        "完整开发环境：本机多装了 {extra} ⇒ 本地会走 CI **永远不会走**的代码分支"
        " ⇒ 「本地全绿」**不能**外推成「CI 绿」。",
        "两条路都能把这一维变成机器判定：① `python scripts/ci_env_probe.py`"
        "（导入层屏蔽那些包，约 10 分钟）；② 用只装 min+dev 的 3.11 等价 venv "
        "跑同一条 `scripts/run_all_checks.py`（约 7 分钟，建法见 docs/360）。",
    ),
    "not_ci_env": (
        "连 CI 必装的东西都缺（缺 {missing}）⇒ 这个解释器**不是**用来验 CI 关心的事的环境。",
        "处置：换 `python -m venv` 出来的干净环境，按 requirements-min.txt + requirements-dev.txt 装。",
    ),
}


def verdict(p: dict) -> tuple:
    """把 `classify()` 的结果翻成人话（纯函数）。"""
    head, next_step = _VERDICT[p["tag"]]
    extra_names = ", ".join(p["extra"]) or "(无)"
    missing_names = ", ".join(p["missing"]) or "(无)"
    return (
        head.format(extra=extra_names, missing=missing_names),
        next_step,
    )


def profile(present: set) -> dict:
    """完整画像（解释器、版本、已装的 CI 名单、判定与人话）。"""
    p = classify(present)
    head, next_step = verdict(p)
    return {
        "executable": sys.executable,
        "python": sys.version.split()[0],
        "present_required": [m for m in CI_REQUIRED if m in present],
        **p,
        "summary": head,
        "next_step": next_step,
    }


def _rc_for(tag: str) -> int:
    # 家族口径：0 等价 / 1 环境不对（失败）/ 2 本机更强（告知）
    return {"ci_equivalent": 0, "not_ci_env": 1, "full_dev": 2}[tag]


def main(argv: "list | None" = None) -> int:
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    ap = argparse.ArgumentParser(description="环境画像：当前解释器是不是 CI 那种环境")
    ap.add_argument("--json", action="store_true", help="输出机器可读的画像")
    ap.add_argument("--as-if", default=None, metavar="MODULES",
                    help="按给定的已装模块名列表（逗号分隔）判定，**不读本机**——"
                         "供文档演示与守卫断言三种判定用")
    ns = ap.parse_args(argv)

    if ns.as_if is not None:
        present = {m.strip() for m in ns.as_if.split(",") if m.strip()}
    else:
        present = installed_modules()

    prof = profile(present)
    if ns.json:
        print(json.dumps(prof, ensure_ascii=False))
    else:
        print("=" * 78)
        print("环境画像 run_all_checks · 解释器")
        print(f"  解释器   : {prof['executable']}  (Python {prof['python']})")
        print(f"  CI 必装  : {' '.join(CI_REQUIRED)}")
        print(f"  本机有   : {', '.join(prof['present_required']) or '(无)'}")
        print(f"  CI 不装  : {', '.join(sorted(CI_FORBIDDEN))}")
        print(f"  本机多装 : {', '.join(prof['extra']) or '(无)'}")
        print(f"  判定     : [{prof['tag']}]")
        print(f"  结论     : {prof['summary']}")
        if prof["next_step"]:
            print(f"  下一步   : {prof['next_step']}")
        print("=" * 78)
    return _rc_for(prof["tag"])


if __name__ == "__main__":
    raise SystemExit(main())
