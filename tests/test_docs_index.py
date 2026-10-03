# -*- coding: utf-8 -*-
"""阶段 196（E3）· docs 索引（docs/INDEX.md）防腐烂守卫。

为什么需要它：`docs/INDEX.md` 是一份**手写的**文档导航。手写清单最大的风险是
**腐烂**——新增了 `docs/NNN-*.md` 却忘了加进索引，索引会静默地越来越不准。
本守卫把「忘记登记」从**静默**变成**机械可发现**：磁盘上每个 `docs/*.md`
（除 `INDEX.md` 自身）都必须在 `INDEX.md` 正文里被**按文件名**引用，否则变红。

（同源经验：168/169 的 i18n 覆盖率守卫、203「改视野须重审清单」——清单类守卫
必须整文件跑、且以磁盘枚举为准，而非回忆。）
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _ROOT / "docs"
_INDEX = _DOCS / "INDEX.md"


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
