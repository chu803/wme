# -*- coding: utf-8 -*-
"""REST/库层 API 集成测试（阶段 9/11/12/13）。

覆盖：
- get_capabilities() 含 engines（含插件）与 models/active_model（阶段 11/12 新增）。
- list_models() / verify_model() / set_active_model() 程序化入口可用且不抛异常。
- 验证不使用网络、不依赖 torch。
"""

import api
import pytest

from core.model_loader import is_model_available

# 环境前提：以下「模型缺失」行为验证仅在权重未就绪时运行。
# 权重就绪（本开发环境已装 torch + big-lama.pt）后行为反转，验证无意义。
_MODEL_PRESENT = is_model_available()


def test_capabilities_has_engines_and_models():
    """能力快照应暴露动态引擎清单与模型管理信息。"""
    caps = api.get_capabilities()
    assert isinstance(caps["engines"], list)
    eng_names = [e["name"] for e in caps["engines"]]
    for needed in ("auto", "lama", "opencv", "demo_model"):
        assert needed in eng_names
    assert "models" in caps
    assert "active_model" in caps
    assert isinstance(caps["models"], list)


def test_list_models():
    """list_models 返回字典列表且含内置 lama 槽。"""
    models = api.list_models()
    assert isinstance(models, list)
    names = [m["name"] for m in models]
    assert "lama" in names


@pytest.mark.skipif(_MODEL_PRESENT, reason="LaMa 权重已就绪，跳过「模型缺失」行为验证")
def test_verify_model_missing():
    """verify_model 对缺失权重返回 ok=False 字典（不抛异常）。"""
    v = api.verify_model("lama")
    assert isinstance(v, dict)
    assert v["ok"] is False


def test_set_active_model_valid():
    """set_active_model('lama') 应返回 True。"""
    assert api.set_active_model("lama") is True


def test_set_active_model_invalid():
    """set_active_model 未知槽应返回 False，而非抛异常。"""
    assert api.set_active_model("no_such_slot_xyz") is False


def test_get_active_model_returns_str():
    """get_active_model 应返回非空字符串。"""
    name = api.get_active_model()
    assert isinstance(name, str) and len(name) > 0
