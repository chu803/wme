# -*- coding: utf-8 -*-
"""阶段 90 审计回归：utils/logger.py「共享 handler 与轮转健康」。

覆盖：
  · L1 多 handler 同文件 → 轮转失败（Windows PermissionError）→ 改为全进程共享
  · L2 `_resolve_level` 级别别名与安全兜底
  · L3 `_ColorFormatter` 整行着色的真实契约（文件日志无 ANSI）
  · L4 清理旧 handler 时 close 避免 fd 泄漏
  · 非缺陷钉死：min(level)、propagate=False、单例缓存
"""
from __future__ import annotations

import glob
import inspect
import io
import logging
import logging.handlers as LH
import os

import pytest

import config
from utils import logger as L


# --------------------------------------------------------------------------- #
# 夹具：把全局状态与日志目录隔离到 tmp，避免污染项目 logs/ 与其它用例
# --------------------------------------------------------------------------- #
@pytest.fixture
def fresh_logger(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)

    old_cache = L._logger_cache
    old_shared = L._shared_handlers
    old_init = L._initialized

    L._logger_cache = {}
    L._shared_handlers = {}
    L._initialized = False

    yield L

    # 关闭本次在 tmp 里新建的 handler，并还原全局引用（不动真实 handler 的开合状态）
    for h in list(L._shared_handlers.values()):
        try:
            h.close()
        except Exception:
            pass
    L._shared_handlers = old_shared
    L._logger_cache = old_cache
    L._initialized = old_init


def _rfh(lg):
    return [h for h in lg.handlers if isinstance(h, LH.RotatingFileHandler)]


def _app_handlers(loggers):
    """返回指向 app.log 的【去重后】handler 对象列表。"""
    seen = {}
    for lg in loggers:
        for h in _rfh(lg):
            if os.path.basename(h.baseFilename) == config.LOG_FILE_NAME:
                seen[id(h)] = h
    return list(seen.values())


# --------------------------------------------------------------------------- #
# A. 共享 handler / 文件唯一属主（L1 修复的核心断言）
# --------------------------------------------------------------------------- #
def test_shared_handlers_reused_across_many_loggers(fresh_logger):
    loggers = [fresh_logger.get_logger(f"t90.a{i}") for i in range(8)]
    all_h = [h for lg in loggers for h in lg.handlers]
    assert len(all_h) == 24                       # 8 × 3 引用
    assert len({id(h) for h in all_h}) == 3       # 但只有 3 个真实对象


def test_each_log_file_has_a_single_owner(fresh_logger):
    loggers = [fresh_logger.get_logger(f"t90.b{i}") for i in range(5)]
    app = _app_handlers(loggers)
    assert len(app) == 1, "同一日志文件必须只有唯一 handler（否则轮转会互相踩踏）"

    err = {}
    for lg in loggers:
        for h in _rfh(lg):
            if os.path.basename(h.baseFilename) == config.LOG_ERROR_FILE_NAME:
                err[id(h)] = h
    assert len(err) == 1


def test_shared_handler_identity_is_stable(fresh_logger):
    a = fresh_logger.get_logger("t90.c1")
    b = fresh_logger.get_logger("t90.c2")
    assert set(map(id, a.handlers)) == set(map(id, b.handlers))


def test_repeated_get_logger_returns_cached_instance(fresh_logger):
    a = fresh_logger.get_logger("t90.d1")
    b = fresh_logger.get_logger("t90.d1")
    assert a is b


def test_no_duplicate_output_per_record(fresh_logger):
    lg = fresh_logger.get_logger("t90.e1")
    fresh_logger.get_logger("t90.e2")           # 再来一个，确认共享不会叠印
    buf = io.StringIO()
    fresh_logger._shared_handlers["console"].setStream(buf)
    lg.warning("唯一标记-QQQ")
    hits = [ln for ln in buf.getvalue().splitlines() if "唯一标记-QQQ" in ln]
    assert len(hits) == 1, "同一条记录只应输出一次"


# --------------------------------------------------------------------------- #
# B. 轮转健康（L1 的回归测试 —— 这是本阶段最关键的一条）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("n_loggers", [1, 4, 8])
def test_rotation_is_healthy_with_many_loggers(fresh_logger, n_loggers):
    loggers = [fresh_logger.get_logger(f"t90.rot{i}") for i in range(n_loggers)]
    handler = _app_handlers(loggers)[0]
    handler.maxBytes = 4096
    handler.backupCount = 4

    err = io.StringIO()
    import sys

    old_stderr = sys.stderr
    sys.stderr = err
    try:
        for _ in range(200):
            for lg in loggers:
                lg.warning("Y" * 60)
    finally:
        sys.stderr = old_stderr

    # 1) 不得发生任何 logging 内部错误（旧实现此处会是 N 条 PermissionError）
    assert "--- Logging error ---" not in err.getvalue()

    # 2) 备份序列必须是连续的 .1..k（旧实现会出现 .1/.3 这种空洞）
    applog = os.path.join(str(config.LOG_DIR), config.LOG_FILE_NAME)
    backups = sorted(
        int(p.rsplit(".", 1)[1]) for p in glob.glob(applog + ".*") if p.rsplit(".", 1)[1].isdigit()
    )
    assert backups, "至少应产生一个备份文件"
    assert backups == list(range(1, len(backups) + 1)), f"备份序列有空洞：{backups}"

    # 3) 主文件不应越界增长
    assert os.path.getsize(applog) <= handler.maxBytes * 2


def test_stale_multi_handler_would_break_rotation(fresh_logger):
    """注入式反例：手工给两个 logger 各挂一个独立 handler（模拟旧实现），必须复现失败。"""
    logs_dir = str(config.LOG_DIR)
    applog = os.path.join(logs_dir, config.LOG_FILE_NAME)

    def standalone():
        h = LH.RotatingFileHandler(applog, mode="a", maxBytes=4096, backupCount=4, encoding="utf-8")
        return h

    h1, h2 = standalone(), standalone()
    lg1 = logging.getLogger("t90.old1")
    lg2 = logging.getLogger("t90.old2")
    for lg, h in ((lg1, h1), (lg2, h2)):
        lg.handlers.clear()
        lg.propagate = False
        lg.setLevel(logging.WARNING)
        lg.addHandler(h)

    err = io.StringIO()
    import sys

    old_stderr = sys.stderr
    sys.stderr = err
    try:
        for _ in range(200):
            lg1.warning("Z" * 60)
            lg2.warning("Z" * 60)
    finally:
        sys.stderr = old_stderr
        h1.close()
        h2.close()

    assert "--- Logging error ---" in err.getvalue(), "旧的多 handler 形态本应轮转失败"


# --------------------------------------------------------------------------- #
# C. L2：级别名解析
# --------------------------------------------------------------------------- #
def test_resolve_level_standard_names():
    assert L._resolve_level("DEBUG") == logging.DEBUG
    assert L._resolve_level("INFO") == logging.INFO
    assert L._resolve_level("WARNING") == logging.WARNING
    assert L._resolve_level("ERROR") == logging.ERROR
    assert L._resolve_level("CRITICAL") == logging.CRITICAL


def test_resolve_level_accepts_aliases():
    assert L._resolve_level("WARN") == logging.WARNING
    assert L._resolve_level("FATAL") == logging.CRITICAL
    assert L._resolve_level("NOTSET") == logging.NOTSET


def test_resolve_level_is_case_and_space_insensitive():
    assert L._resolve_level("warning") == logging.WARNING
    assert L._resolve_level("  Error  ") == logging.ERROR


def test_resolve_level_none_is_safe_fallback():
    # 旧实现 name.upper() 会抛 AttributeError；现在应安全兜底
    assert L._resolve_level(None) == logging.INFO
    assert L._resolve_level("不存在的级别") == logging.INFO
    assert L._resolve_level("WAT", fallback=logging.CRITICAL) == logging.CRITICAL


def test_level_map_covers_every_config_valid_level():
    """对账：config 校验通过的级别必须都能被 _resolve_level 正确解析。"""
    valid = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    for name in valid:
        assert L._resolve_level(name) != logging.INFO or name == "INFO"


# --------------------------------------------------------------------------- #
# D. L3：着色契约
# --------------------------------------------------------------------------- #
def test_color_formatter_colors_whole_line():
    if not L._HAS_COLOR:
        pytest.skip("本环境无 colorama")
    fmt = L._ColorFormatter(fmt="%(levelname)s body", datefmt="%H", use_color=True)
    rec = logging.LogRecord("n", logging.ERROR, "p", 1, "body", None, None)
    text = fmt.format(rec)
    assert text.startswith("\x1b[")          # 行首上色
    assert text.rstrip().endswith("\x1b[0m")  # 行尾复位
    assert "\x1b[" in text


def test_file_formatter_emits_no_ansi(fresh_logger):
    lg = fresh_logger.get_logger("t90.color")
    file_fmt = next(
        h.formatter for h in _rfh(lg) if os.path.basename(h.baseFilename) == config.LOG_FILE_NAME
    )
    rec = logging.LogRecord("n", logging.ERROR, "p", 1, "body", None, None)
    assert "\x1b[" not in file_fmt.format(rec)


def test_console_colors_are_empty_when_color_disabled():
    # 无色时所有级别颜色都应是空串（降级不报错）
    if L._HAS_COLOR:
        pytest.skip("本环境有 colorama")
    assert all(v == "" for v in L._CONSOLE_COLORS.values())


# --------------------------------------------------------------------------- #
# E. L4：资源清理
# --------------------------------------------------------------------------- #
def test_get_logger_deactivates_and_closes_stale_handler(fresh_logger, tmp_path):
    """非共享的历史 handler 必须被【关闭】。

    注意（阶段 90 实测）：Python 3.13 的 `StreamHandler.close()` 只把 `_closed`
    置真并从内部注册表移除，**不会关闭底层 stream**（因为流常是 sys.stdout）；
    只有 `FileHandler.close()` 会真正 close 掉文件句柄。故两种都要断言。
    """
    victim = logging.getLogger("t90.stale")
    victim.handlers.clear()

    # 非共享的【文件】handler：close() 必须释放底层 fd
    file_h = logging.FileHandler(os.path.join(str(tmp_path), "stale.log"), encoding="utf-8")
    file_stream = file_h.stream
    # 非共享的【流】handler：close() 至少必须标记为已停用（不再输出）
    stream_h = logging.StreamHandler(io.StringIO())
    victim.addHandler(file_h)
    victim.addHandler(stream_h)

    fresh_logger.get_logger("t90.stale")

    assert file_h not in victim.handlers
    assert stream_h not in victim.handlers
    assert file_stream.closed, "非共享文件 handler 必须 close 以释放句柄"
    assert stream_h._closed is True, "非共享流 handler 必须被标记关闭"
    assert len(victim.handlers) == 3


def test_get_logger_does_not_close_shared_handlers(fresh_logger):
    a = fresh_logger.get_logger("t90.keep1")
    shared = list(a.handlers)
    fresh_logger.get_logger("t90.keep2")
    # 共享 handler 不得因配置新 logger 而被关闭
    for h in shared:
        assert getattr(h, "_closed", False) is not True


# --------------------------------------------------------------------------- #
# F. 非缺陷钉死
# --------------------------------------------------------------------------- #
def test_logger_level_is_min_of_console_and_file(fresh_logger, monkeypatch):
    monkeypatch.setattr(config, "LOG_CONSOLE_LEVEL", "WARNING")
    monkeypatch.setattr(config, "LOG_LEVEL", "DEBUG")
    lg = fresh_logger.get_logger("t90.minlevel")
    assert lg.level == logging.DEBUG  # 取更细的一级，保证不漏记


def test_propagate_is_disabled(fresh_logger):
    lg = fresh_logger.get_logger("t90.prop")
    assert lg.propagate is False


def test_get_logger_delegates_to_shared_builder():
    """源码结构断言：get_logger 必须消费共享 handler，而非自建。"""
    src = inspect.getsource(L.get_logger)
    assert "_ensure_shared_handlers" in src
    assert "RotatingFileHandler(" not in src, "get_logger 不应再自行创建文件 handler"
