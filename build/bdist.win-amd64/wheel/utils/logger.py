# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : utils/logger.py
 文件说明 : 统一日志系统。全项目只能通过本文件获取 logger 实例，
            禁止在各模块自行 logging.getLogger 后随意配置，避免重复输出。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
------------------------------------------------------------------------------
 【设计要点】
   1. 双通道：控制台（给人看，可彩色）+ 文件（给排查用，纯文本、按大小轮转）。
   2. 彩色降级：若未安装 colorama，Windows 控制台自动退化成无色，不报错。
   3. 单例缓存：同一 name 只配置一次，防止多次调用导致日志重复打印。
   4. 共享 handler：控制台 / app.log / error.log 全进程**各只建一个** handler 并被
      所有 logger 共用——避免多 handler 持有同一文件导致轮转失败（Windows 转名
      PermissionError，日志丢失且文件无限增长）。详见 _shared_handlers 处注释。
==============================================================================
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config

# ------------------------------------------------------------------------------
# 彩色支持：优先尝试 colorama（Windows 上真彩色需要它），失败则无色。
# ------------------------------------------------------------------------------
try:
    from colorama import init as _colorama_init, Fore, Style

    _colorama_init()  # 初始化后 ANSI 转义码在 Windows 控制台也能生效
    _HAS_COLOR = config.LOG_USE_COLOR
except Exception:
    # 没装 colorama 也能正常运行，只是没有颜色。
    _HAS_COLOR = False
    Fore = Style = None  # type: ignore


# 日志级别名称 → 数字映射，方便把配置文件里的字符串转成 logging 需要的数字。
# 除五个标准名外，额外接受社区通用别名 WARN → WARNING、FATAL → CRITICAL，
# 以及 NOTSET；避免它们被静默降级成 fallback（阶段 90 审计）。
_LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,      # 标准别名
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,    # 标准别名
    "NOTSET": logging.NOTSET,
}


def _resolve_level(name: str, fallback: int = logging.INFO) -> int:
    """
    把配置里的级别名解析成 logging 级别数字（英文，大小写不敏感）。

    参数说明：
        name     : 级别名，例如 "INFO"；支持 WARN / FATAL / NOTSET 别名。
                   非字符串（如 None）会被安全地转成字符串再解析，不抛异常。
        fallback : 解析失败时的兜底级别。

    返回值：
        logging 模块定义的级别常量（整数）。

    说明：
        fallback 默认是 INFO——这是一个**偏宽松**的兜底（比 WARNING 更啰嗦），
        保证「配置写错时宁可多记、不可漏记」；调用方若需反向兜底请显式传入。
    """
    return _LEVEL_MAP.get(str(name).strip().upper(), fallback)


# 控制台彩色样式：不同级别用不同颜色，一眼区分严重程度。
_CONSOLE_COLORS: dict[int, str] = {
    logging.DEBUG: Fore.CYAN if _HAS_COLOR else "",
    logging.INFO: Fore.GREEN if _HAS_COLOR else "",
    logging.WARNING: Fore.YELLOW if _HAS_COLOR else "",
    logging.ERROR: Fore.RED if _HAS_COLOR else "",
    logging.CRITICAL: (Fore.RED + Style.BRIGHT) if _HAS_COLOR else "",
}


class _ColorFormatter(logging.Formatter):
    """
    自定义控制台 Formatter：按级别给整行日志着色。

    实际行为（阶段 90 校正，此前注释与代码不符）：
        · 颜色**作用于整行**——在行首插入颜色码、行尾追加复位码；
          并非「只加在级别名前」。
        · 文件日志使用**普通 Formatter**，因此完全不含 ANSI 转义；
          「复制控制台文本不混入 ANSI」这一点是无法保证的（控制台着色
          本身就把转义码写进了终端输出），故不作为设计承诺。
    """

    def __init__(self, fmt: str, datefmt: str, use_color: bool) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        # 先让父类拼出标准文本。
        text: str = super().format(record)
        if self.use_color and _HAS_COLOR:
            color: str = _CONSOLE_COLORS.get(record.levelno, "")
            reset: str = Style.RESET_ALL if _HAS_COLOR else ""
            # 整行着色：行首上色、行尾复位（避免颜色泄漏到后续输出）。
            return f"{color}{text}{reset}"
        return text


# 缓存已创建的 logger，避免重复 addHandler 导致一条日志打印多遍。
_logger_cache: dict[str, logging.Logger] = {}

# 标记：配置是否已初始化（第一个 logger 创建时完成全局基础配置）。
_initialized: bool = False

# 全进程共享的 handler：每个目标（控制台 / app.log / error.log）只建**一个**。
#
# 为什么必须共享（阶段 90 真值审计）：
#   原实现里**每个 logger 各建一套 handler**，于是 N 个模块就有 N 个
#   RotatingFileHandler 同时打开同一个 app.log。轮转时 RotatingFileHandler
#   先 close 自己的流、再 os.rename(app.log → app.log.1)；但**其它 logger
#   的句柄仍开着该文件**，Windows 上改名直接抛 PermissionError →
#     · 轮转失败、日志文件不再受 LOG_MAX_SIZE_MB 约束而无限增长；
#     · 备份序列出现空洞（实测 4 个 logger：171 次 PermissionError，
#       备份只剩 .1/.5），部分日志行丢失。
#   改为共享后「一个文件只有一个 handler」，轮转由唯一属主完成，恢复健康。
_shared_handlers: dict[str, logging.Handler] = {}
_HANDLER_KEYS: tuple[str, ...] = ("console", "file", "error")


def _ensure_global_config() -> None:
    """
    在首次获取 logger 时，确保日志目录存在、全局过滤设置就绪。

    说明：这里不创建根 logger 的 handler（避免影响第三方库日志），
    只处理目录创建这类基础环境准备。
    """
    global _initialized
    if _initialized:
        return
    # 提前确保日志目录存在，防止 RotatingFileHandler 打开文件失败。
    os.makedirs(config.LOG_DIR, exist_ok=True)
    _initialized = True


def _ensure_shared_handlers() -> dict[str, logging.Handler]:
    """
    创建（或复用）全进程共享的三个 handler：控制台、app.log、error.log。

    返回值：
        键为 "console" / "file" / "error" 的字典，所有 logger 共用同一批对象。

    要点：
        · 每个文件只有唯一属主，轮转不再互相踩踏（修复阶段 90 的 L1）。
        · 文件日志一律用**普通 Formatter**，不含 ANSI 颜色。
        · 级别取自全局 config，对所有 logger 天然一致，故可安全共享。
    """
    global _shared_handlers
    if _shared_handlers:
        return _shared_handlers

    console_level: int = _resolve_level(config.LOG_CONSOLE_LEVEL, logging.INFO)
    file_level: int = _resolve_level(config.LOG_LEVEL, logging.INFO)

    # ---------------- 控制台 Handler ----------------
    console_handler: logging.StreamHandler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(
        _ColorFormatter(
            fmt=config.LOG_FORMAT,
            datefmt=config.LOG_DATE_FORMAT,
            use_color=_HAS_COLOR,
        )
    )

    # 文件日志用纯文本 Formatter（不带颜色，方便 grep 与归档）。
    file_fmt: logging.Formatter = logging.Formatter(
        fmt=config.LOG_FORMAT, datefmt=config.LOG_DATE_FORMAT
    )

    # ---------------- 主日志文件 Handler（按大小轮转）----------------
    file_handler: RotatingFileHandler = RotatingFileHandler(
        filename=str(config.LOG_DIR / config.LOG_FILE_NAME),
        mode="a",
        maxBytes=config.LOG_MAX_SIZE_MB * 1024 * 1024,  # MB 转字节
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",  # 关键：中文日志必须指定 utf-8，否则 Windows 下乱码
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(file_fmt)

    # ---------------- 错误日志文件 Handler（只记 ERROR 及以上）----------------
    error_handler: RotatingFileHandler = RotatingFileHandler(
        filename=str(config.LOG_DIR / config.LOG_ERROR_FILE_NAME),
        mode="a",
        maxBytes=config.LOG_MAX_SIZE_MB * 1024 * 1024,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_fmt)

    _shared_handlers = {
        "console": console_handler,
        "file": file_handler,
        "error": error_handler,
    }
    return _shared_handlers


def get_logger(name: str = "WME") -> logging.Logger:
    """
    获取一个配置好「控制台 + 文件」双通道的 logger 实例。

    参数说明：
        name : logger 名称，建议用模块名（如 "core.model_loader"），
               这样日志里能精确定位到是哪段代码打的。

    返回值：
        已配置完成的 logging.Logger 实例。

    用法：
        from utils.logger import get_logger
        log = get_logger(__name__)
        log.info("模型加载完成")
    """
    _ensure_global_config()

    # 命中缓存直接返回。
    if name in _logger_cache:
        return _logger_cache[name]

    logger: logging.Logger = logging.getLogger(name)
    # 避免日志向上冒泡到 root（root 没有被我们配置，冒泡会导致重复或丢失）。
    logger.propagate = False

    # 取配置中的级别。
    console_level: int = _resolve_level(config.LOG_CONSOLE_LEVEL, logging.INFO)
    file_level: int = _resolve_level(config.LOG_LEVEL, logging.INFO)
    logger.setLevel(min(console_level, file_level))  # 取更细的那一级，保证不漏记

    shared: dict[str, logging.Handler] = _ensure_shared_handlers()

    # 清掉「非共享」的历史 handler，保持幂等；并 close 掉它们。
    #   · FileHandler 的 close() 会真正释放文件句柄（防 fd 泄漏）；
    #   · StreamHandler 的 close() 只标记停用、**不**关闭底层流（流常是 sys.stdout）。
    # 共享 handler 绝不能在此关闭（其它 logger 仍在使用同一批对象）。
    for handler in list(logger.handlers):
        if handler not in shared.values():
            try:
                handler.close()
            except Exception:
                pass
    logger.handlers.clear()

    # 挂上共享 handler（同一批对象可安全地被多个 logger 共用）。
    for key in _HANDLER_KEYS:
        logger.addHandler(shared[key])

    # 写入缓存，后续同 name 直接复用。
    _logger_cache[name] = logger
    return logger


# 供 main.py 在启动最早期打印「启动横幅」用，不经过复杂配置也能输出。
def print_banner(text: str) -> None:
    """
    以醒目方式打印一段横幅文本到控制台（不写日志文件）。

    参数说明：
        text : 要打印的文本。
    """
    line: str = "=" * 70
    print(line)
    print(text)
    print(line)


# 自检：直接运行本文件会验证 logger 不重复输出、能正常写文件。
if __name__ == "__main__":
    log = get_logger("test")
    log.debug("这是 DEBUG 级（默认不显示在控制台 INFO 下）")
    log.info("这是 INFO 级")
    log.warning("这是 WARNING 级")
    log.error("这是 ERROR 级（同时写入 error.log）")

    # 验证缓存：再次获取应返回同一实例，不产生重复 handler。
    log2 = get_logger("test")
    assert log2 is log, "logger 缓存失效，可能导致重复日志"

    print("\nlogger.py 自检通过：双通道日志已写入", config.LOG_DIR)
