# -*- coding: utf-8 -*-
"""阶段 335 · 产品写图路径不能用 `cv2.imwrite`，也不能不看写入结果。

复现依据（本机实测，不是推测）：

    cv2.imwrite(C:\\...\\cn_路径_xxx\\结果.png, arr)  ->  返回 False，且**一个字节都没写**
    同目录下的 ascii.png 也因目录含中文而同样失败

⇒ `cv2.imwrite` 在非 ASCII 路径上**静默失败**（不抛错）。而本仓早已知道这件事
（`services/compare_service.py:389` 明写「cv2.imwrite 不支持中文路径，统一走 save_image」，
`core/image_utils.py:680` 的 `save_image` 就是用 `imencode + open(path,'wb')` 绕开它），
却仍有**两条产品路径**留在旧写法上，且都不看返回值：

1. `core/image_utils._save_avif()` 的第三级回退：`cv2.imwrite(png_path, ...)` 后**无条件**
   `return png_path` ⇒ 上层 `file_service.save_result` 记「处理结果已保存」，
   调用方拿到 `status="success"` 与一个**不存在的路径**。
2. `services/cache_service._materialize_original()`：同样不看返回值 ⇒ 历史登记「成功」但原图没落盘。

本机 AVIF 其实走不到第 1 条的分支③（Pillow 12.3 原生支持 AVIF ⇒ 分支②成功），
所以测试用 monkeypatch **把分支①②关掉**来强制走回退（同 LESSONS：注入反例不改工作树）。
"""
from __future__ import annotations

import ast
import os
import pathlib

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import core.image_utils as iu  # noqa: E402
import services.cache_service as cs  # noqa: E402

_ARR = np.full((16, 16, 3), 128, dtype=np.uint8)


def _force_png_fallback(monkeypatch):
    """关掉分支①（cv2 无 AVIF 编码器）与分支②（Pillow 不可用），逼出分支③降级 PNG。"""
    real_encode = cv2.imencode

    def fake_encode(ext, img, params=None):
        if ext == ".avif":
            raise cv2.error("simulated: this OpenCV build has no AVIF encoder")
        return real_encode(ext, img, params if params is not None else [])

    monkeypatch.setattr(cv2, "imencode", fake_encode)
    monkeypatch.setattr(iu, "_save_avif_via_pillow", lambda *a, **k: False)


def _read_back(path: str) -> "np.ndarray | None":
    """读回用本仓自己的 `load_image`（`np.fromfile + imdecode`）。

    ⚠ 第一版这里用了 `cv2.imread` ⇒ 它自己就因中文路径返回 None，测试红了却**不是产品的错**。
    写验证时要用被测系统真实使用的读写通道，否则测的是自己的探针。
    """
    try:
        return iu.load_image(path)
    except Exception:  # noqa: BLE001
        return None


def test_avif_fallback_writes_a_real_file_ascii_path(tmp_path, monkeypatch):
    _force_png_fallback(monkeypatch)
    got = iu._save_avif(str(tmp_path / "out.avif"), _ARR, None, 80)
    assert got.endswith(".png"), got
    assert os.path.exists(got), "降级分支必须真的写出文件"
    back = _read_back(got)
    assert back is not None and back.shape[:2] == (16, 16)


def test_avif_fallback_writes_a_real_file_chinese_path(tmp_path, monkeypatch):
    """**这条就是被修掉的**：中文目录 ⇒ `cv2.imwrite` 返回 False ⇒ 旧代码照样把路径 return。"""
    cn = tmp_path / "中文输出目录"
    cn.mkdir()
    _force_png_fallback(monkeypatch)
    got = iu._save_avif(str(cn / "结果.avif"), _ARR, None, 80)
    assert got.endswith(".png"), got
    assert os.path.exists(got), (
        f"降级到 PNG 后返回了不存在的路径（父目录含中文）：{got}；目录内容={sorted(os.listdir(str(cn)))}")
    assert _read_back(got) is not None, "写出的必须是一张真能读回来的图"


def test_avif_fallback_raises_when_png_encode_fails(tmp_path, monkeypatch):
    """编码本身失败必须**抛错**，不能把「没写出来的路径」当成功返回。

    注意「绝不因格式崩溃」这句承诺仍然成立：AVIF 不可用时照样降级；
    这里抛的是**写盘/编码失败**，属于真正的错误，不是格式问题。
    """
    monkeypatch.setattr(iu, "_save_avif_via_pillow", lambda *a, **k: False)
    monkeypatch.setattr(cv2, "imencode",
                        lambda ext, img, params=None: (False, None))
    with pytest.raises(IOError):
        iu._save_avif(str(tmp_path / "out.avif"), _ARR, None, 80)


def test_materialize_original_writes_file_under_chinese_dir(tmp_path, monkeypatch):
    """缓存原图落盘同样不能静默失败（历史页「重新处理」要读这张图）。"""
    up = tmp_path / "缓存" / "uploads"
    up.mkdir(parents=True)
    monkeypatch.setattr(cs, "UPLOAD_DIR", up)
    monkeypatch.setattr(cs, "ensure_dirs", lambda: None)
    target = cs._materialize_original(_ARR, "我的图片")
    assert os.path.exists(str(target)), f"原图未落盘：{target}；目录内容={sorted(os.listdir(str(up)))}"


# ------------------------------------------------------------------------------
# 结构守卫：产品写图路径整族统一（防「想到一处改一处」）
# ------------------------------------------------------------------------------

def _is_main_guard(node: ast.AST) -> bool:
    """判据：`if __name__ == "__main__":`。

    ⚠ 这条判据本身就是一个「守卫写错 ⇒ 全族误报」的现场：第一版用
    `" == " in ast.dump(node.test)` 去认相等比较，而 `ast.dump` 里根本没有 `" == "`
    （比较是 `ast.Eq(...)`）⇒ `__main__` 子树**一个都没排除**，把 4 处纯自检的调用
    也报成产品缺陷。写守卫时要连**守卫自己**一起验，见 LESSONS #173。
    """
    return (isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value == "__main__")


def _imencode_write_markers(path: pathlib.Path) -> list:
    """返回**不在 `__main__` 子树里**的 `cv2.imwrite(...)` 调用行号。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main_lines: set = set()
    for node in ast.walk(tree):
        if _is_main_guard(node):
            for sub in ast.walk(node):
                if hasattr(sub, "lineno"):
                    main_lines.add(sub.lineno)
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "imwrite":
            continue
        base = node.func.value
        if isinstance(base, ast.Name) and base.id == "cv2" and node.lineno not in main_lines:
            hits.append(node.lineno)
    return hits


def test_repo_has_no_product_cv2_imwrite_left():
    """产品代码里 `cv2.imwrite` 必须为 0 处（`__main__` 自检除外）。

    判据**机械枚举** `core/services/ui/utils` 四个目录，不手抄文件名清单——
    手抄清单会随新增文件静默漏项（LESSONS #167/#173/#315 同一族）。
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for rel in ["core", "services", "ui", "utils"]:
        for p in sorted((root / rel).glob("*.py")):
            hits = _imencode_write_markers(p)
            if hits:
                offenders.append(f"{p.name}:{hits}")
    assert not offenders, (
        f"仍有产品写图路径用 cv2.imwrite（中文路径会静默写空、返回值又被丢弃）：{offenders}")

