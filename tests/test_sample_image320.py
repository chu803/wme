# -*- coding: utf-8 -*-
"""阶段 320 ·「试一张示例」的首屏体验守卫。

为什么要有它：
- 示例图是**随包发布的二进制资源**——最容易被后续「清理无用资产」误删；
- `load_sample_image()` 的返回值必须与 `sample_btn.click(outputs=[upload, editor, status_box])`
  **三值对齐**，错位在 Gradio 里不会报错、只会把图片塞进状态栏（静默错位，同族 LESSONS #297）；
- 资源缺失时必须**降级为提示**，不能让首屏崩掉。
"""
import importlib.util
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ui.tab_auto as tab  # noqa: E402
from utils import i18n  # noqa: E402


def test_sample_asset_exists_and_is_a_real_photo():
    p = tab.SAMPLE_IMAGE_PATH
    assert os.path.isfile(p), f"示例图缺失：{p}（首屏「试一张示例」会失效）"
    from PIL import Image
    with Image.open(p) as im:
        # 阶段 372：640 → 768（真实比例的角标在 640 上小到读不清）。
        assert im.size == (768, 768), f"示例图尺寸变了：{im.size}"
        assert im.convert("RGB").size == (768, 768)


def test_sample_asset_is_declared_for_packaging():
    """示例图必须在**会被打进 wheel** 的位置，且 pyproject 有对应 package-data。

    实测过的坑（阶段 320）：放在 `docs/assets/` 时 `packages.find` 不含 docs、`MANIFEST.in`
    又 prune 了 docs ⇒ 安装态用户点「试一张示例」只有降级提示（功能存在但无资源）。
    """
    p = os.path.abspath(tab.SAMPLE_IMAGE_PATH)
    rel = os.path.relpath(p, _ROOT).replace("\\", "/")
    assert rel.startswith("ui/assets/"), (
        f"示例图位于 {rel}：不在 ui/assets 下就不会随 wheel 发布（docs/ 被 prune）")
    text = open(os.path.join(_ROOT, "pyproject.toml"), encoding="utf-8").read()
    assert "[tool.setuptools.package-data]" in text, "缺 package-data 段：wheel 里不会有示例图"
    assert 'ui = ["assets/*.png"]' in text, "package-data 未声明 ui/assets/*.png"


def test_load_sample_returns_aligned_triplet():
    up, ed, msg = tab.load_sample_image()
    assert isinstance(up, np.ndarray) and up.shape == (768, 768, 3)
    assert up.dtype == np.uint8
    # 真机实测（320）：ImageEditor **不接受**「background + 空 layers」的预填（浏览器回传后当没图），
    # 故示例图只填上传框，编辑框交给「② 自动检测水印」去填（那是已验证可用的形态）。
    assert _is_update(ed), f"编辑框应保持不动（gr.update），实得 {type(ed)}"
    assert isinstance(msg, str) and msg and msg != "auto.sample_loaded"


def _is_update(v) -> bool:
    # Gradio 6 的 gr.update() 返回普通 dict：{'__type__': 'update'}
    return isinstance(v, dict) and v.get("__type__") == "update"


def test_missing_asset_degrades_to_message(monkeypatch):
    monkeypatch.setattr(tab, "SAMPLE_IMAGE_PATH", os.path.join(_ROOT, "docs", "assets", "__nope__.png"))
    up, ed, msg = tab.load_sample_image()
    assert _is_update(up) and _is_update(ed), (
        "资源缺失时应返回 gr.update() 占位（不动现有画布），而不是把 None 直接塞进组件")
    assert "失败" in msg or "fail" in msg.lower()


@pytest.mark.parametrize("key", ["auto.sample_btn", "auto.sample_loaded", "auto.sample_fail"])
def test_sample_i18n_keys_both_langs(key):
    for code in i18n.TRANSLATIONS:
        i18n.set_language(code)
        val = i18n.t(key)
        assert val and val != key, f"{code} 缺键 {key}"


def test_sample_is_detectable_by_the_promised_path():
    """阶段 372 契约：随包的示例图**必须**能被「承诺能力」的角标通路检出。

    起因（实测）：阶段 320 那张示例是 640²、右下角**深色圆角板占宽 36%**——
    角标通路在 corner_ratio 0.20~0.55 下全部 **0 px**，整图检测只把它归成
    「半透明×6 + 边缘小字×2」，一键修复后**深色底板原样残留**（只擦掉部分文字）。
    也就是说：首屏演示恰好落在「承诺处理角标 LOGO」的边界外，新用户第一眼看到的是坏结果。

    换图口径（372）：白底圆角板、占宽 ~10.7%（真实角标比例），实测角标通路 2553 px。
    本守卫钉住「示例仍能被承诺通路检出」——换图或调参把它弄失明就红。
    反例（同一测试内）：纯灰图必须 0 px，证明判据不是恒真。
    """
    from PIL import Image
    import core.detector as det

    arr = np.array(Image.open(tab.SAMPLE_IMAGE_PATH).convert("RGB"))

    def _corner_px(img):
        m = det.detect_watermark_mask(
            img, enable_translucent=False, enable_corner=True, enable_edge=False,
            enable_grid=False, enable_gradient=False, enable_logo=False,
            use_ocr=False, lowres_rescue=None).mask
        return int(np.count_nonzero(m[:, :, 0] if m.ndim == 3 else m))

    got = _corner_px(arr)
    assert got >= 1000, (
        f"示例图的角标通路只检出 {got} px（< 1000）⇒ 首屏演示不再展示「承诺能力」；"
        "换图请保持「白底圆角板 + 占宽约 10~15%」的比例")
    assert _corner_px(np.full_like(arr, 128)) == 0, "纯灰图也被角标通路检出 ⇒ 判据恒真，本守卫不含信息"
