# -*- coding: utf-8 -*-
"""阶段 386：把实测出来的「可操作杠杆」放到用户够得着的地方。

阶段 385 量到：角标 LOGO 擦不净时，把「掩码膨胀」调到 12~24 能把完全擦净率
从 60% 提到 75%。但默认膨胀**不能动**（按类标定，动了会踩网格类的红线），
所以这条信息必须**出现在那个滑块的说明里**，而不是只躺在文档里。

守卫保证这段指引不被悄悄删掉或改写成别的数字。
"""

from __future__ import annotations

from utils.i18n import t_in


def test_dilate_hint_carries_the_measured_guidance():
    zh = t_in("zh-CN", "params.dilate_info")
    en = t_in("en-US", "params.dilate_info")
    assert "24" in zh and "36" in zh, "中文指引里必须写清可用的取值"
    assert "24" in en and "36" in en, "英文指引里必须写清可用的取值"
    assert "84%" in zh and "84%" in en, "指引要带上实测数字（否则用户无从判断值不值得调）"
    assert "51%" in zh and "51%" in en


def test_dilate_hint_still_explains_the_original_purpose():
    zh = t_in("zh-CN", "params.dilate_info")
    assert "羽化" in zh, "原有的用途说明不能被替换掉"
