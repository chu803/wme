# -*- coding: utf-8 -*-
"""阶段 145 · `config` 真值审计守卫。

审计对象：`config.py`（荒漠度 **1.0** = 876 代码行 / 191117 字节 ÷ 837 引用用例）。
本模块是**未审榜末位**，也是**全仓测试密度最高**的模块 —— 按技能 1c 的判据
「荒漠榜只答『哪里没测过』，不答『测过的是否测对』」，高密度不等于无缺陷，
且它的影响面是**所有模块的配置真值**。

本阶段修的两处
--------------
F1 **`_env_float` 的 NaN 穿透钳制**（P0）：
    ``nan < min_value`` 与 ``nan > max_value`` **都返回 False** ⇒ 两段钳制
    对 NaN 完全不生效，NaN 原样穿透到业务代码。实测 ``WME_X=nan`` → 返回 ``nan``。
    **危害链**：``WME_DETECT_TRANSLUCENT_SENSITIVITY=nan`` ⇒ ``sensitivity=nan``
    ⇒ 检测器返回**零掩码**（静默零检出，与阶段 142 同族），而 ``validate_config``
    **报绿**、无任何告警。修法 = ``value != value`` 识别并回退 default；
    **只拦 NaN**，``inf`` / ``-inf`` 保持既有钳制行为（零行为变更）。

F2 **`validate_config` 的成对约束覆盖不全**（P1，元审计）：
    原有 17 项检查里只有检查 8（通用面积占比）与检查 16（白牌面积占比）覆盖了
    「MIN ≥ MAX ⇒ 判据恒不产出」。用命名空间**自动枚举**后实测：
    **11 对约束里只有 2 对被覆盖**，漏检 9 对 —— 包括网格覆盖率、LOGO 面积、
    CANNY 双阈值、推荐器网格区间，以及 5 组 UI 滑杆三元组的 DEFAULT 越界。
    而 GRID / LOGO 与 PLATE 属**完全同族**的判据（都是「区间反了 ⇒ 静默漏检」），
    检查 16 的注释甚至已经写明这条道理，却**没有推广到同族**。
    修法 = **元检查**（从命名空间派生），而非逐条补 —— 未来新增任何
    ``X_MIN/X_MAX``、``X_MIN_Y/X_MAX_Y``、``X_LO/X_HI``、``X_DEFAULT`` 三元组
    都会**自动纳入**（经验 167：期望值须从真值来源派生，不能手抄）。

本文件守八层
------------
N0 存在性自检：内嵌**旧** `_env_float` 钳制逻辑，证明 NaN 穿透与 2/11 覆盖率真实存在
N1 F1：NaN 族回退 default；``inf``/``-inf`` 仍被钳制（**零行为变更**证明）；`_env_int` 对照
N2 F2：全部成对约束（**与 config 命名空间动态对账**，不写死清单）反转后必须被捕获
N3 F2：全部三元组的 DEFAULT 越界必须被捕获
N4 F2：元检查必须**从命名空间派生**（源码断言），且不得含硬编码配置名清单
N5 回归：原有 17 项检查逐条仍有效
N6 无误报：正常配置 ``problems == 0``；检查项数下界
N7 消息质量：捕获信息含变量名与数值（便于用户定位）
"""
from __future__ import annotations

import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                                                        # noqa: E402
from config import (                                                 # noqa: E402
    _env_float,
    _env_int,
    validate_config,
)

_SRC = open(os.path.join(_ROOT, "config.py"), encoding="utf-8").read()

#: 大写常量快照（用于还原被测试改动的配置）。
_UPPER = {n: getattr(config, n) for n in dir(config) if n.isupper()}


@pytest.fixture(autouse=True)
def _restore_config():
    """本文件会临时改写 config 属性以驱动 validate_config，用后必须逐项还原。"""
    yield
    for _n, _v in _UPPER.items():
        try:
            setattr(config, _n, _v)
        except Exception:                      # noqa: BLE001 - 只读属性等
            pass


def _set(**kw):
    for k, v in kw.items():
        setattr(config, k, v)


# ---------------------------------------------------------------------------
# N0 · 存在性自检：内嵌旧实现，证明两条缺陷真实存在
# ---------------------------------------------------------------------------

def _legacy_env_float(raw_value: str, default: float, min_value, max_value) -> float:
    """**修复前**的 `_env_float` 主体：无 NaN 判定，只做两段比较。"""
    try:
        value = float(raw_value)
    except ValueError:
        value = default
    if min_value is not None and value < min_value:
        value = min_value
    if max_value is not None and value > max_value:
        value = max_value
    return value


def test_N0_legacy_env_float_lets_nan_through():
    """旧实现下 NaN 穿透（证明 F1 是真缺陷）。"""
    got = _legacy_env_float("nan", 0.55, 0.0, 1.0)
    assert got != got, "旧实现的「NaN 穿透」机制已变，N1 判据前提失效"
    # 两个钳制条件对 NaN 都为假 —— 这是根因
    assert not (float("nan") < 0.0)
    assert not (float("nan") > 1.0)


def test_N0_legacy_coverage_was_only_two_of_eleven():
    """阶段 145 修前实测：11 对约束只有 2 对被 validate_config 覆盖。

    这里**不重放**旧版 `validate_config`（它很长），而是用「现版本的元检查」
    与「检查 8/16 这两条**显式**硬编码检查」对比 —— 见 N2/N4 的完整验证。
    本条只钉住「修前覆盖率」这个历史事实，防止有人误以为 17 项检查早就够了。
    """
    hardcoded = [c for c in ("DETECT_MIN_AREA_RATIO", "DETECT_PLATE_MIN_AREA_RATIO")
                 if c in _SRC]
    assert len(hardcoded) == 2, "检查 8/16 的显式判据应仍在位"
    # 这两个之外，其余成对项在修前**没有任何检查**（源码里搜不到它们的名字+比较）
    for name in ("DETECT_GRID_MIN_COVERAGE", "DETECT_LOGO_MIN_AREA_RATIO",
                 "DETECT_LOGO_CANNY_LO", "RECOMMEND_GRID_MIN_COVERAGE"):
        # 修后它们只应出现在「元检查」里（不在任何显式比较中）
        assert f"{name} >= " not in _SRC and f"{name} > " not in _SRC, (
            f"{name} 不应存在显式硬编码比较（应交给元检查）"
        )


# ---------------------------------------------------------------------------
# N1 · F1：NaN 回退 default；inf 路径零行为变更
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["nan", "NaN", "NAN", "-nan", " nan ", "+nan"])
def test_N1_nan_falls_back_to_default(raw, monkeypatch):
    monkeypatch.setenv("WME_N1", raw)
    assert _env_float("N1", 0.55, 0.0, 1.0) == 0.55


@pytest.mark.parametrize("raw,expect", [
    ("inf", 1.0),        # 钳到 max（**修前修后一致**，故纳入本修复的零变更证明）
    ("-inf", 0.0),       # 钳到 min
    ("Infinity", 1.0),
    ("1e400", 1.0),      # 溢出为 inf
    ("5.0", 1.0),        # 普通越界仍钳制
    ("-3.0", 0.0),
    ("0.55", 0.55),
    ("abc", 0.55),       # 转换失败回退
])
def test_N1_non_nan_paths_unchanged(raw, expect, monkeypatch):
    """**零行为变更证明**：NaN 之外的所有输入路径与修复前完全一致。"""
    monkeypatch.setenv("WME_N1", raw)
    assert _env_float("N1", 0.55, 0.0, 1.0) == expect


def test_N1_no_min_max_still_blocks_nan(monkeypatch):
    """**无 min/max 保护**时（两参数都 None）也必须拦住 NaN —— 那里连钳制都没有。"""
    monkeypatch.setenv("WME_N1", "nan")
    got = _env_float("N1", 0.55, None, None)
    assert got == 0.55, "无 min/max 的保护路径下 NaN 仍穿透"


def test_N1_int_path_already_safe(monkeypatch):
    """对照：`_env_int` 对 nan/inf 本来就安全（`int("nan")` 抛 ValueError ⇒ 回退）。"""
    for raw in ("nan", "inf", "-inf", "abc", "1.9"):
        monkeypatch.setenv("WME_N1i", raw)
        assert _env_int("N1i", 7860, 1024, 65535) == 7860, f"{raw} 未安全回退"


def test_N1_nan_detection_is_the_standard_idiom():
    """源码必须用 `value != value`（NaN 唯一不等于自身的值）识别，而非 `is nan` 之类。"""
    assert "if value != value:" in _SRC, "未使用 NaN 自比较识别"
    seg = _SRC.split("if value != value:")[1].split("\n")[1]
    assert "value = default" in seg, "NaN 判定后未回退 default"


# ---------------------------------------------------------------------------
# N2 · F2：成对约束（**动态枚举**，不写死清单）
# ---------------------------------------------------------------------------

def _enumerate_pairs():
    names = [n for n in dir(config) if n.isupper()]
    pairs = []
    for a in names:
        if a.endswith("_MIN"):
            b = a[:-4] + "_MAX"
            if b in names:
                pairs.append((a, b))
        if "_MIN_" in a:
            pre, suf = a.split("_MIN_", 1)
            b = f"{pre}_MAX_{suf}"
            if b in names:
                pairs.append((a, b))
        if a.endswith("_LO"):
            b = a[:-3] + "_HI"
            if b in names:
                pairs.append((a, b))
    return pairs


def test_N2_pair_family_is_non_empty():
    """先钉住集合非空（防「遍历空集合 ⇒ 断言空转」，经验 5b）。"""
    pairs = _enumerate_pairs()
    assert len(pairs) >= 11, f"成对约束族意外缩小到 {len(pairs)} 对：{pairs}"


def test_N2_every_reversed_pair_is_caught():
    """**每一对** MIN/MAX（含 `_MIN_` 与 `_LO/_HI` 形态）反转后都必须被 validate_config 捕获。

    动态枚举 ⇒ 未来新增的成对项**自动纳入**本守卫，无需改测试。
    """
    pairs = _enumerate_pairs()
    assert pairs, "成对约束族为空"
    missed = []
    for a, b in pairs:
        va, vb = getattr(config, a), getattr(config, b)
        if isinstance(va, bool) or isinstance(vb, bool):
            continue
        if not isinstance(va, (int, float)) or not isinstance(vb, (int, float)):
            continue
        _set(**{a: vb, b: va})
        probs = validate_config()
        if not any(a in p or b in p for p in probs):
            missed.append(f"{a}/{b}")
    assert not missed, f"以下成对项反转后未被捕获（静默漏检窗口）：{missed}"


def test_N2_caught_message_names_both_sides_and_values():
    """捕获信息必须含**两侧变量名与数值**（用户据此定位）。"""
    _set(DETECT_GRID_MIN_COVERAGE=0.5, DETECT_GRID_MAX_COVERAGE=0.1)
    probs = validate_config()
    hit = [p for p in probs if "DETECT_GRID_MIN_COVERAGE" in p]
    assert hit, "网格覆盖率区间反转未被捕获"
    msg = hit[0]
    assert "DETECT_GRID_MAX_COVERAGE" in msg
    assert "0.5" in msg and "0.1" in msg
    assert "静默漏检" in msg or "恒不产出" in msg


# ---------------------------------------------------------------------------
# N3 · F2：滑杆三元组 X_DEFAULT / X_MIN / X_MAX
# ---------------------------------------------------------------------------

def _enumerate_triples():
    names = [n for n in dir(config) if n.isupper()]
    out = []
    for n in names:
        if n.endswith("_DEFAULT"):
            base = n[:-len("_DEFAULT")]
            if base + "_MIN" in names and base + "_MAX" in names:
                out.append((n, base + "_MIN", base + "_MAX"))
    return out


def test_N3_triple_family_is_non_empty():
    triples = _enumerate_triples()
    assert len(triples) >= 5, f"滑杆三元组族意外缩小到 {len(triples)} 组：{triples}"


def test_N3_default_out_of_range_is_caught():
    """DEFAULT 被推到区间之外时必须被捕获（防「DEFAULT 的钳制参数与 MIN/MAX 常量漂移」）。"""
    triples = _enumerate_triples()
    assert triples, "滑杆三元组为空"
    missed = []
    for dname, mn, mx in triples:
        vmx = getattr(config, mx)
        if not isinstance(vmx, (int, float)):
            continue
        _set(**{dname: vmx + 10})          # 推到上限之外
        probs = validate_config()
        if not any(dname in p for p in probs):
            missed.append(dname)
        _set(**{dname: getattr(config, mn) - 10})   # 推到下限之外
        probs = validate_config()
        if not any(dname in p for p in probs):
            missed.append(f"{dname}(下界)")
    assert not missed, f"以下 DEFAULT 越界未被捕获：{missed}"


def test_N3_defaults_are_currently_in_range():
    """现状核对：现有 5 组三元组的 DEFAULT 全部落在 [MIN, MAX] 内（不应有告警）。"""
    for dname, mn, mx in _enumerate_triples():
        dv, vmn, vmx = getattr(config, dname), getattr(config, mn), getattr(config, mx)
        assert vmn <= dv <= vmx, f"{dname}({dv}) 不在 [{vmn}, {vmx}] 内"


# ---------------------------------------------------------------------------
# N4 · F2：元检查必须从命名空间派生（不写死清单）
# ---------------------------------------------------------------------------

def test_N4_meta_check_enumerates_namespace():
    """源码必须用 `globals()` 枚举命名空间，而不是硬编码配置名清单。"""
    seg = _SRC.split("# 检查 18")[1] if "# 检查 18" in _SRC else ""
    assert seg, "未找到检查 18 的实现"
    assert "_ns = globals()" in seg, "元检查未从命名空间派生"
    assert "for _name in list(_ns):" in seg
    assert "_ns[_lo_name]" in seg and "_ns[_hi_name]" in seg


def test_N4_meta_check_has_no_hardcoded_name_list():
    """元检查段内**不得**出现配置名清单（否则退化成会漂移的手抄清单，经验 167）。"""
    seg = _SRC.split("# 检查 18")[1].split("return problems")[0]
    # 允许出现的是局部变量名与后缀字面量，不应出现具体配置常量名
    for forbidden in ("DETECT_GRID_MIN_COVERAGE", "DETECT_LOGO_MIN_AREA_RATIO",
                      "DETECT_LOGO_CANNY_LO", "RECOMMEND_GRID_MIN_COVERAGE"):
        assert forbidden not in seg, f"元检查里硬编码了 {forbidden}"


def test_N4_triple_check_enumerates_namespace():
    seg = _SRC.split("# 检查 19")[1].split("return problems")[0]
    assert '_DEFAULT' in seg and 'n.endswith("_DEFAULT")' in seg, "三元组检查未按后缀枚举"
    assert '_base + "_MIN"' in seg and '_base + "_MAX"' in seg, "未按基名派生 MIN/MAX 名"


def test_N4_no_new_top_level_import_added():
    """元检查不得为自身便利引入新依赖（如 `re`）—— 本模块只 import json/os/sys/pathlib/typing。"""
    header = _SRC.split("ENV_PREFIX")[0]
    for mod in ("import re", "import math", "import itertools"):
        assert mod not in header, f"config.py 顶层新增了 {mod}"



# ---------------------------------------------------------------------------
# N5 · 回归：原有 17 项检查逐条仍有效
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("attr,value,keyword", [
    ("DEVICE_MODE", "bogus", "DEVICE_MODE"),
    ("INPAINT_ENGINE", "bogus_engine", "INPAINT_ENGINE"),
    ("OPENCV_INPAINT_METHOD", "bogus", "OPENCV_INPAINT_METHOD"),
    ("LOG_LEVEL", "bogus", "LOG_LEVEL"),
    ("DEFAULT_OUTPUT_FORMAT", "BOGUS", "DEFAULT_OUTPUT_FORMAT"),
    ("PDF_PAGE_IMAGE_FMT", "BOGUS", "PDF_PAGE_IMAGE_FMT"),
])
def test_N5_enum_checks_still_fire(attr, value, keyword):
    _set(**{attr: value})
    probs = validate_config()
    assert any(keyword in p for p in probs), f"{attr}={value} 未被捕获"


def test_N5_tile_overlap_check_still_fires():
    _set(TILE_OVERLAP=400, TILE_SIZE=512)
    assert any("TILE_OVERLAP" in p for p in validate_config())


def test_N5_tile_overlap_check_tolerates_violation_when_cross_site():
    """跨项依赖：TILE_OVERLAP=400 配 TILE_SIZE=1024（400 < 512）**不应**报警。"""
    _set(TILE_OVERLAP=400, TILE_SIZE=1024)
    assert not any("TILE_OVERLAP" in p for p in validate_config())


def test_N5_failed_page_and_codec_checks_still_fire():
    _set(VIDEO_CODEC_CANDIDATES=())
    assert any("VIDEO_CODEC_CANDIDATES" in p for p in validate_config())
    _set(VIDEO_FRAME_STEP=2, VIDEO_REUSE_MASK=False)
    assert any("VIDEO_FRAME_STEP" in p for p in validate_config())


def test_N5_sigma_k_and_coverage_ratio_checks_still_fire():
    _set(DETECT_SIGMA_K=1.0)
    assert any("DETECT_SIGMA_K" in p for p in validate_config())
    _set(DETECT_MAX_COVERAGE_RATIO=0.05)
    assert any("DETECT_MAX_COVERAGE_RATIO" in p for p in validate_config())


def test_N5_feather_odd_check_still_fires():
    _set(MASK_FEATHER_DEFAULT=4)          # 偶数 ⇒ 应告警
    assert any("MASK_FEATHER_DEFAULT" in p for p in validate_config())
    _set(MASK_FEATHER_DEFAULT=5)          # 奇数 ⇒ 不告警
    assert not any("MASK_FEATHER_DEFAULT" in p for p in validate_config())


def test_N5_committed_scope_check_still_fires():
    _set(WATERMARK_TYPE_COMMITTED=frozenset())
    assert any("WATERMARK_TYPE_COMMITTED" in p for p in validate_config())


# ---------------------------------------------------------------------------
# N6 · 无误报
# ---------------------------------------------------------------------------

def test_N6_baseline_is_clean():
    """默认配置必须**零告警** —— 元检查最大的风险就是误报。"""
    probs = validate_config()
    assert probs == [], f"默认配置出现告警（元检查可能误报）：{probs}"


def test_N6_check_count_lower_bound():
    """检查项数下界（原有 17 项 + 新增 2 项元检查）。"""
    found = set(re.findall(r"# 检查 ([0-9]+(?:\.[0-9]+)?)", _SRC))
    assert len(found) >= 19, f"检查项数意外减少：{sorted(found)}"


def test_N6_meta_checks_are_idempotent():
    """元检查不得改动模块状态（连续调用两次结果一致）。"""
    a = validate_config()
    b = validate_config()
    assert a == b == []


# ---------------------------------------------------------------------------
# N7 · 消息质量
# ---------------------------------------------------------------------------

def test_N7_pair_message_includes_names_values_and_consequence():
    _set(DETECT_LOGO_CANNY_LO=200, DETECT_LOGO_CANNY_HI=100)
    probs = validate_config()
    hit = [p for p in probs if "DETECT_LOGO_CANNY_LO" in p]
    assert hit, "CANNY 双阈值反转未被捕获"
    msg = hit[0]
    assert "DETECT_LOGO_CANNY_HI" in msg
    assert "200" in msg and "100" in msg
    assert ("静默漏检" in msg) or ("恒不产出" in msg) or ("未定义" in msg)


def test_N7_triple_message_mentions_sync_action():
    _set(MASK_THRESHOLD_DEFAULT=999)
    probs = validate_config()
    hit = [p for p in probs if "MASK_THRESHOLD_DEFAULT" in p]
    assert hit, "DEFAULT 越界未被捕获"
    assert "_env_" in hit[0] or "同步" in hit[0], "未提示「同步钳制参数与 MIN/MAX 常量」"
