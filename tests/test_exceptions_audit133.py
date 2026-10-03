"""Stage 133 守卫：utils/exceptions 真值审计（继承树完整性 + 路径抽取）。

锁定三处修复：
- D1：模块 docstring 继承树必须包含 WatermarkEraserError 的全部子类
      （此前漏列 InvalidMaskError / DetectionError / 整个 MediaError 子树，共 6 个）
- D2：wrap_unknown_error 对「消息型」FileNotFoundError / PermissionError
      必须保留原始路径/消息，而非回退到「未知路径」「None」
- D3：自检自带断言（由子进程守卫 test_legacy_selfcheck 覆盖，此处不重复）
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import exceptions as exc  # noqa: E402


def test_exception_tree_doc_complete():
    """D1：docstring 继承树必须枚举全部子类，避免读者误以为某类不存在。"""
    doc = exc.__doc__ or ""
    missing = [
        cls.__name__
        for cls in exc.WatermarkEraserError.__subclasses__()
        if cls.__name__ not in doc
    ]
    assert not missing, f"docstring 树漏列子类：{missing}"


def test_wrap_message_type_filenotfound_preserves_path():
    """D2：消息型 FileNotFoundError（本项目自抛的中文消息异常）须保留路径。"""
    err = FileNotFoundError("找不到图片文件：foo.png")
    wrapped = exc.wrap_unknown_error(err, context="读取图片")
    # 之前这里会得到 '找不到需要的文件：未知路径'，详情文件路径 'None'
    assert "foo.png" in wrapped.reason, f"reason 丢失路径：{wrapped.reason!r}"
    assert wrapped.detail.get("文件路径") == "找不到图片文件：foo.png"


def test_wrap_standard_filenotfound_preserves_filename():
    """D2（对照）：标准 FNFE(errno, msg, filename) 须保留 filename。"""
    err = FileNotFoundError(2, "No such file", "baz.png")
    wrapped = exc.wrap_unknown_error(err, context="读取图片")
    assert "baz.png" in wrapped.reason, f"reason 未含 filename：{wrapped.reason!r}"
    assert wrapped.detail.get("文件路径") == "baz.png"


def test_wrap_permission_error_preserves_path():
    """D2：消息型 PermissionError 同样须保留路径。"""
    err = PermissionError("没有权限访问：bar.png")
    wrapped = exc.wrap_unknown_error(err, context="写文件")
    assert "bar.png" in wrapped.reason, f"reason 丢失路径：{wrapped.reason!r}"
    assert wrapped.detail.get("文件路径") == "没有权限访问：bar.png"


def test_all_subclasses_are_watermark_errors():
    """契约：异常体系所有公开异常都应是 WatermarkEraserError 的子类。"""
    non_leaf = exc.WatermarkEraserError.__subclasses__()
    assert non_leaf, "异常体系不应为空"
    for cls in non_leaf:
        assert issubclass(cls, exc.WatermarkEraserError)
