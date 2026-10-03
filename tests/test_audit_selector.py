"""阶段 94 · 选点指标（`scripts/audit_selector.py`）的口径守卫测试。

为什么需要这些用例（经验 5 / 52）
--------------------------------------------------------------------------
「覆盖度荒漠」排名**历来靠临时探针手算**，于是同一条目在不同阶段被写下互相矛盾的
值——`auto_pipeline` 记为 `575/10 = 57.5`（"第三荒漠"），重算实为 `389/50 = 7.8`
（中游）。决策因此被安静地引向错误目标。

故本文件守两类东西：
  1) **口径本身**（合成夹具，零 I/O 依赖）：`code_lines` 的剔除规则、`match_refs`
     的四写法覆盖 **与精度**（不能把注释提及、别的层同名模块算成引用）。
  2) **口径未退化**（真实仓库）：`KNOWN_AUDITED` 里阶段 88–91 已审过的模块必须有引用
     ——这正是阶段 94 第一版探针掉进的陷阱（`utils/logger`、`ui/theme` 被算成 0 引用）。
"""
from __future__ import annotations

import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts import audit_selector as asel  # noqa: E402


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _write(tmp_path, name: str, text: str):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _mk_tests_dir(tmp_path, files: dict):
    """在 tmp_path 下建一个只含指定测试文件的 tests/ 目录。"""
    d = tmp_path / "tests"
    d.mkdir(exist_ok=True)
    for fn, body in files.items():
        (d / fn).write_text(body, encoding="utf-8")
    return str(d)


def _target(key="ui/tab_models") -> asel.Target:
    layer, name = key.split("/") if "/" in key else ("", key)
    return asel.Target(key=key, layer=layer, name=name, path="")


# ---------------------------------------------------------------------------
# 1) 分子：code_lines
# ---------------------------------------------------------------------------

def test_code_lines_excludes_blank_comment_and_docstrings(tmp_path):
    src = (
        "# 纯注释\n"                 # 不计
        '"""\n'                      # 文档串起始（纯分隔行）
        "模块文档\n"
        '"""\n'                      # 文档串结束
        "\n"                         # 空行
        "import os\n"                # 计 1
        "\n"
        "def f():\n"                 # 计 2
        '    """内联\n'              # 文档串起始（前缀为空）
        '    文档"""\n'
        "    return 1\n"             # 计 3
        'x = """one-line"""\n'       # 计 4（引号成对，是代码）
        'y = """open\n'              # 计 5（引号前有真实代码）
        'rest"""\n'
    )
    p = _write(tmp_path, "sample.py", src)
    assert asel.code_lines(p) == 5


def test_code_lines_counts_empty_file_as_zero(tmp_path):
    assert asel.code_lines(_write(tmp_path, "empty.py", "")) == 0
    assert asel.code_lines(_write(tmp_path, "only_comment.py", "# a\n\n# b\n")) == 0


# ---------------------------------------------------------------------------
# 2) 分母：match_refs —— 四写法必须全覆盖
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn,body", [
    ("test_a.py", "from ui.tab_models import build_models_tab\n\n\ndef test_a():\n    pass\n"),
    ("test_b.py", "import ui.tab_models\n\n\ndef test_b():\n    pass\n"),
    ("test_c.py", "from ui import tab_models\n\n\ndef test_c():\n    pass\n"),
    ("test_d.py", "def test_d(monkeypatch):\n"
                  "    monkeypatch.setattr(\"ui.tab_models._CACHE\", {})\n"),
])
def test_match_refs_covers_each_import_form(tmp_path, fn, body):
    """四写法（含 `from ui import tab_models` 这种别名导入）都必须命中。"""
    tgt = _target()
    asel.match_refs(tgt, asel._test_files(_mk_tests_dir(tmp_path, {fn: body})))
    assert tgt.hit_files == [fn], f"写法 {fn} 未被识别为引用"


def test_match_refs_ignores_comment_mention(tmp_path):
    """只在**注释**里提到模块名 ≠ 引用——否则排名会被噪声拉低。"""
    body = "# 这里顺便提一下 ui.tab_models 的实现\n\n\ndef test_x():\n    pass\n"
    tgt = _target()
    asel.match_refs(tgt, asel._test_files(_mk_tests_dir(tmp_path, {"test_x.py": body})))
    assert tgt.hit_files == []


def test_match_refs_rejects_same_name_in_other_layer(tmp_path):
    """`services.tab_models` 不是 `ui.tab_models`——跨层同名不得互相污染。"""
    tgt = _target("ui/tab_models")
    asel.match_refs(
        tgt,
        asel._test_files(_mk_tests_dir(tmp_path, {
            "test_e.py": "from services import tab_models\n\n\ndef test_e():\n    pass\n",
            "test_f.py": "import services.tab_models\n\n\ndef test_f():\n    pass\n",
        })),
    )
    assert tgt.hit_files == []


def test_match_refs_sums_all_tests_in_hit_files(tmp_path):
    """分母是**整个文件的用例数**，不是 touch 到模块的用例数。"""
    body = ("from ui.tab_models import build_models_tab\n\n\n"
            "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n"
            "\n\ndef test_three():\n    pass\n")
    tgt = _target()
    asel.match_refs(tgt, asel._test_files(_mk_tests_dir(tmp_path, {"test_g.py": body})))
    assert tgt.hit_tests == 3


def test_match_refs_top_level_module(tmp_path):
    """顶层模块（无包前缀）按模块名匹配，且同样要求是导入或引号字面量。"""
    tgt = _target("config")
    asel.match_refs(
        tgt,
        asel._test_files(_mk_tests_dir(tmp_path, {
            "test_h.py": "import config\n\n\ndef test_h():\n    pass\n",
            "test_i.py": "def test_i(monkeypatch):\n"
                         "    monkeypatch.setattr(\"config.X\", 1)\n",
            "test_j.py": "# config 在这里只是被提到\n\n\ndef test_j():\n    pass\n",
        })),
    )
    assert sorted(tgt.hit_files) == ["test_h.py", "test_i.py"]


# ---------------------------------------------------------------------------
# 3) 自检：必须能抓到「口径退化」
# ---------------------------------------------------------------------------

def _synth_targets(keys):
    """按给定 key 列表造合成 Target（引用数=5 ⇒ 不触发"引用为 0"那条）。"""
    out = []
    for key in keys:
        layer, _, name = key.partition("/")
        out.append(asel.Target(key, layer, name, "x", 100, ["t.py"], 5))
    return out


def test_self_check_detects_zero_ref_audited_module():
    """已审模块引用数 = 0 时必须报问题——这是防空转的唯一防线。"""
    stale = [asel.Target("utils/logger", "utils", "logger", "x", 135, [], 0)]
    problems = asel.self_check(stale)
    assert problems and any("utils/logger" in p for p in problems)


def test_self_check_detects_missing_audited_module():
    """空扫描结果必须同时触发**两类**问题：已审模块不在扫描内 + 登记全数不可扫到。"""
    problems = asel.self_check([])
    assert len(problems) >= len(asel.KNOWN_AUDITED)
    assert any("已审模块" in p for p in problems), problems
    assert any("[登记]" in p for p in problems), problems


def test_self_check_passes_when_all_audited_have_refs():
    """**阶段 109 同步夹具**：登记从 6 条（`KNOWN_AUDITED`）扩到 13 条（`AUDITED_SINCE`），
    夹具须同步到**全量登记**——否则新加的「登记里的模块必须真被扫到」这条会误报。"""
    ok = _synth_targets(asel.AUDITED_SINCE)
    assert asel.self_check(ok) == []


def test_self_check_detects_registered_module_not_scanned():
    """登记里有、但扫描结果里没有 ⇒ 报「改名/删除后未同步本表」。"""
    keys = [k for k in asel.AUDITED_SINCE if k != "services/report_service"]
    problems = asel.self_check(_synth_targets(keys))
    assert any("services/report_service" in p and "[登记]" in p for p in problems), problems


def test_self_check_detects_registry_divergence(monkeypatch):
    """两份登记必须同源：`KNOWN_AUDITED` 的 key 缺在 `AUDITED_SINCE` 里 ⇒ 必须报错。

    （这正是阶段 109 踩空的形态：一份记录说"审过"、另一份说"没审"。）
    """
    trimmed = {k: v for k, v in asel.AUDITED_SINCE.items() if k != "services/queue_worker"}
    monkeypatch.setattr(asel, "AUDITED_SINCE", trimmed)
    problems = asel.self_check(_synth_targets(trimmed))
    assert any("未进 AUDITED_SINCE" in p for p in problems), problems


def test_unaudited_filters_registered_modules():
    """`--unaudited` 的语义：剔除全部已审登记，且**不误删**未审模块。

    ⚠ 幸存者从「合成输入」取，避免与具体模块名 / 真实榜状态耦合
    （阶段 110 把 `ui/tab_single` 注册进 AUDITED_SINCE 后，原写死的样本误判为已审、
    测试因此红 —— 这是改 `audit_selector.py` 注册表却漏跑本文件的真实回归）。

    **阶段 146 订正**：原文还有一句 `assert unreg, "当前必须仍有未审模块"`，
    而 145 阶段把最后一个模块（`config`）登记后**真实榜已空** ⇒ 该前提断言失效。
    榜空是**里程碑**而不是失败，因此：
      · **过滤逻辑本身**改用合成输入验证（与真实榜状态无关）；
      · 真实榜若**仍有**未审模块，顺带验证它们不被误删（动态取，不耦合名字）；
      · 榜空时**显式断言**这一里程碑（若将来新增模块使榜非空，本行会提醒更新语义）。
    """
    all_targets = asel.collect()
    unreg = [t.key for t in all_targets if t.key not in asel.AUDITED_SINCE]

    # ① 过滤逻辑（合成输入 —— 与真实榜状态无关）
    sample = ["__synth_unaudited_a__", "__synth_unaudited_b__", "__synth_unaudited_c__"]
    keys = list(asel.AUDITED_SINCE) + sample
    left = [t.key for t in asel.unaudited(_synth_targets(keys))]
    assert set(left) == set(sample), f"幸存者应恰好是 {sample}，实得 {left}"
    assert left, "剔除后不得为空（否则断言空转）"

    # ② 真实榜：若有未审模块，必须一个不少地留下
    if unreg:
        real_left = [t.key for t in asel.unaudited(all_targets)]
        assert set(real_left) == set(unreg), "真实未审模块被误删"

    # ③ 里程碑记录：全部模块已审（或将来出现新模块时给出明确指引）
    if not unreg:
        assert len(asel.AUDITED_SINCE) >= 44, (
            f"榜已空但登记只有 {len(asel.AUDITED_SINCE)} 条 —— 请核对登记完整性"
        )


# ---------------------------------------------------------------------------
# 3b) 观察者效应：量具自身不得计入分母
# ---------------------------------------------------------------------------

def test_meta_test_excludes_is_explicitly_registered():
    """测**量具本身**的测试文件必须显式登记，不做隐式名字猜测。"""
    assert "test_audit_selector.py" in asel.META_TEST_EXCLUDES


def test_meta_test_files_are_not_returned_by_test_files():
    """已登记的测量装置文件必须真的被排除出分母。

    ⚠ 第一版这里**没有**先断言集合非空，于是注入「把 META_TEST_EXCLUDES 清空」
    时，`for` 循环体一次都不执行、断言空转，测试照常通过 —— 一个哑巴守卫。
    （阶段 94 的注入式反例 I5 抓到的，正是本条自己。）
    """
    assert asel.META_TEST_EXCLUDES, (
        "META_TEST_EXCLUDES 不得为空 —— 否则本断言退化为空转"
    )
    fns = [fn for fn, _s, _n in asel._test_files(os.path.join(_ROOT, "tests"))]
    for meta in asel.META_TEST_EXCLUDES:
        assert meta not in fns, f"{meta} 未被剔除出分母"


def test_meta_excluded_file_does_not_contaminate(tmp_path):
    """造一个「只提模块名、不导入」的测试文件：不登记则命中，登记后不命中。"""
    d = _mk_tests_dir(tmp_path, {
        "test_meta.py": "def test_x():\n    assert \"ui.tab_models\"\n",
    })
    tgt = _target()
    asel.match_refs(tgt, asel._test_files(d, excludes=()))
    assert tgt.hit_files == ["test_meta.py"]  # 未登记 → 会命中（正是污染途径）

    tgt2 = _target()
    asel.match_refs(tgt2, asel._test_files(d, excludes=("test_meta.py",)))
    assert tgt2.hit_files == []


def test_selector_own_tests_do_not_count_as_module_coverage():
    """阶段 94 观察者效应钉子：**量具**自己的测试不得算成任何模块的覆盖。

    踩坑记录：补上 `test_audit_selector.py` 后，`ui/tab_models` 的命中文件从
    `[test_ui.py]` 变成 `[test_audit_selector.py, test_ui.py]`，比值 **65.5 → 11.4**，
    它甚至掉出了前三 —— 模块会因为「有人测了量具」而显得被测过了。

    ⚠ 注意与**审计测试**的区别：`test_ui_audit94.py` 真的在测 `ui/tab_models`，
    它**应当**计入（见 `test_tab_models_ratio_dropped_after_audit`）。只有
    「测量具本身」的文件才是污染。
    """
    targets = {t.key: t for t in asel.collect(_ROOT)}
    hits = targets["ui/tab_models"].hit_files
    assert "test_audit_selector.py" not in hits, (
        "量具自身的测试被算成了对 ui/tab_models 的覆盖 —— 观察者效应回归"
    )
    assert "test_ui.py" in hits, hits


def test_tab_models_ratio_dropped_after_audit():
    """阶段 94 的**结论编码**：审计 `ui/tab_models` 并补 15 例后，其选点比值应显著下降。

    审计前 65.5（262 行 / 4 例）；审计后因 `test_ui_audit94.py` 真实覆盖该模块而下降。

    ⚠ 这条同时登记了本指标的一个**固有局限**：比值会因「刚被审计过」而下降，
    故它衡量的是「当前测试密度」，**不能**用来判断「该模块是否还有缺陷」。
    """
    targets = {t.key: t for t in asel.collect(_ROOT)}
    tgt = targets["ui/tab_models"]
    assert tgt.ratio < 20.0, f"审计后比值仍为 {tgt.ratio:.1f}，请确认测试文件真的覆盖了它"
    assert "test_ui_audit94.py" in tgt.hit_files, tgt.hit_files


# ---------------------------------------------------------------------------
# 4) 真实仓库：口径未退化 + 排名自洽
# ---------------------------------------------------------------------------

def test_real_repo_known_audited_modules_all_have_refs():
    """阶段 94 的教训钉子：已审模块绝不能被算成 0 引用。"""
    targets = asel.collect(_ROOT)
    index = {t.key: t for t in targets}
    for layer, name, stage in asel.KNOWN_AUDITED:
        key = f"{layer}/{name}"
        assert key in index, f"{key} 不在扫描结果内（阶段 {stage} 审过，应存在）"
        assert index[key].hit_tests > 0, (
            f"{key}（阶段 {stage} 审过）引用数为 0 —— 导入匹配口径退化了"
        )


def test_real_repo_ranking_is_sorted_desc():
    targets = asel.collect(_ROOT)
    assert targets, "选点结果不应为空"
    for a, b in zip(targets, targets[1:]):
        assert a.ratio >= b.ratio, f"排序错误：{a.key} < {b.key}"
    assert all(t.code > 0 for t in targets)


def test_real_repo_current_top_desert_snapshot():
    """当前第一荒漠快照（结论编码，非不变量——每次排名变化时更新）。

    历史（本条为「结论编码，非不变量」而存在，随审计推进更新）：
      · 阶段 94 前第一是 `ui/tab_models`（262/4 = 65.5）；
      · 审完 tab_models 后第一让给 `ui/tab_auto`；
      · 阶段 142 审计 `core/inpainter` 顺带为 `tests/test_processing_prefs.py`
        补 3 例 ⇒ tab_auto / tab_batch 比值下降，第一让给 `services/queue_worker`
        （当时 264/14 = 18.86）；
      · 阶段 161（queue_worker +14 例）/ 163（tab_auto +15 例）/ 164
        （tab_batch +40 例）等批量补测后各模块比值整体下移：
        现 queue_worker 271/27 = 10.04、tab_auto 720/50 = 14.40，
        第一荒漠回摆为 `ui/tab_auto`（14.40）。
      · 阶段 167 覆盖度荒漠审计程序已收口（`--unaudited` 返回 0 模块），
        故「第一荒漠」均为已审模块，仅反映代码/测试比，不再指向待审目标。
    **本条是"结论编码"，不是不变量**——下次排名变化时更新本条。
    """
    targets = asel.collect(_ROOT)
    assert targets[0].key == "ui/tab_auto", (
        f"当前第一荒漠已变为 {targets[0].key}；若排名再变化，请更新本条"
    )


def test_ranking_gap_is_not_asserted_as_invariant():
    """**刻意不**断言「第一是第二的 N 倍」。

    阶段 94 曾写下 `top1 ≥ top2 × 2.5`（当时 65.5 vs 22.2），审计完 tab_models 后
    该性质立刻失效（22.2 vs 19.5）。**把「一时显著」写成不变量，只会在下次
    正常变化时变成假红灯**（经验 64 的反面教训）。此处只记录该决定，断言保持为空。
    """
    targets = asel.collect(_ROOT)
    assert all(t.ratio > 0 for t in targets)


# ---------------------------------------------------------------------------
# 5) CLI 接线
# ---------------------------------------------------------------------------

def test_cli_check_exit_zero_on_healthy_repo():
    assert asel.main(["--check"]) == 0


def test_cli_unknown_module_returns_one(capsys):
    assert asel.main(["--module", "ui/definitely_not_a_module"]) == 1


def test_cli_module_lookup_prints_hit_files(capsys):
    assert asel.main(["--module", "ui/tab_models"]) == 0
    out = capsys.readouterr().out
    assert "ui/tab_models" in out
    assert "test_ui.py" in out


def test_cli_top_limits_rows(capsys):
    asel.main(["--top", "3"])
    out = capsys.readouterr().out
    rows = [ln for ln in out.strip().splitlines()
            if re.match(r"^\s*(inf|\d+\.\d+)\s*\|", ln)]
    assert len(rows) == 3, out


def test_cli_json_roundtrip(tmp_path, capsys):
    out_json = str(tmp_path / "sel.json")
    asel.main(["--top", "3", "--json", out_json])
    import json
    with open(out_json, encoding="utf-8") as fh:
        data = json.load(fh)
    # 阶段 142：第一荒漠由 `ui/tab_auto` 变为 `services/queue_worker`
    # （原因见 test_real_repo_current_top_desert_snapshot 的 docstring）。
    # 这里只断言「与 collect() 的首位一致」，避免再写死一个会过时的名字。
    assert data["targets"][0]["key"] == asel.collect(_ROOT)[0].key
    assert data["targets"][0]["code"] > 0
    assert len(data["known_audited"]) == len(asel.KNOWN_AUDITED)
