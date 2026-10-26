# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/cache_service.py
 文件说明 : 处理记录缓存服务（阶段 5 核心服务）。
            实现需求「文件缓存：保存近期处理记录，不用重复上传图片」。

            能力清单：
            1. 把每次成功处理的「原图 + 结果图 + 缩略图 + 参数」登记进本地 JSON 索引，
               界面可在「历史记录」标签页直接浏览、下载、重新处理、删除。
            2. 按文件内容哈希做去重：同一张图再次批量处理时命中缓存，跳过重复计算。
            3. 启动 / 写入时自动清理：超出 CACHE_MAX_RECORDS 或早于 CACHE_KEEP_DAYS 的
               记录会被删除，并连带清理孤立的缩略图，避免磁盘无限膨胀。

            索引文件：config.CACHE_DIR / config.CACHE_INDEX_FILE（history_index.json）
            缩略图目录：config.CACHE_DIR / "thumbs"
            原图落盘：复用 services.file_service.save_upload（CACHE_DIR / "uploads"）

            本模块归属「服务层」（services），只允许调用 core / utils / config。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
import os as _os
import sys as _sys
import threading as _threading
import time as _time
from dataclasses import dataclass, field
from typing import Optional

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np

import config
from services.file_service import (
    HISTORY_DIR,
    THUMB_DIR,
    UPLOAD_DIR,
    ensure_dirs,
    make_thumbnail,
    save_thumbnail,
    save_upload,
)
from utils.logger import get_logger
from utils.path_helper import make_unique_path, sanitize_filename

log = get_logger("services.cache_service")

INDEX_PATH: _os.PathLike = HISTORY_DIR / config.CACHE_INDEX_FILE


@dataclass
class HistoryRecord:
    """
    单条处理记录的轻量结构（可 JSON 序列化）。

    id           : 唯一记录号。
    ts           : 处理完成的时间戳（time.time() 浮点秒）。
    original_name: 原图文件名（展示用）。
    original_path: 原图落盘路径（用于「重新处理」时直接读取，免重复上传）。
    result_path  : 修复结果图路径（用于「下载」）。
    thumb_path   : 缩略图路径（用于画廊展示）。
    mode         : 处理模式（auto / manual）。
    engine       : 使用的修复引擎（lama / opencv / auto）。
    status       : 成功 / 失败（"success" / "failed"）。
    file_hash    : 原图内容哈希（用于去重命中）。
    params       : 处理参数摘要（字符串，便于回溯）。
    """

    id: str
    ts: float
    original_name: str
    original_path: Optional[str] = None
    result_path: Optional[str] = None
    thumb_path: Optional[str] = None
    mode: str = "auto"
    engine: str = "auto"
    status: str = "success"
    file_hash: Optional[str] = None
    params: str = ""
    # 阶段 37：视频记录扩展字段（图片记录保持默认，互不影响旧数据）。
    kind: str = "image"            # "image" | "video" | "pdf"
    video_path: Optional[str] = None
    duration: float = 0.0
    fps: float = 0.0
    frames: int = 0
    width: int = 0
    height: int = 0
    upscaled: bool = False
    # 阶段 40：PDF 记录扩展字段（图片/视频记录保持默认，互不影响旧数据）。
    page_count: int = 0
    file_size: int = 0
    # 阶段 334：**去重命中键**。同一张图在不同参数下产出**不同结果**，只用文件哈希命中会把
    # 上一次的旧成品当成本次要的结果返回（换引擎 / 改灵敏度 / 换掩码都算「同一张图」）。
    # 空串 = 这条记录没登记签名（旧数据或其他页签写的）⇒ **永不参与命中**（宁可重算一次）。
    cache_sig: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "original_name": self.original_name,
            "original_path": self.original_path,
            "result_path": self.result_path,
            "thumb_path": self.thumb_path,
            "mode": self.mode,
            "engine": self.engine,
            "status": self.status,
            "file_hash": self.file_hash,
            "params": self.params,
            "kind": self.kind,
            "video_path": self.video_path,
            "duration": self.duration,
            "fps": self.fps,
            "frames": self.frames,
            "width": self.width,
            "height": self.height,
            "upscaled": self.upscaled,
            "page_count": self.page_count,
            "file_size": self.file_size,
            "cache_sig": self.cache_sig,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryRecord":
        return cls(
            id=d.get("id", ""),
            ts=d.get("ts", 0.0),
            original_name=d.get("original_name", ""),
            original_path=d.get("original_path"),
            result_path=d.get("result_path"),
            thumb_path=d.get("thumb_path"),
            mode=d.get("mode", "auto"),
            engine=d.get("engine", "auto"),
            status=d.get("status", "success"),
            file_hash=d.get("file_hash"),
            params=d.get("params", ""),
            cache_sig=d.get("cache_sig", ""),
            kind=d.get("kind", "image"),
            video_path=d.get("video_path"),
            duration=d.get("duration", 0.0),
            fps=d.get("fps", 0.0),
            frames=d.get("frames", 0),
            width=d.get("width", 0),
            height=d.get("height", 0),
            upscaled=d.get("upscaled", False),
            page_count=d.get("page_count", 0),
            file_size=d.get("file_size", 0),
        )


def compute_file_hash(path: str | _os.PathLike, algo: str = "md5") -> str:
    """
    计算文件内容的哈希值（用于去重判定「是否同一张图」）。

    参数说明：
        path : 文件路径。
        algo : 哈希算法名（默认 md5，速度快、碰撞概率可忽略）。

    返回值：
        十六进制哈希字符串；文件不可读时返回空串。
    """
    try:
        h = _hashlib.new(algo)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as exc:
        log.warning("计算文件哈希失败 %s：%s", path, exc)
        return ""


def _materialize_original(arr: np.ndarray, original_name: str) -> _os.PathLike:
    """
    把内存中的原图数组（RGB uint8）落盘到上传目录，返回稳定路径。

    阶段 45：单图 / 自动页通过 ImageEditor 拿到的是 numpy 数组而非文件路径，
    历史「重新处理」需要原图文件，故在此把数组落盘到 UPLOAD_DIR，
    与批量页经由 save_upload 复制上传文件的语义保持一致。

    参数说明：
        arr           : 原图 H×W×3 uint8（RGB）。
        original_name : 用于生成可读文件名的主名。

    返回值：
        UPLOAD_DIR 下唯一化的 .png 路径。
    """
    ensure_dirs()
    stem = sanitize_filename(_os.path.splitext(original_name)[0] or "upload", 120)
    target = make_unique_path(UPLOAD_DIR, f"{stem}.png")
    # 阶段 335：原来用 `cv2.imwrite` 且不看返回值 ⇒ 中文目录（例如用户名含中文、
    # 或缓存目录被设到 `D:\图片\`）下它返回 False、**一个字节都不写**，函数却返回路径，
    # 于是历史记录登记成「成功」而「重新处理」读的那张原图其实不存在。
    # 改走 core.image_utils.save_image（imencode + open，编码失败会抛 IOError），
    # 不在此另写一份落盘逻辑。延迟导入也避免了 core ↔ services 的导入环。
    from core.image_utils import save_image
    rgb = arr if arr.ndim == 3 else np.dstack([arr, arr, arr])
    return save_image(str(target), rgb, fmt="PNG")


class CacheService:
    """
    处理记录缓存管理器（线程安全，单例风格使用）。

    典型用法：
        cache = CacheService()          # 构造时自动加载索引 + 启动清理
        rec = cache.add_success(original_path, result_arr, ...)
        recs = cache.list_records()
        cache.delete(rec.id)
    """

    def __init__(self, enabled: Optional[bool] = None):
        """
        初始化缓存服务。

        参数说明：
            enabled : 是否启用缓存（默认取 config.CACHE_ENABLED）。
                      关闭后所有 add/list 变为空操作，便于极低资源环境。
        """
        self.enabled = config.CACHE_ENABLED if enabled is None else enabled
        self._lock = _threading.RLock()
        self._records: list[HistoryRecord] = []
        if self.enabled:
            ensure_dirs()
            self._load()
            self.prune()

    # --------------------------------------------------------------------------
    # 索引加载 / 持久化
    # --------------------------------------------------------------------------
    def _load(self) -> None:
        if not _os.path.exists(INDEX_PATH):
            self._records = []
            return
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                data = _json.load(f)
            records = [HistoryRecord.from_dict(d) for d in data]
            # 过滤掉结果文件已丢失的记录（例如被手动删除），避免界面显示死链。
            kept = [r for r in records if r.result_path and _os.path.exists(r.result_path)]
            self._records = kept
            if len(kept) != len(records):
                log.info("已忽略 %d 条结果文件缺失的历史记录。", len(records) - len(kept))
                self._persist()
        except (OSError, _json.JSONDecodeError) as exc:
            log.warning("历史索引读取失败，将重建：%s", exc)
            self._records = []

    def _persist(self) -> None:
        try:
            with open(INDEX_PATH, "w", encoding="utf-8") as f:
                _json.dump([r.to_dict() for r in self._records], f,
                           ensure_ascii=False, indent=2)
        except OSError as exc:
            log.warning("历史索引写入失败：%s", exc)

    # --------------------------------------------------------------------------
    # 写入：成功 / 失败
    # --------------------------------------------------------------------------
    def add_success(
        self,
        original_path: Optional[str | _os.PathLike],
        result_arr: np.ndarray,
        *,
        original_name: str,
        mode: str = "auto",
        engine: str = "auto",
        params: str = "",
        file_hash: Optional[str] = None,
        original_arr: Optional[np.ndarray] = None,
        cache_sig: str = "",
    ) -> Optional[HistoryRecord]:
        """
        登记一条成功处理记录：落盘原图、生成缩略图、写入索引。

        参数说明：
            original_path : 原图路径（用于落盘缓存，便于重处理）。
                            阶段 45 起，若提供 original_arr 则以内存数组落盘为准，
                            本参数可传 None（单图 / 自动页 ImageEditor 无文件路径）。
            result_arr    : 修复结果数组（H×W×3），用于生成缩略图与结果回显。
            original_name : 原图文件名。
            mode/engine   : 处理模式 / 引擎（记录用）。
            params        : 参数摘要字符串。
            file_hash     : 原图哈希（用于后续去重）；为空时自动从落盘原图计算。
            cache_sig     : 阶段 334：去重命中键（见 `find_by_hash`）。为空表示「不参与命中」，
                            因此**新接入的调用方必须显式传签名**，否则等于没写进可命中缓存。
            original_arr  : 阶段 45 新增：内存中的原图数组（RGB uint8），优先于
                            original_path 落盘，解决单图 / 自动页无文件路径的痛点。

        返回值：
            新建的 HistoryRecord；缓存在关闭状态下返回 None。
        """
        if not self.enabled:
            return None
        ensure_dirs()
        try:
            # 1) 落盘原图：优先用内存数组直接落盘（单图 / 自动页），否则复制上传文件。
            if original_arr is not None:
                saved_src = _materialize_original(original_arr, original_name)
            else:
                saved_src = save_upload(str(original_path), original_name)
            # 1.5) 哈希兜底：未显式传入时，从落盘后的原图计算，保证去重一致。
            if not file_hash:
                file_hash = compute_file_hash(saved_src)
            # 2) 缩略图（来自结果图，更直观）。
            thumb = make_thumbnail(result_arr, size=config.CACHE_THUMBNAIL_SIZE)
            thumb_path = str(save_thumbnail(thumb, sanitize_filename(original_name, 120)))
            rec = HistoryRecord(
                id=_os.path.basename(saved_src).split(".")[0] + "_" + str(int(_time.time()))[-6:],
                ts=_time.time(),
                original_name=original_name,
                original_path=str(saved_src),
                result_path=None,  # 结果文件由上层 save_result 写出，稍后回填
                thumb_path=thumb_path,
                mode=mode,
                engine=engine,
                status="success",
                file_hash=file_hash,
                params=params,
                cache_sig=cache_sig,
            )
            with self._lock:
                self._records.append(rec)
                self._persist()
            log.info("历史记录已登记：%s", original_name)
            return rec
        except Exception as exc:  # noqa: BLE001
            log.exception("登记历史记录失败：%s", exc)
            return None

    def update_result_path(self, record: HistoryRecord, result_path: str,
                           file_size: Optional[int] = None) -> None:
        """回填结果文件路径（结果由上层 save_result 写出后再调用）。

        阶段 47 增强：同时回填结果文件体积 file_size——优先用传入值，
        否则从 result_path 现场计算——供历史页「磁盘占用」统计与按体积清理使用。
        """
        if not self.enabled or record is None:
            return
        with self._lock:
            record.result_path = result_path
            if file_size is not None:
                try:
                    record.file_size = int(file_size)
                except (TypeError, ValueError):
                    record.file_size = 0
            elif result_path and _os.path.exists(result_path):
                try:
                    record.file_size = _os.path.getsize(result_path)
                except OSError:
                    record.file_size = 0
            self._persist()

    def add_video_record(
        self,
        src_path: str | _os.PathLike,
        out_path: str | _os.PathLike,
        *,
        original_name: str,
        params: str = "",
        duration: float = 0.0,
        fps: float = 0.0,
        frames: int = 0,
        width: int = 0,
        height: int = 0,
        upscaled: bool = False,
    ) -> Optional[HistoryRecord]:
        """
        阶段 37：登记一条视频处理记录（首帧缩略图 + 视频路径 + 元信息）。

        参数说明：
            src_path     : 源视频路径（用于「重新处理」入口，但视频页暂不支持重处理）。
            out_path     : 结果视频路径（同时作为缩略图来源与下载/预览对象）。
            original_name: 源视频文件名。
            params       : 参数摘要。
            duration 等  : 视频元信息（秒/帧率/帧数/分辨率/是否超分）。

        返回值：
            新建的 HistoryRecord（kind="video"）；缓存在关闭状态下返回 None。
        """
        if not self.enabled:
            return None
        ensure_dirs()
        try:
            from services.video_service import extract_sampled_frames

            # 1) 抽首帧作缩略图。
            first = extract_sampled_frames(str(out_path), n=1, return_indices=True)
            thumb_path = None
            if first:
                thumb = make_thumbnail(first[0]["image"], size=config.CACHE_THUMBNAIL_SIZE)
                thumb_path = str(save_thumbnail(thumb, sanitize_filename(original_name, 120)))

            rec = HistoryRecord(
                id="video_" + str(int(_time.time()))[-8:] + "_" + str(_os.getpid()),
                ts=_time.time(),
                original_name=original_name,
                original_path=str(src_path),
                result_path=str(out_path),
                thumb_path=thumb_path,
                mode="video",
                engine="video",
                status="success",
                params=params,
                kind="video",
                video_path=str(out_path),
                duration=float(duration),
                fps=float(fps),
                frames=int(frames),
                width=int(width),
                height=int(height),
                upscaled=bool(upscaled),
            )
            with self._lock:
                self._records.append(rec)
                self._persist()
            log.info("视频历史记录已登记：%s", original_name)
            return rec
        except Exception as exc:  # noqa: BLE001
            log.exception("登记视频历史记录失败：%s", exc)
            return None

    def add_pdf_record(
        self,
        src_path: str | _os.PathLike,
        out_path: str | _os.PathLike,
        *,
        original_name: str,
        params: str = "",
        page_count: int = 0,
        file_size: int = 0,
        upscaled: bool = False,
    ) -> Optional[HistoryRecord]:
        """
        阶段 40：登记一条 PDF 处理记录（首页缩略图 + 结果路径 + 元信息）。

        参数说明：
            src_path     : 源 PDF 路径（用于「重新处理」入口，但 PDF 页暂不支持重处理）。
            out_path     : 结果 PDF 路径（同时作为缩略图来源与下载对象）。
            original_name: 源 PDF 文件名。
            params       : 参数摘要（通常传 PdfResult.summary()）。
            page_count   : 文档总页数。
            file_size    : 结果 PDF 文件字节数（用于历史页展示大小）。
            upscaled     : 是否启用了真实超分。

        返回值：
            新建的 HistoryRecord（kind="pdf"）；缓存在关闭状态下返回 None。
            若首页渲染失败，则回退到纯色占位缩略图，保证记录始终可注册。
        """
        if not self.enabled:
            return None
        ensure_dirs()
        try:
            from services.pdf_service import render_pdf_first_page

            # 1) 渲染首页作缩略图；失败则回退占位图（避免记录因缩略图缺失而流产）。
            first_arr = render_pdf_first_page(str(out_path))
            thumb_path = None
            if first_arr is not None:
                thumb = make_thumbnail(first_arr, size=config.CACHE_THUMBNAIL_SIZE)
                thumb_path = str(save_thumbnail(thumb, sanitize_filename(original_name, 120)))
            else:
                placeholder = np.zeros(
                    (config.CACHE_THUMBNAIL_SIZE, config.CACHE_THUMBNAIL_SIZE, 3),
                    dtype=np.uint8,
                ) + 235
                thumb_path = str(save_thumbnail(placeholder, sanitize_filename(original_name, 120)))

            rec = HistoryRecord(
                id="pdf_" + str(int(_time.time()))[-8:] + "_" + str(_os.getpid()),
                ts=_time.time(),
                original_name=original_name,
                original_path=str(src_path),
                result_path=str(out_path),
                thumb_path=thumb_path,
                mode="pdf",
                engine="pdf",
                status="success",
                params=params,
                kind="pdf",
                page_count=int(page_count),
                file_size=int(file_size),
                upscaled=bool(upscaled),
            )
            with self._lock:
                self._records.append(rec)
                self._persist()
            log.info("PDF 历史记录已登记：%s（%d 页）", original_name, int(page_count))
            return rec
        except Exception as exc:  # noqa: BLE001
            log.exception("登记 PDF 历史记录失败：%s", exc)
            return None

    def add_failure(
        self,
        original_name: str,
        error: str,
        *,
        mode: str = "auto",
        engine: str = "auto",
    ) -> None:
        """登记一条失败记录（仅作日志留存，不含结果/缩略图）。"""
        if not self.enabled:
            return
        with self._lock:
            self._records.append(HistoryRecord(
                id="fail_" + str(int(_time.time()))[-8:],
                ts=_time.time(),
                original_name=original_name,
                mode=mode,
                engine=engine,
                status="failed",
                params=error[:200],
            ))
            self._persist()

    # --------------------------------------------------------------------------
    # 查询
    # --------------------------------------------------------------------------
    def list_records(self, kind: Optional[str] = None,
                     query: Optional[str] = None) -> list[HistoryRecord]:
        """
        返回全部记录，按时间倒序（最新在前）。

        参数说明：
            kind  : 阶段 41 新增 —— 类型过滤；为 None（缺省）时返回全部，
                    否则仅返回 getattr(r, "kind", "image") == kind 的记录。
            query : 阶段 42 新增 —— 文件名模糊匹配；为 None 或空串时忽略，
                    否则仅返回 original_name 包含 query（不区分大小写）的记录。
                    与 kind 叠加生效（AND）。
        """
        with self._lock:
            recs = sorted(self._records, key=lambda r: r.ts, reverse=True)
        if kind is not None:
            recs = [r for r in recs if getattr(r, "kind", "image") == kind]
        if query:
            q = query.strip().lower()
            if q:
                recs = [r for r in recs if q in (r.original_name or "").lower()]
        return recs

    def find_by_hash(self, file_hash: str,
                     cache_sig: Optional[str] = None) -> Optional[HistoryRecord]:
        """
        按原图哈希查找最近一条成功记录（用于去重命中）。
        返回 None 表示未命中缓存。

        参数说明（阶段 334）：
            file_hash : 原图内容哈希。
            cache_sig : **去重命中键**。传入非 None 时，只命中 `r.cache_sig == cache_sig` 的记录；
                        记录里 `cache_sig` 为空（旧数据、或别的页签没登记签名）⇒ **不命中**。
                        不传（None）保持旧行为，供尚未接签名的调用方使用。

        为什么要加这个参数：同一张图在「换引擎 / 改灵敏度 / 换掩码」下产出**不同结果**，
        只按文件哈希命中会把上一次的旧成品当成本次的结果返回给-user（批量页曾无条件这样命中）。
        正确性优先于命中率：签名不匹配就多算一次，代价是时间，不是错的图。
        """
        if not file_hash:
            return None
        with self._lock:
            for r in sorted(self._records, key=lambda r: r.ts, reverse=True):
                if r.status != "success" or r.file_hash != file_hash or not r.result_path:
                    continue
                if cache_sig is not None and r.cache_sig != cache_sig:
                    continue
                return r
        return None

    def get(self, record_id: str) -> Optional[HistoryRecord]:
        with self._lock:
            for r in self._records:
                if r.id == record_id:
                    return r
        return None

    def update_record(self, record: HistoryRecord) -> None:
        """
        按 id 替换一条记录（若不存在则追加），并持久化索引。

        参数说明：
            record : 已更新的 HistoryRecord 对象（如重新处理后的新缩略图/结果路径）。
        """
        if not self.enabled:
            return
        with self._lock:
            replaced = False
            for i, r in enumerate(self._records):
                if r.id == record.id:
                    self._records[i] = record
                    replaced = True
                    break
            if not replaced:
                self._records.append(record)
            self._persist()

    # --------------------------------------------------------------------------
    # 删除 / 清理
    # --------------------------------------------------------------------------
    def delete(self, record_id: str) -> bool:
        """删除一条记录，并清理其缩略图文件（结果成品图保留，不误删用户资产）。"""
        if not self.enabled:
            return False
        with self._lock:
            # 阶段 132：必须在**移除记录之前**取出它的 thumb_path——此前先移除、
            # 再 `rec = self.get(record_id)`，而 get 在已移除的列表里查找必然返回
            # None ⇒ 缩略图清理是死代码、永远不执行（历史页每删一条就漏一个缩略图）。
            # 兄弟方法 delete_by_filter / clear / prune 都是「先快照、后删除」，此处对齐。
            thumb = next((r.thumb_path for r in self._records if r.id == record_id), None)
            before = len(self._records)
            self._records = [r for r in self._records if r.id != record_id]
            changed = len(self._records) != before
            if changed:
                self._persist()
        # 缩略图清理（在锁外执行，避免持有锁做 IO）。
        if changed and thumb and _os.path.exists(thumb):
            try:
                _os.remove(thumb)
            except OSError:
                pass
        return changed

    def clear(self) -> int:
        """清空全部记录（保留 outputs 成品图）。返回删除的记录数。"""
        if not self.enabled:
            return 0
        with self._lock:
            n = len(self._records)
            thumbs = [r.thumb_path for r in self._records if r.thumb_path]
            self._records.clear()
            self._persist()
        for tp in thumbs:
            if tp and _os.path.exists(tp):
                try:
                    _os.remove(tp)
                except OSError:
                    pass
        log.info("已清空 %d 条历史记录。", n)
        return n

    def delete_by_filter(self, kind: Optional[str] = None,
                          query: Optional[str] = None) -> int:
        """
        阶段 43 新增：删除「当前筛选视图」下的全部记录（按类型 + 文件名关键词，
        与 list_records 同一套过滤逻辑），并清理各自缩略图（结果成品图保留，
        不误删用户资产）。返回实际删除的记录数。

        参数说明：
            kind  : 类型过滤；None / "all" 表示不过滤。
            query : 文件名模糊匹配；空/None 忽略。与 kind 叠加生效（AND）。

        用途：历史页「批量删除当前筛选结果」按钮——用户先用类型筛选 + 搜索框
        缩小范围，再一键清空该范围内的所有记录。是否删除成品图由调用方（UI）
        经确认复选框二次确认后决定；本方法只删除缓存索引与缩略图。
        """
        if not self.enabled:
            return 0
        # 复用 list_records 的过滤口径，保证「所见即所删」。
        recs = self.list_records(kind=kind, query=query)
        if not recs:
            return 0
        ids = {r.id for r in recs}
        with self._lock:
            before = len(self._records)
            self._records = [r for r in self._records if r.id not in ids]
            changed = len(self._records) != before
            if changed:
                self._persist()
        removed = before - len(self._records)
        # 缩略图清理（锁外 IO，避免持有锁做文件操作；结果成品图保留）。
        for r in recs:
            if r.thumb_path and _os.path.exists(r.thumb_path):
                try:
                    _os.remove(r.thumb_path)
                except OSError:
                    pass
        if removed:
            log.info("已按筛选批量删除 %d 条历史记录（kind=%s, query=%s）。",
                     removed, kind, query)
        return removed

    def prune(self) -> int:
        """
        自动清理：删除过期（> CACHE_KEEP_DAYS 天）与超出上限（> CACHE_MAX_RECORDS）的记录。
        返回实际删除的记录数。
        """
        if not self.enabled:
            return 0
        now = _time.time()
        max_age = config.CACHE_KEEP_DAYS * 86400.0
        with self._lock:
            kept = []
            removed = []
            for r in self._records:
                age = now - r.ts
                if r.status == "success" and age > max_age:
                    removed.append(r)
                else:
                    kept.append(r)
            # 数量上限：超出部分删最旧。
            if len(kept) > config.CACHE_MAX_RECORDS:
                kept_sorted = sorted(kept, key=lambda r: r.ts)  # 最旧在前
                overflow = len(kept_sorted) - config.CACHE_MAX_RECORDS
                removed.extend(kept_sorted[:overflow])
                kept = kept_sorted[overflow:]
            self._records = kept
            if removed:
                self._persist()
        # 清理被删记录的缩略图。
        for r in removed:
            if r.thumb_path and _os.path.exists(r.thumb_path):
                try:
                    _os.remove(r.thumb_path)
                except OSError:
                    pass
        if removed:
            log.info("已按缓存策略清理 %d 条过期/超额记录。", len(removed))
        return len(removed)

    def get_stats(self) -> dict:
        """
        阶段 47 新增：聚合历史结果的磁盘占用统计（以结果文件体积为准）。

        返回值（dict）：
            total_bytes : 全部记录结果文件体积之和（字节）。
            total_count : 记录总数。
            by_kind     : {kind: {"count": int, "bytes": int}}，按类型拆分。
        说明：file_size 字段若已记录（视频/PDF/阶段 47 后的图片）直接采用，
              否则尝试从 result_path 现场计算；均不可得则记 0，不影响其它记录。
        """
        by_kind: dict = {}
        total_bytes = 0
        total_count = 0
        for r in self._records:
            if r.file_size and r.file_size > 0:
                sz = int(r.file_size)
            elif r.result_path and _os.path.exists(r.result_path):
                try:
                    sz = _os.path.getsize(r.result_path)
                except OSError:
                    sz = 0
            else:
                sz = 0
            k = getattr(r, "kind", "image") or "image"
            d = by_kind.setdefault(k, {"count": 0, "bytes": 0})
            d["count"] += 1
            d["bytes"] += sz
            total_bytes += sz
            total_count += 1
        return {"total_bytes": total_bytes, "total_count": total_count, "by_kind": by_kind}

    def prune_by_size(self, max_bytes: int) -> int:
        """
        阶段 47 新增：按总体积上限清理——从最旧记录开始删除，直至剩余总体积
        <= max_bytes。返回实际删除的记录数；max_bytes<=0 或无需清理时返回 0。
        删除时仅清理缩略图（结果成品图保留，不误删用户资产）。
        """
        if not self.enabled or max_bytes <= 0:
            return 0
        with self._lock:
            # 最旧在前，便于「删旧留新」。
            recs = sorted(self._records, key=lambda r: r.ts)

            def _sz(r):
                if r.file_size and r.file_size > 0:
                    return int(r.file_size)
                if r.result_path and _os.path.exists(r.result_path):
                    try:
                        return _os.path.getsize(r.result_path)
                    except OSError:
                        return 0
                return 0

            total = sum(_sz(r) for r in recs)
            if total <= max_bytes:
                return 0
            to_remove = []
            for r in recs:
                if total <= max_bytes:
                    break
                total -= _sz(r)
                to_remove.append(r)
            ids = {r.id for r in to_remove}
            self._records = [x for x in self._records if x.id not in ids]
            self._persist()
        removed = len(to_remove)
        # 缩略图清理（锁外 IO，与 delete / prune 一致）。
        for r in to_remove:
            if r.thumb_path and _os.path.exists(r.thumb_path):
                try:
                    _os.remove(r.thumb_path)
                except OSError:
                    pass
        if removed:
            log.info("已按体积上限清理 %d 条记录。", removed)
        return removed

    # ------------------------------------------------------------------
    # 阶段 48：孤儿文件扫描与清理（磁盘治理闭环）
    # ------------------------------------------------------------------
    # 背景：删除历史记录时只清理缩略图、保留结果成品图（阶段 43/47 的既有约定），
    # 且原图落盘副本（uploads）从未被回收。久而久之 cache/ 下会堆积大量
    # 「没有任何记录引用」的孤儿文件，索引越干净、孤儿越多。
    # 本组方法提供「先预览、再确认、后删除」的安全回收路径。

    def referenced_paths(self) -> set:
        """
        阶段 48 新增：收集全部历史记录引用的磁盘路径集合（规范化后去重）。

        覆盖 HistoryRecord 上可能出现的四类路径字段：
            original_path（原图落盘副本）/ thumb_path（缩略图）/
            result_path（结果成品图）/ video_path（视频记录）。

        返回值：
            规范化（normcase + abspath）后的路径字符串集合，用于与磁盘文件做差集。

        说明：
            先持锁快照 _records、再在锁外做路径规范化，避免将磁盘 IO
            或较慢的字符串处理拖进临界区（孤儿扫描涉及上万文件）。
        """
        with self._lock:
            records = list(self._records)

        refs = set()
        for r in records:
            for attr in ("original_path", "thumb_path", "result_path", "video_path"):
                p = getattr(r, attr, None)
                if not p:
                    continue
                try:
                    refs.add(_os.path.normcase(_os.path.abspath(str(p))))
                except (TypeError, ValueError):
                    continue
        return refs

    def scan_orphans(self) -> dict:
        """
        阶段 48 新增：扫描缓存目录中「不被任何历史记录引用」的孤儿文件。

        扫描范围（仅限缓存目录，绝不触碰用户资产）：
            thumbs  —— config.CACHE_DIR / "thumbs"（缩略图）
            uploads —— config.CACHE_DIR / "uploads"（原图落盘副本）
        outputs / exports 是用户可见的成品目录，一律排除，避免误删用户资产。

        返回值（dict）：
            {
              "thumbs"   : {"count": int, "bytes": int, "files": [绝对路径, ...]},
              "uploads"  : {"count": int, "bytes": int, "files": [...]},
              "__total__": {"count": int, "bytes": int},
            }
        说明：只做只读扫描，不删除任何文件；files 供上层「预览清单」与
              purge_orphans 的二次确认使用。
        """
        refs = self.referenced_paths()
        result: dict = {}
        total_count = 0
        total_bytes = 0

        for origin, directory in (("thumbs", THUMB_DIR), ("uploads", UPLOAD_DIR)):
            files = []
            nbytes = 0
            root = str(directory)
            if _os.path.isdir(root):
                for name in _os.listdir(root):
                    fp = _os.path.join(root, name)
                    if not _os.path.isfile(fp):
                        continue  # 跳过子目录（缓存目录应保持扁平）
                    try:
                        key = _os.path.normcase(_os.path.abspath(fp))
                        if key in refs:
                            continue  # 仍被记录引用，不是孤儿
                        sz = _os.path.getsize(fp)
                    except OSError:
                        continue  # 权限/占用等异常：跳过，不计入、不删除
                    files.append(fp)
                    nbytes += sz

            result[origin] = {"count": len(files), "bytes": nbytes, "files": files}
            total_count += len(files)
            total_bytes += nbytes

        result["__total__"] = {"count": total_count, "bytes": total_bytes}
        return result

    def purge_orphans(self, origins: Optional[list] = None) -> "tuple[int, int]":
        """
        阶段 48 新增：删除孤儿文件，回收磁盘空间。

        参数说明：
            origins : 需要清理的来源列表，元素取自 {"thumbs", "uploads"}。
                      None   → 清理全部来源；
                      []     → 一个都不清理（空列表语义明确为「未选择」，
                                绝不退化成「全删」，避免 UI 未勾选时误清）；
                      ["thumbs"] → 只清理缩略图。

        返回值：
            (删除文件数, 释放字节数)。

        安全性：
            1. 执行前重新扫描一次（而非复用预览结果）——若预览之后新增了历史记录，
               其引用文件会被重新排除，避免误删刚产生的资产。
            2. 仅删除 scan_orphans 判定的孤儿，绝不删除被引用文件。
            3. 单文件删除失败（占用/权限）仅跳过并继续，不影响其余文件。
        """
        if not self.enabled:
            return (0, 0)

        want = set(origins) if origins is not None else {"thumbs", "uploads"}
        if not want:
            return (0, 0)

        scan = self.scan_orphans()  # 重新扫描，保证以最新引用集为准
        removed = 0
        freed = 0
        for origin, info in scan.items():
            if origin == "__total__" or origin not in want:
                continue
            for fp in info["files"]:
                try:
                    sz = _os.path.getsize(fp)
                except OSError:
                    sz = 0
                try:
                    _os.remove(fp)
                except OSError:
                    continue  # 被占用/无权限：跳过，不中断整体清理
                removed += 1
                freed += sz

        if removed:
            log.info("已清理孤儿文件 %d 个，释放 %.1f MB。",
                     removed, freed / 1024 / 1024)
        return (removed, freed)


# ==============================================================================
# 进程级单例：让多个标签页共享同一份缓存状态
# ==============================================================================
_cache_singleton: "Optional[CacheService]" = None
_cache_singleton_lock = _threading.RLock()


def get_cache() -> "CacheService":
    """
    返回进程内唯一的 CacheService 实例（懒加载、线程安全）。

    为什么要单例：
        历史记录页、批量处理页、设置页都会读写同一份历史索引。
        若各自 new 一个 CacheService，清空/清理操作只影响自己的内存副本，
        切到另一个标签页会看到「已删除却还在」的脏数据。共享单例可保证
        clear() / prune() / delete() 在所有标签页即时一致。

    返回值：
        全局唯一的 CacheService 实例。
    """
    global _cache_singleton
    with _cache_singleton_lock:
        if _cache_singleton is None:
            _cache_singleton = CacheService()
        return _cache_singleton


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    import tempfile
    import shutil as _shutil
    from pathlib import Path

    # 阶段 132：自检必须**在隔离的临时目录**里跑——此前直接用真实 config.CACHE_DIR
    # 并调用 cache.clear()，会清空用户真实的历史索引与缩略图（破坏性自检，也使其无法
    # 被登记进子进程守卫）。这里把 file_service 与本模块的目录统一切到临时目录。
    _tmp_root = Path(tempfile.mkdtemp(prefix="cache_selftest_"))
    _up, _th, _hi = _tmp_root / "uploads", _tmp_root / "thumbs", _tmp_root / "history"
    import services.file_service as _fs
    _fs.UPLOAD_DIR, _fs.THUMB_DIR, _fs.HISTORY_DIR = _up, _th, _hi
    THUMB_DIR, UPLOAD_DIR = _th, _up          # 本模块（scan_orphans / purge_orphans 使用）
    INDEX_PATH = _hi / config.CACHE_INDEX_FILE

    cache = CacheService(enabled=True)
    cache.clear()

    # 构造两张合成图用于测试。
    tmp_a = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_b = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    import cv2  # 仅自检使用
    cv2.imwrite(tmp_a.name, np.zeros((60, 80, 3), dtype=np.uint8) + 200)
    cv2.imwrite(tmp_b.name, np.zeros((60, 80, 3), dtype=np.uint8) + 100)
    tmp_a.close()
    tmp_b.close()

    arr = np.zeros((60, 80, 3), dtype=np.uint8) + 50
    ha = compute_file_hash(tmp_a.name)
    hb = compute_file_hash(tmp_b.name)

    rec_a = cache.add_success(tmp_a.name, arr, original_name="a.png",
                              mode="auto", engine="opencv", file_hash=ha)
    rec_b = cache.add_success(tmp_b.name, arr, original_name="b.png",
                              mode="auto", engine="opencv", file_hash=hb)
    assert rec_a and rec_b, "登记失败"
    assert len(cache.list_records()) == 2, "列表数量错误"

    # 回填结果路径（模拟真实流程：process → save_result → update_result_path）。
    cache.update_result_path(rec_a, tmp_a.name)
    cache.update_result_path(rec_b, tmp_b.name)

    hit = cache.find_by_hash(ha)
    assert hit is not None and hit.original_name == "a.png", "哈希去重命中失败"
    print(f"用例1通过：登记 2 条，去重重中 a.png（hash={ha[:8]}…）")

    # 删除测试。
    ok = cache.delete(rec_a.id)
    assert ok and len(cache.list_records()) == 1, "删除失败"
    print("用例2通过：删除记录成功。")

    # 重新加载索引，验证持久化。
    cache2 = CacheService(enabled=True)
    assert len(cache2.list_records()) == 1, "索引持久化失败"
    print("用例3通过：索引持久化正确。")

    cache2.clear()
    _os.remove(tmp_a.name)
    _os.remove(tmp_b.name)
    _shutil.rmtree(_tmp_root, ignore_errors=True)
    print("cache_service.py 自检全部通过。")
