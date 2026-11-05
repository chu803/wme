# -*- coding: utf-8 -*-
"""修复引擎的「如实展示」（阶段 382）。

为什么需要：界面以前显示的是**用户选的标签**（"auto" / "lama"），
而实际用的引擎由 `core.inpainter` 决定 —— auto 默认走 OpenCV（阶段 55 的实测取舍），
于是「这次到底用哪个引擎」「是不是降级了」对用户完全不可见。
本模块把两处都改成真话：展示**实际引擎**，并在 auto 落到 OpenCV 时说明原因。
"""

from __future__ import annotations

from core.inpainter import resolve_effective_engine
from core.model_loader import is_model_available
from utils.i18n import t


def engine_display(requested: str) -> tuple[str, str]:
    """返回 (展示用引擎名, 补充说明)。

    参数说明：
        requested : 用户选择的引擎标签（auto / lama / opencv / 插件引擎名）。

    行为：
        · 实际引擎按 `core.inpainter.resolve_effective_engine` 解析（与修复路径同源）；
        · 仅当「用户选 auto 且实际落到 OpenCV」时追加说明：
          模型不可用 ⇒ 说明是"权重未就绪"；模型可用 ⇒ 说明是"auto 按配置默认 OpenCV，
          想用 LaMa 可显式选 lama"。
    """
    effective = resolve_effective_engine(requested)
    label = effective.upper()
    note = ""
    if (requested or "").lower() == "auto" and effective == "opencv":
        if is_model_available():
            note = t("single.msg.engine_note_opencv_default")
        else:
            note = t("single.msg.engine_note_no_model")
    return label, note
