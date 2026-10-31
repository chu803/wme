# -*- coding: utf-8 -*-
"""阶段 361 · 环境画像必须**说得出、判得准、且与 CI 清单同源**。

入口的步骤全用 `sys.executable` ⇒ 解释器决定「门禁量的是哪个环境」，
而这句话以前**没人打印**：本地跑的是装了 torch/PyMuPDF/easyocr 的完整环境，
CI 跑的是只装 min+dev 的精简环境 ⇒ 「本地全绿」与「CI 红」同时为真，
连续四次（347 / 351 / 358 / 360）都栽在同一处**沉默**上。

本守卫钉四件事：
  ① 三种判定（等价 / 完整开发环境 / 连必装都缺）都能被构造出来，且**退出码符合家族口径**；
  ② 文案要点名「本机多装了哪些包」并给出下一步——只说"是什么"不 actionable 的报告等于没报告；
  ③ 探测**不许走 `find_spec`**（LESSONS #334：`ci_env_probe.py` 钩的正是它，
     用它判定 = 与假点共用一层，会在被模拟的环境里自己炸）；
  ④ `CI_REQUIRED` 名单必须与 `requirements-min/dev` **同源**（清单改了名字而画像没跟上 ⇒ 画像在说谎）。
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from scripts import env_profile as ep  # noqa: E402

#: 分发名（用户 `pip install` 用的名字）↔ 画像里的 import 名。
_DIST_FOR_REQUIRED = {
    "cv2": "opencv-python-headless",
    "gradio": "gradio",
    "numpy": "numpy",
    "PIL": "Pillow",
    "pytest": "pytest",
}


def _run(args):
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_classify_covers_the_three_verdicts():
    """① 判定是纯函数：三种环境各归各位，**没有第四种**。"""
    required = set(ep.CI_REQUIRED)
    assert ep.classify(required)["tag"] == "ci_equivalent"
    assert ep.classify(required | {"torch"})["tag"] == "full_dev"
    assert ep.classify(required | {"torch", "fitz", "easyocr"})["extra"] == ["easyocr", "fitz", "torch"]
    assert ep.classify(set())["tag"] == "not_ci_env"
    # 「必装都缺」优先于「多装了东西」：那种环境谈不上等价
    assert ep.classify({"torch"})["tag"] == "not_ci_env"


def test_exit_codes_follow_the_gate_family():
    """①b 家族口径：0 等价 / 1 环境不对 / 2 本机更强（**告知**，不是失败）。"""
    assert ep._rc_for("ci_equivalent") == 0
    assert ep._rc_for("not_ci_env") == 1
    assert ep._rc_for("full_dev") == 2


def test_every_tag_has_a_human_verdict():
    """判定枚举与文案表**必须同源**：新增了 tag 而没写文案，入口会 KeyError。"""
    assert set(ep._VERDICT) == {"ci_equivalent", "full_dev", "not_ci_env"}, "tag 与文案表漂移"
    for tag, (head, _nxt) in ep._VERDICT.items():
        assert head.strip(), f"{tag} 的结论是空的"


def test_full_dev_text_names_the_extras_and_gives_next_step():
    """② 文案必须 actionable：点名多装的包，并给出**两条可执行的等价验证**。"""
    head, nxt = ep.verdict(ep.classify(set(ep.CI_REQUIRED) | {"torch"}))
    assert "torch" in head, f"没点名多装了谁：{head}"
    assert "不能" in head and "CI" in head, "没说清「本地绿 ≠ CI 绿」这条因果"
    assert "ci_env_probe" in nxt and "run_all_checks" in nxt, f"下一步不 actionable：{nxt}"


def test_probe_does_not_use_find_spec():
    """③ 探测层必须错开模拟层（LESSONS #334）。

    ⚠ 判据走 **AST** 而不是文本包含：本文件的说明里**故意**提到 `find_spec`（讲的就是"别用它"），
    文本判据会把"讲道理的那句话"当成违规——第一版就是这么误报的（同族教训：#316 的 AST 判据）。
    """
    tree = ast.parse((_ROOT / "scripts" / "env_profile.py").read_text(encoding="utf-8"))
    used = [n.lineno for n in ast.walk(tree)
            if (isinstance(n, ast.Attribute) and n.attr == "find_spec")
            or (isinstance(n, ast.Name) and n.id == "find_spec")]
    assert not used, (
        f"画像的代码里真的调用了 find_spec（第 {used} 行）⇒ 在 ci_env_probe 的模拟环境里会自己抛 "
        "ModuleNotFoundError，探测器比被测行为先死（#334 原样复发）"
    )
    src = (_ROOT / "scripts" / "env_profile.py").read_text(encoding="utf-8")
    assert "packages_distributions" in src, "应走 importlib.metadata（不 import、不钩 finder）"


def _requirements_text():
    out = []
    for name in ("requirements-min.txt", "requirements-dev.txt"):
        p = _ROOT / name
        assert p.is_file(), f"CI 清单缺失：{name}"
        out.append(p.read_text(encoding="utf-8"))
    return out


def test_ci_required_list_is_derived_from_the_real_requirements():
    """④ `CI_REQUIRED` 的分发名必须真的写在 CI 装的那两份清单里（清单改名而画像不跟 ⇒ 画像说谎）。"""
    text = "".join(_requirements_text())
    for mod, dist in _DIST_FOR_REQUIRED.items():
        assert mod in ep.CI_REQUIRED, f"画像漏了必装模块 {mod}"
        assert dist.lower() in text.lower(), (
            f"{dist} 不在 CI 实际安装的两份清单里 ⇒ `CI_REQUIRED` 与 requirements 已经漂移"
        )


def test_as_if_reaches_all_three_verdicts(capsys):
    """①c `--as-if` 让三种判定**可确定性复现**（不依赖本机装了什么）。"""
    required = ",".join(ep.CI_REQUIRED)
    assert ep.main(["--as-if", required]) == 0
    assert ep.main(["--as-if", required + ",torch"]) == 2
    assert ep.main(["--as-if", "torch"]) == 1
    out = capsys.readouterr().out
    assert "ci_equivalent" in out and "full_dev" in out and "not_ci_env" in out


def test_entry_header_declares_the_environment():
    """⑤ 入口开头必须自报环境（`--list` 也要报 ⇒ 零成本就能看到）。"""
    rc, out = _run(["scripts/run_all_checks.py", "--list"])
    assert rc == 2, f"--list 约定退出码 2，实得 {rc}：{out[-400:]}"
    assert "环境画像" in out, "入口没有自报环境 ⇒ 又一次「本地绿 CI 红」没人看见"
    assert sys.executable in out, f"画像里的解释器不是当前这个：{out[:600]}"
    assert any(tag in out for tag in ("ci_equivalent", "full_dev", "not_ci_env")), "只打印了标题没有判定"


def test_env_profile_cli_agrees_with_the_header(capsys):
    """脚本单独跑与入口内嵌的判定必须**同源**（同 tag ⇒ 两处不会各说各话）。"""
    rc, out = _run(["scripts/env_profile.py", "--json"])
    prof = json.loads(out.strip().splitlines()[-1])
    assert prof["tag"] == ep.classify(ep.installed_modules())["tag"], (
        f"子进程与进程内判定不一致：{prof['tag']}"
    )
    assert rc in (0, 1, 2)
