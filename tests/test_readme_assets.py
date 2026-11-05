# -*- coding: utf-8 -*-
"""阶段 376 · README 里的图片必须**真在仓库里**，且指向仓库资源的链接必须是绝对 raw URL。

起因（实测两层）：
  ① §2.2 的四张「四联对比图」曾指向 `docs/assets/demo_*_opencv.png`，而那四个文件**从未入库**
     ⇒ GitHub 上就是破图（没有任何守卫会失败，所以挂了很久）；
  ② 更隐蔽：README 同时是 **PyPI 项目页正文**，PyPI 只渲染 Markdown、**相对路径解析不到仓库**
     ⇒ 就算文件在，PyPI 上也加载不出来。

⚠ 为什么判据是「git 已跟踪」而不是「磁盘上存在」：本地可能存在**未入库/被忽略**的产物
（本仓 `docs/assets` 同时有"生成的图"和"提交的图"两类），只看磁盘会把 404 放过去。

本文件锁三层：
  ① 所有 `![alt](url)` 指向本仓库 `docs/assets/` 的：必须是绝对 raw URL、文件必须存在、**必须 git 已跟踪**；
  ② 相对路径一律判红（并说明 PyPI 原因）；
  ③ 自带反例：注入不存在 / 未跟踪 / 相对路径三种，必须分别被抓到（否则守卫是恒绿的装饰）。
"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_RAW_PREFIX = "https://raw.githubusercontent.com/chu803/wme/main/"
_IMG_RE = __import__("re").compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def iter_image_refs(text: str):
    return [m.group(1) for m in _IMG_RE.finditer(text)]


def _is_tracked(rel_path: str) -> bool:
    r = subprocess.run(["git", "-C", _ROOT, "ls-files", "--error-unmatch", "--", rel_path],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30)
    return r.returncode == 0


def problems_for(refs, exists=os.path.exists, tracked=None):
    """返回 (missing, relative, untracked)。tracked 可注入，便于反例测试。"""
    tracked = tracked or _is_tracked
    missing, relative, untracked = [], [], []
    for url in refs:
        if url.startswith(_RAW_PREFIX):
            rel = url[len(_RAW_PREFIX):].replace("/", os.sep)
        elif url.startswith(("http://", "https://")):
            continue
        else:
            relative.append(url)
            rel = url.replace("/", os.sep)
        full = os.path.join(_ROOT, rel)
        if not exists(full):
            missing.append(url)
        elif not tracked(rel):
            untracked.append(url)
    return missing, relative, untracked


def test_readme_images_exist_and_are_absolute():
    text = open(os.path.join(_ROOT, "README.md"), encoding="utf-8").read()
    refs = iter_image_refs(text)
    assert refs, "README 里一张图都没解析到 ⇒ 正则或文档结构已变，本守卫在空转"
    missing, relative, untracked = problems_for(refs)
    assert not missing, f"README 引用了不存在的图片（GitHub/PyPI 上都是破图）：{missing}"
    assert not relative, (
        f"README 里有相对路径图片（PyPI 项目页解析不到仓库，需改成 {_RAW_PREFIX}…）：{relative}")
    assert not untracked, (
        f"README 引用的图片在磁盘上存在但**未入库**（远端 404）：{untracked}")


def test_parser_catches_all_three_failure_modes():
    """反例：缺失 / 未跟踪 / 相对路径，三种都必须被抓到。"""
    gone = _RAW_PREFIX + "docs/assets/__definitely_missing__.png"
    m, r, u = problems_for([gone], exists=lambda _p: False, tracked=lambda _p: True)
    assert m == [gone] and not r and not u, (m, r, u)

    local_only = _RAW_PREFIX + "docs/assets/bench_grid_seed1.png"
    m2, r2, u2 = problems_for([local_only], exists=lambda _p: True, tracked=lambda _p: False)
    assert not m2 and not r2 and u2 == [local_only], (m2, r2, u2)

    rel = "docs/assets/bench_grid_seed1.png"
    _m3, r3, _u3 = problems_for([rel], exists=lambda _p: True, tracked=lambda _p: True)
    assert r3 == [rel], "相对路径没被要求改成绝对 URL"

    ok = _RAW_PREFIX + "docs/assets/bench_grid_seed1.png"
    m4, r4, u4 = problems_for([ok], exists=lambda _p: True, tracked=lambda _p: True)
    assert not m4 and not r4 and not u4, (m4, r4, u4)
