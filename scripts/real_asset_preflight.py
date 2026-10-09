"""真实基准「交图 / 升级承诺类型」预检（阶段 108）。

它回答一个问题：
    **把某个水印类型从「不承诺」升级为 `committed: True`，现在够不够？**

为什么需要它
------------
承诺面（`config.WATERMARK_TYPE_SCOPE[*]["committed"]`）是本项目唯一一处
「对外声明会处理哪些类型」的地方，而 `real_gate` 对**承诺类型缺真值证据**是**硬失败**
（阶段 95：承诺比证据多 = 不可接受）。于是升级前必须先确认：
  · 该类在 `benchmark_real/ground_truth.json` 里**真有**达到数量下限的标注；
  · 这些标注**不是**自动预填的（见下）；
  · 清单与真值没有结构损坏（重复 id/sha、枚举外 style、孤儿条目）。

⚠ **最要紧的一条：`auto:` 标注不能当承诺类型的真值**
    `scripts/build_real_ground_truth.py auto` 是用**检测器自己的输出**预填真值
    （`labeled_by = "auto:<preset>"`）。拿它去评检测器是**循环论证**——
    recall/precision 会因此结构性偏高。故本预检把「该类存在 auto 标注」判为**不可升级**，
    现有的 60 条标注全部是 `manual:measured`（人工量取），是合规基线。

用法
----
    python scripts/real_asset_preflight.py                 # 全量预检报告（含收件箱）
    python scripts/real_asset_preflight.py --class ai_grid  # 单类：还差什么
    python scripts/real_asset_preflight.py --min-images 5   # 改数量下限（默认 3）
    python scripts/real_asset_preflight.py --json out.json

**交图口**：把真实图拖进 `benchmark_real/inbox/`（或直接给路径），本工具会列出
「待收编」清单并**查重**（与已收编的 30 张比对 sha256、以及收件箱内部自重复）。
收编用 `scripts/collect_real_assets.py add <path> --style <s> --copy`。

退出码
------
    0 = 通过（已承诺类型都有真值；无结构问题）
    1 = 发现问题（结构损坏 / 存在 auto 标注 / 已承诺类型缺真值）
    2 = 数据集缺失（manifest 或 ground_truth 不存在）

依赖：仅 json / config / scripts.real_gate（复用其 `annotation_scope`，零 gradio）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from scripts.build_real_ground_truth import STYLE_ENUM  # noqa: E402
from scripts.collect_real_assets import _sha256_of  # noqa: E402
from scripts.real_gate import MANIFEST_PATH, TRUTH_PATH, annotation_scope  # noqa: E402

#: 升级为承诺类型的最小真实图数（与 README「补 3~5 张」一致）。
MIN_IMAGES_FOR_COMMIT = 3

#: `labeled_by` 里表示「检测器自动预填」的前缀 —— 用它当真值是循环论证。
AUTO_PREFIX = "auto:"

#: 收件箱：交图暂存区（拖进图片即可，**不是**数据集本身）。
INBOX_DIR = os.path.join(os.path.dirname(MANIFEST_PATH), "inbox")
#: 收编时接受的图片扩展名（与 `collect_real_assets.IMAGE_EXTS` 同口径）。
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")

#: 升级一个类型的完整检查清单（可执行项，逐条都有守卫；改动时必须同步本清单）。
UPGRADE_CHECKLIST: Tuple[str, ...] = (
    "1. 把图放进 benchmark_real/images/，并登记清单："
    "`python scripts/collect_real_assets.py add <path> --style <style> --copy`"
    "（或 `scan` 批量收编 images/ 下的新文件）",
    "2. 人工标注真值（**不要用 auto**）："
    "`python scripts/build_real_ground_truth.py list` 看清单 → 手工写 `watermarks[]`"
    "（`bbox=[x,y,w,h]`、`style` 取 STYLE_ENUM、`labeled_by=\"manual:*\"`）"
    "→ `python scripts/build_real_ground_truth.py validate`",
    "3. 配置翻转：`config.WATERMARK_TYPE_SCOPE[<preset>][\"committed\"] = True`",
    "4. 同步 README：①「支持的水印类型」清单里把该类的验证状态列改成承诺态；"
    "② 承诺类数量那句要跟着改（两处都有测试守卫）",
    "5. 同步测试夹具：`tests/test_watermark_scope95.py` 的"
    "`test_committed_face_is_exactly_corner_logo` 与"
    "`test_real_dataset_scope_reports_commitment_face`（它们**故意**钉住承诺面，"
    "就是为了逼这次同步）",
    "6. 刷新真实图基线：`python scripts/real_gate.py --update`，"
    "再跑 `--strict-scope` 与 `--presets general,<新类预设>` 取该类真实指标",
    "7. 全量 pytest（承诺面守卫会连带检查 UI 文案与 README 是否同进同退）",
)


# ---------------------------------------------------------------------------
# 纯函数层（便于单测；不读文件除了显式传入的 dict）
# ---------------------------------------------------------------------------

def truth_styles_by_preset() -> Dict[str, Tuple[str, ...]]:
    """`{预设: 该类在真值里的 style 名}` —— 唯一来源 = `config.WATERMARK_TYPE_SCOPE`。"""
    return {name: tuple(entry.get("truth_styles") or ())
            for name, entry in config.WATERMARK_TYPE_SCOPE.items()}


def class_coverage(manifest: Dict[str, Any], truth: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """逐类型的**真值覆盖计数**与**标注来源构成**。

    返回 `{preset: {"n_images": n, "n_marks": m, "image_ids": [...],
                    "auto_images": [...], "labeled_by": {来源: 计数}, "committed": bool}}`
    """
    styles_by = truth_styles_by_preset()
    entries = truth.get("entries") or []
    out: Dict[str, Dict[str, Any]] = {}
    for preset, styles in styles_by.items():
        ids: List[str] = []
        n_marks = 0
        auto_ids: List[str] = []
        by_source: Dict[str, int] = {}
        for e in entries:
            marks = [w for w in (e.get("watermarks") or []) if w.get("style") in styles]
            if not marks:
                continue
            iid = str(e.get("image_id"))
            ids.append(iid)
            n_marks += len(marks)
            src = str(e.get("labeled_by") or "unknown")
            by_source[src] = by_source.get(src, 0) + 1
            if src.startswith(AUTO_PREFIX):
                auto_ids.append(iid)
        out[preset] = {
            "n_images": len(ids),
            "n_marks": n_marks,
            "image_ids": ids,
            "auto_images": auto_ids,
            "labeled_by": by_source,
            "committed": bool((config.WATERMARK_TYPE_SCOPE.get(preset) or {}).get("committed")),
        }
    return out


def audit_manifest(manifest: Dict[str, Any]) -> List[str]:
    """清单结构检查：id 重复 / sha256 重复（同一张图被收编两次）/ 缺必要字段。"""
    problems: List[str] = []
    seen_ids: Dict[str, int] = {}
    seen_sha: Dict[str, str] = {}
    for im in manifest.get("images") or []:
        iid = str(im.get("id"))
        seen_ids[iid] = seen_ids.get(iid, 0) + 1
        sha = im.get("sha256")
        if sha:
            if sha in seen_sha:
                problems.append(
                    f"[清单] {iid} 与 {seen_sha[sha]} 的 sha256 相同（同一张图被收编两次）")
            else:
                seen_sha[sha] = iid
        for key in ("id", "filename", "path"):
            if not im.get(key):
                problems.append(f"[清单] 条目缺少 {key}：{im!r}")
    for iid, n in seen_ids.items():
        if n > 1:
            problems.append(f"[清单] image_id {iid} 重复 {n} 次")
    return problems


def audit_truth(manifest: Dict[str, Any], truth: Dict[str, Any]) -> List[str]:
    """真值结构检查：枚举外 style / 孤儿条目 / 空标注 / bbox 非法·越界 / 缺 labeled_by。

    阶段 153：把原先只存在于 `build_real_ground_truth.validate` 的
    「bbox 必须为非负整数、且须在图内」两条**上收到此处**——让预检与 validate
    共用同一判据。否则同一个真值文件会出现「validate 绿（errors=0）而 preflight 红」
    的工具矛盾（阶段 153 审计发现），用户会被 validate 的绿灯误导。
    """
    problems: List[str] = []
    manifest_ids = {str(im.get("id")) for im in (manifest.get("images") or [])}
    sizes = {str(im.get("id")): im.get("original_size")
             for im in (manifest.get("images") or [])}
    for e in truth.get("entries") or []:
        iid = str(e.get("image_id"))
        if iid not in manifest_ids:
            problems.append(f"[真值] {iid} 不在清单里（孤儿条目）")
        marks = e.get("watermarks") or []
        if not marks:
            problems.append(f"[真值] {iid} 的 watermarks 为空——空标注会被读成「没有水印」")
        for w in marks:
            style = w.get("style")
            if style not in STYLE_ENUM:
                problems.append(f"[真值] {iid} 的 style={style!r} 不在 STYLE_ENUM 内")
            bbox = w.get("bbox") or []
            if len(bbox) != 4 or any(not isinstance(v, int) or v < 0 for v in bbox):
                problems.append(f"[真值] {iid} 的 bbox 非法（应为 4 个非负整数）：{bbox!r}")
            elif bbox[2] <= 0 or bbox[3] <= 0:
                problems.append(f"[真值] {iid} 的 bbox 宽高非正：{bbox!r}")
            else:
                size = sizes.get(iid)
                if (isinstance(size, (list, tuple)) and len(size) == 2
                        and all(isinstance(v, int) and v > 0 for v in size)
                        and (bbox[0] + bbox[2] > size[0] or bbox[1] + bbox[3] > size[1])):
                    problems.append(
                        f"[真值] {iid} 的 bbox 超出图像（{size[0]}x{size[1]}）：{bbox!r}")
        if not str(e.get("labeled_by") or "").strip():
            problems.append(f"[真值] {iid} 缺少 labeled_by（无法判断是否自动预填）")
    # 阶段 149：量取完整性——既要"格式对"，也要"量取真"。
    # 既有守卫只查 style/bbox/labeled_by 标签，一个把同一 bbox 复制到 30 张不同图、
    # 却标 manual:measured 的伪真值能 100% 通过。下面这层抓这种"标签合规但内容伪造"。
    problems.extend(gt_integrity_issues(truth))
    return problems


def gt_integrity_issues(truth: Dict[str, Any]) -> List[str]:
    """真值**量取完整性**检查（阶段 149 新增）。

    既有 `audit_truth` 只查"格式对不对"（style 在枚举内 / bbox 是 4 整数 / 有 labeled_by）。
    但它**不查"量取是否真实"**——一个把同一 bbox 复制到 30 张不同图、却标
    `manual:measured` 的伪真值，能 100% 通过既有所有守卫，却让"角标 recall ≥0.98"
    这类指标建立在伪造真值上（详见 `docs/149`）。

    本函数抓这种"标签合规但内容伪造"的盲区，两条信号都只在样本足够多时触发，避免小数据集误报：
      · 模板化：某个 bbox 在 **过半（≥ `MIN_TEMPLATE_FRACTION`）的图上** 逐字节相同
        （阶段 239 由绝对计数改为占图比：固定角标水印会自然在少数图上重复，
        用占比可避免误伤豆包等生成器把水印放在固定角落的合法数据；
        真正的模板填充是近乎全图一致，占比远超地板）。
      · 信息量过低：去重后的 bbox 数 / 总标注数 < `DEDUP_RATIO_FLOOR`
        （以 60 条标注为例，若只有 2 个互不相同的 bbox，则 2/60≈0.03，远低于地板）。
    """
    # 模板化判定改为「占图比」口径：同一 bbox 出现在**过半**图上才判（阶段 239）。
    # 旧阈值「≥3 张」会把「AI 生成图固定角标水印」在少数图上的自然重复误判为模板填充
    # ——豆包等生成器把水印放在固定角落，少数图共享同一 bbox 是合法现象，并非复制填充。
    MIN_TEMPLATE_FRACTION = 0.5  # 同一 bbox 出现在 >50% 的图上 ⇒ 模板填充嫌疑
    DEDUP_RATIO_FLOOR = 0.5    # 去重 bbox 占比低于此值 ⇒ 信息量过低
    MIN_SAMPLE = 10            # 样本太少不判（避免误报）

    from collections import Counter
    entries = truth.get("entries") or []
    n_images = len(entries)
    bboxes: List[Tuple] = []
    for e in entries:
        for w in (e.get("watermarks") or []):
            b = w.get("bbox")
            if isinstance(b, (list, tuple)) and len(b) == 4:
                bboxes.append(tuple(b))
    total = len(bboxes)
    if total < MIN_SAMPLE:
        return []
    distinct = len(set(bboxes))
    issues: List[str] = []
    top_box, top_n = Counter(bboxes).most_common(1)[0]
    # 占比口径：3/29≈10%（固定角标，合法）不报；30/30=100%（149 事故模板填充）才报。
    if n_images > 0 and (top_n / n_images) >= MIN_TEMPLATE_FRACTION:
        pct = top_n / n_images
        issues.append(
            f"[真值完整性] 模板化疑似：bbox {list(top_box)} 在 {top_n}/{n_images} 张图上逐字节相同"
            f"（占比 {pct:.0%} ≥ {MIN_TEMPLATE_FRACTION:.0%}）；真实量取的水印位置应各异"
            f"——疑似复制填充而非逐图量取。")
    ratio = distinct / total
    if ratio < DEDUP_RATIO_FLOOR:
        issues.append(
            f"[真值完整性] 信息量过低：{total} 条标注仅 {distinct} 个互不相同的 bbox"
            f"（去重占比 {ratio:.2f} < 地板 {DEDUP_RATIO_FLOOR}）——疑似复制填充而非真实量取。")
    return issues


def auto_annotated_entries(truth: Dict[str, Any]) -> List[Tuple[str, str]]:
    """返回 `[(image_id, labeled_by)]` —— 检测器自动预填的条目（循环论证风险）。"""
    return [(str(e.get("image_id")), str(e.get("labeled_by")))
            for e in (truth.get("entries") or [])
            if str(e.get("labeled_by") or "").startswith(AUTO_PREFIX)]


def structural_problems(manifest: Dict[str, Any], truth: Dict[str, Any]) -> List[str]:
    """真值文件的**全部结构 / 来源**问题（单一实现）。

    = `audit_manifest`（清单结构）+ `audit_truth`（真值结构）+ `auto:` 预填检查
    （循环论证风险）。预检 `main` 与 `build_real_ground_truth.validate` **都调它**，
    从而不会出现「同一规则两套实现 → 两工具对同一文件给出矛盾结论」（阶段 153）。
    """
    problems = audit_manifest(manifest) + audit_truth(manifest, truth)
    auto_entries = auto_annotated_entries(truth)
    if auto_entries:
        problems.append(
            f"[真值] {len(auto_entries)} 条为 auto 预填（示例：{auto_entries[:3]}）——"
            "这些条目不得作为承诺类型的真值（循环论证）")
    return problems


def inbox_files(directory: Optional[str] = None) -> List[str]:
    """收件箱里待收编的图片文件名（**只列目录，不动文件**）。"""
    d = directory or INBOX_DIR
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d)
                  if f.lower().endswith(IMAGE_EXTS))


def audit_inbox(pending: Dict[str, str], manifest: Dict[str, Any]) -> List[str]:
    """收件箱查重：与**已收编**的重复、或收件箱内部自重复。

    `pending` = `{文件名: sha256}`（由调用方算，便于单测不碰文件系统）。
    两个都必须拦：同一张图收编两次会让 benchmark 的计数虚高（且被
    `audit_manifest` 的 sha 检查抓到，但那时已经写进清单了——早拦更好）。
    """
    problems: List[str] = []
    known = {im.get("sha256"): str(im.get("id"))
             for im in (manifest.get("images") or []) if im.get("sha256")}
    seen: Dict[str, str] = {}
    for name, sha in sorted(pending.items()):
        if sha in known:
            problems.append(
                f"[收件箱] {name} 与已收编的 {known[sha]} 是同一张图（sha256 相同）")
        if sha in seen:
            problems.append(f"[收件箱] {name} 与 {seen[sha]} 重复（同一张图放了两份）")
        else:
            seen[sha] = name
    return problems


def commit_readiness(manifest: Dict[str, Any], truth: Dict[str, Any],
                     min_images: int = MIN_IMAGES_FOR_COMMIT) -> Dict[str, Dict[str, Any]]:
    """逐类型的**升级就绪判定**。

    `verdict` 三态：
        `已承诺`   —— 当前 `committed=True`
        `可升级`   —— 真值图数 ≥ `min_images` 且**无 auto 标注**
        `不可升级` —— 否则（`reasons` 说明差在哪）
    """
    cov = class_coverage(manifest, truth)
    out: Dict[str, Dict[str, Any]] = {}
    for preset, c in cov.items():
        reasons: List[str] = []
        if c["n_images"] < min_images:
            reasons.append(f"真值图数 {c['n_images']} < 下限 {min_images}")
        if c["auto_images"]:
            reasons.append(
                f"{len(c['auto_images'])} 张为 auto 预填（{', '.join(c['auto_images'][:5])}"
                f"{'…' if len(c['auto_images']) > 5 else ''}）——用检测器自己的输出当真值是循环论证")
        if c["committed"]:
            verdict = "已承诺"
            if c["n_images"] == 0:
                reasons.append("承诺类型缺真值证据 ⇒ `real_gate` 硬失败")
        else:
            verdict = "可升级" if not reasons else "不可升级"
        out[preset] = {**c, "verdict": verdict, "reasons": reasons,
                       "min_images": min_images}
    return out


def format_report(readiness: Dict[str, Dict[str, Any]],
                  problems: List[str],
                  scope: Optional[Dict[str, Any]] = None,
                  inbox: Optional[Dict[str, Any]] = None) -> str:
    lines = ["真实基准「交图 / 升级承诺类型」预检", ""]
    lines.append("| 预设 | 承诺 | 真值图数 | 标注数 | 标注来源 | auto | 判定 | 差在哪 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for preset, r in readiness.items():
        src = "、".join(f"{k}×{v}" for k, v in sorted(r["labeled_by"].items())) or "—"
        auto = len(r["auto_images"])
        reasons = "；".join(r["reasons"]) or "—"
        lines.append(
            f"| {preset} | {'✅' if r['committed'] else '—'} | {r['n_images']} | "
            f"{r['n_marks']} | {src} | {auto} | **{r['verdict']}** | {reasons} |")
    if scope is not None:
        lines.append("")
        lines.append(f"承诺面：{len(scope.get('committed') or [])} 类 "
                     f"({'、'.join(scope.get('committed') or []) or '无'}）；"
                     f"未承诺：{'、'.join(sorted(scope.get('uncommitted') or [])) or '无'}"
                     f"；承诺类缺真值：{'、'.join(scope.get('committed_missing') or []) or '无'}")
    if inbox is not None:
        names = inbox.get("names") or []
        if names:
            lines.append("")
            lines.append(f"待收编（inbox）：**{len(names)} 个** —— "
                         f"{'、'.join(names[:12])}{'…' if len(names) > 12 else ''}"
                         "（收编命令见 `docs/108` 第 1 步）")
        else:
            lines.append("")
            lines.append("待收编（inbox）：无 —— 把图拖进 `benchmark_real/inbox/`，"
                         "或直接给我路径。")
    if problems:
        lines.append("")
        lines.append(f"结构问题 {len(problems)} 项：")
        lines.extend(f"  · {p}" for p in problems)
    return "\n".join(lines)


def format_class_detail(preset: str, readiness: Dict[str, Dict[str, Any]]) -> str:
    """单类详情：还差什么 + 升级清单（可执行项）。"""
    r = readiness.get(preset)
    if r is None:
        return f"[错误] 未知类型 {preset!r}；可选：{'、'.join(readiness)}"
    lines = [f"### {preset}",
             f"- 当前判定：**{r['verdict']}**",
             f"- 真值图数：{r['n_images']}（下限 {r['min_images']}）／标注数：{r['n_marks']}",
             f"- 标注来源：{'、'.join(f'{k}×{v}' for k, v in sorted(r['labeled_by'].items())) or '—'}",
             f"- auto 预填：{len(r['auto_images'])} 张"
             + (f"（{', '.join(r['auto_images'])}）" if r['auto_images'] else ""),
             f"- 差在哪：{'；'.join(r['reasons']) or '—'}"]
    if r["verdict"] == "可升级":
        lines += ["", "升级步骤（每一项都有守卫，漏一项就会红）：", ""]
        lines += [f"  {step}" for step in UPGRADE_CHECKLIST]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _load(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="真实基准「交图 / 升级承诺类型」预检")
    ap.add_argument("--class", dest="preset", default=None, help="只看某一类型（预设名）")
    ap.add_argument("--min-images", type=int, default=MIN_IMAGES_FOR_COMMIT,
                    help=f"升级为承诺类型的最小真实图数（默认 {MIN_IMAGES_FOR_COMMIT}）")
    ap.add_argument("--json", dest="json_out", default=None, help="把结果写成 JSON")
    args = ap.parse_args()

    # 阶段 170：编码加固统一走共用件。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    for p in (MANIFEST_PATH, TRUTH_PATH):
        if not os.path.exists(p):
            print(f"[跳过] 数据集缺失：{p}", file=sys.stderr)
            return 2

    manifest, truth = _load(MANIFEST_PATH), _load(TRUTH_PATH)
    # 结构 / 来源问题走**单一实现** `structural_problems`——`build_real_ground_truth.validate`
    # 用同一函数，消灭「同一规则两套实现」（阶段 153）。
    problems = structural_problems(manifest, truth)
    # 收件箱：只列目录 + 算 sha256（**不动文件**），并查重（与已收编的、以及内部自重复）。
    inbox_names = inbox_files()
    pending: Dict[str, str] = {}
    for name in inbox_names:
        try:
            pending[name] = _sha256_of(os.path.join(INBOX_DIR, name))
        except Exception as exc:                                  # noqa: BLE001
            problems.append(f"[收件箱] {name} 读取失败：{exc}")
    problems.extend(audit_inbox(pending, manifest))
    inbox_info = {"n": len(inbox_names), "names": inbox_names}
    readiness = commit_readiness(manifest, truth, args.min_images)
    # 承诺面相关判定**复用 `real_gate.annotation_scope`**（同一语义不写两遍，经验 24/77）：
    # 门禁对这三类问题是硬失败，预检也必须同口径，否则会出现「预检绿、门禁红」。
    scope = annotation_scope(manifest, truth)
    if scope.get("contradictory"):
        problems.append(f"[范围] 清单声明与真值矛盾：{'；'.join(scope['contradictory'])}")
    if scope.get("unknown_styles"):
        problems.append(f"[范围] 真值含枚举外 style：{'、'.join(scope['unknown_styles'])}")
    if scope.get("committed_missing"):
        problems.append(
            f"[承诺] 已承诺却缺真值证据：{'、'.join(scope['committed_missing'])}"
            "（`real_gate` 对这条是硬失败：承诺不得比证据多）")

    if args.preset:
        print(format_class_detail(args.preset, readiness))
    else:
        print(format_report(readiness, problems, scope, inbox=inbox_info))

    if args.json_out:
        payload = {"min_images": args.min_images, "readiness": readiness,
                   "problems": problems, "scope": scope, "inbox": inbox_info}
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 已写入 {args.json_out}")

    if problems:
        print(f"\n❌ 预检未通过：{len(problems)} 项")
        for p in problems:
            print(f"  · {p}")
        return 1
    if not args.preset:
        # `--class` 模式已在详情里给出该类判定与下一步，不再打印全局结论——
        # 否则会出现「ai_grid 不可升级」紧接着「✅ 预检通过」这种自相矛盾的输出。
        print("\n✅ 预检通过（承诺类型都有真值证据，且标注来源合规）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
