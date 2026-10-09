# -*- coding: utf-8 -*-
"""阶段 95 · 能力承诺面收敛（「只处理角标 LOGO 类图片」）的回归测试。

背景（docs/92 缺口 S1 的第二条出路）
------------------------------------
阶段 93 把「真值只标了 `corner_logo` 一类，另外四类的召回是**未知**」这件事
从沉默变成可见，并给出两条出路：① 补四类真实图；② **把能力声明收敛到已验证范围**。
阶段 95 用户拍定走 ②：**只处理角标 LOGO 类图片**。

但「口头收敛」不算收敛——必须落成可判定的东西，否则下一个人会照着旧 README
继续以为五类都承诺。本文件四个契约（对应 `docs/95`）：

  A. **承诺面单一真值来源**：`config.WATERMARK_TYPE_COMMITTED` 由 SCOPE 表**派生**，
     当前恰好 = {"ai_logo"}；承诺面为空必须在 `validate_config()` 里被拦住
     （空集会让人读的判定「无事可守」而静默报绿——经验 54 的变体）。
  B. **门禁语义**：承诺类型缺真值 → **始终硬失败**（承诺比证据多，且可执行）；
     非承诺类型缺真值 → 只记录不失败；清单 catch-all（提示精度）**不再**让
     `--strict-scope` 变红 ⇒ 该开关在本项目里首次可以长期为绿。
  C. **限定随数字走**：只要承诺面已声明，横幅**无条件**渲染（哪怕覆盖面完整），
     且横幅里的类型名全部由 config 派生。
  D. **人读副本守卫**：i18n 模板里不得出现任何字面类型名（否则又是手抄副本）；
     README 的「承诺级别」列必须与承诺面**同进同退**。

设计取舍（与阶段 93 一致）：
  · 判定逻辑用**纯函数 + 确定数据**锁死，不跑检测、不依赖 LaMa；
  · 涉及真实数据集的两处只读 `manifest.json` / `ground_truth.json`（纯解析、秒级），
    缺失则 skip；
  · 「语义变更」的那两条阶段 93 断言已在原文件里显式改写并注明理由，
    这里补上**新语义**的正向断言（防止旧语义被悄悄改回去）。
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts import real_gate as rg          # noqa: E402
import config                                # noqa: E402

README_PATH = os.path.join(_ROOT, "README.md")
MANIFEST_PATH = os.path.join(_ROOT, "benchmark_real", "manifest.json")
TRUTH_PATH = os.path.join(_ROOT, "benchmark_real", "ground_truth.json")


# ---------------------------------------------------------------------------
# 构造确定数据
# ---------------------------------------------------------------------------

#: 当前实测的真实图指标（红线恰好通过这些值）。
#: 阶段 100 角标 MSER 降级通路默认关闭后：precision 0.0385→0.1382、
#: mask_iou 0.0635→0.1163、mask_coverage 0.1618→0.0745、coverage_ratio 16.62→7.65；
#: mean_iou 0.7573→0.7518（MSER 框不再抢先匹配真值）、recall 逐位不变。
#: **阶段 106 框内证据几何收敛后**（pad 2→1、k_scale 0.5→0.65）：
#: precision → **0.1552**、mask_iou → **0.1705**、mask_coverage → **0.0365**、
#: coverage_ratio → **3.75**（recall 1.0000 与 mean_iou 0.7518 逐位不变）。
#: ⚠ **阶段 183 重锚**：上述值标定于**伪造真值期**（149 判定 60 条 GT 系模板复制；
#: 172 收编真实图 + 逐图实测真值 → 177 全面重算）。因本夹具是「当前实测」语义，
#: 随 183 的一致性审计同步到真实真值实测值（177 基线，杠杆 OFF）。
#: ⚠ **阶段 240（Option A）再锚**：2026-10-05 导入 q 系列 11 张（数据集 10→29 图，
#: 豆包角标更小更靠角）使过检天然更低；当前 29 图实测 precision **0.0761** /
#: mask_iou **0.2714** / mask_coverage **0.0107** / coverage_ratio **1.92**，本夹具随之同步。
_MEASURED = {"n": 30, "precision": 0.0761, "recall": 0.8000, "mean_iou": 0.5347,
             "mask_iou": 0.2714, "mask_coverage": 0.0107, "coverage_ratio": 1.92}


def _metrics(**over) -> dict:
    m = dict(_MEASURED)
    m.update(over)
    return m


def _summary(scope, metrics=None, presets=("general",), n_images=30, n_expected=30) -> dict:
    return {
        "config": {"presets": list(presets), "n_images": n_images,
                   "n_expected": n_expected, "dataset_dir": "benchmark_real"},
        "aggregate": {"per_preset": {presets[0]: metrics or _metrics()},
                      "n_images": n_images},
        "scope": scope,
    }


def _manifest(hints) -> dict:
    return {"images": [{"id": f"{i:02d}", "watermark_style_hint": h}
                       for i, h in enumerate(hints, 1)]}


def _truth(styles) -> dict:
    return {"entries": [{"image_id": f"{i:02d}",
                         "watermarks": [{"bbox": [0, 0, 10, 10], "style": s} for s in ss]}
                        for i, ss in enumerate(styles, 1)]}


def _declared_scope(labeled_styles=("corner_logo",), n_images=30) -> dict:
    """一份**承诺面已声明**的 scope（＝真实运行时的形状）。"""
    return rg.annotation_scope(
        _manifest(["mixed"] * n_images),
        _truth([list(labeled_styles)] * n_images),
    )


def _real_scope() -> dict:
    """真实数据集的 scope（只读 JSON，不跑检测）。"""
    for p in (MANIFEST_PATH, TRUTH_PATH):
        if not os.path.exists(p):
            pytest.skip(f"真实基准未就位：{p}")
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(TRUTH_PATH, encoding="utf-8") as f:
        truth = json.load(f)
    return rg.annotation_scope(manifest, truth)


@pytest.fixture
def scope_table_restored():
    """`config.WATERMARK_TYPE_SCOPE` 是模块级可变全局——改动后必须原样还原。"""
    snap = copy.deepcopy(config.WATERMARK_TYPE_SCOPE)
    yield config.WATERMARK_TYPE_SCOPE
    config.WATERMARK_TYPE_SCOPE.clear()
    config.WATERMARK_TYPE_SCOPE.update(snap)


# ===========================================================================
# A · 承诺面单一真值来源
# ===========================================================================

def test_committed_face_is_exactly_corner_logo():
    """**把用户的决定钉成事实**（阶段 95：只处理角标 LOGO 类图片）。

    若日后要新增承诺类型，本测试失败——这是有意的：承诺面变化必须是一次
    显式决策，同时把 README 的能力表、UI 文案（自动派生）与真实图证据一起对齐。
    """
    assert config.WATERMARK_TYPE_COMMITTED == frozenset({"ai_logo"}), (
        f"承诺面已变为 {sorted(config.WATERMARK_TYPE_COMMITTED)}——"
        f"请同步确认这些类型都有真实图真值证据，并更新 README 的承诺级别列"
    )


def test_commitment_is_derived_from_scope_table():
    """承诺面必须**由 SCOPE 表派生**，不是另写一份字面集合。

    双向：既要求每个条目都有布尔 `committed` 字段（不能靠缺省），
    也要求派生结果与逐条计算一致（防「改了字段没改副本」）。
    """
    for name, entry in config.WATERMARK_TYPE_SCOPE.items():
        assert isinstance(entry.get("committed"), bool), f"{name} 缺布尔 committed 字段"
    expected = frozenset(n for n, e in config.WATERMARK_TYPE_SCOPE.items() if e["committed"])
    assert config.WATERMARK_TYPE_COMMITTED == expected, (
        "承诺面与 SCOPE 表派生结果不一致——出现了第二份来源"
    )


def test_every_entry_declares_labels_and_labels_are_unique():
    """UI 适用范围提示靠类型名拼装：缺名会退化成预设名，重名会让文案出现歧义。"""
    labels = []
    for name, entry in config.WATERMARK_TYPE_SCOPE.items():
        for key in ("label_zh", "label_en"):
            val = entry.get(key)
            assert isinstance(val, str) and val.strip(), f"{name} 缺 {key}"
        labels += [entry["label_zh"], entry["label_en"]]
    assert len(labels) == len(set(labels)), f"类型名有重复：{labels}"


def test_validate_config_blocks_empty_commitment_face(monkeypatch):
    """承诺面为空 → `validate_config()` 必须报出问题。

    这是「遍历式守卫空转」的守门人：`committed_missing` 用空集合遍历时
    循环体永不执行，门禁会**静默报绿**（经验 54）。故必须在配置自检层硬拦。
    """
    monkeypatch.setattr(config, "WATERMARK_TYPE_COMMITTED", frozenset())
    problems = config.validate_config()
    assert any("承诺面" in p for p in problems), problems


def test_label_falls_back_to_preset_name():
    """未登记标签时回退为预设名——不抛异常、不留空白（与 t_in 的缺失键回退同风格）。"""
    assert config.watermark_type_label("ai_not_registered") == "ai_not_registered"
    assert config.watermark_type_label("ai_logo", "en-US") == "corner logo"
    assert config.watermark_type_label("ai_logo", "zh-CN") == "角标 LOGO"


# ===========================================================================
# B · 门禁语义
# ===========================================================================

def test_real_dataset_scope_reports_commitment_face():
    """真实数据集的承诺面与真值覆盖必须如声明（读真文件，不跑检测）。

    阶段 150（Option A）曾移除 60 条伪造 corner_logo 标注（0 条真值）；
    **阶段 172** 经 `inbox/` 收编 10 张豆包真实图并逐图标注 `corner_logo`
    ⇒ ai_logo **承诺处理且已有真值**（committed_labeled=['ai_logo']、
    committed_missing=[]、labeled=['corner_logo']）。
    """
    sc = _real_scope()
    assert sc["committed"] == ["ai_logo"]
    assert sc["committed_labeled"] == ["ai_logo"], "阶段 172：承诺类型已有真实真值"
    assert sc["committed_missing"] == [], "阶段 172：承诺面缺口已闭合"
    assert sorted(sc["uncommitted"]) == ["ai_faint_tiled", "ai_gradient",
                                        "ai_grid", "ai_text_heavy"]
    assert sc["uncommitted_missing"] == sorted(sc["uncommitted"]), \
        "非承诺类型的缺真值必须被**逐条记录**，不能留白"
    assert sc["labeled"] == ["corner_logo"], "阶段 172：当前标注 = corner_logo（豆包角标）"


def test_real_dataset_is_strict_scope_green_after_real_truth():
    """阶段 150 时真实数据集应为红灯（承诺类型缺真实真值）。

    **阶段 172** 收编 10 张真实图并标注后 `committed_missing` 变空、`complete=True`
    ⇒ 即使开 `--strict-scope` 也应**转绿**。这条守住「绝不允许用假证据让门禁
    长期报绿」的反面：**真证据到位，门禁就该绿**（且承诺面缺口已闭合）。
    """
    sc = _real_scope()
    assert sc["complete"] is True, sc
    ok, problems = rg.evaluate_real(_summary(sc), None, strict_scope=True)
    assert ok, problems


def test_committed_missing_is_hard_failure_without_strict():
    """承诺类型缺真值 → **不开严格模式也必须失败**。

    它的性质是「承诺比证据多」，既严重又必然可执行（补图 / 收回承诺），
    因此不给它「默认只告警」的退路，否则承诺可以随便加。
    """
    sc = _declared_scope(labeled_styles=("grid_watermark",))   # 只标了非承诺类型
    assert sc["committed_missing"] == ["ai_logo"]
    ok, problems = rg.evaluate_real(_summary(sc), None)
    assert not ok
    hit = [p for p in problems if "承诺" in p]
    assert hit, problems
    assert "ai_logo" in hit[0]


def test_committed_missing_message_is_actionable():
    """报错必须给出两条可执行出路，否则收到红门禁的人只能猜。"""
    sc = _declared_scope(labeled_styles=("grid_watermark",))
    _ok, problems = rg.evaluate_real(_summary(sc), None)
    msg = next(p for p in problems if "承诺" in p)
    assert "真值" in msg and ("committed=False" in msg)


def test_uncommitted_missing_never_fails_even_in_strict_mode():
    """非承诺类型缺真值 → **严格模式也不失败**（它们不在承诺范围内）。"""
    sc = _declared_scope(labeled_styles=("corner_logo",))
    assert sc["uncommitted_missing"], "本用例前提：确有非承诺类型缺真值"
    for strict in (False, True):
        ok, problems = rg.evaluate_real(_summary(sc), None, strict_scope=strict)
        assert ok, (strict, problems)


def test_catch_all_hint_no_longer_blocks_strict():
    """阶段 95 的核心语义变更：清单 catch-all 降级为**提示精度诊断**。

    30 张图全写 `mixed`、而真值覆盖了承诺类型时，`complete` 为真、
    严格模式通过——但 `unspecified_images` 仍被逐条记录（不许静默丢弃）。
    """
    sc = _declared_scope(labeled_styles=("corner_logo",), n_images=30)
    assert sc["unspecified_images"] == 30
    assert sc["complete"] is True
    ok, problems = rg.evaluate_real(_summary(sc), None, strict_scope=True)
    assert ok, problems


def test_undeclared_commitment_keeps_conservative_strict_behavior():
    """旧格式 scope（无 `committed`）→ 按**最严**理解，严格模式仍然失败。

    单一规则（不是两套实现）：没说哪些被承诺 ⇒ 当作全部都被承诺看待。
    这条断言同时锁住阶段 93 的旧口径不被无声改掉——旧格式下语义不变。
    """
    legacy = {"labeled": ["corner_logo"], "n_labeled_styles": 1, "declared_images": 0,
              "unspecified_images": 30, "contradictory": [], "unknown_styles": [],
              "complete": False}
    ok, problems = rg.evaluate_real(_summary(legacy), None, strict_scope=True)
    assert not ok
    assert any("范围" in p for p in problems)


def test_metrics_unchanged_by_scope_semantics():
    """范围语义的改动**不得**动到任何指标口径（回归护栏）。

    用**合成**的「承诺类有真值」scope 隔离测试：验证 `evaluate_real` 不因范围
    语义改动而篡改传入的指标值。阶段 150 起真实数据集无真实真值（红灯），故不再
    依赖真实文件状态来断言「绿灯 + 指标」——那会把测试绑死在 transient 的真实覆盖上。
    """
    sc = _declared_scope(labeled_styles=("corner_logo",))
    ok, problems = rg.evaluate_real(_summary(sc), None, strict_scope=True)
    assert ok, problems
    m = _summary(sc)["aggregate"]["per_preset"]["general"]
    # 与夹具自身对齐（而非硬编码数字）——否则夹具重锚时本断言会变成第二处陈旧锚点（阶段 183）。
    assert (m["precision"], m["recall"], m["mean_iou"]) == (
        _MEASURED["precision"], _MEASURED["recall"], _MEASURED["mean_iou"])


# ===========================================================================
# C · 限定随数字走（横幅）
# ===========================================================================

def test_banner_always_renders_once_commitment_declared():
    """承诺面已声明 → 横幅**无条件**出现（哪怕覆盖面完整）。

    理由：既然承诺了「只做角标」，那么表格里那个 recall 就必须永久带着
    「仅代表角标」这句话走，否则把数字贴出去的人会把它读成全类型结论。
    """
    sc = _declared_scope(labeled_styles=("corner_logo",))
    assert sc["complete"] is True
    banner = rg.render_scope_banner(sc)
    assert banner, "承诺面已声明却不出横幅——限定就没有跟着数字走"
    assert "仅代表" in banner


def test_banner_names_promise_face_and_exclusions_from_config():
    """横幅里的类型名必须与承诺面一致，且中文名由 config 派生。"""
    sc = _declared_scope(labeled_styles=("corner_logo",))
    banner = rg.render_scope_banner(sc)
    for name in config.WATERMARK_TYPE_COMMITTED:
        assert name in banner
        assert config.watermark_type_label(name) in banner
    for name in set(config.WATERMARK_TYPE_SCOPE) - set(config.WATERMARK_TYPE_COMMITTED):
        assert name in banner, f"非承诺类型 {name} 必须被点名（否则读者会以为是承诺的）"
    assert "承诺处理" in banner and "不承诺" in banner


def test_banner_reports_committed_missing_loudly():
    sc = _declared_scope(labeled_styles=("grid_watermark",))
    banner = rg.render_scope_banner(sc)
    assert "✖" in banner and "缺真值" in banner


def test_banner_empty_only_for_legacy_complete_scope():
    """两种「不出横幅」的情形，都必须有明确理由。"""
    assert rg.render_scope_banner({}) == ""          # 没有覆盖面信息 → 不凭空断言
    legacy_complete = {"labeled": ["corner_logo"], "n_labeled_styles": 1,
                       "declared_images": 30, "unspecified_images": 0,
                       "contradictory": [], "unknown_styles": [], "complete": True}
    assert rg.render_scope_banner(legacy_complete) == ""   # 旧格式且完整 → 无需限定


def test_markdown_carries_banner_for_real_shaped_summary():
    md = rg.to_markdown(_summary(_real_scope()))
    assert "仅代表" in md and "承诺处理" in md
    # 数字与限定同框；数字从夹具派生，避免夹具重锚后本断言变成第二处陈旧锚点（阶段 183）。
    assert f"{_MEASURED['precision']:.4f}" in md


# ===========================================================================
# D · 人读副本守卫
# ===========================================================================

def test_scope_note_template_has_no_literal_type_names():
    """i18n 模板里不得出现任何字面类型名 / 预设名。

    这是「人读副本」的通解（经验 51）：文案可以存在，但**必须由注入产生**——
    只要模板里手抄过一次，config 改动就会与界面各说各话。
    """
    from utils.i18n import TRANSLATIONS
    names = set(config.WATERMARK_TYPE_SCOPE)
    labels = {e["label_zh"] for e in config.WATERMARK_TYPE_SCOPE.values()} | \
             {e["label_en"] for e in config.WATERMARK_TYPE_SCOPE.values()}
    for lang, table in TRANSLATIONS.items():
        tpl = table["scope"]["note"]
        for token in names | labels:
            assert token not in tpl, f"scope.note（{lang}）手抄了类型名「{token}」：{tpl}"


def test_scope_note_zh_mentions_every_committed_type():
    from utils.i18n import set_language, t_in
    set_language("zh-CN")
    from ui.components import render_scope_note
    note = render_scope_note("zh-CN")
    for name in config.WATERMARK_TYPE_COMMITTED:
        assert name in note and config.watermark_type_label(name, "zh-CN") in note
    assert "承诺" in note
    assert t_in("zh-CN", "scope.note") != "scope.note"


def test_scope_note_en_mentions_every_committed_type():
    from ui.components import render_scope_note
    note = render_scope_note("en-US")
    for name in config.WATERMARK_TYPE_COMMITTED:
        assert name in note
        assert config.watermark_type_label(name, "en-US") in note


def test_scope_note_puts_each_type_on_the_right_side():
    """承诺类型只能出现在「承诺」一侧，非承诺类型只能出现在「不承诺」一侧。

    判定手段是**切段**：文案被两个显式标记切成两段，逐类型断言它只在正确那段出现。
    这比「出现与否」严格——它能抓住「把不承诺的类型也写进承诺列表」这种错，
    而那种错恰恰是最容易发生、也最容易被读者误信的（经验 51 的典型形态）。
    """
    from ui.components import render_scope_note
    for lang, m_yes, m_no in (("zh-CN", "承诺处理", "明确不承诺"),
                              ("en-US", "commits to", "not committed to")):
        note = render_scope_note(lang)
        assert m_yes in note and m_no in note, f"{lang}: 缺分段标记"
        i_yes, i_no = note.index(m_yes), note.index(m_no)
        assert i_yes < i_no, f"{lang}: 分段标记顺序反了"
        seg_yes = note[i_yes:i_no]
        seg_no = note[i_no:]
        for name in config.WATERMARK_TYPE_COMMITTED:
            assert name in seg_yes, f"{lang}: 承诺类型 {name} 不在承诺段"
            assert name not in seg_no, f"{lang}: 承诺类型 {name} 又出现在不承诺段"
        for name in set(config.WATERMARK_TYPE_SCOPE) - set(config.WATERMARK_TYPE_COMMITTED):
            assert name in seg_no, f"{lang}: 非承诺类型 {name} 不在不承诺段"
            assert name not in seg_yes, f"{lang}: 非承诺类型 {name} 混进了承诺段"


def test_scope_note_is_derived_from_config_labels(scope_table_restored):
    """改 config 的标签 → 文案跟着变（证明文案是派生的，不是手抄的）。"""
    from ui.components import render_scope_note
    before = render_scope_note("zh-CN")
    scope_table_restored["ai_logo"]["label_zh"] = "角落标识（改）"
    after = render_scope_note("zh-CN")
    assert "角落标识（改）" in after
    assert before != after


def test_scope_note_switches_with_language():
    from utils.i18n import set_language
    from ui.components import render_scope_note
    try:
        set_language("zh-CN")
        zh = render_scope_note()
        set_language("en-US")
        en = render_scope_note()
        assert zh != en
        assert "适用范围" in zh and "Scope" in en
    finally:
        set_language("zh-CN")


# ===========================================================================
# D2 · README 承诺级别列必须与承诺面同进同退
# ===========================================================================

def _readme_type_rows() -> list:
    with open(README_PATH, encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next(i for i, l in enumerate(lines) if "支持的水印类型" in l)
    rows = []
    for l in lines[start + 1:]:
        if not l.startswith("|"):
            if rows:
                break
            continue
        rows.append(l)
    return rows


def _row_preset(row: str):
    m = re.findall(r"`(ai_[a-z_]+)`", row)
    return m[0] if m else None


def test_readme_commitment_column_matches_promise_face():
    """README 的「承诺级别」列必须与承诺面**双向一致**。

    · 承诺的行：承诺级别列必须写「承诺处理」，且该列**不得**出现「未验证」
      （承诺级别列出现未验证 = 承诺与证据自相矛盾）。
    · 非承诺的行：承诺级别列必须写「不承诺」。
    · 注：验证状态列**允许**写「未验证」（承诺类型暂无真实真值时诚实标注，
      如阶段 150 后的 ai_logo），这不构成承诺级别列的冲突。
    """
    rows = _readme_type_rows()
    assert rows, "README 水印类型表没找到（表格结构可能被改动）"
    seen = set()
    for row in rows:
        name = _row_preset(row)
        if name is None or name not in config.WATERMARK_TYPE_SCOPE:
            continue
        seen.add(name)
        cells = [c.strip() for c in row.split("|")]
        # 表头：| 类型 | 预设名 | 说明 | 承诺级别 | 验证状态 |
        commitment_col = cells[4] if len(cells) > 4 else ""
        if name in config.WATERMARK_TYPE_COMMITTED:
            assert "承诺处理" in commitment_col, f"{name} 承诺级别列未标「承诺处理」：{row}"
            assert "未验证" not in commitment_col, \
                f"{name} 承诺级别列出现「未验证」自相矛盾：{row}"
        else:
            assert "不承诺" in commitment_col, f"{name} 承诺级别列未标「不承诺」：{row}"
    assert seen == set(config.WATERMARK_TYPE_SCOPE), \
        f"README 未覆盖全部登记类型：缺 {sorted(set(config.WATERMARK_TYPE_SCOPE) - seen)}"


def test_readme_promise_count_matches_committed_size():
    """能力声明里的「当前承诺 N 类」这个数字必须与承诺面一致。

    数字是最容易过期的「人读副本」——故让它是**可判定的**。
    """
    with open(README_PATH, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"当前承诺\s*\*\*(\d+)\s*类\*\*", text)
    assert m, "README 未写明「当前承诺 N 类」（能力声明需要一个人眼可读的规模）"
    assert int(m.group(1)) == len(config.WATERMARK_TYPE_COMMITTED), (
        f"README 说承诺 {m.group(1)} 类，实际 {len(config.WATERMARK_TYPE_COMMITTED)} 类"
    )


# ===========================================================================
# E · 界面接线（真构建一次，验证提示真的在页面上、且随语言切换刷新）
# ===========================================================================

def test_build_app_renders_scope_note_and_language_switch_refreshes_it():
    """端到端接线断言：构建一次界面，提示必须在场，且切语言后跟着变。

    只测「函数返回了正确字符串」是不够的——若忘了把组件挂进 `build_app`，
    用户永远看不到它（阶段 94 刚在 `gr.Markdown` label 上踩过同类空接线）。

    ⚠ **必须自己钉住语言状态**：`i18n` 的语言是**进程级全局**（还持久化到磁盘），
    上一个测试文件若把它留在 en-US，这里就会渲染出英文提示而断言失效
    （实测：单跑本文件绿，与 `test_i18n.py` 同跑则红）。
    语言与 `_CACHE` 同属「全局可变状态」，用例必须**自己建场景、自己还原**。
    """
    from ui import app as app_mod
    from ui import i18n_rt
    from utils.i18n import set_language, save_language

    snap = list(i18n_rt._REGISTRY)
    try:
        set_language("zh-CN")
        save_language("zh-CN")
        i18n_rt._REGISTRY.clear()
        app_mod.build_app()
        values = [getattr(comp, "value", None) for comp, _ in i18n_rt._REGISTRY]
        assert any(isinstance(v, str) and "适用范围" in v for v in values), \
            "界面上没有适用范围提示——组件未挂进 build_app"

        updates = i18n_rt.apply_language("en-US")
        assert len(updates) == len(i18n_rt._REGISTRY)
        # 注意：此处必须看**update 载荷**而不是组件当前值——组件实例不会被
        # gr.update 就地改写，读 `comp.value` 只会拿到构建期的中文初值，
        # 于是「切语言是否真的刷新了提示」根本验不出来（自比无效）。
        payloads = [u.get("value") for u in updates if isinstance(u, dict)]
        assert any(isinstance(p, str) and "Scope" in p for p in payloads), \
            "切到英文后适用范围提示未刷新（或未注册进 i18n 运行时表）"
    finally:
        set_language("zh-CN")
        save_language("zh-CN")
        i18n_rt._REGISTRY.clear()
        i18n_rt._REGISTRY.extend(snap)
