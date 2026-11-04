# -*- coding: utf-8 -*-
"""阶段 376 · README 里的图片必须**真实存在**，且指向仓库资源的链接必须是绝对 raw URL。

起因（实测）：§2.2 的四张「四联对比图」写着 `docs/assets/demo_*_opencv.png`，
而那四个文件**从未入库** ⇒ GitHub 上就是破图；更隐蔽的是——README 同时是 PyPI 项目页的正文，
**相对路径在 PyPI 上解析不到仓库**（PyPI 只渲染 Markdown，不会把相对链接重写成 GitHub 地址）。
于是「展示能力」的那一段对两类读者都是坏的。

本文件锁两层：
  ① 所有 `![alt](url)`：指向本仓库 `docs/assets/` 的，必须是绝对 raw URL，且文件存在；
  ② 解析器自带反例：注入一条不存在的文件引用必须被抓到（否则守卫是恒绿的装饰）。
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_RAW_PREFIX = "https://raw.githubusercontent.com/chu803/wme/main/"
_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def iter_image_refs(text: str):
    return [m.group(1) for m in _IMG_RE.finditer(text)]


def problems_for(refs, exists=os.path.exists):
    """返回 (missing, relative) 两个列表：缺失的文件；需要改成绝对 URL 的相对引用。"""
    missing, relative = [], []
    for url in refs:
        if url.startswith(_RAW_PREFIX):
            rel = os.path.join(_ROOT, *url[len(_RAW_PREFIX):].split("/"))
            if not exists(rel):
                missing.append(url)
        elif url.startswith(("http://", "https://")):
            continue
        else:
            # 相对路径：GitHub 能看，PyPI 看不到 ⇒ 一律要求绝对 raw URL。
            relative.append(url)
            rel = os.path.join(_ROOT, *url.split("/"))
            if not exists(rel):
                missing.append(url)
    return missing, relative


def test_readme_images_exist_and_are_absolute():
    text = open(os.path.join(_ROOT, "README.md"), encoding="utf-8").read()
    refs = iter_image_refs(text)
    assert refs, "README 里一张图都没解析到 ⇒ 正则或文档结构已变，本守卫在空转"
    missing, relative = problems_for(refs)
    assert not missing, f"README 引用了不存在的图片（GitHub/PyPI 上都是破图）：{missing}"
    assert not relative, (
        f"README 里有相对路径图片（PyPI 项目页解析不到仓库，需改成 {_RAW_PREFIX}…）：{relative}")


def test_parser_catches_a_missing_file():
    """反例：注入一条指向不存在文件的绝对 raw URL ⇒ 必须进 missing。"""
    fake = _RAW_PREFIX + "docs/assets/__definitely_missing__.png"
    missing, relative = problems_for([fake], exists=lambda _p: False)
    assert missing == [fake] and not relative, (missing, relative)
    ok = _RAW_PREFIX + "docs/assets/bench_grid_seed1.png"
    missing2, relative2 = problems_for([ok], exists=lambda _p: True)
    assert missing2 == [] and relative2 == []
    rel = "docs/assets/bench_grid_seed1.png"
    _m3, r3 = problems_for([rel], exists=lambda _p: True)
    assert r3 == [rel], "相对路径没被要求改成绝对 URL"
