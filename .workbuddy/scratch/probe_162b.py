# -*- coding: utf-8 -*-
"""Stage 162 探针 B：stub core.model_manager 后驱动 tab_models 的全部 on_* 处理器，
检查（a）边界输入不崩、（b）返回元组长度正确、（c）异常被兜住（不穿透）。"""
from __future__ import annotations

import sys
import types

sys.path.insert(0, "D:/work 111/2026-08-03-17-38-51")

import ui.tab_models as tm  # noqa: E402


class FakeMM:
    def __init__(self):
        self.raise_on = set()

    def _maybe(self, name):
        if name in self.raise_on:
            raise RuntimeError(f"boom:{name}")

    def list_models(self):
        self._maybe("list_models")
        return [{"name": "lama", "display_name": "LaMa", "size_mb": 200.5,
                 "loaded": True, "note": "内置", "expected_mb": None},
                {"name": "x", "display_name": "X", "size_mb": None,
                 "loaded": False, "note": "", "expected_mb": 100}]

    def get_active_model(self):
        self._maybe("get_active_model")
        return "lama"

    def is_model_available(self, name):
        return name == "lama"

    def get_model_info(self):
        self._maybe("get_model_info")
        return {"device": "cpu", "device_name": "CPU", "device_is_gpu": False,
                "fp16": False, "cache_count": 1, "loaded": True, "path": "/m/lama.pt"}

    def set_active_model(self, name):
        self._maybe("set_active_model")
        return name == "lama"

    def download_model(self, name, progress_cb=None):
        self._maybe("download_model")
        if progress_cb:
            progress_cb(50, 100, 1234.0)
        return "/m/lama.pt"

    def verify_model(self, name=None):
        self._maybe("verify_model")
        return {"ok": True, "name": name or "lama", "reason": ""}

    def rollback_model(self, name):
        self._maybe("rollback_model")
        return True

    def reload_active(self):
        self._maybe("reload_active")
        return {"ok": True, "name": "lama", "loaded": True, "error": ""}

    def slot_detail(self, name=None):
        self._maybe("slot_detail")
        return {"exists": True, "name": name or "lama", "ok": True, "path": "/m/lama.pt",
                "size_mb": 200.5, "mtime": "2026-01-01 00:00:00", "sha256": "aa",
                "sha_computed": "aa", "reason": ""}


class FakeProgress:
    def __call__(self, *a, **k):
        pass


def expect(tag, fn, want_len):
    try:
        r = fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[{tag}] ✗ 未兜住异常：{type(exc).__name__}: {exc}")
        return
    n = len(r) if isinstance(r, tuple) else 1
    print(f"[{tag}] OK 返回 {n} 项 {'✓' if n == want_len else '✗ 期望 ' + str(want_len)}")


def run_case(label, mutate, calls):
    mm = FakeMM()
    mutate(mm)
    tm._mm = mm
    print(f"--- {label} ---")
    for tag, fn, want in calls:
        expect(tag, fn, want)
    print()


if __name__ == "__main__":
    # 正常
    run_case("正常", lambda m: None, [
        ("on_refresh", lambda: tm.on_refresh(), 4),
        ("on_set_active(空)", lambda: tm.on_set_active(""), 4),
        ("on_set_active(lama)", lambda: tm.on_set_active("lama"), 4),
        ("on_set_active(x)", lambda: tm.on_set_active("x"), 4),
        ("on_download(空)", lambda: tm.on_download("", FakeProgress()), 1),
        ("on_download(lama)", lambda: tm.on_download("lama", FakeProgress()), 1),
        ("on_verify(空)", lambda: tm.on_verify(""), 2),
        ("on_rollback(空)", lambda: tm.on_rollback(""), 2),
        ("on_reload", lambda: tm.on_reload(), 3),
        ("on_detail(空)", lambda: tm.on_detail(""), 1),
        ("on_detail(lama)", lambda: tm.on_detail("lama"), 1),
    ])
    # 每个底层函数抛异常：处理器必须兜住
    for fname in ("list_models", "get_active_model", "get_model_info", "set_active_model",
                  "download_model", "verify_model", "rollback_model", "reload_active",
                  "slot_detail"):
        run_case(f"底层 {fname} 抛异常", lambda m, f=fname: m.raise_on.add(f), [
            ("on_refresh", lambda: tm.on_refresh(), 4),
            ("on_set_active(lama)", lambda: tm.on_set_active("lama"), 4),
            ("on_download(lama)", lambda: tm.on_download("lama", FakeProgress()), 1),
            ("on_verify(lama)", lambda: tm.on_verify("lama"), 2),
            ("on_rollback(lama)", lambda: tm.on_rollback("lama"), 2),
            ("on_reload", lambda: tm.on_reload(), 3),
            ("on_detail(lama)", lambda: tm.on_detail("lama"), 1),
        ])
    print("PROBE DONE")
