# -*- coding: utf-8 -*-
"""Stage 134 守卫：锁定 export_service 的真缺陷修复。

覆盖：
- D1：export_paths_as_zip 必须真正去重（同名 basename 不被静默覆盖）
- D2：make_contact_sheet([]) 空输入返回完整 bg 色块（非单通道平铺）
- D3：make_compare_array 零高度输入不再 ZeroDivisionError
- D4：_array_to_data_uri 大写扩展名 .JPG 的 mime 应为 image/jpeg
- D5：export_history_as_zip 的桩契约 list_records(kind, query)（自检用例4）
"""
import os
import sys
import zipfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import export_service as es  # noqa: E402


def _write_png(path, color, size=(40, 40)):
    import cv2
    cv2.imwrite(path, np.full((size[1], size[0], 3), color, np.uint8))


def test_export_paths_as_zip_dedup_same_basename(tmp_path):
    """D1：两同名 basename 应去重为 photo.png / photo_1.png，而非写重名丢首条。"""
    p1 = str(tmp_path / "photo.png")
    p2 = str(tmp_path / "photo.png")
    _write_png(p1, 200)
    _write_png(p2, 80)
    zp = es.export_paths_as_zip([p1, p2], names=None)
    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
    assert len(names) == 2, f"去重失败，条目数={len(names)}"
    assert names == ["photo.png", "photo_1.png"], f"去重命名不符预期：{names}"
    # 首条内容应可独立读回（不再因重名而不可达）。
    with zipfile.ZipFile(zp) as zf:
        first = zf.read("photo.png")
    assert len(first) > 0


def test_make_contact_sheet_empty_uses_full_bg():
    """D2：空输入应返回完整 bg 色块，而非把三通道都填成 bg[0] 的灰块。"""
    bg = (30, 41, 59)
    out = es.make_contact_sheet([], cols=4, thumb_size=160, bg=bg)
    assert out.shape == (160, 160, 3)
    # 颜色应为 bg 本身（三通道一致）。
    assert out[0, 0].tolist() == list(bg), f"空联系表颜色错误：{out[0,0].tolist()}"


def test_make_compare_array_zero_height_no_crash():
    """D3：零高度左图不应触发 ZeroDivisionError。"""
    a = np.zeros((0, 50, 3), np.uint8)
    b = np.full((40, 50, 3), 120, np.uint8)
    c = es.make_compare_array(a, b)
    assert isinstance(c, np.ndarray) and c.ndim == 3


def test_array_to_data_uri_uppercase_ext_mime():
    """D4：大写扩展名 .JPG 应标成 image/jpeg（字节确为 JPEG）。"""
    uri = es._array_to_data_uri(np.full((10, 10, 3), 128, np.uint8), ext=".JPG")
    assert uri.startswith("data:image/jpeg;base64,"), f"mime 错误：{uri[:30]}"


def test_history_export_stub_contract(tmp_path):
    """D5：export_history_as_zip 调用 list_records(kind, query) 的桩契约。"""

    class _Rec:
        def __init__(self, name, path, rid):
            self.status = "success"
            self.result_path = path
            self.original_name = name
            self.id = rid

    class _StubCache:
        def list_records(self, kind=None, query=None):
            return [_Rec("测试图", str(tmp_path / "r.png"), "abc123")]

    _write_png(str(tmp_path / "r.png"), 100)
    # 不应因桩不接受关键字参数而 TypeError（修复前用例4 崩溃）。
    zp = es.export_history_as_zip(_StubCache(), fmt="WEBP")
    assert os.path.exists(zp)
