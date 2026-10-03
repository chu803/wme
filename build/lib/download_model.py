# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : download_model.py
 文件说明 : LaMa 模型权重多源下载脚本（独立可运行）。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
------------------------------------------------------------------------------
 【使用方式】
   方式一（推荐）：直接双击或在命令行运行
       python download_model.py
   方式二：被 main.py 在「模型缺失且开启自动下载」时自动调用

 【特性】
   1. 多源按顺序尝试，前一源失败自动切换下一源；
   2. 断点续传：已下载部分会被保留，下次从中断处继续；
   3. 完成后校验体积（及可选的 SHA256）；
   4. 带文本进度条，无需第三方库（仅用标准库 urllib）。
==============================================================================
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.request
from pathlib import Path

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config
from utils.exceptions import (
    ModelDownloadError,
    NetworkError,
    DownloadTimeoutError,
)
from utils.logger import get_logger

log = get_logger("download_model")


# ------------------------------------------------------------------------------
# 进度回调：默认在控制台打印进度条；也可由 GUI 接管显示百分比。
# ------------------------------------------------------------------------------
def _default_progress(downloaded: int, total: int, speed_kbps: float) -> None:
    """默认的文本进度条回调。"""
    if total and total > 0:
        pct = downloaded / total * 100
        bar_len = 30
        filled = int(bar_len * downloaded / total)
        bar = "#" * filled + "-" * (bar_len - filled)
        sys.stdout.write(
            f"\r[{bar}] {pct:5.1f}%  {downloaded/1e6:6.1f}/{total/1e6:6.1f} MB  {speed_kbps:6.1f} KB/s"
        )
        sys.stdout.flush()
    else:
        # 服务器未返回总长度，只能显示已下载量。
        sys.stdout.write(f"\r已下载 {downloaded/1e6:.1f} MB")
        sys.stdout.flush()


def _pick_sources() -> list[str]:  # pragma: no cover - 保留作兼容占位
    """（已废弃）原按扩展名筛选下载源逻辑，现内联于 download_to。保留以兼容潜在引用。"""
    target_ext = Path(config.MODEL_FILENAME).suffix.lower()
    matched = [u for u in config.MODEL_DOWNLOAD_URLS if Path(u.split("?")[0]).suffix.lower() == target_ext]
    return matched if matched else list(config.MODEL_DOWNLOAD_URLS)


def _sha256_of_path(path: str, chunk: int = 8 * 1024 * 1024) -> str:
    """计算文件 SHA256（分块读取，避免大文件占满内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def _download_one(
    url: str,
    dest: str,
    timeout: int,
    progress_cb,
) -> None:
    """
    从单个 URL 下载文件到 dest，支持断点续传。

    参数说明：
        url        : 下载地址。
        dest       : 目标文件完整路径。
        timeout    : 单次连接/读取超时（秒）。
        progress_cb: 进度回调 (downloaded, total, speed_kbps)。

    异常：
        DownloadTimeoutError : 下载超时。
        NetworkError         : 网络相关错误。
        ModelDownloadError   : HTTP 非 200/206 等下载失败。
    """
    # 已存在部分文件 → 断点续传。
    resume_pos = 0
    if os.path.exists(dest):
        resume_pos = os.path.getsize(dest)

    req = urllib.request.Request(url)
    if resume_pos > 0:
        # Range 头告知服务器从哪个字节继续。
        req.add_header("Range", f"bytes={resume_pos}-")

    opener = urllib.request.build_opener()
    # 模拟浏览器 UA，部分 CDN 会拦截空 UA。
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AIWatermarkEraser/1.0",
    )

    try:
        resp = opener.open(req, timeout=timeout)
    except Exception as exc:
        # 区分超时与其它网络错误，给出更准确提示。
        msg = str(exc).lower()
        if "timed out" in msg or "timeout" in msg:
            raise DownloadTimeoutError(f"下载超时（>{timeout}s）：{url}") from exc
        raise NetworkError(f"网络错误：{exc}（源：{url}）") from exc

    # HTTP 206 = 部分内容（断点续传成功）；200 = 从头开始。
    code = resp.getcode()
    total = resume_pos
    content_len = resp.headers.get("Content-Length")
    if content_len:
        total += int(content_len)
    if code not in (200, 206):
        raise ModelDownloadError(f"下载失败，HTTP 状态码 {code}：{url}")

    mode = "ab" if resume_pos > 0 else "wb"
    downloaded = resume_pos
    last_time = time.time()
    last_bytes = downloaded

    with open(dest, mode) as f:
        if resume_pos > 0:
            f.seek(resume_pos)
        while True:
            chunk = resp.read(1024 * 256)  # 256KB 一块
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            now = time.time()
            if now - last_time >= 0.5:  # 每 0.5s 刷新一次进度
                speed = (downloaded - last_bytes) / (now - last_time) / 1024
                progress_cb(downloaded, total, speed)
                last_time, last_bytes = now, downloaded

    progress_cb(downloaded, total if total else downloaded, 0.0)
    print()  # 进度条结束后换行


def download_to(
    dest: str,
    urls: list[str],
    progress_cb=None,
    auto_pick: bool = True,
    expected_size: int | None = None,
    sha256: str = "",
    timeout: int | None = None,
    retry: int | None = None,
) -> str:
    """
    通用下载入口：把文件下载到指定路径，多源 + 断点续传 + 校验。

    参数说明：
        dest         : 目标文件完整路径。
        urls         : 下载地址列表（按顺序尝试）。
        progress_cb  : 进度回调 (downloaded, total, speed_kbps)；None 用文本进度条。
        auto_pick    : 是否按 dest 扩展名筛选下载源（与旧 _pick_sources 行为一致）。
        expected_size: 预期字节数（None 表示不校验体积）。
        sha256       : SHA256 校验值（空表示不校验）。
        timeout      : 单次超时（秒）；None 用 config.MODEL_DOWNLOAD_TIMEOUT。
        retry        : 每源重试次数；None 用 config.MODEL_DOWNLOAD_RETRY。

    返回值：
        下载完成的文件绝对路径。

    异常：
        全部源失败后抛出 ModelDownloadError（含各源错误摘要）。
    """
    progress_cb = progress_cb or _default_progress
    timeout = config.MODEL_DOWNLOAD_TIMEOUT if timeout is None else timeout
    retry = config.MODEL_DOWNLOAD_RETRY if retry is None else retry

    # 按目标文件扩展名筛选源（.pt 槽只下 .pt 源，避免错格式）。
    if auto_pick:
        target_ext = Path(dest).suffix.lower()
        matched = [u for u in urls if Path(u.split("?")[0]).suffix.lower() == target_ext]
        if matched:
            urls = matched

    # 确保目标目录存在。
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    # 已存在且体积正常 → 跳过下载。
    if os.path.exists(dest):
        size = os.path.getsize(dest)
        if expected_size is None or size >= int(expected_size * 0.9):
            log.info("模型文件已存在且体积正常，跳过下载：%s", dest)
            return dest

    errors: list[str] = []
    for url in urls:
        for attempt in range(1, retry + 1):
            log.info("尝试源 [%d/%d] 第 %d 次：%s", urls.index(url) + 1, len(urls), attempt, url)
            try:
                _download_one(url, dest, timeout, progress_cb)
                # 下载后校验。
                size = os.path.getsize(dest)
                if expected_size and size < int(expected_size * 0.9):
                    raise ModelDownloadError(
                        f"文件体积过小（{size/1e6:.1f} MB），疑似下载不完整。"
                    )
                if sha256.strip():
                    actual = _sha256_of_path(dest)
                    if actual.lower() != sha256.strip().lower():
                        raise ModelDownloadError("SHA256 校验失败，文件可能被篡改。")
                log.info("模型下载并校验成功：%s", dest)
                return dest
            except (DownloadTimeoutError, NetworkError, ModelDownloadError) as exc:
                errors.append(f"{url} 第{attempt}次: {exc}")
                log.warning("下载失败：%s，准备重试/切换源。", exc)
                # 删除不完整文件，避免下次误判为断点续传。
                if os.path.exists(dest):
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                continue

    summary = "\n".join(errors)
    raise ModelDownloadError(
        f"所有下载源均失败，无法获取模型。\n错误摘要：\n{summary}\n\n"
        f"建议：\n"
        f" 1. 检查网络连接（国内用户可尝试配置代理）；\n"
        f" 2. 手动下载权重放到目标目录；\n"
        f" 3. 运行 python download_model.py 重试。"
    )


def download_model(
    auto_pick: bool = True,
    progress_cb=None,
) -> str:
    """
    下载「内置 LaMa 模型」（薄封装，保持向后兼容）。

    参数说明：
        auto_pick    : 是否按扩展名筛选下载源。
        progress_cb  : 自定义进度回调，默认文本进度条。

    返回值：
        下载完成的模型文件绝对路径。
    """
    return download_to(
        dest=str(config.MODEL_PATH),
        urls=list(config.MODEL_DOWNLOAD_URLS),
        progress_cb=progress_cb,
        auto_pick=auto_pick,
        expected_size=config.MODEL_EXPECTED_SIZE,
        sha256=config.MODEL_SHA256.strip(),
    )


# 直接运行本脚本即触发下载。
if __name__ == "__main__":
    print("=" * 60)
    print(f"  {config.APP_NAME} — 模型下载工具")
    print("=" * 60)
    try:
        path = download_model()
        print(f"\n✅ 模型已就绪：{path}")
    except ModelDownloadError as e:
        print(f"\n❌ 下载失败：{e}")
        sys.exit(1)
