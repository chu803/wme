"""阶段 190 · 单图结果区「本次耗时」显示守卫（优化清单 D2）。

为什么加（`docs/185` D2）
-----------------------
用户看不到「为什么这次慢」：单图状态文案只给了**引擎**与保存路径，没有耗时。
按「纯本地、不涉遥测」的定位，在结果区显示本次耗时即可。

本守卫锁三件事：
  ① **文案存在且双语同形**：`single.msg.elapsed` 在 zh-CN / en-US 都有，占位符集合一致；
  ② **口径可机检**：耗时的「起止点」必须与文案措辞一致 —— 本文件用**顺序断言**钉死
     「测量起点 → `pp_postprocess` → 收口 → 写入状态」的先后关系；
  ③ **en-US 不得混入 CJK/全角标点**（阶段 162/194 的整类教训）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.i18n import t_in, validate_i18n_parity  # noqa: E402

_KEY = "single.msg.elapsed"
_PLACEHOLDER = re.compile(r"\{(\w+)(?::[^}]*)?\}")
#: CJK 统一表意文字 + 中日韩标点 + 全角形式（与阶段 162/194 的守卫同族）
_CJK_OR_FULLWIDTH = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def _source(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ① 文案存在 + 双语同形
# ---------------------------------------------------------------------------

def test_key_resolves_in_both_languages():
    zh = t_in("zh-CN", _KEY, seconds=1.234)
    en = t_in("en-US", _KEY, seconds=1.234)
    assert zh != _KEY, "zh-CN 缺该键（t_in 会回退为键名）"
    assert en != _KEY, "en-US 缺该键（t_in 会回退为键名）"
    # 占位符必须真的被格式化（{seconds:.2f} 保留两位）
    assert "1.23" in zh, zh
    assert "1.23" in en, en


def test_both_languages_have_identical_placeholders():
    # ⚠ 取**原始模板**必须不传 kwargs：`t_in` 一旦收到 kwargs 就会就地 format，
    #   返回值里不再有占位符（本测试首版就踩了这个坑）。
    zh = t_in("zh-CN", _KEY)
    en = t_in("en-US", _KEY)
    assert set(_PLACEHOLDER.findall(zh)) == set(_PLACEHOLDER.findall(en)) == {"seconds"}


def test_i18n_parity_guard_is_clean():
    """仓库自带的「双语键对称」守卫必须仍然为 0 差异（新增键不得单边）。"""
    assert validate_i18n_parity() == []


def test_english_text_has_no_cjk_or_fullwidth_punctuation():
    en = t_in("en-US", _KEY, seconds=1.0)
    bad = _CJK_OR_FULLWIDTH.findall(en)
    assert not bad, f"en-US 文案混入 CJK/全角标点：{bad}"


# ---------------------------------------------------------------------------
# ② 口径可机检（顺序断言）
# ---------------------------------------------------------------------------

def test_elapsed_window_covers_repair_and_postprocess_only():
    """耗时的起止点必须与 `single.msg.elapsed` 的措辞（修复 + 增强）一致。

    断言顺序（**只在 `process_single` 函数体内**查找，避免命中别处的文档字符串）：
        `_t0 = _time.perf_counter()`  <  `inpaint(`  <  `pp_postprocess(`
          <  `elapsed = _time.perf_counter() - _t0`  <  `_compose_status(...)`
    任何一环被挪动（例如把起点提到涂抹解析之前、或把收口挪到落盘之后），
    文案与实际口径就脱节 —— 本断言即为此拦住。
    """
    src = _source("ui/tab_single.py")
    start = src.find("def process_single(")
    assert start >= 0, "找不到 process_single"
    body = src[start:]
    order = [
        "_t0 = _time.perf_counter()",
        "result = inpaint(",
        "result = pp_postprocess(",
        "elapsed = _time.perf_counter() - _t0",
        "status = _compose_status(engine, out_path, elapsed",
    ]
    pos = []
    for token in order:
        i = body.find(token)
        assert i >= 0, f"process_single 体内找不到：{token}"
        pos.append(i)
    assert pos == sorted(pos), f"耗时口径的代码顺序被改动：{list(zip(order, pos))}"


def test_status_text_concatenates_elapsed_segment():
    """状态文案由 `_compose_status` 统一拼装（三段：done + elapsed + upscaled_note）。"""
    src = _source("ui/tab_single.py")
    assert "def _compose_status(" in src, "缺少状态文案拼装函数"
    i = src.find("status = _compose_status(")
    assert i >= 0, "process_single 未使用 _compose_status（三段拼接可能被绕过）"


# ---------------------------------------------------------------------------
# ③ 运行时验证（真调用拼装函数，而非只读源码）
# ---------------------------------------------------------------------------

def test_compose_status_runtime_contains_engine_and_seconds():
    """真实调用 `ui.tab_single._compose_status`，断言三段都在、数字按 {:.2f} 格式化。"""
    from utils.i18n import get_language
    from ui.tab_single import _compose_status

    msg = _compose_status("lama", "/tmp/out.png", 1.2345, "  [upscaled 2x]")
    assert "LAMA" in msg, "引擎名（大写）未出现在状态文案"
    assert "/tmp/out.png" in msg
    assert "1.23" in msg, f"耗时未按两位小数格式化：{msg!r}"
    assert msg.endswith("  [upscaled 2x]"), "超分提示必须拼在末尾"
    # 与当前语言的 elapsed 键逐字一致（证明走的是 i18n 而非硬编码）
    assert t_in(get_language(), _KEY, seconds=1.2345) in msg


def test_compose_status_without_upscale_note():
    from ui.tab_single import _compose_status

    msg = _compose_status("opencv", "/tmp/a.png", 0.0)
    assert "0.00" in msg
    assert "OPENCV" in msg
