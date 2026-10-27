# -*- coding: utf-8 -*-
"""阶段 347 · 安装清单里不许出现「只有本机装得到」的版本。

背景：2026-10-18 的提交 `0cb20e3`（"对齐依赖清单到已验证 venv 版本"）把 `requirements-min.txt`
的 6 个核心包钉成**本机 venv 的精确版本**（numpy 2.5.1 / opencv-python-headless 5.0.0.93 /
Pillow 12.3.0 / scikit-image 0.26.0 / scipy 1.18.1 / pydantic 2.13.4）。

代价（CI 上整整 8 天全红）：那些版本**不是 PyPI 稳定发行版**——本机 opencv 5.0.0.93 是从 CI 源码树
（`D:\a\opencv-python`）构建的，runner 上按名字+版本号根本装不到 ⇒ `pip install` 直接失败
⇒ CI 每次都红在 26~92 秒（跑不完入口步）。而 `requirements-min.txt` 同时是**README 让用户装的清单**
⇒ 照着装的用户同样装不上。

本守卫把「这条���不会再犯」变成机器判定：

* 精确 pin（`==`）**只允许**出现在「CI 装成功过」的集合里（#14 绿那次，2026-10-18）；
* 其余包**一律用区间**；
* 区间的下界不得高于那个装成功过的版本（保证 CI 仍能装到已知可用的组合）；
* 本机验证过的精确组合记在 `requirements-verified.txt`，且必须被安装清单的上界覆盖
  （否则「本机那套组合」会被清单悄悄排除）。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: CI 上**装成功过**的精确 pin（来自 CI #14 / commit ef64fd7，2026-10-18 绿的那次）。
#: 这是「可解析」的**唯一实证来源**——没在 CI 上装成功过的东西，就不许写成精确 pin。
CI_PROVEN: dict = {
    "opencv-python": "4.10.0.84",     # #14 用的是非 headless 版；现清单用 headless（区间 >=4.10）
    "numpy": "1.26.4",
    "Pillow": "10.4.0",
    "scikit-image": "0.24.0",
    "scipy": "1.14.1",
    "pydantic": "2.9.2",
    "requests": "2.32.3",
    "tqdm": "4.66.5",
    "urllib3": "2.2.3",
    "certifi": "2024.8.30",
    "psutil": "6.0.0",
    "py-cpuinfo": "9.0.0",
    "colorama": "0.4.6",
    "python-dotenv": "1.0.1",
}

_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)\s*==\s*([^\s;#]+)")
_RANGE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*(>=|>)\s*([^\s,;#]+)\s*(?:,\s*<\s*([^\s;#]+))?")


def _req_lines(path: pathlib.Path) -> list:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def test_exact_pins_must_be_ci_proven():
    """精确 pin 只能在「CI 装成功过」的集合里。

    ⚠ 判据**只管 CI 真正安装的那两份清单**（`requirements-min.txt` / `requirements-dev.txt`）。
    `requirements.txt` 与 `requirements-gpu.txt` 里的 `torch==…+cu128` 来自 PyTorch 自己的
    index（不是 PyPI），CI 也不装它们 ⇒ 不能拿本判据去卡它们（那是误报）。
    """
    offenders = []
    for rel in ("requirements-min.txt", "requirements-dev.txt"):
        p = ROOT / rel
        if not p.exists():
            continue
        for line in _req_lines(p):
            m = _PIN.match(line)
            if not m:
                continue
            name, ver = _norm(m.group(1)), m.group(2)
            if name not in CI_PROVEN:
                offenders.append(f"{rel}: {name}=={ver}（不在 CI 装成功过的集合里）")
            elif ver != CI_PROVEN[name] and name != "opencv-python-headless":
                offenders.append(
                    f"{rel}: {name}=={ver}（CI 装成功过的是 {CI_PROVEN[name]}）")
    assert not offenders, (
        "清单里出现了「CI 从没装成功过」的精确版本 ⇒ runner 上大概率装不到，"
        f"CI 会在 pip install 那一秒就红（而且照 README 装包的用户也装不上）：{offenders}")


def test_ranges_keep_a_proven_lower_bound():
    """区间下界不得高于「CI 装成功过」的版本 ⇒ CI 仍能装到已知可用的组合。

    只判 `requirements-min.txt`（CI 装的那份）：`opencv-python-headless` 与 `opencv-python`
    同版本线，故 `>=4.10` 与已证明的 `opencv-python==4.10.0.84` 等价。
    """
    offenders = []
    p = ROOT / "requirements-min.txt"
    if p.exists():
        for line in _req_lines(p):
            m = _RANGE.match(line)
            if not m:
                continue
            name, lo = _norm(m.group(1)), m.group(3)
            if name in CI_PROVEN:
                prov = CI_PROVEN[name]
                if tuple(int(x) for x in lo.split(".")) > tuple(int(x) for x in prov.split(".")):
                    offenders.append(f"{name}>={lo} 高于装成功过的 {prov}")
    assert not offenders, f"区间下界高于已知可用版本：{offenders}"


def test_verified_combination_is_recorded_and_covered():
    """本机验证过的组合必须留档，且被安装清单的上界覆盖（否则清单会把「我们测过的那套」排除）。"""
    vpath = ROOT / "requirements-verified.txt"
    assert vpath.exists(), "缺少 requirements-verified.txt ⇒ 阶段 297 的验证结论会丢失"
    min_lines = {(_norm(m.group(1)), m.group(3), m.group(4))
                 for line in _req_lines(ROOT / "requirements-min.txt")
                 for m in [_RANGE.match(line)] if m}

    missing = []
    for line in _req_lines(vpath):
        m = _PIN.match(line)
        if not m:
            continue
        name, ver = _norm(m.group(1)), m.group(2)
        hit = [r for r in min_lines if r[0] == name]
        if not hit:
            missing.append(f"{name}=={ver}（安装清单里没有这个包）")
            continue
        _n, lo, hi = hit[0]
        if hi and tuple(int(x) for x in ver.split(".")) > tuple(int(x) for x in hi.split(".")):
            missing.append(f"{name}=={ver} 超出安装清单上界 {hi}")
    assert not missing, f"本机验证组合与安装清单对不上：{missing}"


def test_dev_requirements_use_ranges_only():
    """测试依赖同理：CI 上装得到才算数。"""
    offenders = []
    p = ROOT / "requirements-dev.txt"
    if p.exists():
        for line in _req_lines(p):
            m = _PIN.match(line)
            if m and _norm(m.group(1)) not in CI_PROVEN:
                offenders.append(line)
    assert not offenders, f"requirements-dev.txt 里有 CI 没装成功过的精确 pin：{offenders}"
