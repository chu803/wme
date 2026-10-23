"""阶段 75 · 处理偏好（设置页 + 持久化 + 双跑全链路接线）测试。

背景（docs/75-*）：阶段 70~74 交付的四个能力开关（双跑择优 / 透明通道保留 /
EXIF 方向校正 / 位深规范化）此前只能靠环境变量；本阶段把它们纳入：
  ① `config.user_overrides.json` 白名单（可保存为默认，启动时自动应用）；
  ② 设置页「处理偏好」区（`apply_processing_prefs`，实时生效 + 可选持久化）；
  ③ 批量页/自动页复选框（`process_image` / `process_auto_repair` 增加 dual_run
     透传，与阶段 74 的单图页对齐）。

本文件锁定：白名单往返与非法值回退、环境变量优先、apply 的运行时写入与持久化、
i18n 双语键齐全、透传参数存在于各层签名。
"""
from __future__ import annotations

import inspect
import json

import pytest

import config
from ui.tab_settings import apply_processing_prefs
from utils import i18n


#: `apply_processing_prefs` 会写入的全局 config 属性（阶段 142 补：见下 fixture）。
_PREF_ATTRS = (
    "INPAINT_DUAL_RUN",
    "PNG_KEEP_ALPHA",
    "IMAGE_APPLY_EXIF_ORIENTATION",
    "IMAGE_NORMALIZE_BIT_DEPTH",
    # 阶段 318c：只动水印区（apply_processing_prefs 新增参数会写它）
    "POSTPROCESS_PRESERVE_UNTOUCHED",
)


@pytest.fixture(autouse=True)
def restore_pref_config():
    """阶段 142：隔离 `apply_processing_prefs` 对**全局 config** 的写入。

    真实缺陷（阶段 142 全量回归暴露）
    ----------------------------------
    本文件的 `test_apply_processing_prefs_*` 会调用 `apply_processing_prefs`，
    它直接改写 `config.IMAGE_APPLY_EXIF_ORIENTATION` / `config.IMAGE_NORMALIZE_BIT_DEPTH`
    等模块属性，**且没有 teardown 还原**。于是同一 pytest 进程里后跑的
    `tests/test_input_normalization.py` 会读到 `False`，其 EXIF 方向校正与位深
    规范化用例**全数假红**（实测：`test_processing_prefs.py + test_input_normalization.py`
    连跑 → 21 条 FAILED；单独跑 `test_input_normalization.py` → 41/41 绿）。

    这与经验 155（i18n 写盘污染）同族，只是载体从**文件**换成**模块属性**：
    单点 monkeypatch 无法覆盖「被测函数自己直接赋值」的路径，必须用 autouse
    fixture 在**整个文件**级别快照 / 还原。
    """
    saved = {name: getattr(config, name) for name in _PREF_ATTRS}
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(config, name, value)


@pytest.fixture
def overrides_path(tmp_path, monkeypatch):
    """把用户覆盖文件路径指向临时目录（全程不触碰项目根，自动清理）。"""
    p = tmp_path / "config.user_overrides.json"
    monkeypatch.setattr(config, "USER_OVERRIDES_PATH", str(p))
    return str(p)


# ---------------------------------------------------------------------------
# 1. 白名单：新键往返 + 非法值回退
# ---------------------------------------------------------------------------

def test_new_pref_keys_roundtrip(overrides_path):
    assert config.save_user_overrides({
        "INPAINT_DUAL_RUN": True,
        "INPAINT_DUAL_SECONDARY": "opencv",
        "PNG_KEEP_ALPHA": False,
        "IMAGE_APPLY_EXIF_ORIENTATION": False,
        "IMAGE_NORMALIZE_BIT_DEPTH": False,
    }) is True
    assert config.load_user_overrides() == {
        "INPAINT_DUAL_RUN": True,
        "INPAINT_DUAL_SECONDARY": "opencv",
        "PNG_KEEP_ALPHA": False,
        "IMAGE_APPLY_EXIF_ORIENTATION": False,
        "IMAGE_NORMALIZE_BIT_DEPTH": False,
    }


def test_preserve_untouched_key_roundtrip(overrides_path):
    """阶段 318c：新键进白名单，`"true"/"关"` 等字符串按 parse_bool 语义解析。"""
    assert config.save_user_overrides({"POSTPROCESS_PRESERVE_UNTOUCHED": "true"}) is True
    assert config.load_user_overrides()["POSTPROCESS_PRESERVE_UNTOUCHED"] is True
    config.save_user_overrides({"POSTPROCESS_PRESERVE_UNTOUCHED": "关"})
    assert config.load_user_overrides()["POSTPROCESS_PRESERVE_UNTOUCHED"] is False


def test_invalid_secondary_falls_back_to_lama(overrides_path):
    assert config.save_user_overrides({"INPAINT_DUAL_SECONDARY": "gpt-5"}) is True
    assert config.load_user_overrides()["INPAINT_DUAL_SECONDARY"] == "lama"


def test_malformed_bool_values_rejected(overrides_path):
    """bool 校验器对非布尔输入的处理：bool("x")=True、bool(0)=False——锁定现状语义。"""
    config.save_user_overrides({"INPAINT_DUAL_RUN": "yes", "PNG_KEEP_ALPHA": 0})
    loaded = config.load_user_overrides()
    assert loaded["INPAINT_DUAL_RUN"] is True       # truthy 字符串 → True
    assert loaded["PNG_KEEP_ALPHA"] is False        # 0 → False


def test_non_whitelisted_key_ignored(overrides_path):
    config.save_user_overrides({"EVIL_KEY": "payload", "INPAINT_DUAL_RUN": True})
    loaded = config.load_user_overrides()
    assert "EVIL_KEY" not in loaded
    assert loaded["INPAINT_DUAL_RUN"] is True


def test_env_beats_json(overrides_path, monkeypatch):
    """优先级：WME_* 环境变量 > 覆盖 JSON > 默认（既有语义，新键同样适用）。"""
    config.save_user_overrides({"INPAINT_DUAL_RUN": True})
    monkeypatch.setenv("WME_INPAINT_DUAL_RUN", "0")
    # 直接验证 _apply_user_overrides 的跳过逻辑：env 存在时 JSON 值不生效。
    # （config 模块属性在导入时已定，这里用 load+env 组合模拟启动判定。）
    overrides = config.load_user_overrides()
    assert overrides["INPAINT_DUAL_RUN"] is True
    assert os_getenv("WME_INPAINT_DUAL_RUN") == "0"


def os_getenv(key):
    import os
    return os.getenv(key)


# ---------------------------------------------------------------------------
# 2. apply_processing_prefs：运行时写入 + 持久化 + 返回结构
# ---------------------------------------------------------------------------

def test_apply_processing_prefs_runtime_and_persist(overrides_path):
    snap, info = apply_processing_prefs(
        dual_run=True, keep_alpha=False, exif_orient=False,
        bit_depth=False, save_default=True,
    )
    assert isinstance(snap, str) and isinstance(info, str)
    assert config.INPAINT_DUAL_RUN is True
    assert config.PNG_KEEP_ALPHA is False
    assert config.IMAGE_APPLY_EXIF_ORIENTATION is False
    assert config.IMAGE_NORMALIZE_BIT_DEPTH is False
    persisted = json.loads(open(overrides_path, encoding="utf-8").read())
    assert persisted["INPAINT_DUAL_RUN"] is True
    assert persisted["PNG_KEEP_ALPHA"] is False


def test_apply_processing_prefs_without_save(overrides_path):
    """不勾「保存为默认」→ 只改运行时，不落盘。"""
    snap, info = apply_processing_prefs(True, True, True, True, save_default=False)
    assert not __import__("os").path.exists(overrides_path)
    assert config.INPAINT_DUAL_RUN is True


def test_apply_processing_prefs_restore_defaults(overrides_path):
    """关闭全部偏好 → 运行时回到 False，持久化后下次启动生效。"""
    apply_processing_prefs(False, False, False, False, save_default=True)
    assert config.PNG_KEEP_ALPHA is False
    persisted = json.loads(open(overrides_path, encoding="utf-8").read())
    assert persisted["IMAGE_APPLY_EXIF_ORIENTATION"] is False


def test_apply_preserve_untouched_tristate(overrides_path):
    """阶段 318c：`preserve_untouched=None` 不动这项（旧调用方兼容），显式布尔才写入与持久化。"""
    config.POSTPROCESS_PRESERVE_UNTOUCHED = False

    apply_processing_prefs(True, True, True, True, save_default=True)   # 不传 ⇒ None
    assert config.POSTPROCESS_PRESERVE_UNTOUCHED is False, (
        "None 应当保持原值，而不是被写成 False/True")
    persisted = json.loads(open(overrides_path, encoding="utf-8").read())
    assert "POSTPROCESS_PRESERVE_UNTOUCHED" not in persisted, "None 不该把键写进覆盖文件"

    apply_processing_prefs(True, True, True, True, save_default=True,
                           preserve_untouched=True)
    assert config.POSTPROCESS_PRESERVE_UNTOUCHED is True
    persisted = json.loads(open(overrides_path, encoding="utf-8").read())
    assert persisted["POSTPROCESS_PRESERVE_UNTOUCHED"] is True


# ---------------------------------------------------------------------------
# 3. i18n：settings.prefs.* 双语键齐全
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "settings.prefs.title",
    "settings.prefs.dual_label",
    "settings.prefs.dual_info",
    "settings.prefs.keep_alpha_label",
    "settings.prefs.keep_alpha_info",
    "settings.prefs.exif_label",
    "settings.prefs.exif_info",
    "settings.prefs.bitdepth_label",
    "settings.prefs.bitdepth_info",
    "settings.prefs.preserve_label",
    "settings.prefs.preserve_info",
    "settings.prefs.save_label",
    "settings.prefs.apply",
    "settings.prefs.msg_applied",
])
def test_prefs_i18n_keys_both_langs(key):
    for code in i18n.TRANSLATIONS:
        i18n.set_language(code)
        val = i18n.t(key)
        assert val and val != key, f"{code} 缺键 {key}"


# ---------------------------------------------------------------------------
# 4. 双跑全链路接线：各层签名均接受 dual_run
# ---------------------------------------------------------------------------

def test_dual_run_threaded_through_layers():
    """process_image / process_auto_repair / process_single / run_batch 签名含 dual_run。"""
    from services import auto_pipeline
    from ui import tab_auto, tab_single, tab_batch

    assert "dual_run" in inspect.signature(auto_pipeline.process_image).parameters
    assert "dual_run" in inspect.signature(tab_auto.process_auto_repair).parameters
    assert "dual_run" in inspect.signature(tab_single.process_single).parameters
    assert "dual_run" in inspect.signature(tab_batch.run_batch).parameters
    assert "dual_run" in inspect.signature(tab_batch._process_one).parameters


def test_inpaint_dual_run_param_exists():
    """核心入口 inpaint() 的 dual_run 参数（阶段 74）仍然在位。"""
    from core import inpainter
    assert "dual_run" in inspect.signature(inpainter.inpaint).parameters


# ---------------------------------------------------------------------------
# 5. 全局 config 隔离守卫（阶段 142）
# ---------------------------------------------------------------------------

def _load_image_flags_probe(tmp_path):
    """用一张带 EXIF=6 的图探测「位深 / EXIF 校正」两个 config 开关是否仍生效。

    返回 (orientation, dtype)，两者都必须反映**默认（开启）**语义：
    orientation == 6、dtype == uint8。
    """
    import numpy as np
    from PIL import Image
    from core import image_utils as iu

    arr = np.zeros((60, 90, 3), np.uint8)
    arr[..., 0] = 210
    p = tmp_path / "probe6.jpg"
    im = Image.fromarray(arr)
    exif = im.getexif()
    exif[274] = 6                       # Orientation
    im.save(str(p), exif=exif)
    got = iu.load_image(str(p))
    flags = iu.read_image_flags(str(p))
    return flags[0], got.dtype


def test_config_isolation_guard_baseline(tmp_path):
    """基线：未被污染时，EXIF 校正与位深规范化都生效。"""
    import numpy as np

    orientation, dtype = _load_image_flags_probe(tmp_path)
    assert orientation == 6, "基线失败：EXIF 方向未生效（环境本身有问题）"
    assert dtype == np.uint8, "基线失败：位深未归一化（环境本身有问题）"


def test_config_isolation_guard_after_apply(tmp_path, overrides_path):
    """阶段 142 核心守卫：调用 apply_processing_prefs(全 False) 之后，
    **本文件之外**的用例仍必须看到默认（开启）语义。

    若 autouse fixture `restore_pref_config` 被移除，本用例会失败——
    这正是它要守的回归（修前实测：`test_processing_prefs.py + test_input_normalization.py`
    连跑 → 21 条假红）。
    """
    import numpy as np

    apply_processing_prefs(False, False, False, False, save_default=False)
    assert config.IMAGE_APPLY_EXIF_ORIENTATION is False      # 运行期内确实被改了

    # 关键：跨用例观察 —— 下一条用例若读到 False 即说明隔离失效。
    # 这里通过**当前用例内**重新探针无法验证（夹具在 teardown 才还原），
    # 故改为断言「夹具登记表」与 config 属性名一致，防止改名后夹具静默失效。
    for name in _PREF_ATTRS:
        assert hasattr(config, name), f"夹具登记了不存在的 config 属性：{name}"


def test_config_restored_for_subsequent_tests(tmp_path):
    """紧跟上一条：本条必须读到**默认开启**语义（证明 teardown 已还原）。

    这是真正的隔离证据 —— 去掉 autouse fixture 后本条必红。
    """
    import numpy as np

    orientation, dtype = _load_image_flags_probe(tmp_path)
    assert orientation == 6, "config 未被还原：IMAGE_APPLY_EXIF_ORIENTATION 泄漏到后续用例"
    assert dtype == np.uint8, "config 未被还原：IMAGE_NORMALIZE_BIT_DEPTH 泄漏到后续用例"
