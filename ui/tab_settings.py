# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/tab_settings.py
 文件说明 : 设置与导出标签页（阶段 6 核心界面）。
            把「查看运行配置、调整导出偏好、一键带走历史成果、管理缓存」
            集中到一个标签页，避免把导出/缓存按钮塞进其它处理页里干扰主线。

            复用关系：
            · 缓存：services.cache_service.get_cache()（与历史页/批量页同一单例，
              清空/清理在所有标签页即时一致）。
            · 导出：services.export_service（重编码 / 对比图 / 打包 ZIP）。
            · 控件：ui.components.build_export_format_selector（与批量页一致）。

            注意：主题切换、设备模式等需要重启才能生效的项，本页只做「展示」，
            并明确提示用户「改完需在 .env / 配置后重启程序」，不误导实时生效。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import gradio as gr

import config
from utils.i18n import t
from ui.i18n_rt import register, reg_value, reg_label, reg_label_info
from services.cache_service import get_cache
from services.export_service import (
    export_compare,
    export_history_as_zip,
    export_paths_as_zip,
)
from ui.components import build_export_format_selector, get_superres_widgets
from utils.format_utils import format_bytes
from utils.logger import get_logger
from utils.path_helper import sanitize_filename
from utils.exceptions import format_exception_for_ui

log = get_logger("ui.tab_settings")

# 共享缓存单例（与历史页/批量页同一实例）。
_CACHE = get_cache()


def _snap_state(flag: bool) -> str:
    """把布尔开关翻译成「开启 / 关闭」文案。"""
    return t("common.on") if flag else t("common.off")


def build_snapshot_md() -> str:
    """
    生成本次运行的配置快照 Markdown（只读展示，随当前语言变化）。

    返回值：
        Markdown 字符串，列出与用户最相关的运行参数。
    """
    lines = [
        t("settings.snap.title"),
        "",
        t("settings.snap.app", name=config.APP_NAME, version=config.APP_VERSION),
        t("settings.snap.device", mode=config.DEVICE_MODE),
        t("settings.snap.engine", engine=config.INPAINT_ENGINE),
        t("settings.snap.concurrency", n=config.BATCH_CONCURRENCY),
        t("settings.snap.cache", state=_snap_state(config.CACHE_ENABLED),
          days=config.CACHE_KEEP_DAYS, max=config.CACHE_MAX_RECORDS,
          cap_gb=round(config.CACHE_MAX_BYTES / (1024 * 1024 * 1024))),
        t("settings.snap.format", fmt=config.DEFAULT_OUTPUT_FORMAT,
          q=config.DEFAULT_OUTPUT_QUALITY),
        t("settings.snap.out", path=config.OUTPUT_DIR),
        t("settings.snap.export_dir", path=config.EXPORT_DIR),
        t("settings.snap.cache_dir", path=config.CACHE_DIR),
        t("settings.snap.listen", host=config.SERVER_HOST, port=config.SERVER_PORT),
        "",
        t("settings.snap.real_superres_title"),
        t("settings.snap.enabled", state=_snap_state(config.REAL_SUPERRES_ENABLED)),
        t("settings.snap.scale", scale=config.REAL_SUPERRES_SCALE),
        t("settings.snap.backend", backend=config.REAL_SUPERRES_BACKEND),
        "",
        t("settings.snap.interact_title"),
        t("settings.snap.drag", state=_snap_state(config.UI_GLOBAL_DRAGDROP)),
        t("settings.snap.shortcut", state=_snap_state(config.UI_SHORTCUTS_ENABLED)),
        t("settings.snap.compare", state=_snap_state(config.UI_COMPARE_VIEW)),
        "",
        t("settings.snap.restart"),
    ]
    return "\n".join(lines)


def on_export_history(fmt: str, quality: int) -> tuple[str, "str | None"]:
    """
    把全部历史成功结果导出为 ZIP（可按所选格式重编码）。

    参数说明：
        fmt     : 导出格式（PNG/JPG/WEBP）；空或 "PNG" 等同打包原文件。
        quality : 画质（1~100），配合 fmt 使用。

    返回值：
        (信息文本, ZIP 路径 or None)。
    """
    try:
        out_fmt = (fmt or config.DEFAULT_OUTPUT_FORMAT).upper()
        zip_path = export_history_as_zip(_CACHE, fmt=out_fmt, quality=quality)
        n = len([
            r for r in _CACHE.list_records()
            if r.status == "success" and r.result_path and _os.path.exists(r.result_path)
        ])
        return t("settings.snap.msg.export_zip_ok", n=n, path=zip_path), zip_path
    except FileNotFoundError as exc:
        return t("settings.snap.msg.err", exc=format_exception_for_ui(exc)), None
    except Exception as exc:  # noqa: BLE001
        log.exception("导出历史 ZIP 失败")
        return t("settings.snap.msg.err", exc=format_exception_for_ui(exc)), None


def on_export_compares(gap: int) -> tuple[str, "str | None"]:
    """
    为全部历史成功记录生成「原图|修复后」对比图并打包成 ZIP。

    参数说明：
        gap : 对比图间隔像素（来自页面滑块，实时生效）。

    返回值：
        (信息文本, ZIP 路径 or None)。
    """
    recs = [
        r for r in _CACHE.list_records()
        if r.status == "success"
        and r.result_path and _os.path.exists(r.result_path)
        and r.original_path and _os.path.exists(r.original_path)
    ]
    if not recs:
        return t("settings.snap.msg.no_compare"), None
    import tempfile

    paths: list[str] = []
    names: list[str] = []
    # 阶段 342：对比图是**打包用的中间产物**，必须写到临时目录。
    # 以前 `export_compare(...)` 不传 `out_path` ⇒ 每张都落到 `config.EXPORT_DIR`，
    # 点一次「导出对比图 ZIP」在 `exports/` 里留下 N 张全尺寸 PNG，而 ZIP 才是交付物；
    # `scan_orphans` 明确不扫 exports/（用户成品目录）⇒ 孤儿清理也收不回，磁盘只会一直涨。
    failed: list = []
    with tempfile.TemporaryDirectory(prefix="wme_cmpzip_") as work:
        for i, r in enumerate(recs):
            try:
                base = sanitize_filename(_os.path.splitext(r.original_name or "image")[0], 120)
                name = f"{base}__{r.id}.png"
                cmp = export_compare(r.original_path, r.result_path, gap=int(gap), label=True,
                                     out_path=_os.path.join(work, f"{i:04d}_{name}"))
                paths.append(cmp)
                names.append(name)
            except Exception as exc:  # noqa: BLE001
                failed.append(r.original_name or r.id)
                log.warning("生成对比图失败 %s：%s", r.original_name, exc)
        if not paths:
            return t("settings.snap.msg.compare_all_fail"), None
        try:
            zip_path = export_paths_as_zip(paths, names=names)
            if failed:
                return (t("settings.snap.msg.compare_zip_partial", n=len(paths),
                           failed=len(failed), names=", ".join(failed[:3]),
                           path=zip_path), zip_path)
            return t("settings.snap.msg.compare_zip_ok", n=len(paths), path=zip_path), zip_path
        except Exception as exc:  # noqa: BLE001
            log.exception("打包对比图失败")
            return t("settings.snap.msg.zip_fail", exc=format_exception_for_ui(exc)), None


def on_clear_cache() -> tuple[str, "str | None"]:
    """立即清空全部历史记录（保留 outputs 成品图）。"""
    n = _CACHE.clear()
    return t("settings.snap.msg.clear_ok", n=n), None


def on_prune_cache() -> tuple[str, "str | None"]:
    """立即按保留策略清理过期/超额记录。"""
    n = _CACHE.prune()
    return t("settings.snap.msg.prune_ok", n=n), None


def _superres_refresh_updates() -> "list":
    """构造跨页刷新更新列表：按注册表顺序，每组 [enabled, scale, backend]。"""
    updates: list = []
    for w in get_superres_widgets():
        updates.append(gr.update(value=config.REAL_SUPERRES_ENABLED))
        updates.append(gr.update(value=config.REAL_SUPERRES_SCALE))
        updates.append(gr.update(value=config.REAL_SUPERRES_BACKEND))
    return updates


def apply_superres_config(
    enabled: bool, scale: int, backend: str, save_default: bool
) -> "list":
    """
    设置页「应用真实超分」：实时写入运行时配置 + 可选持久化 + 跨页刷新控件。

    参数说明：
        enabled      : 总开关（来自设置页 sr_enabled）。
        scale        : 放大倍数（来自设置页 sr_scale）。
        backend      : 后端种类（来自设置页 sr_backend）。
        save_default : 是否把三旋钮写入 config.user_overrides.json 持久化。

    返回值：
        扁平列表：依次为每个已注册功能页的 [enabled, scale, backend] 更新，
        末尾追加 [快照 Markdown, 信息文案]，与 sr_apply.click 的 outputs 对齐。
    """
    try:
        # Final 仅类型检查约束，运行时模块属性可安全覆盖（与阶段 34 一致）。
        config.REAL_SUPERRES_ENABLED = bool(enabled)
        config.REAL_SUPERRES_SCALE = int(scale)
        config.REAL_SUPERRES_BACKEND = str(backend)
    except Exception as exc:  # noqa: BLE001
        log.exception("写入真实超分运行时配置失败")
        return _superres_refresh_updates() + [
            build_snapshot_md(),
            t("settings.snap.msg.err", exc=format_exception_for_ui(exc)),
        ]

    # 可选：持久化为默认（仅白名单内键；缺权限等失败不影响实时生效）。
    _persist_warn = ""
    # 合并式写入：只更新真实超分三键，不会抹掉已保存的保留策略。
    if save_default:
        try:
            ok = config.save_user_overrides({
                "REAL_SUPERRES_ENABLED": config.REAL_SUPERRES_ENABLED,
                "REAL_SUPERRES_SCALE": config.REAL_SUPERRES_SCALE,
                "REAL_SUPERRES_BACKEND": config.REAL_SUPERRES_BACKEND,
            })
            if not ok:
                # 阶段 129：save_user_overrides 失败时返回 False 且不抛异常；
                # 不消费返回值 ⇒「保存为默认」写盘失败将完全无痕（下方 except 永不触发）。
                log.warning("持久化真实超分默认未生效：save_user_overrides 返回 False"
                            "（无写权限或值非法）。")
                _persist_warn = t("settings.snap.msg.save_default_failed")
        except Exception as exc:  # noqa: BLE001
            log.warning("持久化真实超分默认失败：%s", exc)
            _persist_warn = t("settings.snap.msg.save_default_failed")

    snap = build_snapshot_md()
    info = t(
        "settings.snap.msg.superres_applied",
        state=_snap_state(config.REAL_SUPERRES_ENABLED),
        scale=config.REAL_SUPERRES_SCALE,
        backend=config.REAL_SUPERRES_BACKEND,
    )
    if _persist_warn:
        info = f"{info}\n{_persist_warn}"
    return _superres_refresh_updates() + [snap, info]


def apply_cache_config(
    keep_days: int, max_records: int, max_bytes_gb: int, save_default: bool
) -> "tuple[str, str]":
    """
    设置页「应用历史保留策略」：实时写入运行时配置（保留天数 / 最大条数 / 总体积上限）
    + 可选持久化 + 立即按策略清理（天数/条数 + 体积）+ 刷新快照。

    参数说明：
        keep_days    : 保留天数（来自设置页 cp_keep_days）。
        max_records  : 最大记录条数（来自设置页 cp_max_records）。
        max_bytes_gb : 总体积上限（GB，来自设置页 cp_max_bytes），内部换算为字节。
        save_default : 是否把三旋钮写入 config.user_overrides.json 持久化。

    返回值：
        (快照 Markdown, 信息文案)，与 cache_apply.click 的 outputs 对齐。
    """
    try:
        # Final 仅类型检查约束，运行时模块属性可安全覆盖（与阶段 34/35 一致）。
        config.CACHE_KEEP_DAYS = int(keep_days)
        config.CACHE_MAX_RECORDS = int(max_records)
        config.CACHE_MAX_BYTES = int(max_bytes_gb) * 1024 * 1024 * 1024
    except Exception as exc:  # noqa: BLE001
        log.exception("写入历史保留策略运行时配置失败")
        return build_snapshot_md(), t("settings.snap.msg.err", exc=format_exception_for_ui(exc))

    # 可选：持久化为默认（合并式写入，不会抹掉真实超分默认）。
    if save_default:
        try:
            ok = config.save_user_overrides({
                "CACHE_KEEP_DAYS": config.CACHE_KEEP_DAYS,
                "CACHE_MAX_RECORDS": config.CACHE_MAX_RECORDS,
                "CACHE_MAX_BYTES": config.CACHE_MAX_BYTES,
            })
            if not ok:
                log.warning("持久化历史保留策略默认未生效：save_user_overrides 返回 False"
                            "（无写权限或值非法）。")
        except Exception as exc:  # noqa: BLE001
            log.warning("持久化历史保留策略默认失败：%s", exc)

    # 立即按新策略清理一次（保留天数 / 条数上限 + 总体积上限均已实时生效）。
    n_keep = 0
    n_size = 0
    try:
        n_keep = _CACHE.prune()
        n_size = _CACHE.prune_by_size(config.CACHE_MAX_BYTES)
    except Exception as exc:  # noqa: BLE001
        log.exception("按策略清理历史失败")
        n_keep = n_keep or 0
        n_size = n_size or 0

    snap = build_snapshot_md()
    cap_str = f"{config.CACHE_MAX_BYTES / 1024 / 1024 / 1024:.0f} GB"
    info = t(
        "settings.snap.msg.cache_applied",
        days=config.CACHE_KEEP_DAYS,
        max=config.CACHE_MAX_RECORDS,
        bytes=cap_str,
        n=n_keep + n_size,
    )
    return snap, info


# ==============================================================================
# 阶段 48：孤儿文件扫描与清理（磁盘治理闭环）
# ==============================================================================
# 与「历史保留策略」互补：
#   保留策略管的是「记录」——按时间 / 条数 / 总体积决定留哪些历史条目；
#   孤儿清理管的是「文件」——回收那些已没有任何记录引用的缓存文件。
# 两者都遵循同一安全范式：先预览（只读扫描），再经二次确认才落删。

def apply_processing_prefs(
    dual_run: bool,
    keep_alpha: bool,
    exif_orient: bool,
    bit_depth: bool,
    save_default: bool,
    preserve_untouched: bool | None = None,
    follow_input_fmt: bool | None = None,
) -> "tuple[str, str]":
    """
    设置页「处理偏好」（阶段 75）：实时写入运行时配置 + 可选持久化 + 刷新快照。

    覆盖四个偏好（均可经 user_overrides 持久化为默认）：
        dual_run   : 双跑择优（阶段 74，耗时约翻倍）；
        keep_alpha : 导出保留透明通道（阶段 71；关闭时合成到背景色）；
        exif_orient: 加载端应用 EXIF 方向（阶段 70）；
        bit_depth  : 非 8 位输入位深规范化（阶段 70）。

    阶段 318c 新增 `preserve_untouched`（只动水印区）；阶段 320d 新增 `follow_input_fmt`
    （输出格式跟随输入）：两者默认 **None = 不改这项**，以便旧调用方/旧测试保持原语义；
    设置页显式传布尔值时才写入与持久化。

    返回值：
        (快照 Markdown, 信息文案)，与 prefs_apply.click 的 outputs 对齐。
    """
    try:
        # Final 仅类型检查约束，运行时模块属性可安全覆盖（与阶段 34/35 一致）。
        config.INPAINT_DUAL_RUN = bool(dual_run)
        config.PNG_KEEP_ALPHA = bool(keep_alpha)
        config.IMAGE_APPLY_EXIF_ORIENTATION = bool(exif_orient)
        config.IMAGE_NORMALIZE_BIT_DEPTH = bool(bit_depth)
        if preserve_untouched is not None:
            config.POSTPROCESS_PRESERVE_UNTOUCHED = bool(preserve_untouched)
        if follow_input_fmt is not None:
            config.OUTPUT_FMT_FOLLOW_INPUT = bool(follow_input_fmt)
    except Exception as exc:  # noqa: BLE001
        log.exception("写入处理偏好运行时配置失败")
        return build_snapshot_md(), t("settings.snap.msg.err", exc=format_exception_for_ui(exc))

    # 可选：持久化为默认（合并式写入，不影响真实超分/保留策略键）。
    if save_default:
        try:
            patch = {
                "INPAINT_DUAL_RUN": config.INPAINT_DUAL_RUN,
                "PNG_KEEP_ALPHA": config.PNG_KEEP_ALPHA,
                "IMAGE_APPLY_EXIF_ORIENTATION": config.IMAGE_APPLY_EXIF_ORIENTATION,
                "IMAGE_NORMALIZE_BIT_DEPTH": config.IMAGE_NORMALIZE_BIT_DEPTH,
            }
            if preserve_untouched is not None:
                patch["POSTPROCESS_PRESERVE_UNTOUCHED"] = config.POSTPROCESS_PRESERVE_UNTOUCHED
            if follow_input_fmt is not None:
                patch["OUTPUT_FMT_FOLLOW_INPUT"] = config.OUTPUT_FMT_FOLLOW_INPUT
            ok = config.save_user_overrides(patch)
            if not ok:
                log.warning("持久化处理偏好默认未生效：save_user_overrides 返回 False"
                            "（无写权限或值非法）。")
        except Exception as exc:  # noqa: BLE001
            log.warning("持久化处理偏好失败：%s", exc)

    snap = build_snapshot_md()
    info = t(
        "settings.prefs.msg_applied",
        dual=_snap_state(config.INPAINT_DUAL_RUN),
        alpha=_snap_state(config.PNG_KEEP_ALPHA),
        exif=_snap_state(config.IMAGE_APPLY_EXIF_ORIENTATION),
        depth=_snap_state(config.IMAGE_NORMALIZE_BIT_DEPTH),
        # 阶段 318c：确认文案必须带上这一项，否则用户勾了也看不出是否生效
        #   （实测点击「应用」后原文案只报四项）。
        preserve=_snap_state(config.POSTPROCESS_PRESERVE_UNTOUCHED),
        fmt=_snap_state(config.OUTPUT_FMT_FOLLOW_INPUT),
    )
    return snap, info


def scan_orphans_md() -> str:
    """
    只读扫描缓存目录中的孤儿文件，并渲染为可预览的 Markdown 清单。

    返回值：
        Markdown 文本：按来源列出「文件数 + 可释放空间」，末尾给出合计；
        缓存目录干净时返回无孤儿提示；扫描异常时返回错误提示（不抛出）。

    说明：
        扫描范围仅限 cache/thumbs 与 cache/uploads，绝不触碰 outputs /
        exports 等用户成品目录（详见 CacheService.scan_orphans）。
    """
    try:
        scan = _CACHE.scan_orphans()
    except Exception as exc:  # noqa: BLE001
        log.exception("扫描孤儿文件失败")
        return t("settings.disk_clean.fail", exc=format_exception_for_ui(exc))

    total = scan.get("__total__", {"count": 0, "bytes": 0})
    if not total.get("count"):
        return t("settings.disk_clean.none")

    lines = []
    for origin, label_key in (("thumbs", "src_thumbs"), ("uploads", "src_uploads")):
        info = scan.get(origin)
        if not info or not info.get("count"):
            continue
        lines.append(t(
            "settings.disk_clean.line",
            label=t(f"settings.disk_clean.{label_key}"),
            count=info["count"],
            size=format_bytes(info["bytes"]),
        ))

    md = "\n".join(lines)
    md += t(
        "settings.disk_clean.total",
        count=total["count"],
        size=format_bytes(total["bytes"]),
    )
    return md


def purge_orphans(src_thumbs: bool, src_uploads: bool, confirm: bool):
    """
    按所选来源清理孤儿文件（破坏性操作，必须二次确认）。

    参数说明：
        src_thumbs  : 是否清理缩略图（cache/thumbs）。
        src_uploads : 是否清理原图副本（cache/uploads）。
        confirm     : 二次确认复选框（沿用阶段 43 范式）。

    返回值（三元组，与 clean_btn.click 的 outputs 对齐）：
        1. 刷新后的孤儿清单 Markdown（清理后重新扫描，直观看到归零）；
        2. 信息文案（未确认 / 未选来源 / 成功 / 部分失败 / 异常）；
        3. 确认复选框复位（gr.update(value=False)）。

    安全性：
        · confirm=False 时直接拒绝执行，不删除任何文件。
        · 空来源（两个都没勾）语义为「未选择 → 不删」，绝不退化成全删。
        · 服务层在执行前会重新扫描，避免误删预览之后新产生的引用文件。
    """
    if not confirm:
        return scan_orphans_md(), t("settings.disk_clean.confirm_needed"), gr.update(value=False)

    origins = []
    if src_thumbs:
        origins.append("thumbs")
    if src_uploads:
        origins.append("uploads")
    if not origins:
        return scan_orphans_md(), t("settings.disk_clean.need_select"), gr.update(value=False)

    try:
        # 清理前先取总数，用于推算「被跳过」的文件数（partial 文案）。
        before = _CACHE.scan_orphans()
        want = before["__total__"]["count"]
        if origins and len(origins) < 2:
            want = sum(before.get(o, {}).get("count", 0) for o in origins)

        removed, freed = _CACHE.purge_orphans(origins)
    except Exception as exc:  # noqa: BLE001
        log.exception("清理孤儿文件失败")
        return scan_orphans_md(), t("settings.disk_clean.fail", exc=format_exception_for_ui(exc)), gr.update(value=False)

    # 阶段 129：判据不能用 `removed and removed < want`——`removed and` 会把
    # 「want>0 但一个都没删掉」（全部被占用/权限拒绝）短路进 ok 分支，报「已清理 0 个」，
    # 掩盖了「0 成功 / N 失败」的事实。改为纯数量比较：0 < want 即 partial（含失败数）。
    if removed < want:
        info = t("settings.disk_clean.partial", count=removed,
                 size=format_bytes(freed), failed=want - removed)
    else:
        info = t("settings.disk_clean.ok", count=removed, size=format_bytes(freed))
    return scan_orphans_md(), info, gr.update(value=False)


def build_settings_tab():
    """构建「设置与导出」标签页。"""
    with gr.Column():
        reg_value(gr.Markdown(t("settings.title")), "settings.title")
        reg_value(gr.Markdown(t("settings.subtitle")), "settings.subtitle")

        snapshot = gr.Markdown(value=build_snapshot_md(), label=t("settings.snapshot_label"))
        register(snapshot, lambda: {"value": build_snapshot_md()})

        with gr.Accordion(t("settings.export_accordion"), open=True) as _exp_acc:
            reg_label(_exp_acc, "settings.export_accordion")
            fmt, quality = build_export_format_selector()
            compare_gap = gr.Slider(
                minimum=0, maximum=200,
                value=config.EXPORT_COMPARE_GAP, step=1,
                label=t("settings.compare_gap_label"),
                info=t("settings.compare_gap_info"),
            )
            reg_label_info(compare_gap, "settings.compare_gap_label", "settings.compare_gap_info")

        # 阶段 35：真实超分旋钮（设置页暴露，可实时生效 + 保存为默认）。
        with gr.Accordion(t("settings.real_superres.title"), open=True) as _sr_acc:
            reg_label(_sr_acc, "settings.real_superres.title")
            sr_enabled = gr.Checkbox(
                value=config.REAL_SUPERRES_ENABLED,
                label=t("settings.real_superres.enabled_label"),
                info=t("settings.real_superres.enabled_info"),
            )
            reg_label_info(sr_enabled, "settings.real_superres.enabled_label", "settings.real_superres.enabled_info")
            sr_scale = gr.Slider(
                minimum=1, maximum=8,
                value=config.REAL_SUPERRES_SCALE, step=1,
                label=t("settings.real_superres.scale_label"),
                info=t("settings.real_superres.scale_info"),
            )
            reg_label_info(sr_scale, "settings.real_superres.scale_label", "settings.real_superres.scale_info")
            sr_backend = gr.Dropdown(
                choices=["auto", "esrgan", "realesrgan"],
                value=config.REAL_SUPERRES_BACKEND,
                label=t("settings.real_superres.backend_label"),
                info=t("settings.real_superres.backend_info"),
            )
            reg_label_info(sr_backend, "settings.real_superres.backend_label", "settings.real_superres.backend_info")
            with gr.Row():
                sr_save = gr.Checkbox(
                    value=False,
                    label=t("settings.real_superres.save_label"),
                    info=t("settings.real_superres.save_info"),
                )
                reg_label_info(sr_save, "settings.real_superres.save_label", "settings.real_superres.save_info")
                sr_apply = gr.Button(t("settings.real_superres.apply"), variant="primary")
                reg_value(sr_apply, "settings.real_superres.apply")

        # 阶段 46：历史保留策略（设置页暴露，可实时生效 + 保存为默认）。
        with gr.Accordion(t("settings.cache_policy.title"), open=True) as _cp_acc:
            reg_label(_cp_acc, "settings.cache_policy.title")
            cp_keep_days = gr.Slider(
                minimum=1, maximum=3650,
                value=config.CACHE_KEEP_DAYS, step=1,
                label=t("settings.cache_policy.keep_days_label"),
                info=t("settings.cache_policy.keep_days_info"),
            )
            reg_label_info(cp_keep_days, "settings.cache_policy.keep_days_label", "settings.cache_policy.keep_days_info")
            cp_max_records = gr.Slider(
                minimum=10, maximum=5000,
                value=config.CACHE_MAX_RECORDS, step=1,
                label=t("settings.cache_policy.max_records_label"),
                info=t("settings.cache_policy.max_records_info"),
            )
            reg_label_info(cp_max_records, "settings.cache_policy.max_records_label", "settings.cache_policy.max_records_info")
            cp_max_bytes = gr.Slider(
                minimum=1, maximum=100,
                value=max(1, round(config.CACHE_MAX_BYTES / (1024 * 1024 * 1024))),
                step=1,
                label=t("settings.cache_policy.max_bytes_label"),
                info=t("settings.cache_policy.max_bytes_info"),
            )
            reg_label_info(cp_max_bytes, "settings.cache_policy.max_bytes_label", "settings.cache_policy.max_bytes_info")
            with gr.Row():
                cp_save = gr.Checkbox(
                    value=False,
                    label=t("settings.cache_policy.save_label"),
                    info=t("settings.cache_policy.save_info"),
                )
                reg_label_info(cp_save, "settings.cache_policy.save_label", "settings.cache_policy.save_info")
                cp_apply = gr.Button(t("settings.cache_policy.apply"), variant="primary")
                reg_value(cp_apply, "settings.cache_policy.apply")

        # 阶段 75：处理偏好（双跑择优 / 透明通道 / EXIF 方向 / 位深规范化）
        #         ——实时生效 + 可保存为默认（user_overrides 白名单已扩容）。
        with gr.Accordion(t("settings.prefs.title"), open=True) as _pp_acc:
            reg_label(_pp_acc, "settings.prefs.title")
            with gr.Row():
                pp_dual = gr.Checkbox(
                    value=config.INPAINT_DUAL_RUN,
                    label=t("settings.prefs.dual_label"),
                    info=t("settings.prefs.dual_info"),
                )
                reg_label_info(pp_dual, "settings.prefs.dual_label", "settings.prefs.dual_info")
                pp_alpha = gr.Checkbox(
                    value=config.PNG_KEEP_ALPHA,
                    label=t("settings.prefs.keep_alpha_label"),
                    info=t("settings.prefs.keep_alpha_info"),
                )
                reg_label_info(pp_alpha, "settings.prefs.keep_alpha_label", "settings.prefs.keep_alpha_info")
            with gr.Row():
                pp_exif = gr.Checkbox(
                    value=config.IMAGE_APPLY_EXIF_ORIENTATION,
                    label=t("settings.prefs.exif_label"),
                    info=t("settings.prefs.exif_info"),
                )
                reg_label_info(pp_exif, "settings.prefs.exif_label", "settings.prefs.exif_info")
                pp_depth = gr.Checkbox(
                    value=config.IMAGE_NORMALIZE_BIT_DEPTH,
                    label=t("settings.prefs.bitdepth_label"),
                    info=t("settings.prefs.bitdepth_info"),
                )
                reg_label_info(pp_depth, "settings.prefs.bitdepth_label", "settings.prefs.bitdepth_info")
            # 阶段 318c：只动水印区（掩码 + 8px 带之外逐字节还原为原图）。
            #   实测：误伤（水印外被改动像素）中位 50% → 约 1%，0/60 用例擦除度变差。
            #   默认关：关着时保持现行「整图轻微锐化」观感；开了则承诺更诚实。
            with gr.Row():
                pp_preserve = gr.Checkbox(
                    value=config.POSTPROCESS_PRESERVE_UNTOUCHED,
                    label=t("settings.prefs.preserve_label"),
                    info=t("settings.prefs.preserve_info"),
                )
                reg_label_info(pp_preserve, "settings.prefs.preserve_label",
                               "settings.prefs.preserve_info")
                pp_follow_fmt = gr.Checkbox(
                    value=config.OUTPUT_FMT_FOLLOW_INPUT,
                    label=t("settings.prefs.follow_fmt_label"),
                    info=t("settings.prefs.follow_fmt_info"),
                )
                reg_label_info(pp_follow_fmt, "settings.prefs.follow_fmt_label",
                               "settings.prefs.follow_fmt_info")
            with gr.Row():
                prefs_save = gr.Checkbox(
                    value=False,
                    label=t("settings.prefs.save_label"),
                    info=t("settings.prefs.save_info"),
                )
                reg_label_info(prefs_save, "settings.prefs.save_label", "settings.prefs.save_info")
                prefs_apply = gr.Button(t("settings.prefs.apply"), variant="primary")
                reg_value(prefs_apply, "settings.prefs.apply")

        # 阶段 48：磁盘清理（孤儿文件回收）——与保留策略互补：
        #         保留策略管「记录」，孤儿清理管「无引用的缓存文件」。
        with gr.Accordion(t("settings.disk_clean.title"), open=False) as _dc_acc:
            reg_label(_dc_acc, "settings.disk_clean.title")
            gr.Markdown(t("settings.disk_clean.hint"))
            orphan_md = gr.Markdown(t("settings.disk_clean.need_scan"))
            with gr.Row():
                dc_thumbs = gr.Checkbox(
                    value=True, label=t("settings.disk_clean.src_thumbs"))
                reg_label(dc_thumbs, "settings.disk_clean.src_thumbs")
                dc_uploads = gr.Checkbox(
                    value=True, label=t("settings.disk_clean.src_uploads"))
                reg_label(dc_uploads, "settings.disk_clean.src_uploads")
            dc_confirm = gr.Checkbox(
                value=False, label=t("settings.disk_clean.confirm_label"))
            reg_label(dc_confirm, "settings.disk_clean.confirm_label")
            with gr.Row():
                dc_scan = gr.Button(t("settings.disk_clean.scan_btn"))
                reg_value(dc_scan, "settings.disk_clean.scan_btn")
                dc_clean = gr.Button(
                    t("settings.disk_clean.clean_btn"), variant="stop")
                reg_value(dc_clean, "settings.disk_clean.clean_btn")

        with gr.Row():
            export_btn = gr.Button(t("settings.export_zip"), variant="primary")
            reg_value(export_btn, "settings.export_zip")
            compares_btn = gr.Button(t("settings.export_compares"))
            reg_value(compares_btn, "settings.export_compares")
        with gr.Row():
            clear_btn = gr.Button(t("settings.clear"), variant="stop")
            reg_value(clear_btn, "settings.clear")
            prune_btn = gr.Button(t("settings.prune"))
            reg_value(prune_btn, "settings.prune")
            snap_btn = gr.Button(t("settings.refresh"))
            reg_value(snap_btn, "settings.refresh")

        info = gr.Markdown("")
        dl = gr.File(label=t("settings.download_label"), interactive=False)
        reg_label(dl, "settings.download_label")

        # 绑定：导出历史 ZIP
        export_btn.click(
            fn=on_export_history,
            inputs=[fmt, quality],
            outputs=[info, dl],
        )
        # 绑定：导出对比图 ZIP（实时用 compare_gap）
        compares_btn.click(
            fn=on_export_compares,
            inputs=[compare_gap],
            outputs=[info, dl],
        )
        # 绑定：清空 / 清理 / 刷新快照
        clear_btn.click(fn=on_clear_cache, inputs=[], outputs=[info, dl])
        prune_btn.click(fn=on_prune_cache, inputs=[], outputs=[info, dl])
        snap_btn.click(fn=build_snapshot_md, inputs=[], outputs=[snapshot])

        # 阶段 35：真实超分「应用」——跨页刷新全部功能页真实超分控件 + 刷新快照。
        # 设置页在功能页之后构建，注册表此时已填满，可安全列出所有目标控件。
        sr_targets: list = []
        for w in get_superres_widgets():
            sr_targets.append(w["enabled"])
            sr_targets.append(w["scale"])
            sr_targets.append(w["backend"])
        sr_apply.click(
            fn=apply_superres_config,
            inputs=[sr_enabled, sr_scale, sr_backend, sr_save],
            outputs=sr_targets + [snapshot, info],
        )

        # 阶段 46：历史保留策略「应用」——实时写入运行时 + 可选持久化 +
        #         立即按策略清理 + 刷新快照（无跨页控件，outputs=[snapshot, info]）。
        cp_apply.click(
            fn=apply_cache_config,
            inputs=[cp_keep_days, cp_max_records, cp_max_bytes, cp_save],
            outputs=[snapshot, info],
        )

        # 阶段 75：处理偏好「应用」——实时写入运行时 + 可选持久化 + 刷新快照。
        prefs_apply.click(
            fn=apply_processing_prefs,
            inputs=[pp_dual, pp_alpha, pp_exif, pp_depth, prefs_save, pp_preserve, pp_follow_fmt],
            outputs=[snapshot, info],
        )

        # 阶段 48：孤儿文件「扫描」（只读，仅刷新清单）与「清理」
        #         （破坏性，需二次确认；复用下方 info 展示结果文案）。
        dc_scan.click(
            fn=scan_orphans_md,
            inputs=[],
            outputs=[orphan_md],
        )
        dc_clean.click(
            fn=purge_orphans,
            inputs=[dc_thumbs, dc_uploads, dc_confirm],
            outputs=[orphan_md, info, dc_confirm],
        )
