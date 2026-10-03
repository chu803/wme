# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 137 守卫测试：utils/i18n 结构对称性校验 / dict 节点 / save_language 校验
------------------------------------------------------------------------------
 背景（docs/137）：
   D1  契约「中英文一一对应 + 占位符同名」无机器校验 ⇒ 新增
       validate_i18n_parity()（562 叶子全量扫：键集 / 类型 / 占位符三重），
       接入 __main__ 自检；本文件同时注入**合成漂移**证明校验器本身有效。
   D2  t() 命中中间层 dict 时把 dict 的 repr 泄漏给界面 ⇒ 按缺失处理返回 key。
   D3  save_language 非法代码直接落盘垃圾 ⇒ 拒绝并告警。
 注入验证：回退 D2 后本文件必须红（见 docs/137 注入记录）。
==============================================================================
"""

from __future__ import annotations

import copy
import os as _os

import pytest

from utils import i18n


# ------------------------------------------------------------------------------
# D1：结构对称性校验——现状必须干净
# ------------------------------------------------------------------------------
def test_validate_parity_clean():
    problems = i18n.validate_i18n_parity()
    assert problems == [], f"语言包存在结构漂移：{problems[:10]}"


def test_validate_detects_missing_leaf(monkeypatch):
    """注入合成漂移（en-US 删一个叶子）——证明校验器本身不是哑巴。"""
    broken = copy.deepcopy(i18n.TRANSLATIONS)
    del broken["en-US"]["common"]["on"]
    monkeypatch.setattr(i18n, "TRANSLATIONS", broken)
    problems = i18n.validate_i18n_parity()
    assert any("common.on" in p for p in problems), f"未检出缺失叶子：{problems}"


def test_validate_detects_placeholder_mismatch(monkeypatch):
    broken = copy.deepcopy(i18n.TRANSLATIONS)
    # zh 用 {coverage}，en 改名 {pct} ⇒ format 语义漂移，必须被检出
    broken["en-US"]["detect"]["stats"]["coverage"] = broken["en-US"]["detect"]["stats"][
        "coverage"
    ].replace("{coverage}", "{pct}")
    monkeypatch.setattr(i18n, "TRANSLATIONS", broken)
    problems = i18n.validate_i18n_parity()
    assert any("detect.stats.coverage" in p and "占位符" in p for p in problems), (
        f"未检出占位符漂移：{problems}"
    )


def test_validate_detects_type_mismatch(monkeypatch):
    broken = copy.deepcopy(i18n.TRANSLATIONS)
    broken["en-US"]["common"]["on"] = 123  # zh 是 str ⇒ 叶子类型不一致
    monkeypatch.setattr(i18n, "TRANSLATIONS", broken)
    problems = i18n.validate_i18n_parity()
    assert any("common.on" in p and "类型不一致" in p for p in problems)


def test_validate_detects_dict_substitution_as_key_drift(monkeypatch):
    """叶子被换成子树 ⇒ 展平后表现为键集漂移（同样必须被检出，类目不同）。"""
    broken = copy.deepcopy(i18n.TRANSLATIONS)
    broken["en-US"]["app"]["compliance"] = {"nested": "dict"}  # zh 是 str
    monkeypatch.setattr(i18n, "TRANSLATIONS", broken)
    problems = i18n.validate_i18n_parity()
    assert any("app.compliance" in p for p in problems), f"dict 替换未被检出：{problems}"


# ------------------------------------------------------------------------------
# D2：dict 中间层按缺失处理
# ------------------------------------------------------------------------------
def test_t_dict_node_returns_key(monkeypatch):
    """
    阶段 140 加固（经验 122）：本用例断言中文译文，**必须显式设置语言**，
    不能依赖「本机状态文件恰好是 zh-CN」——历史上 `tests/test_i18n.py`
    的 apply_language 用例会把真实 cache 写成 en-US，导致本用例在后续运行中假红。
    """
    monkeypatch.setattr(i18n, "_LANG", "zh-CN")
    assert i18n.t("app.tab") == "app.tab"
    assert i18n.t_in("en-US", "detect.preset") == "detect.preset"
    # 对照：正常叶子不受影响
    assert i18n.t("app.tab.single") == "单图处理（手动涂抹）"

    # 显式切到英文，验证同一回退契约在英文下同样成立。
    monkeypatch.setattr(i18n, "_LANG", "en-US")
    assert i18n.t("app.tab") == "app.tab"
    assert i18n.t("app.tab.single") == "Single Image (Manual)"

# ------------------------------------------------------------------------------
# D3：save_language 拒绝非法代码
# ------------------------------------------------------------------------------
def test_save_language_rejects_invalid_code(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(i18n, "_STATE_FILE", str(state))
    i18n.save_language("fr-FR")  # 非法
    assert not state.exists(), "非法语言代码不应落盘"
    i18n.save_language("en-US")  # 合法
    assert state.exists()
    assert '"lang": "en-US"' in state.read_text(encoding="utf-8")


# ------------------------------------------------------------------------------
# D4（阶段 140 勘误）：自检里的静态 t("...") 探针键必须已在覆盖白名单登记
# ------------------------------------------------------------------------------
def test_selfcheck_static_keys_are_allowlisted():
    """
    阶段 137 自检曾用未登记键验证「缺失键回退为键名」，
    但 `tests/test_i18n_coverage._scan_static_keys()` 会正则扫描**整个**
    utils/i18n.py（**注释与文档字符串同样会被扫到**），把自检探针/说明文字
    误判为「生产代码引用了未定义键」→ 全量 pytest 红（Stage 140 实测捕获）。

    这条守卫钉住规则：**utils/i18n.py 全文（含注释）里出现的 `t("X")` 形式
    字面量，要么真实存在于键表、要么已在 i18n_coverage 的 `_ALLOWED_UNKNOWN` 登记**。
    严格程度对齐真实扫描器（不剥注释），这样注释里写示例也不会误伤。
    """
    import re as _re

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    src = open(_os.path.join(root, "utils", "i18n.py"), encoding="utf-8").read()
    # 对齐 test_i18n_coverage._scan_static_keys 的正则与范围（全文，不剥注释）。
    keys = set(_re.findall(r"""\bt\(\s*["']([^"']+)["']""", src))

    from utils.i18n import TRANSLATIONS

    def _leaves(d, pre=""):
        out = {}
        for k, v in d.items():
            kk = f"{pre}.{k}" if pre else k
            if isinstance(v, dict):
                out.update(_leaves(v, kk))
            else:
                out[kk] = v
        return out

    all_keys = set(_leaves(TRANSLATIONS["zh-CN"])) | set(_leaves(TRANSLATIONS["en-US"]))
    cov_src = open(
        _os.path.join(root, "tests", "test_i18n_coverage.py"), encoding="utf-8"
    ).read()
    m = _re.search(r"_ALLOWED_UNKNOWN\s*=\s*\{(.*?)\}", cov_src, _re.S)
    assert m, "未能解析 _ALLOWED_UNKNOWN"
    allowed = set(_re.findall(r"""["']([^"']+)["']""", m.group(1)))

    unregistered = sorted(k for k in keys if k not in all_keys and k not in allowed)
    assert not unregistered, (
        "utils/i18n.py 中出现的静态 t() 字面量既不在键表、也未在 _ALLOWED_UNKNOWN "
        f"登记，会导致 test_i18n_coverage 误报：{unregistered}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
