# -*- coding: utf-8 -*-
"""阶段 277 · `ui/tab_models` 续审（5j 深度复审）— 钉死首轮遗漏的「成功/边界分支路由」。

背景
----
阶段 94 的首轮审计（test_ui_audit94.py）聚焦**缺陷**三类：D1 注册表半初始化、
D2 构建期崩溃、D3 异常必须转文案；阶段 162（test_tab_models_i18n.py）补了
「全角分隔符泄漏」。两者都**没有**逐条钉住各回调在**正常 / 边界输入**下的
返回值不变量（元组形状、gr.update 实参、Markdown 子分支文案）。这正是 5j
续审要补的「首轮遗漏」。

方法
----
桩掉 `tm._mm`（可控 FakeMM）+ 桩掉 `tm.gr.update`（Recorder 捕获实参），
只验证**分支路由与返回不变量**，不依赖真实模型注册表 / GPU / 网络。
每条断言都用「当前语言下的 `t(key)` 子串」或「语言无关的 structural token」
（'200.5 MB'、'*'、'—'、'/models/lama.pt'、'INJECTED'），对 zh/en 双语稳定。

覆盖清单（首轮遗漏的成功/边界分支）
-----------------------------------
· `_render_models_md`：size_mb / expected_mb / size_unknown / 空槽 / active 星标 /
  loaded 标签 / note / list_models 抛错 / get_active_model 抛错
· `_render_status_md`：cpu(无 vram 行) / gpu(含 vram 行) / fp16 开 / fp16 关 /
  path=None→'—' / get_model_info 抛错
· `on_set_active`：空名→no_slot / ok→gr.update(choices,value) / fail→gr.update()
· `on_download`：空名回退 active / 进度分数映射 / total=0 不崩
· `on_verify`：None 名 / ok / fail
· `on_rollback`：None 名 / ok / fail
· `on_reload`：ok+loaded / ok+未载 / fail
· `on_detail`：None 名 / missing / ok / fail
· `_safe_active_model`：正常 / 抛错回退 lama
· `on_refresh`：4 元组 + 下拉 gr.update(choices,value)
"""
from __future__ import annotations

import pytest

import ui.tab_models as tm
from utils.i18n import get_language, set_language, t


# ---------------------------------------------------------------------------
# 受控替身
# ---------------------------------------------------------------------------
class FakeMM:
    """按 ctrl 字典返回受控数据，或按 boom_* 键抛错（模拟 model_manager 异常）。"""

    def __init__(self, ctrl):
        self.c = ctrl

    def list_models(self):
        if self.c.get("boom_list"):
            raise OSError("INJECTED")
        return self.c["slots"]

    def get_active_model(self):
        if self.c.get("boom_active"):
            raise OSError("INJECTED")
        return self.c["active"]

    def is_model_available(self, name):
        return any(s["name"] == name for s in self.c["slots"])

    def get_model_info(self):
        if self.c.get("boom_info"):
            raise OSError("INJECTED")
        return self.c["info"]

    def set_active_model(self, name):
        if self.c.get("boom_set"):
            raise OSError("INJECTED")
        return self.c.get("set_ok", True)

    def download_model(self, name, progress_cb=None):
        if self.c.get("boom_dl"):
            raise OSError("INJECTED")
        if progress_cb:
            progress_cb(self.c.get("dl_done", 50), self.c.get("dl_total", 100), 10.0)
        return f"/models/{name}.pt"

    def verify_model(self, name):
        if self.c.get("boom_verify"):
            raise OSError("INJECTED")
        return self.c["verify"]

    def rollback_model(self, name):
        if self.c.get("boom_rollback"):
            raise OSError("INJECTED")
        return self.c.get("rollback_ok", True)

    def reload_active(self):
        if self.c.get("boom_reload"):
            raise OSError("INJECTED")
        return self.c["reload"]

    def slot_detail(self, name):
        if self.c.get("boom_detail"):
            raise OSError("INJECTED")
        return self.c["detail"]


DEFAULT_CTRL = {
    "slots": [
        {"name": "lama", "display_name": "LaMa", "size_mb": 200.5, "loaded": True,
         "available": True, "note": "built-in"},
        {"name": "big", "display_name": "Big", "size_mb": None, "expected_mb": 300,
         "loaded": False, "available": True, "note": None},
    ],
    "active": "lama",
    "info": {"device": "cpu", "device_name": "CPU", "device_is_gpu": False,
             "fp16": False, "cache_count": 1, "loaded": True, "path": "/m/lama.pt"},
    "verify": {"ok": True, "name": "lama", "reason": ""},
    "rollback_ok": True,
    "reload": {"ok": True, "name": "lama", "loaded": True},
    "detail": {"exists": True, "name": "lama", "path": "/m/lama.pt", "size_mb": 200.5,
               "mtime": "x", "sha256": "abc", "sha_computed": "abc",
               "ok": True, "reason": ""},
}


class Recorder:
    """捕获 `gr.update` 调用实参（gr.update 返回对象无 .value 属性，无法直接 introspect）。"""

    def __init__(self):
        self.calls = []

    def __call__(self, *a, **k):
        self.calls.append((a, k))
        return ("UPDATE_SENTINEL", k)


@pytest.fixture(autouse=True)
def _lang_guard():
    prev = get_language()
    yield
    set_language(prev)


@pytest.fixture
def stub_mm():
    """安装可控 FakeMM，测试可就地改 ctrl。"""
    ctrl = dict(DEFAULT_CTRL)
    orig = tm._mm
    tm._mm = FakeMM(ctrl)
    yield ctrl
    tm._mm = orig


@pytest.fixture
def capture_update():
    """把 `tm.gr.update` 换成 Recorder，捕获每次调用的实参。"""
    rec = Recorder()
    orig = tm.gr.update
    tm.gr.update = rec
    yield rec
    tm.gr.update = orig


# ===========================================================================
# _render_models_md 子分支
# ===========================================================================
def test_render_models_md_normal_has_size_active_loaded_note(stub_mm):
    md = tm._render_models_md()
    assert "200.5 MB" in md
    assert "*" in md                       # active='lama' 的星标
    assert t("models.loaded_tag") in md
    assert "built-in" in md                # note 行


def test_render_models_md_expected_mb_branch(stub_mm):
    stub_mm["slots"] = [{"name": "big", "display_name": "Big", "size_mb": None,
                         "expected_mb": 300, "loaded": False, "available": True,
                         "note": None}]
    stub_mm["active"] = "big"
    md = tm._render_models_md()
    assert "300" in md                     # expected_mb 文本
    assert "200.5" not in md               # 确属 expected 分支而非 size_mb


def test_render_models_md_size_unknown_branch(stub_mm):
    stub_mm["slots"] = [{"name": "x", "display_name": "X", "size_mb": None,
                         "expected_mb": None, "loaded": False, "available": True,
                         "note": None}]
    md = tm._render_models_md()
    assert t("models.size_unknown") in md
    assert "MB" not in md


def test_render_models_md_empty(stub_mm):
    stub_mm["slots"] = []
    assert tm._render_models_md() == t("models.empty")


def test_render_models_md_list_fail_to_text(stub_mm):
    stub_mm["boom_list"] = True
    assert "INJECTED" in tm._render_models_md()


def test_render_models_md_active_fail_to_text(stub_mm):
    stub_mm["boom_active"] = True
    assert "INJECTED" in tm._render_models_md()


# ===========================================================================
# _render_status_md 子分支
# ===========================================================================
def test_render_status_md_cpu_no_vram_line(stub_mm):
    stub_mm["info"] = {"device": "cpu", "device_name": "CPU", "device_is_gpu": False,
                       "fp16": False, "cache_count": 1, "loaded": True, "path": "/m/lama.pt"}
    sm = tm._render_status_md()
    assert t("models.status_device_cpu") in sm
    assert t("models.status_vram", vram=0) not in sm


def test_render_status_md_gpu_has_vram_line(stub_mm):
    stub_mm["info"] = {"device": "cuda", "device_name": "RTX", "device_is_gpu": True,
                       "fp16": False, "cache_count": 1, "loaded": True,
                       "path": "/m/lama.pt", "vram_mb": 8000}
    sm = tm._render_status_md()
    assert t("models.status_device_gpu") in sm
    assert t("models.status_vram", vram=8000) in sm


def test_render_status_md_fp16_on_off(stub_mm):
    stub_mm["info"]["fp16"] = True
    assert t("models.status_fp16_on") in tm._render_status_md()
    stub_mm["info"]["fp16"] = False
    assert t("models.status_fp16_off") in tm._render_status_md()


def test_render_status_md_path_none_shows_dash(stub_mm):
    stub_mm["info"]["path"] = None
    assert "—" in tm._render_status_md()


def test_render_status_md_info_fail_to_text(stub_mm):
    stub_mm["boom_info"] = True
    assert "INJECTED" in tm._render_status_md()


# ===========================================================================
# on_set_active 边界
# ===========================================================================
def test_on_set_active_empty_name_no_slot(stub_mm):
    out = tm.on_set_active("")
    assert len(out) == 4
    assert out[3] == t("models.no_slot")


def test_on_set_active_ok_updates_selector(capture_update, stub_mm):
    out = tm.on_set_active("big")
    assert capture_update.calls[-1][1] == {"choices": ["lama", "big"], "value": "big"}
    assert t("models.set_active_done", name="big") in out[3]


def test_on_set_active_fail_no_update(capture_update, stub_mm):
    stub_mm["set_ok"] = False
    out = tm.on_set_active("big")
    assert capture_update.calls[-1][1] == {}      # gr.update() 无 choices/value
    assert t("models.set_active_fail", name="big") in out[3]


# ===========================================================================
# on_download 边界
# ===========================================================================
def test_on_download_empty_name_falls_back_to_active(stub_mm):
    assert "/models/lama.pt" in tm.on_download("")


class _Prog:
    def __init__(self):
        self.calls = []
    def __call__(self, *a, **k):
        self.calls.append(a)


def test_on_download_maps_progress_fraction(stub_mm):
    p = _Prog()
    tm.on_download("lama", progress=p)
    # calls[0] 是初始 progress(0.0, download_start)；calls[1] 才是 _cb 映射的进度分数
    assert len(p.calls) >= 2
    assert p.calls[1][0] == 0.5                    # 50/100 -> 0.5


def test_on_download_progress_total_zero_no_crash(stub_mm):
    stub_mm["dl_done"] = 0
    stub_mm["dl_total"] = 0
    p = _Prog()
    r = tm.on_download("lama", progress=p)
    assert "INJECTED" not in r
    assert len(p.calls) >= 2
    assert p.calls[1][0] == 0.0                    # 0/0 -> 0.0，不抛 ZeroDivision


# ===========================================================================
# on_verify 边界
# ===========================================================================
def test_on_verify_none_name_two_tuple(stub_mm):
    assert len(tm.on_verify(None)) == 2


def test_on_verify_ok(stub_mm):
    stub_mm["verify"] = {"ok": True, "name": "lama", "reason": ""}
    assert t("models.verify_pass") in tm.on_verify("lama")[1]


def test_on_verify_fail(stub_mm):
    stub_mm["verify"] = {"ok": False, "name": "lama", "reason": "bad"}
    assert t("models.verify_fail") in tm.on_verify("lama")[1]


# ===========================================================================
# on_rollback 边界
# ===========================================================================
def test_on_rollback_none_name_two_tuple(stub_mm):
    assert len(tm.on_rollback(None)) == 2


def test_on_rollback_ok(stub_mm):
    stub_mm["rollback_ok"] = True
    assert t("models.rollback_done", target="lama") in tm.on_rollback("lama")[1]


def test_on_rollback_fail(stub_mm):
    stub_mm["rollback_ok"] = False
    assert t("models.rollback_fail", target="lama") in tm.on_rollback("lama")[1]


# ===========================================================================
# on_reload 边界
# ===========================================================================
def test_on_reload_ok_loaded(stub_mm):
    stub_mm["reload"] = {"ok": True, "name": "lama", "loaded": True}
    out = tm.on_reload()
    assert len(out) == 3
    assert t("models.reload_loaded_yes") in out[2]


def test_on_reload_ok_not_loaded(stub_mm):
    stub_mm["reload"] = {"ok": True, "name": "lama", "loaded": False}
    assert t("models.reload_loaded_no") in tm.on_reload()[2]


def test_on_reload_fail(stub_mm):
    stub_mm["reload"] = {"ok": False, "error": "boom"}
    assert t("models.reload_fail", error="boom") in tm.on_reload()[2]


# ===========================================================================
# on_detail 边界
# ===========================================================================
def test_on_detail_none_name_returns_string(stub_mm):
    assert isinstance(tm.on_detail(None), str)


def test_on_detail_missing(stub_mm):
    stub_mm["detail"] = {"exists": False, "name": "lama"}
    assert t("models.detail_missing", name="lama") in tm.on_detail("lama")


def test_on_detail_ok(stub_mm):
    stub_mm["detail"] = {"exists": True, "name": "lama", "path": "/m/lama.pt",
                         "size_mb": 200.5, "mtime": "x", "sha256": "abc",
                         "sha_computed": "abc", "ok": True, "reason": ""}
    assert t("models.verify_pass") in tm.on_detail("lama")


def test_on_detail_fail(stub_mm):
    stub_mm["detail"] = {"exists": True, "name": "lama", "path": "/m/lama.pt",
                         "size_mb": 200.5, "mtime": "x", "sha256": "abc",
                         "sha_computed": "abc", "ok": False, "reason": "bad"}
    assert t("models.verify_fail") in tm.on_detail("lama")


# ===========================================================================
# _safe_active_model / on_refresh
# ===========================================================================
def test_safe_active_model_normal(stub_mm):
    assert tm._safe_active_model() == "lama"


def test_safe_active_model_fallback(stub_mm):
    stub_mm["boom_active"] = True
    assert tm._safe_active_model() == "lama"


def test_on_refresh_four_tuple_with_selector_update(capture_update, stub_mm):
    out = tm.on_refresh()
    assert len(out) == 4
    assert capture_update.calls[-1][1] == {"choices": ["lama", "big"], "value": "lama"}
    assert out[3] == t("models.refresh_done")
