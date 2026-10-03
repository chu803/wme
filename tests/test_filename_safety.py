# -*- coding: utf-8 -*-
"""文件命名与路径安全回归测试（阶段 78）。

背景：命名链路（`sanitize_filename` / `build_output_filename` / `make_unique_path`）
是**每次上传与导出都会经过**的公共路径，但阶段 78 审计发现它此前**零直接测试覆盖**，
并因此长期潜伏七项缺陷：

  N1 `build_output_filename` 强制扩展名时盲目 `rsplit(".", 1)[0]`，而**默认模板
     `{stem}_cleaned_{time}` 不含扩展名** → 含点主名（`my.photo`）的信息被静默丢弃
     （输出 `my.png`），`{time}` 一并消失还会让多张图坍缩同名。**默认配置即触发**。
  N2 `sanitize_filename` 的**扩展名部分完全未经清洗** → 路径分隔符可从「扩展名」位置漏过。
  N3 Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）未处理。
  N4 控制字符（\\x00-\\x1f、\\x7f）未剔除（`\\x00` 会让 open() 抛 ValueError）。
  N5 尾部空格/点只对主名生效，扩展名位置的尾随空白漏过。
  N6 `normalize_unicode` 是死代码（唯一「调用」是自检断言），全角转半角从未生效。
  N7 `max_length` 只约束主名，不约束整个文件名。

另有阶段 78 顺带修复的**测试隔离缺陷**：部分用例设置 i18n 语言后不还原，
污染后续用例（已由 `conftest._isolate_i18n_language` 根治）。
"""

from __future__ import annotations

import unicodedata

import pytest

import config
from utils.path_helper import (
    build_output_filename,
    make_unique_path,
    normalize_unicode,
    sanitize_filename,
)


# ==============================================================================
# N2 · 扩展名位置的清洗盲区（路径分隔符 / 非法字符）
# ==============================================================================
@pytest.mark.parametrize("raw", [
    "a.//evil",                     # 扩展名位置带路径分隔符
    "x./../../etc/passwd",          # 试图向上穿越
    "shot.\\..\\..\\win.ini",       # Windows 分隔符
    'q."onmouseover=',              # 引号
    "clip.*.mp4",                   # 通配符
    "a.b|c.txt",                    # 竖线
])
def test_extension_position_is_sanitized(raw):
    """清洗必须覆盖「扩展名」位置——路径分隔符与非法字符一律不得残留。"""
    out = sanitize_filename(raw)
    assert "/" not in out and "\\" not in out, f"残留路径分隔符：{out!r}"
    assert not (set(out) & set('<>:"|?*')), f"残留非法字符：{out!r}"


def test_path_separator_cannot_escape_target_dir(tmp_path):
    """（纵深防御）即便名字带分隔符，拼接后也不会逃出目标目录。"""
    crafted = sanitize_filename("x./../../escaped.txt")
    joined = (tmp_path / crafted).resolve()
    assert joined.parent == tmp_path.resolve(), f"发生了目录穿越：{joined}"


def test_make_unique_path_strips_directory_components(tmp_path):
    """make_unique_path 自身也剥离目录成分（写出路径的最后一道防线）。"""
    assert make_unique_path(tmp_path, "../escape.txt").name == "escape.txt"
    assert make_unique_path(tmp_path, "a/b.png").name == "b.png"
    assert make_unique_path(tmp_path, "..").name == "output"


# ==============================================================================
# N3 · Windows 保留设备名
# ==============================================================================
@pytest.mark.parametrize("raw,expect_stem", [
    ("CON.png", "_CON"),
    ("con.png", "_con"),
    ("PrN.png", "_PrN"),
    ("AUX.jpg", "_AUX"),
    ("NUL.png", "_NUL"),
    ("COM1.png", "_COM1"),
    ("com9.png", "_com9"),
    ("LPT1.png", "_LPT1"),
    ("lpt9.txt", "_lpt9"),
])
def test_reserved_device_names_are_prefixed(raw, expect_stem):
    """保留设备名必须加前缀，否则 Windows 上创建文件会失败或写入设备。"""
    out = sanitize_filename(raw)
    assert out.startswith("_"), f"保留名未加前缀：{out!r}"
    assert out.rsplit(".", 1)[0] == expect_stem


@pytest.mark.parametrize("raw", ["CONSOLE.png", "COM10.png", "LPT10.png", "nulify.png", "consolidate.txt"])
def test_similar_but_not_reserved_names_untouched(raw):
    """只有**恰好等于**保留名的基名才加前缀；形近名不得被误伤。"""
    assert sanitize_filename(raw) == raw


# ==============================================================================
# N4 · 控制字符
# ==============================================================================
@pytest.mark.parametrize("raw", [
    "a\x00b.png",       # NUL：open() 会直接抛 ValueError
    "a\x1fb.jpg",
    "line\nbreak.png",  # 换行：破坏日志与命令行
    "tab\there.png",
    "del\x7f.png",
])
def test_control_characters_removed(raw):
    """控制字符一律不得出现在结果中。"""
    out = sanitize_filename(raw)
    leftovers = [hex(ord(c)) for c in out if ord(c) < 32 or ord(c) == 127]
    assert leftovers == [], f"残留控制字符：{leftovers} → {out!r}"


# ==============================================================================
# N5 · 尾部空格与点（含扩展名位置）
# ==============================================================================
@pytest.mark.parametrize("raw", ["a.png ", "shot.jpg  ", "trail. ", "x.png... ", "b. .png "])
def test_no_trailing_space_or_dot(raw):
    """结果不得以空格或点结尾（Windows 不允许）。"""
    out = sanitize_filename(raw)
    assert not out.endswith((" ", ".")), f"尾部非法：{out!r}"


# ==============================================================================
# N6 · 全角归一化（normalize_unicode 曾是死代码）
# ==============================================================================
@pytest.mark.parametrize("raw,expected", [
    ("ａｂｃ.png", "abc.png"),          # 全角字母
    ("１２３.jpg", "123.jpg"),          # 全角数字
    ("图片．ｐｎｇ", "图片.png"),         # 全角点号 + 全角扩展名
])
def test_fullwidth_normalized(raw, expected):
    """全角字符应被 NFKC 归一化为半角（文档承诺，此前从未生效）。"""
    assert sanitize_filename(raw) == expected


def test_normalize_unicode_is_wired_not_dead_code():
    """normalize_unicode 必须真的被清洗管线调用，而不只是存在。"""
    assert normalize_unicode("ＡＢＣ") == "ABC"
    # 反证：关掉归一化后全角应原样保留，说明该开关确实接在管线上。
    assert sanitize_filename("ａｂｃ.png", normalize=False) != "abc.png"


# ==============================================================================
# 回归保护 · 正常文件名必须逐字不变
# ==============================================================================
@pytest.mark.parametrize("raw", [
    "photo.png",
    "我的照片.png",
    "a.b.c.jpg",            # 多点主名
    "IMG_2024.08.13.png",
    "screenshot (1).webp",
    "报告-终稿_v2.PNG",
    "file_name-1.2.jpeg",
])
def test_normal_names_unchanged(raw):
    """无非法字符、非保留名、不超长的正常文件名必须**逐字不变**（零回归）。"""
    assert sanitize_filename(raw) == raw


# ==============================================================================
# N7 · 长度控制
# ==============================================================================
def test_total_length_bounded_including_suffix():
    """max_length 约束**整个文件名**（主名 + 扩展名），而非只约束主名。"""
    assert len(sanitize_filename("a" * 300 + ".png", max_length=64)) <= 64
    assert len(sanitize_filename("a" * 300 + ".png")) <= 200


def test_overlong_suffix_is_truncated():
    """超长扩展名单独限长，不得挤占（或突破）整体额度。"""
    out = sanitize_filename("ok." + "x" * 120)
    assert len(out) <= 200
    assert out.startswith("ok.")


def test_empty_input_uses_fallback():
    """空输入与「清洗后为空」都应回退到兜底名。"""
    assert sanitize_filename("") == "未命名"
    # 全是点号：点号被 strip 掉后为空 → 兜底。
    assert sanitize_filename("...") == "未命名"
    assert sanitize_filename("...", fallback="img") == "img"
    # 全是**非法字符**：按既定语义「非法字符 → 下划线」，结果形如 "___"
    # （非空，故不触发兜底；与修复前行为一致，属刻意保留的向后兼容语义）。
    assert sanitize_filename("???") == "___"
    assert sanitize_filename("///") == "___"


# ==============================================================================
# N1 · build_output_filename 不得丢失主名信息（默认配置即触发）
# ==============================================================================
@pytest.mark.parametrize("stem,expect_part", [
    ("my.photo", "my.photo_cleaned_"),
    ("screenshot.2024.08", "screenshot.2024.08_cleaned_"),
    ("v1.2.3", "v1.2.3_cleaned_"),
])
def test_dotted_stem_keeps_full_information(monkeypatch, stem, expect_part):
    """含点主名 + **默认模板**（不含扩展名）时，主名与 `_cleaned_{time}` 都不得丢失。"""
    monkeypatch.setattr(config, "OUTPUT_NAME_TEMPLATE", "{stem}_cleaned_{time}")
    out = build_output_filename(stem, "png", time_str="181955")
    assert out == f"{stem}_cleaned_181955.png", f"信息丢失：{out!r}"


def test_dotted_stem_with_chinese_suffix_template(monkeypatch):
    """主名含点 + 模板自带中文后缀：后缀也必须保留（修复前被整段砍掉）。"""
    monkeypatch.setattr(config, "OUTPUT_NAME_TEMPLATE", "{stem}_去水印")
    out = build_output_filename("a.b", "webp")
    assert out == "a.b_去水印.webp", f"后缀丢失：{out!r}"


def test_known_extension_still_replaced(monkeypatch):
    """模板写死已知扩展名时，仍应被目标格式覆盖（保持原有意图）。"""
    monkeypatch.setattr(config, "OUTPUT_NAME_TEMPLATE", "{stem}.jpg")
    assert build_output_filename("shot", "png") == "shot.png"
    monkeypatch.setattr(config, "OUTPUT_NAME_TEMPLATE", "out_{stem}.png")
    assert build_output_filename("shot", "webp") == "out_shot.webp"


def test_unknown_tail_is_appended_not_truncated(monkeypatch):
    """末尾段不是已知扩展名时**安全追加**，绝不截断。"""
    monkeypatch.setattr(config, "OUTPUT_NAME_TEMPLATE", "{stem}_v1")
    assert build_output_filename("a", "png") == "a_v1.png"


def test_extension_case_normalized(monkeypatch):
    """扩展名大写应归一化为小写。"""
    monkeypatch.setattr(config, "OUTPUT_NAME_TEMPLATE", "{stem}")
    assert build_output_filename("photo", "WEBP").endswith(".webp")


def test_all_placeholders_replaced(monkeypatch):
    """全部占位符（stem/date/time/index/engine）都应被替换。"""
    monkeypatch.setattr(
        config, "OUTPUT_NAME_TEMPLATE",
        "{stem}_{date}_{time}_i{index}_{engine}",
    )
    out = build_output_filename(
        "pic", "png", index=7, engine="lama", date_str="20260913", time_str="120000",
    )
    assert out == "pic_20260913_120000_i7_lama.png", f"占位符替换异常：{out!r}"
    for token in ("{stem}", "{date}", "{time}", "{index}", "{engine}"):
        assert token not in out


def test_empty_template_falls_back(monkeypatch):
    """模板渲染为空时必须兜底，不得产出 `.png` 这类畸形名。"""
    monkeypatch.setattr(config, "OUTPUT_NAME_TEMPLATE", "___")
    out = build_output_filename("", "png")
    assert out not in ("", ".png", "png")
    assert out.endswith(".png")


def test_consecutive_underscores_collapsed(monkeypatch):
    """连续下划线折叠为单个（修复前 str.replace 只替换一次）。"""
    monkeypatch.setattr(config, "OUTPUT_NAME_TEMPLATE", "{stem}___x")
    out = build_output_filename("a", "png")
    assert "__" not in out, f"连续下划线未折叠：{out!r}"


def test_distinct_stems_do_not_collapse(monkeypatch):
    """N1 后果验证：不同原图不得因截断而坍缩成同一文件名。"""
    monkeypatch.setattr(config, "OUTPUT_NAME_TEMPLATE", "{stem}_cleaned_{time}")
    a = build_output_filename("a.b", "png", time_str="111111")
    b = build_output_filename("a.c", "png", time_str="111111")
    assert a != b, f"不同原图坍缩同名：{a!r} == {b!r}"


# ==============================================================================
# make_unique_path · 序号与冲突处理
# ==============================================================================
def test_unique_path_adds_sequence(tmp_path):
    """已存在时依次追加 (1)、(2)。"""
    (tmp_path / "r.png").write_bytes(b"x")
    assert make_unique_path(tmp_path, "r.png").name == "r(1).png"
    (tmp_path / "r(1).png").write_bytes(b"x")
    assert make_unique_path(tmp_path, "r.png").name == "r(2).png"


def test_unique_path_keeps_multi_dot_stem(tmp_path):
    """`photo.tar.gz` 冲突时序号插在最后一段扩展名之前（.tar 保留在主名里）。"""
    (tmp_path / "photo.tar.gz").write_bytes(b"x")
    assert make_unique_path(tmp_path, "photo.tar.gz").name == "photo.tar(1).gz"


def test_unique_path_free_name_untouched(tmp_path):
    """无冲突时原样返回。"""
    assert make_unique_path(tmp_path, "brand_new.png").name == "brand_new.png"


# ==============================================================================
# 端到端 · 模拟「上传 → 命名 → 落盘」链路
# ==============================================================================
def test_end_to_end_naming_pipeline(tmp_path, monkeypatch):
    """脏文件名走完整链路后：安全、可落盘、不覆盖、不丢信息。"""
    monkeypatch.setattr(config, "OUTPUT_NAME_TEMPLATE", "{stem}_cleaned_{time}")

    raw = "我的 照片.2024/../v1?.PNG"          # 含空格、点、路径分隔符、问号
    stem = sanitize_filename(raw.rsplit(".", 1)[0], 120)
    fname = build_output_filename(stem, "png", time_str="181955")
    target = make_unique_path(tmp_path, fname)

    # 名字本身合法且信息保留。
    assert "/" not in target.name and "\\" not in target.name
    assert "2024" in target.name and "181955" in target.name
    # 真能落盘。
    target.write_bytes(b"data")
    assert target.exists()
    # 第二次导出不覆盖第一次。
    second = make_unique_path(tmp_path, fname)
    assert second != target and "(1)" in second.name
