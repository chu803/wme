# -*- coding: utf-8 -*-
"""阶段 345 · 视频页两个互斥开关的**初始状态**必须与实际生效状态一致（审计 F4）。

缺陷：默认 `config.VIDEO_REUSE_MASK=True`（复用掩码开着），而「逐帧智能推荐」复选框初始
`value=True` ⇒ **界面显示勾选，实际被服务层强制关闭**
（`services/video_service.py:493`：`detect_auto = bool(auto_preset) and not reuse`）。
连带两处误导：检测模式下拉照常可点但选了被 `preset=None` 忽略；
复选框自己的 info 写着「勾选『复用水印位置』时自动关闭」——**文案与实际相反**。

判决（阶段 345）：**阶段 27 的语义是对的**（复用掩码 ⇒ 整段共用一个预设更合理），
错的只是**初始显示**。所以只把初始状态按同一套语义推出来，不改判定逻辑、不改 config 默认值、
也不改用户手动勾选后的行为。
"""
from __future__ import annotations

import ast
import pathlib

import config
import ui.tab_media as tm


def _effective_detect_auto(auto_preset: bool, reuse: bool) -> bool:
    """服务层的公式（`video_service.py:493`）——这里复制一份作为**判据的权威出处**。"""
    return bool(auto_preset) and not reuse


def test_defaults_follow_the_same_rule_as_the_service():
    """复用开着 ⇒ 推荐关着；复用关着 ⇒ 推荐开着。"""
    on = tm.video_recommend_defaults(reuse=True)
    assert on["reuse"] is True
    assert on["auto_preset"] is False
    assert on["auto_preset_interactive"] is False, "复用开着时这个开关不该可点（与 .change 语义一致）"

    off = tm.video_recommend_defaults(reuse=False)
    assert off["auto_preset"] is True
    assert off["auto_preset_interactive"] is True


def test_defaults_never_contradict_the_service_formula():
    """**核心不变量**：显示的初始状态代入服务层公式，结果必须与显示一致
    （显示勾选 ⇒ 实际生效；显示未勾 ⇒ 实际不生效）。"""
    for reuse in (True, False):
        d = tm.video_recommend_defaults(reuse=reuse)
        effective = _effective_detect_auto(d["auto_preset"], d["reuse"])
        assert effective == d["auto_preset"], (
            f"reuse={reuse}：界面显示 auto_preset={d['auto_preset']}，"
            f"服务层实际算出 {effective} ⇒ 显示与实际不一致")
        # 阶段 27 承诺：复用开着 ⇒ 推荐必须关（info 文案就是这么写的）。
        assert not (d["auto_preset"] and d["reuse"]), d


def test_default_config_does_not_show_a_switch_that_is_off():
    """按**本机真实默认值**验：VIDEO_REUSE_MASK=True 时，界面不该把推荐显示成勾选。"""
    d = tm.video_recommend_defaults()
    assert d["reuse"] == bool(config.VIDEO_REUSE_MASK)
    assert d["auto_preset"] is False, (
        f"默认 VIDEO_REUSE_MASK={config.VIDEO_REUSE_MASK} ⇒ 推荐必须显示为关，"
        f"实得 auto_preset={d['auto_preset']}")


def test_ui_uses_the_helper_instead_of_a_literal():
    """接线守卫：构造处必须用 helper 的值，**不许再写死 `value=True`**。

    这条比运行时断言更早失败：写死的初值就是本缺陷的形状本身。
    """
    src = pathlib.Path(tm.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _checkbox_value(name: str):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if name not in targets:
                continue
            if not isinstance(node.value, ast.Call):
                continue
            fn = node.value.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "Checkbox"):
                continue
            for kw in node.value.keywords:
                if kw.arg == "value":
                    return ast.unparse(kw.value)
            return None
        return None

    # `ast.unparse` 会把引号统一成单引号 ⇒ 比较前归一化。
    got = _checkbox_value("video_auto_preset")
    assert got is not None and got.replace("'", '"') == '_vd["auto_preset"]', (
        "「逐帧智能推荐」复选框的初值必须来自 video_recommend_defaults()，"
        f"写死字面量就是本缺陷的形状（实得 {got!r}）")
    assert "value=True" not in src.split("video_auto_preset = gr.Checkbox")[1][:200], (
        "复选框初值又写回了字面量 True")
