# -*- coding: utf-8 -*-
"""阶段 196（E3）· docs 索引（docs/INDEX.md）防腐烂守卫。

为什么需要它：`docs/INDEX.md` 是一份**手写的**文档导航。手写清单最大的风险是
**腐烂**——新增了 `docs/NNN-*.md` 却忘了加进索引，索引会静默地越来越不准。
本守卫把「忘记登记」从**静默**变成**机械可发现**：磁盘上每个 `docs/*.md`
（除 `INDEX.md` 自身）都必须在 `INDEX.md` 正文里被**按文件名**引用，否则变红。

（同源经验：168/169 的 i18n 覆盖率守卫、203「改视野须重审清单」——清单类守卫
必须整文件跑、且以磁盘枚举为准，而非回忆。）

阶段 203 补：**判据必须双向**（LESSONS 185「判据不对称即 bug」）
----------------------------------------------------------------
原守卫只做**一个方向**：磁盘文档 → 是否被索引引用。
**反向无人守** ⇒ 文档被删除 / 改名后，索引里的引用会变成**死链**，
而不会有任何守卫变红（「索引指向空气」是静默腐烂的另一种形态）。
本阶段补 `test_every_index_reference_exists`：索引里每个 `*.md` 引用
都必须能解析到真实文件（按引用形态分别解析：含路径 ⇒ 相对仓库根；
裸文件名 ⇒ `docs/` 下）。
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _ROOT / "docs"
_INDEX = _DOCS / "INDEX.md"

#: 明确豁免的「占位符式引用」——它们**故意**不是真实文件，而是命名约定说明。
#: 每条都必须带理由，避免把豁免表变成藏死链的后门（LESSONS 203）。
_PLACEHOLDER_REFS = {
    # 说明「每日工作日志」的命名约定（YYYY-MM-DD 是格式占位，非具体文件）
    ".workbuddy/memory/YYYY-MM-DD.md",
}


def _index_text() -> str:
    return _INDEX.read_text(encoding="utf-8")


def test_docs_index_exists():
    assert _INDEX.is_file(), "docs/INDEX.md 不存在"


def test_every_doc_is_referenced_in_index():
    """磁盘上每个 docs/*.md（除 INDEX.md）都必须出现在 INDEX.md 正文中。"""
    text = _index_text()
    missing = []
    for p in sorted(_DOCS.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        if p.name not in text:
            missing.append(p.name)
    assert not missing, (
        "以下 docs/NNN-*.md 未被 docs/INDEX.md 引用（索引已腐烂）：\n  "
        + "\n  ".join(missing)
        + "\n处置：把新文档补进 docs/INDEX.md 的「全量文档目录」对应区间。"
    )


def test_index_has_optimization_checklist_section():
    """索引必须含「全产品优化清单执行索引」小节，且列出清单项与状态图例。"""
    text = _index_text()
    assert "全产品优化清单" in text
    assert "状态图例" in text
    # 抽查若干清单项锚点（避免整节被误删）
    for token in ("E2", "E3", "C5", "A4", "D2"):
        assert token in text, f"优化清单索引缺少清单项锚点：{token}"


# ---------------------------------------------------------------------------
# 阶段 203：反向检查（索引引用 → 文件必须存在）
# ---------------------------------------------------------------------------

def test_every_index_reference_exists():
    """索引里每个 `*.md` 引用都必须**解析到真实文件**（否则是死链）。

    为什么需要（LESSONS 185「判据不对称即 bug」）：`test_every_doc_is_referenced_in_index`
    只保证「磁盘 → 索引」，删/改文档名不会让任何守卫变红，索引里会**静默留下死链**。
    本用例补上「索引 → 磁盘」这一半，两个方向合起来才是完整判据。

    解析规则（两类引用形态）：
      · 含路径分隔符（如 `.workbuddy/memory/LESSONS.md`）⇒ 相对**仓库根**解析；
      · 裸文件名（如 `197-C5-Docker容器化.md`）⇒ `docs/` 下解析。
    """
    text = _index_text()
    refs = set(re.findall(r"`([^`\s]+\.md)`", text))
    assert refs, "未从 INDEX.md 解析出任何 *.md 引用（解析式已失效，需更新）"

    dead = []
    for ref in sorted(refs):
        if ref in _PLACEHOLDER_REFS:
            continue                                   # 已登记的占位符，跳过
        if "/" in ref or "\\" in ref:
            ok = (_ROOT / ref).exists()                # 相对仓库根
        else:
            ok = (_DOCS / ref).exists() or ref == "INDEX.md"
        if not ok:
            dead.append(ref)
    assert not dead, (
        "docs/INDEX.md 含有解析不到真实文件的引用（死链）：\n  "
        + "\n  ".join(dead)
        + "\n处置：① 引用写错了 ⇒ 改正；② 文档确已删除 ⇒ 从索引移除；"
        "③ 是命名约定占位符 ⇒ 加进 _PLACEHOLDER_REFS 并写明理由。"
    )


def test_placeholder_exemptions_are_all_actually_used():
    """豁免表里的每一条都必须**真的被索引用到**（防豁免表变成藏死链的后门）。

    这是豁免机制自身的守卫（同 203「幽灵条目」判据）：若某条豁免指向的字符串
    已不在索引里，它就成了「幽灵豁免」——留着会让**未来**的同名死链被静默放过。
    """
    text = _index_text()
    stale = [p for p in _PLACEHOLDER_REFS if p not in text]
    assert not stale, (
        f"_PLACEHOLDER_REFS 中存在已不再被索引使用的幽灵豁免：{stale}"
        f"（应从豁免表删除，否则未来同名死链会被静默放过）"
    )
