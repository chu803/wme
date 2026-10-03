"""
utils.format_utils —— 展示层通用格式化助手。

阶段 48 抽取：字节数的人类可读格式化原先仅存在于 ui.tab_history._fmt_bytes
（阶段 47 引入，供「磁盘占用」面板使用）。阶段 48 的「磁盘清理（孤儿文件回收）」
面板同样需要它，若各自实现一份会导致格式不一致、且违反「utils 承载可复用
纯函数」的分层约定，因此下沉到本模块统一提供。

设计约定：
    · 纯函数、零第三方依赖（不导入 gradio / numpy / cv2）。
    · 采用 1024 进制（KiB 语义），单位标签沿用业界常见的 KB/MB/GB/TB。
    · 输入容错：None / 负 / 非数字一律按 0 处理，绝不抛异常打断 UI 渲染。

分层位置：utils（可被 ui / services / core 依赖，自身不反向依赖上层）。
"""

from __future__ import annotations

# 单位阶梯：索引即 1024 的幂次。
_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_bytes(n) -> str:
    """
    把字节数格式化为人类可读字符串（1024 进制）。

    参数说明：
        n : 字节数（int / float / str 均可；无法解析时按 0 处理）。

    返回值：
        形如 "512 B" / "1.50 KB" / "2.22 MB" / "1.00 GB" 的字符串。
        小于 1024 B 时输出整数（不显示小数），更贴合直觉。

    示例：
        >>> format_bytes(0)
        '0 B'
        >>> format_bytes(1536)
        '1.50 KB'
        >>> format_bytes(5 * 1024 ** 3)
        '5.00 GB'
    """
    try:
        size = float(int(n or 0))
    except (TypeError, ValueError):
        size = 0.0
    if size < 0:
        size = 0.0

    i = 0
    while size >= 1024.0 and i < len(_UNITS) - 1:
        size /= 1024.0
        i += 1
    # 字节档位不显示小数；其余档位保留两位小数。
    return f"{int(size)} B" if i == 0 else f"{size:.2f} {_UNITS[i]}"


# ==============================================================================
# 模块自检：python -m utils.format_utils
# ==============================================================================
if __name__ == "__main__":
    # 基础档位。
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1536) == "1.50 KB"
    assert format_bytes(1024 ** 2) == "1.00 MB"
    assert format_bytes(2.22 * 1024 ** 2).endswith("MB")
    assert format_bytes(1024 ** 3) == "1.00 GB"
    assert format_bytes(5 * 1024 ** 3) == "5.00 GB"
    assert format_bytes(1024 ** 4) == "1.00 TB"
    # 超过 TB 不再升级单位（保持 TB 档位，不产生未知单位）。
    assert "TB" in format_bytes(1024 ** 6)
    # 容错：None / 负数 / 非法字符串一律按 0 处理，不抛异常。
    assert format_bytes(None) == "0 B"
    assert format_bytes(-100) == "0 B"
    assert format_bytes("abc") == "0 B"
    # 字符串数字可解析。
    assert format_bytes("2048") == "2.00 KB"
    print("utils/format_utils.py 自检通过。")
