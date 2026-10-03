# -*- coding: utf-8 -*-
"""阶段 31：增强对比视图（拼接 / 拖动滑块 / 差异热图）单元测试。

覆盖：
- services.export_service 的三个新增构建器：_array_to_data_uri / make_diff_heatmap /
  make_before_after_html（纯 numpy + cv2，零 gradio 依赖）。
- ui.tab_auto / ui.tab_single 的 on_enhance(mode) 路由（拼接→data URI <img>，
  slider→自包含 HTML 控件，heatmap→差异热图，缺结果→提示）。
"""

import numpy as np

from services import export_service as _es
from utils import i18n


# --------------------------------------------------------------------------- #
# 数据构造辅助
# --------------------------------------------------------------------------- #
def _black(h=64, w=64):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _with_box(h=64, w=64):
    """在左上角放一个明显更亮的方块，制造可检测的差异。"""
    arr = _black(h, w)
    arr[4:28, 4:28] = 240
    return arr


# --------------------------------------------------------------------------- #
# export_service 构建器
# --------------------------------------------------------------------------- #
def test_array_to_data_uri_is_jpeg_data_uri():
    uri = _es._array_to_data_uri(_with_box())
    assert uri.startswith("data:image/jpeg;base64,")
    # base64 主体非空且无空白。
    body = uri.split(",", 1)[1]
    assert len(body) > 50 and " " not in body


def test_array_to_data_uri_strips_alpha():
    rgba = np.zeros((8, 8, 4), dtype=np.uint8)
    rgba[..., 3] = 255
    uri = _es._array_to_data_uri(rgba)
    assert uri.startswith("data:image/jpeg;base64,")


def test_diff_heatmap_identical_is_black():
    a = _with_box()
    b = a.copy()
    hm = _es.make_diff_heatmap(a, b)
    assert hm.shape == (64, 64, 3)
    assert hm.sum() == 0  # 完全相同 → 全黑


def test_diff_heatmap_detects_changed_region():
    a = _black()
    b = _with_box()
    hm = _es.make_diff_heatmap(a, b)
    assert hm.shape == (64, 64, 3)
    # 改动区域（左上角）应被点亮，整图不应全黑。
    assert hm.sum() > 0
    # 差异热图应在改动区中心附近有非零像素（取左上角局部均值验证）。
    assert hm[4:28, 4:28].sum() > 0


def test_diff_heatmap_resizes_mismatched_dims():
    a = _black(64, 64)
    b = _with_box(32, 32)  # 尺寸不一致，应被 resize 到 64×64。
    hm = _es.make_diff_heatmap(a, b)
    assert hm.shape == (64, 64, 3)
    assert hm.sum() > 0


def test_before_after_html_contains_labels_and_no_script():
    html = _es.make_before_after_html(_black(), _with_box(),
                                       before_label="原图", after_label="结果")
    assert "原图" in html and "结果" in html
    assert "ba-wrap" in html and "ba-before" in html and "ba-divider" in html
    assert "<script" not in html and "<script " not in html  # Gradio 兼容：无 <script>
    assert "oninput=" in html  # 拖动交互依赖内联 oninput。


def test_before_after_html_handles_encode_failure(monkeypatch):
    """编码异常时返回错误段落而非抛异常（gr.HTML 安全）。"""

    def _boom(arr):
        raise RuntimeError("encode fail")

    monkeypatch.setattr(_es, "_array_to_data_uri", _boom)
    html = _es.make_before_after_html(_black(), _with_box())
    assert html.startswith("<p") and "encode fail" in html


# --------------------------------------------------------------------------- #
# UI 层 on_enhance 路由（mock-free：仅依赖 _es 与 i18n）
# --------------------------------------------------------------------------- #
def test_auto_on_enhance_no_result():
    from ui import tab_auto as ta
    out = ta.on_enhance(_black(), None, "concat")
    assert out == i18n.t("compare.no_result")


def test_auto_on_enhance_concat():
    from ui import tab_auto as ta
    out = ta.on_enhance(_black(), _with_box(), "concat")
    assert "<img" in out and "data:image" in out


def test_auto_on_enhance_slider():
    from ui import tab_auto as ta
    out = ta.on_enhance(_black(), _with_box(), "slider")
    assert "ba-wrap" in out and "ba-before" in out and "<script" not in out


def test_auto_on_enhance_heatmap():
    from ui import tab_auto as ta
    out = ta.on_enhance(_black(), _with_box(), "heatmap")
    assert "<img" in out and i18n.t("compare.heatmap_label") in out


def test_single_on_enhance_concat_and_slider():
    from ui import tab_single as ts
    out_c = ts.on_enhance(_black(), _with_box(), "concat")
    out_s = ts.on_enhance(_black(), _with_box(), "slider")
    assert "<img" in out_c and "data:image" in out_c
    assert "ba-wrap" in out_s and "<script" not in out_s


def test_single_on_enhance_no_result():
    from ui import tab_single as ts
    out = ts.on_enhance(None, _with_box(), "heatmap")
    assert out == i18n.t("compare.no_result")
