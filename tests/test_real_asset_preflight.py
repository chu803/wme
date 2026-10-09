"""阶段 108 · 「交图 / 升级承诺类型」预检的回归测试。

预检要守住的核心不是"文件格式对不对"（那是 `collect_real_assets.py` 与
`build_real_ground_truth.py validate` 的职责），而是三件**语义**的事：

  A. **逐类型的真值覆盖计数**：某类在真值里到底有几张图、几个标注 —— 唯一来源是
     `config.WATERMARK_TYPE_SCOPE[*]["truth_styles"]`（不另写一份风格映射）。
  B. **标注来源合规**：`auto:` 前缀 = 用**检测器自己的输出**预填真值 ⇒ 拿它评检测器是
     **循环论证**，recall/precision 会结构性偏高 ⇒ 该类**不得升级为承诺类型**。
  C. **升级判定三态**（已承诺 / 可升级 / 不可升级）与「差在哪」必须可读、可判。

另加两条：
  D. 升级清单必须覆盖所有**带守卫**的落点（config / README / 承诺面测试 / 门禁刷新 / 全量测试），
     否则"照清单做完"仍会红。
  E. 真实数据集的**标注来源**现场守卫：现有 60 条必须全是 `manual:*`。
"""
import os
import sys
from typing import Any, Dict

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from scripts import real_asset_preflight as pf  # noqa: E402


def _manifest(ids, sha=None):
    return {"version": 1,
            "images": [{"id": str(i), "filename": f"{i}.jpg", "path": f"images/{i}.jpg",
                        "sha256": (sha or {}).get(str(i), f"sha-{i}")} for i in ids]}


def _truth(entries):
    """entries: [(image_id, [(style, labeled_by), ...]), ...]"""
    return {"version": 1,
            "entries": [{"image_id": str(iid),
                         "watermarks": [{"bbox": [0, 0, 10, 10], "style": s}
                                        for s, _ in marks],
                         "labeled_by": (marks[0][1] if marks else "manual:measured")}
                        for iid, marks in entries]}


# ---------------------------------------------------------------------------
# A. 覆盖计数（按 config 的 truth_styles 归并）
# ---------------------------------------------------------------------------

def test_class_coverage_counts_by_declared_truth_styles():
    """`translucent`/`overlay` 都算 ai_faint_tiled；`grid_watermark` 只算 ai_grid。"""
    m = _manifest([1, 2, 3])
    t = _truth([(1, [("translucent", "manual:measured")]),
                (2, [("overlay", "manual:measured")]),
                (3, [("grid_watermark", "manual:measured")])])
    cov = pf.class_coverage(m, t)
    assert cov["ai_faint_tiled"]["n_images"] == 2
    assert cov["ai_faint_tiled"]["n_marks"] == 2
    assert cov["ai_grid"]["n_images"] == 1
    assert cov["ai_text_heavy"]["n_images"] == 0
    assert cov["ai_logo"]["n_images"] == 0


def test_class_coverage_multi_style_entry_counts_once_per_class():
    """一条标注里混多种风格：每类各计一次图，但标注数按该类的条目数累加。"""
    m = _manifest([1])
    t = _truth([(1, [("corner_logo", "manual:measured"),
                     ("logo_watermark", "manual:measured"),
                     ("edge_text", "manual:measured")])])
    cov = pf.class_coverage(m, t)
    assert cov["ai_logo"]["n_images"] == 1 and cov["ai_logo"]["n_marks"] == 2
    assert cov["ai_text_heavy"]["n_images"] == 1 and cov["ai_text_heavy"]["n_marks"] == 1


def test_class_coverage_records_annotation_source():
    m = _manifest([1, 2])
    t = _truth([(1, [("edge_text", "manual:measured")]),
                (2, [("edge_text", "auto:general")])])
    cov = pf.class_coverage(m, t)["ai_text_heavy"]
    assert cov["labeled_by"] == {"manual:measured": 1, "auto:general": 1}
    assert cov["auto_images"] == ["2"]


# ---------------------------------------------------------------------------
# B/C. 标注来源合规 + 就绪判定三态
# ---------------------------------------------------------------------------

def test_auto_annotations_block_upgrade_even_with_enough_images():
    """**本预检的核心**：图够多但标注是 auto 预填 ⇒ 仍不可升级（循环论证）。"""
    m = _manifest([1, 2, 3])
    t = _truth([(i, [("edge_text", "auto:ai_text_heavy")]) for i in (1, 2, 3)])
    r = pf.commit_readiness(m, t, min_images=3)["ai_text_heavy"]
    assert r["verdict"] == "不可升级"
    assert any("auto" in x for x in r["reasons"]), r["reasons"]


def test_readiness_three_states():
    m = _manifest([1, 2, 3, 4])
    t = _truth([(1, [("edge_text", "manual:x")]),
                (2, [("edge_text", "manual:x")]),
                (3, [("corner_logo", "manual:x")]),
                (4, [("corner_logo", "manual:x")])])
    r = pf.commit_readiness(m, t, min_images=3)
    assert r["ai_text_heavy"]["verdict"] == "不可升级"          # 2 < 3
    assert "下限 3" in "；".join(r["ai_text_heavy"]["reasons"])
    # 把下限降到 2 ⇒ 可升级
    assert pf.commit_readiness(m, t, min_images=2)["ai_text_heavy"]["verdict"] == "可升级"
    # 已承诺的类型恒为「已承诺」（不因数量少而报"不可升级"）
    assert r["ai_logo"]["verdict"] == "已承诺"


def test_committed_without_truth_is_reported_by_scope():
    """承诺类无真值 ⇒ 交给 `real_gate.annotation_scope` 判（同一语义不写两遍）。"""
    from scripts.real_gate import annotation_scope
    sc = annotation_scope(_manifest([1]), _truth([(1, [("edge_text", "manual:x")])]))
    assert sc["committed_missing"] == ["ai_logo"]


# ---------------------------------------------------------------------------
# 结构审计
# ---------------------------------------------------------------------------

def test_audit_manifest_detects_duplicates():
    m = {"images": [
        {"id": "1", "filename": "a.jpg", "path": "images/a.jpg", "sha256": "same"},
        {"id": "2", "filename": "b.jpg", "path": "images/b.jpg", "sha256": "same"},
        {"id": "2", "filename": "c.jpg", "path": "images/c.jpg", "sha256": "other"},
        {"id": "3", "filename": "", "path": "images/d.jpg", "sha256": "third"},
    ]}
    problems = pf.audit_manifest(m)
    assert any("sha256" in p for p in problems), problems
    assert any("image_id 2 重复" in p for p in problems), problems
    assert any("缺少 filename" in p for p in problems), problems


def test_audit_truth_detects_unknown_style_orphan_and_empty():
    m = _manifest([1])
    t = _truth([(1, [("not_a_style", "manual:x")]),
                (99, [("edge_text", "manual:x")]),
                (2, [])])
    problems = pf.audit_truth(m, t)
    assert any("不在 STYLE_ENUM" in p for p in problems), problems
    assert any("孤儿条目" in p for p in problems), problems
    assert any("watermarks 为空" in p for p in problems), problems


def test_audit_truth_detects_bad_bbox():
    m = _manifest([1, 2, 3])
    t = {"entries": [
        {"image_id": "1", "watermarks": [{"bbox": [0, 0, 10], "style": "edge_text"}],
         "labeled_by": "manual:x"},
        {"image_id": "2", "watermarks": [{"bbox": [0, 0, 0, 5], "style": "edge_text"}],
         "labeled_by": "manual:x"},
        {"image_id": "3", "watermarks": [{"bbox": [0.0, 0, 10, 10], "style": "edge_text"}],
         "labeled_by": "manual:x"},
    ]}
    problems = pf.audit_truth(m, t)
    assert sum("bbox" in p for p in problems) == 3, problems


# ---------------------------------------------------------------------------
# D. 升级清单必须覆盖所有带守卫的落点
# ---------------------------------------------------------------------------

def test_upgrade_checklist_covers_guarded_landing_spots():
    """清单漏一处，就会出现「照清单做完仍然红」——故清单本身要有守卫。"""
    text = "\n".join(pf.UPGRADE_CHECKLIST)
    for needle in ("collect_real_assets.py", "build_real_ground_truth.py", "validate",
                   "WATERMARK_TYPE_SCOPE", "README", "test_watermark_scope95.py",
                   "real_gate.py --update", "--strict-scope", "pytest"):
        assert needle in text, f"升级清单漏了带守卫的落点：{needle}"
    assert "**不要用 auto**" in text, "清单必须显式警告 auto 预填的循环论证风险"


def test_checklist_only_shown_when_upgradable():
    """`可升级` 才给步骤；`不可升级` 时给的是「差在哪」而不是一堆无关步骤。"""
    m = _manifest([1])
    t = _truth([(1, [("edge_text", "manual:x")])])
    r = pf.commit_readiness(m, t, min_images=3)
    detail = pf.format_class_detail("ai_text_heavy", r)
    assert "不可升级" in detail and "下限 3" in detail
    assert "升级步骤" not in detail
    r2 = pf.commit_readiness(m, t, min_images=1)
    assert "升级步骤" in pf.format_class_detail("ai_text_heavy", r2)


# ---------------------------------------------------------------------------
# E. 真实数据集现场守卫：标注必须全部人工
# ---------------------------------------------------------------------------

def test_real_dataset_annotations_are_all_manual():
    """真实基准不得出现 `auto:` 预填标注（循环论证污染承诺面）。

    阶段 150（Option A）移除了阶段 149 发现的 60 条模板化伪造 corner_logo 标注；
    **阶段 172 收编 10 张真实豆包图并逐图量取真值（`manual:measured`）** ⇒ 真值条目
    已从 0 回到 10。故本用例守的是「要么全 manual:*, 绝不许 auto:* 混进来」，
    且真值条数须与 `class_coverage` 自洽（阶段 183 按 172 后的真实数据重锚，
    旧断言 `n_marks == 0` 停在 150 的清空态、已陈旧）。
    """
    if not (os.path.exists(pf.MANIFEST_PATH) and os.path.exists(pf.TRUTH_PATH)):
        pytest.skip("真实基准数据集不存在")
    truth = pf._load(pf.TRUTH_PATH)
    auto = pf.auto_annotated_entries(truth)
    assert auto == [], f"真实基准里出现了 auto 预填标注：{auto[:5]}"
    marks = [w for e in truth.get("entries", []) for w in e.get("watermarks", [])]
    labeled = [e for e in truth.get("entries", []) if e.get("watermarks")]
    assert labeled, "真实真值应存在（阶段 172 收编 10 张真实豆包图）"
    # 溯源标在**条目级** `labeled_by`（如 `manual:measured` / `measured:highpass`），不是水印级。
    # 守卫意图是「禁 auto 预填（循环论证）」，故对齐模块自身的 `AUTO_PREFIX`，
    # 接受一切非 auto 的来源（含程序化高通路量取的 `measured:highpass`），阶段 239。
    assert all(not str(e.get("labeled_by", "")).startswith(pf.AUTO_PREFIX) for e in labeled), \
        f"真实基准的标注不得为 auto 预填（循环论证），实际 {[e.get('labeled_by') for e in labeled][:5]}"
    cov = pf.class_coverage(pf._load(pf.MANIFEST_PATH), truth)["ai_logo"]
    assert cov["n_marks"] == len(marks), "class_coverage 计数与真值条数不一致"
    # 阶段 172 交付物防回退：10 张真实豆包图（右下角角标）的真值不得被清空；
    # 此处用下限而非写死数量，允许后续合法收编新图（阶段 239：q 系列已扩至 29）。
    assert cov["n_marks"] >= 10, "阶段 172 收编的 10 张真实图真值应仍在（且可合法增长）"
    assert cov["committed"] is True, "ai_logo 仍承诺处理（config 要求承诺面非空）"


# ---------------------------------------------------------------------------
# F. 收件箱（交图口）：列目录 + 查重
# ---------------------------------------------------------------------------

def test_inbox_files_lists_only_images(tmp_path):
    """只列图片、排序、且**不碰文件**（README 之类不算待收编）。"""
    for name in ("b.png", "a.JPG", "README.md", "note.txt", "c.webp"):
        (tmp_path / name).write_bytes(b"x")
    assert pf.inbox_files(str(tmp_path)) == ["a.JPG", "b.png", "c.webp"]
    assert pf.inbox_files(str(tmp_path / "not-exist")) == []


def test_audit_inbox_detects_duplicate_with_manifest():
    m = {"images": [{"id": "07", "sha256": "same"}]}
    problems = pf.audit_inbox({"new.jpg": "same"}, m)
    assert problems and "已收编的 07" in problems[0], problems


def test_audit_inbox_detects_internal_duplicate():
    problems = pf.audit_inbox({"a.png": "s", "b.png": "s"}, {"images": []})
    assert problems and "重复" in problems[0], problems
    assert pf.audit_inbox({"a.png": "s1", "b.png": "s2"}, {"images": []}) == []


def test_report_renders_inbox_section():
    m = _manifest([1])
    t = _truth([(1, [("corner_logo", "manual:x")])])
    r = pf.commit_readiness(m, t)
    with_pending = pf.format_report(r, [], None, inbox={"n": 1, "names": ["a.png"]})
    assert "待收编（inbox）" in with_pending and "a.png" in with_pending
    empty = pf.format_report(r, [], None, inbox={"n": 0, "names": []})
    assert "待收编（inbox）：无" in empty
    # 不传 inbox 时不出现该段（向后兼容旧调用）
    assert "待收编" not in pf.format_report(r, [], None)


def test_real_scope_matches_config_commitment():
    """承诺面与真实数据集的一致性：ai_logo 承诺处理，且**真实真值已补齐**。

    阶段 150 时该数据集无真实真值 ⇒ `committed_missing == ["ai_logo"]`（期望红灯）；
    **阶段 172 收编 10 张真实豆包图并量取真值后真值补齐** ⇒ 按本用例自身写下的
    「待真值补齐后此断言应回到 []」重锚（阶段 183 更新，旧断言停在 150 态）。
    """
    if not (os.path.exists(pf.MANIFEST_PATH) and os.path.exists(pf.TRUTH_PATH)):
        pytest.skip("真实基准数据集不存在")
    from scripts.real_gate import annotation_scope
    sc = annotation_scope(pf._load(pf.MANIFEST_PATH), pf._load(pf.TRUTH_PATH))
    assert sc["committed_missing"] == [], sc["committed_missing"]
    assert sc["committed"] == ["ai_logo"]
    assert sorted(sc["uncommitted"]) == ["ai_faint_tiled", "ai_gradient",
                                        "ai_grid", "ai_text_heavy"]


# ---------------------------------------------------------------------------
# G. 真值量取完整性（阶段 149）：标签合规 ≠ 量取真实
# ---------------------------------------------------------------------------

def _templated_truth(n_images: int = 30) -> Dict[str, Any]:
    """复现当前真实基准的结构：每张图 2 个水印，bbox 逐张完全一样。"""
    return {"version": 1, "entries": [
        {"image_id": str(i), "labeled_by": "manual:measured",
         "watermarks": [{"bbox": [25, 28, 49, 37], "style": "corner_logo"},
                        {"bbox": [875, 952, 140, 60], "style": "corner_logo"}]}
        for i in range(1, n_images + 1)]}


def test_gt_integrity_flags_templated_current_real_data():
    """守卫必须能抓住模板化伪真值：30 张图的标注只有 2 个互不相同的 bbox、
    且每个 bbox 在 30 张图上逐字节复制——这是模板填充，不是量取。

    阶段 150 起真实基准已移除这 60 条伪造标注（见 docs/149），故本用例改用
    **合成**模板化真值验证守卫本身（与真实文件状态解耦），确保守卫逻辑不退化。
    """
    truth = _templated_truth(n_images=30)
    issues = pf.gt_integrity_issues(truth)
    assert issues, "守卫竟未检测到模板化伪真值！"
    joined = "\n".join(issues)
    assert "模板化" in joined, issues
    assert "信息量过低" in joined, issues


def test_gt_integrity_passes_distinct_bboxes():
    """干净真值：每张图的水印 bbox 各不相同 ⇒ 不应误报。"""
    truth = {"version": 1, "entries": [
        {"image_id": str(i), "labeled_by": "manual:measured",
         "watermarks": [{"bbox": [i * 7 % 900 + 20, i * 11 % 900 + 20, 40, 30],
                         "style": "corner_logo"},
                        {"bbox": [i * 13 % 800 + 40, i * 5 % 800 + 40, 25, 25],
                         "style": "edge_text"}]}
        for i in range(1, 31)]}
    assert pf.gt_integrity_issues(truth) == [], "干净真值被误报为模板化"


def test_gt_integrity_small_sample_not_flagged():
    """样本太少（< MIN_SAMPLE）即便 bbox 全相同也不判，避免小数据集误报。"""
    truth = {"version": 1, "entries": [
        {"image_id": str(i), "labeled_by": "manual:measured",
         "watermarks": [{"bbox": [10, 10, 40, 30], "style": "corner_logo"}]}
        for i in range(1, 6)]}
    assert pf.gt_integrity_issues(truth) == [], "小样本不应触发完整性判定"


def test_gt_integrity_fixed_corner_watermark_minority_not_flagged():
    """阶段 239 注入式反例：固定角标水印在少数图上共享同一 bbox 是合法的，不应误报模板化。

    复现真实 q 系列数据形态：29 张图里 3 张（豆包固定角位）共享同一 bbox，
    其余各不相同 ⇒ 占比 3/29≈10% < 50% 地板 ⇒ 守卫必须放过。这把「绝对计数 ≥3 即报」
    的旧缺陷锁死，确保修复后固定角标水印不再被误判为模板填充。
    """
    entries = []
    shared = [2446, 1433, 244, 62]
    for i in range(1, 30):
        box = shared if i <= 3 else [i * 7 % 900 + 20, i * 11 % 900 + 20, 40, 30]
        entries.append({"image_id": str(i), "labeled_by": "manual:measured",
                        "watermarks": [{"bbox": box, "style": "corner_logo"}]})
    truth = {"version": 1, "entries": entries}
    assert pf.gt_integrity_issues(truth) == [], "固定角标水印（少数图共享 bbox）被误报为模板化"


def test_gt_integrity_majority_shared_bbox_still_flagged():
    """占比口径仍须抓住真正的模板填充：>50% 的图共享同一 bbox（如 149 事故）必须报。

    10 张图里 8 张共享同一 bbox ⇒ 占比 80% ≥ 50% 地板 ⇒ 守卫必须报模板化，
    证明修复没有放松对真实复制填充的捕获。
    """
    entries = [{"image_id": str(i), "labeled_by": "manual:measured",
                "watermarks": [{"bbox": [25, 28, 49, 37], "style": "corner_logo"}]}
               for i in range(1, 9)]
    entries += [{"image_id": str(i), "labeled_by": "manual:measured",
                 "watermarks": [{"bbox": [i * 5 % 900 + 20, 30, 40, 30], "style": "corner_logo"}]}
                for i in range(9, 11)]
    truth = {"version": 1, "entries": entries}
    issues = pf.gt_integrity_issues(truth)
    assert issues, "真正过半图共享 bbox 的模板填充竟未被报出"
    assert any("模板化" in x for x in issues), issues
