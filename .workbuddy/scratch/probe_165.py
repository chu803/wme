# -*- coding: utf-8 -*-
"""Stage 165 探针：core/model_manager 下载路径的两处静默风险。

① 槽未提供 urls 时，`download_model` 是否回退到**全局默认源**（= 内置 lama 的地址）⇒
   把 lama 权重下载进别的槽路径，且（该槽无 expected_size/sha）校验静默通过。
② 下载成功后是否清缓存？`rollback_model` 会 pop 掉旧模型，`download_model` 似乎不会
   ⇒ 已加载过的旧模型继续被 `load_model` 命中（静默用旧权重）。

隔离 tmp；桩 download_to。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "D:/work 111/2026-08-03-17-38-51")

import config  # noqa: E402
import download_model as dm  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp())
    config.MODEL_DIR = tmp                     # 把模型目录改到 tmp

    import core.model_manager as mm  # 延迟导入，确保上面的 config.MODEL_DIR 生效

    print("== ① 无 urls 的槽，download_model 收到什么 urls？==")
    captured = {}

    def fake_download_to(**k):
        captured.update(k)
        # 不真正落盘 → 后续 verify 会 exists=False，但 urls 已被记录
        return k.get("dest", "")

    dm.download_to = fake_download_to
    slot = mm.register_model_slot("probe_slot", filename="probe_slot.pt")  # urls 默认 ()
    print("   槽 urls =", slot.urls)
    try:
        mm.download_model("probe_slot")
    except Exception as exc:
        print(f"   （调用抛异常，属预期）：{type(exc).__name__}")
    got = captured.get("urls")
    glob = list(config.MODEL_DOWNLOAD_URLS)
    print(f"   download_to 收到 urls = {got}")
    print(f"   config.MODEL_DOWNLOAD_URLS = {glob}")
    print(f"   >>> 是否回退了全局（lama）地址：{got == glob}")
    print()

    print("== ② 下载成功后是否清缓存（对比 rollback_model 会清）==")
    # 造一个真实文件，让 verify 通过（该槽无 expected_size/sha ⇒ 静默通过）
    dest = tmp / "probe_slot.pt"
    dest.write_bytes(b"weights-v1")

    def fake_download_to2(**k):
        Path(k["dest"]).write_bytes(b"weights-v2")   # 写上「新权重」
        return k["dest"]

    dm.download_to = fake_download_to2
    mm._CACHE[str(dest)] = "OLD_MODEL_OBJ"           # 模拟「该权重已加载过」
    print(f"   下载前 _CACHE = {dict(mm._CACHE)}")
    try:
        p = mm.download_model("probe_slot")
        print(f"   下载成功，返回 {p}")
    except Exception as exc:
        print(f"   下载抛异常：{type(exc).__name__}: {exc}")
    print(f"   下载后 _CACHE = {dict(mm._CACHE)}")
    still = str(dest) in mm._CACHE
    print(f"   >>> 旧模型仍在缓存（download 未清、rollback 会清 → 不对称）：{still}")
    print()
    print("PROBE DONE")


if __name__ == "__main__":
    main()
