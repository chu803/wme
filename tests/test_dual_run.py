"""阶段 74 · 双跑择优（dual-run）回归测试。

背景（docs/74-*）：阶段 56 建好的「无参考质量分 + 双跑」基础设施此前只进诊断工具
（P0 真实图验证前不进热路径）。本阶段把它落地为**部署级放行闸能力**：
`INPAINT_DUAL_RUN`（默认 False）+ `INPAINT_DUAL_SECONDARY`（默认 lama）+
`inpaint(..., dual_run=)` 显式覆盖（UI/CLI 入口）。

本文件锁定：
① `pick_better_result` 纯判定语义（分高者胜、平局取主引擎、eps 容差）；
② `_resolve_dual_secondary` 跳过条件（同引擎 / 未知 / auto / 模型不可用）；
③ 默认关闭（配置门控）——副引擎不被调用；
④ 端到端择优：分数决定胜负（副引擎胜 / 主引擎胜 / 副引擎失败回退主引擎）。

引擎注入用 `core.plugins` 的临时注册表（与 test_plugins 同模式，finally 清理）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import core.inpainter as inp  # noqa: E402
from core.plugins import InpaintEnginePlugin, _REGISTRY  # noqa: E402


# ---------------------------------------------------------------------------
# 公共构造
# ---------------------------------------------------------------------------

def _image_and_mask():
    """平坦底图 + 中央方块水印（OpenCV 修复会把它填回 ~200）。"""
    img = np.full((120, 160, 3), 200, np.uint8)
    img[40:80, 60:100] = 50
    mask = np.zeros((120, 160), np.uint8)
    mask[40:80, 60:100] = 255
    return img, mask


class _FakeEngine(InpaintEnginePlugin):
    """可控输出的假引擎：返回 `image + delta`（中心像素 = 200 + delta）。"""
    name = "dual_fake"
    display_name = "双跑测试引擎"
    description = "test-only"
    requires_model = False

    def __init__(self, delta: int, fail: bool = False):
        self.delta = delta
        self.fail = fail
        self.calls = 0

    def inpaint(self, image, mask, *, device=None, progress=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("fake engine failure")
        return np.clip(image.astype(np.int16) + self.delta, 0, 255).astype(np.uint8)


@pytest.fixture()
def fake_engine():
    eng = _FakeEngine(delta=15)
    _REGISTRY["dual_fake"] = eng
    try:
        yield eng
    finally:
        _REGISTRY.pop("dual_fake", None)


@pytest.fixture()
def fixed_scores(monkeypatch):
    """把无参考质量分替换为按调用顺序出队的固定分数（首个 = 主引擎，第二个 = 副引擎）。"""
    queue = []

    def _fake(res, source, mask):
        return queue.pop(0) if queue else 0.5

    monkeypatch.setattr(inp, "no_reference_inpaint_score", _fake)
    return queue


_KW = dict(strength=1.0, dilate=0, feather=0, denoise=0, engine="opencv")


# ---------------------------------------------------------------------------
# 1. pick_better_result 纯判定
# ---------------------------------------------------------------------------

def test_pick_secondary_when_clearly_better():
    a = np.zeros((4, 4, 3), np.uint8)
    b = np.ones((4, 4, 3), np.uint8)
    assert inp.pick_better_result(a, b, 0.5, 0.9) is b


def test_pick_primary_when_better():
    a = np.zeros((4, 4, 3), np.uint8)
    b = np.ones((4, 4, 3), np.uint8)
    assert inp.pick_better_result(a, b, 0.9, 0.5) is a


def test_tie_returns_primary():
    """平局（含 eps 容差内）取主引擎 a——保守：不因噪声换引擎。"""
    a = np.zeros((4, 4, 3), np.uint8)
    b = np.ones((4, 4, 3), np.uint8)
    assert inp.pick_better_result(a, b, 0.5, 0.5) is a
    assert inp.pick_better_result(a, b, 0.5, 0.5 + config.INPAINT_DUAL_EPS) is a


def test_pick_respects_custom_eps():
    a = np.zeros((4, 4, 3), np.uint8)
    b = np.ones((4, 4, 3), np.uint8)
    # 分差 0.01：eps=0.001 → 副引擎胜；eps=0.1 → 平局取主引擎。
    assert inp.pick_better_result(a, b, 0.50, 0.51, eps=0.001) is b
    assert inp.pick_better_result(a, b, 0.50, 0.51, eps=0.1) is a


# ---------------------------------------------------------------------------
# 2. _resolve_dual_secondary 跳过条件
# ---------------------------------------------------------------------------

def test_secondary_same_engine_skipped():
    assert inp._resolve_dual_secondary("opencv", "opencv") is None


def test_secondary_auto_or_unknown_skipped():
    assert inp._resolve_dual_secondary("opencv", "auto") is None
    assert inp._resolve_dual_secondary("opencv", "no_such_engine") is None
    assert inp._resolve_dual_secondary("opencv", "") is None


def test_secondary_valid_engine_resolved(fake_engine):
    assert inp._resolve_dual_secondary("opencv", "dual_fake") == "dual_fake"


def test_secondary_model_unavailable_skipped(monkeypatch):
    """副引擎需要模型而模型不可用 → 跳过双跑（保证总能修）。"""
    monkeypatch.setattr(inp, "is_model_available", lambda: False)
    assert inp._resolve_dual_secondary("opencv", "lama") is None


def test_secondary_model_available_resolved(monkeypatch):
    monkeypatch.setattr(inp, "is_model_available", lambda: True)
    assert inp._resolve_dual_secondary("opencv", "lama") == "lama"


# ---------------------------------------------------------------------------
# 3. 配置门控：默认关闭
# ---------------------------------------------------------------------------

def test_dual_disabled_by_config(fake_engine, monkeypatch):
    """INPAINT_DUAL_RUN=False（默认）且未显式覆盖 → 副引擎根本不被调用。"""
    monkeypatch.setattr(config, "INPAINT_DUAL_RUN", False)
    monkeypatch.setattr(config, "INPAINT_DUAL_SECONDARY", "dual_fake")
    img, mask = _image_and_mask()
    inp.inpaint(img, mask, **_KW, dual_run=None)
    assert fake_engine.calls == 0


def test_dual_disabled_by_default_constant():
    assert config.INPAINT_DUAL_RUN is False
    assert config.INPAINT_DUAL_SECONDARY == "lama"


# ---------------------------------------------------------------------------
# 4. 端到端择优（假引擎 + 固定分数）
# ---------------------------------------------------------------------------

def test_dual_secondary_wins(fake_engine, fixed_scores, monkeypatch):
    monkeypatch.setattr(config, "INPAINT_DUAL_RUN", True)
    monkeypatch.setattr(config, "INPAINT_DUAL_SECONDARY", "dual_fake")
    fixed_scores.extend([0.5, 0.9])            # 主 0.5 < 副 0.9
    img, mask = _image_and_mask()
    out = inp.inpaint(img, mask, **_KW, dual_run=None)
    assert fake_engine.calls == 1
    # 副引擎输出 = 输入图+15：掩码内中心 = 50(水印)+15 = 65（strength=1.0 直通）。
    assert out[60, 80, 0] == 65


def test_dual_primary_wins_on_scores(fake_engine, fixed_scores, monkeypatch):
    monkeypatch.setattr(config, "INPAINT_DUAL_RUN", True)
    monkeypatch.setattr(config, "INPAINT_DUAL_SECONDARY", "dual_fake")
    fixed_scores.extend([0.9, 0.5])            # 主 0.9 > 副 0.5
    img, mask = _image_and_mask()
    out = inp.inpaint(img, mask, **_KW, dual_run=None)
    assert fake_engine.calls == 1              # 副引擎仍跑过（择优需要分数）
    # 主引擎（opencv）胜出：中心像素被填回 ~200，绝不是副引擎的 215。
    assert abs(int(out[60, 80, 0]) - 200) <= 5


def test_dual_secondary_failure_falls_back(fake_engine, fixed_scores, monkeypatch):
    """副引擎中途崩溃 → 回退主引擎单跑，绝不拖垮主链路。"""
    fake_engine.fail = True
    monkeypatch.setattr(config, "INPAINT_DUAL_RUN", True)
    monkeypatch.setattr(config, "INPAINT_DUAL_SECONDARY", "dual_fake")
    fixed_scores.extend([0.9])                 # 只有主引擎会被打分
    img, mask = _image_and_mask()
    out = inp.inpaint(img, mask, **_KW, dual_run=None)
    assert fake_engine.calls == 1              # 副引擎尝试过一次
    assert abs(int(out[60, 80, 0]) - 200) <= 5


def test_dual_explicit_override_beats_config_off(fake_engine, fixed_scores, monkeypatch):
    """config 关闭但调用方显式 dual_run=True → 显式值优先（UI 复选框路径）。"""
    monkeypatch.setattr(config, "INPAINT_DUAL_RUN", False)
    monkeypatch.setattr(config, "INPAINT_DUAL_SECONDARY", "dual_fake")
    fixed_scores.extend([0.5, 0.9])
    img, mask = _image_and_mask()
    inp.inpaint(img, mask, **_KW, dual_run=True)
    assert fake_engine.calls == 1
