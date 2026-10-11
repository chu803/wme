# -*- coding: utf-8 -*-
"""
阶段 289 续审守卫：utils/logger.py 首轮 (阶段 90, tests/test_utils_audit90.py) 未机控的
分支/公共 API 不变量钉死。

阶段 90 已覆盖：L1 共享 handler + 轮转健康、L2 级别别名/安全兜底、L3 整行着色契约、
L4 陈旧 handler 关闭 + 夹具 teardown 不泄漏、min(level)/propagate=False/委托共享。

本套件补齐首轮漏掉：
  F1  _ColorFormatter.format(use_color=False) 不注入 ANSI（即便环境有 colorama）。
  F2  _ColorFormatter.format 在 _HAS_COLOR=False 时不注入 ANSI（无论 use_color）。
  F3  print_banner() 公共函数：三行包裹、文本居中、不创建/写日志文件、不崩。
  F4  _ensure_global_config() 幂等（_initialized 守卫 + 目录 exist_ok）。
  F5  _ensure_shared_handlers() 复用同一批对象（不重建 -> 防 L1 多 handler 回归）；
       error handler 恒为 ERROR 级。
  F6  get_logger() 默认名 "WME" 返回同名 logger 且入缓存。
  F7  _resolve_level() 非字符串入参（int/字节串）安全兜底（str() 转换）。

探针 (.scratch/probe_logger_reaudit.py) 全绿，结论：0 真缺陷，仅补齐分支守卫。
"""
import io
import logging
import logging.handlers as LH
import os
from pathlib import Path

import pytest

import config
from utils import logger as L


@pytest.fixture
def fresh_logger(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", Path(tmp_path))
    old_cache = L._logger_cache
    old_shared = L._shared_handlers
    old_init = L._initialized
    old_color = L._HAS_COLOR

    L._logger_cache = {}
    L._shared_handlers = {}
    L._initialized = False

    yield L

    # 阶段 246 同法：先摘除、后关闭，避免关闭的 handler 留全局注册表跨用例存活。
    for lg in list(L._logger_cache.values()):
        for h in list(lg.handlers):
            lg.removeHandler(h)
    for h in list(L._shared_handlers.values()):
        h.close()
    L._shared_handlers = old_shared
    L._logger_cache = old_cache
    L._initialized = old_init
    L._HAS_COLOR = old_color


# --------------------------------------------------------------------------- #
# F1 + F2：_ColorFormatter 不着色的两个分支
# --------------------------------------------------------------------------- #
def test_color_formatter_no_ansi_when_use_color_false():
    fmt = L._ColorFormatter(fmt="%(levelname)s %(message)s", datefmt="", use_color=False)
    rec = logging.LogRecord("n", logging.ERROR, "p", 1, "body", None, None)
    out = fmt.format(rec)
    assert out == "ERROR body", "use_color=False 必须返回纯文本"
    assert "\x1b[" not in out, "use_color=False 绝不含 ANSI 转义"


def test_color_formatter_no_ansi_when_color_disabled(monkeypatch):
    monkeypatch.setattr(L, "_HAS_COLOR", False)
    fmt = L._ColorFormatter(fmt="%(levelname)s %(message)s", datefmt="", use_color=True)
    rec = logging.LogRecord("n", logging.ERROR, "p", 1, "body", None, None)
    out = fmt.format(rec)
    assert "\x1b[" not in out, "_HAS_COLOR=False 时即便 use_color=True 也无 ANSI"
    assert out == "ERROR body"


# --------------------------------------------------------------------------- #
# F3：print_banner 公共函数契约
# --------------------------------------------------------------------------- #
def test_print_banner_wraps_text_in_three_lines(fresh_logger, capsys):
    L.print_banner("启动横幅")
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) == 3, f"恰好三行（上分隔/文本/下分隔），实际 {len(lines)}"
    assert lines[0].startswith("=") and lines[0] == lines[-1], "首尾为等号分隔行且等长"
    assert lines[1] == "启动横幅", "中间行为传入文本"
    # 不写日志文件：print_banner 只打 stdout，不碰 logger。
    app_log = os.path.join(str(config.LOG_DIR), config.LOG_FILE_NAME)
    assert not os.path.exists(app_log), "print_banner 不应创建/写入日志文件"


# --------------------------------------------------------------------------- #
# F4：_ensure_global_config 幂等
# --------------------------------------------------------------------------- #
def test_ensure_global_config_is_idempotent(fresh_logger):
    L._ensure_global_config()
    assert L._initialized is True
    assert os.path.isdir(str(config.LOG_DIR))
    before = L._initialized
    L._ensure_global_config()  # 二次调用不应重建/报错
    assert L._initialized is before, "二次调用不改变 _initialized（早返回）"
    assert os.path.isdir(str(config.LOG_DIR))


# --------------------------------------------------------------------------- #
# F5：_ensure_shared_handlers 复用同一批对象（防 L1 回归）
# --------------------------------------------------------------------------- #
def test_ensure_shared_handlers_reuses_same_objects(fresh_logger):
    h1 = L._ensure_shared_handlers()
    h2 = L._ensure_shared_handlers()
    assert h1 is h2, "二次调用必须返回同一字典（不重建 handler）"
    assert len(h1) == 3 and set(h1) == {"console", "file", "error"}
    assert all(id(h1[k]) == id(h2[k]) for k in h1), "handler 对象身份一致（同一批）"
    err_h = h1["error"]
    assert isinstance(err_h, LH.RotatingFileHandler) and err_h.level == logging.ERROR, \
        "error handler 恒为 ERROR 级"


# --------------------------------------------------------------------------- #
# F6：get_logger 默认名
# --------------------------------------------------------------------------- #
def test_get_logger_default_name_is_wme(fresh_logger):
    lg = L.get_logger()
    assert lg.name == "WME", f"默认名应为 WME，实际 {lg.name!r}"
    assert L.get_logger() is lg, "默认名 logger 也入缓存（二次同对象）"


# --------------------------------------------------------------------------- #
# F7：_resolve_level 非字符串入参安全兜底
# --------------------------------------------------------------------------- #
def test_resolve_level_non_string_inputs(fresh_logger):
    assert L._resolve_level(10) == logging.INFO, "int 入参安全兜底 INFO"
    assert L._resolve_level(10, fallback=logging.CRITICAL) == logging.CRITICAL, \
        "int 入参 + 自定义 fallback"
    assert L._resolve_level(b"DEBUG") == logging.INFO, "字节串入参安全兜底 INFO"
    assert L._resolve_level("warn") == logging.WARNING, "小写别名 WARN 仍正常"
