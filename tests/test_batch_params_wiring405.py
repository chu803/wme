# -*- coding: utf-8 -*-
"""阶段 405：批量页「增强/锐化」接线守卫。

外部验收（2026-11-10）实测批量 4/4 全失败：
`❌ xxx.jpg：name 'params' is not defined`。
根因：run_batch 的 _processor 引用了 build_batch_tab 作用域里的 `params`
（params.sharpen / params.postprocess），但 run_batch 既没有这两个形参，
UI 的 inputs 也没把控件传进来 ⇒ 真实批处理第一步就 NameError。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import ui.tab_batch as tb


def test_run_batch_signature_has_postprocess_wiring():
    """run_batch 必须显式接住两个增强参数（而不是闭包抓 builder 的 params）。"""
    sig = inspect.signature(tb.run_batch)
    assert "enable_postprocess" in sig.parameters
    assert "sharpen" in sig.parameters


def test_processor_forwards_postprocess_values(monkeypatch, tmp_path):
    """真实调用 _processor：增强开关与锐化值必须原样转给 _process_one。"""
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    seen: dict = {}

    def fake_process(task, on_progress, **kwargs):
        seen.update(kwargs)
        return {"result_path": str(img), "status": "ok"}

    monkeypatch.setattr(tb, "_process_one", fake_process)
    monkeypatch.setattr(tb, "build_detection_stats_markdown", lambda *a, **k: "")
    monkeypatch.setattr(tb, "build_aggregate_detection_stats_markdown", lambda *a, **k: "")

    class _Queue:
        def __init__(self):
            self.tasks = []

        def submit(self, name, path):
            self.tasks.append((name, path))

        def summary(self):
            n = len(self.tasks)
            return {"current": "x", "done": 0, "failed": 0, "cancelled": 0,
                    "total": n, "progress": 0.0, "finished": 0}

        def run(self, processor, on_update=None):
            for name, path in self.tasks:
                processor(SimpleNamespace(path=path, name=name), lambda *a: None)
                yield None

    monkeypatch.setattr(tb, "BatchQueue", _Queue)

    gen = tb.run_batch(
        folder_value=str(img), files_value=None, mask_folder_value=None,
        mode="auto", engine="opencv",
        strength=1.0, dilate=0, feather=0, threshold=128, denoise=0,
        sensitivity=0.5,
        poisson_blend=False, poisson_strength=0.0,
        texture_transfer=False, texture_amount=0.0,
        artifact_reduce=False, artifact_amount=0.0,
        superres=False, superres_amount=0.0,
        real_superres=False, real_scale=1, real_backend="auto",
        enable_postprocess=False, sharpen=0.7,
        evaluate=False, compare=False, preset="general",
        auto_preset=False, dual_run=None, progress=lambda *a: None,
    )
    list(gen)

    assert seen.get("sharpen") == 0.7
    assert seen.get("enable_postprocess") is False


def test_batch_ui_inputs_pass_postprocess_controls():
    """UI 绑定必须把 params.postprocess / params.sharpen 放进 inputs。"""
    src = inspect.getsource(tb.build_batch_tab)
    click = src.split("run_btn.click(", 1)[1]
    assert "params.postprocess" in click
    assert "params.sharpen" in click


def test_process_one_unpacks_process_auto_repair_in_order(monkeypatch, tmp_path):
    """批量的单图处理器必须按真实返回序解包；把状态文案当 result 会崩。

    process_auto_repair 的返回是
    (orig_view, result_view, compare_view, status_text, download_file)。
    旧代码写成 `_, result, status, _, out_path` ⇒ status=compare_view(ndarray)
    ⇒ `status or ...` 抛「数组真值含糊」，首次处理必失败、靠重试+缓存掩盖。
    """
    import numpy as np
    from PIL import Image

    img_path = tmp_path / "a.png"
    Image.fromarray(np.full((16, 16, 3), 128, np.uint8)).save(img_path)
    frame = np.full((16, 16, 3), 128, np.uint8)

    class _Res:
        mask = np.full((16, 16), 255, np.uint8)
        regions: list = []
        stats = None
        used_ocr = False

    class _Rec:
        thumb_path = None

        def update_result_path(self, *a, **k):
            pass

    class _Cache:
        def find_by_hash(self, *a, **k):
            return None

        def add_success(self, *a, **k):
            return _Rec()

        def update_result_path(self, *a, **k):
            pass

    monkeypatch.setattr(tb, "validate_image_file", lambda p: {})
    monkeypatch.setattr(tb, "load_image", lambda p: frame)
    monkeypatch.setattr(tb, "detect_watermark_mask", lambda img, **k: _Res())
    monkeypatch.setattr(tb, "compute_file_hash", lambda p: "hash")
    monkeypatch.setattr(tb, "_CACHE", _Cache())
    monkeypatch.setattr(
        tb, "process_auto_repair",
        lambda ev, **k: (frame, frame, frame, "STATUS-OK", str(tmp_path / "out.png")),
    )

    out = tb._process_one(
        SimpleNamespace(name="a.png", path=str(img_path)),
        lambda *a: None,
        mode="auto", engine="opencv",
        strength=1.0, dilate=0, feather=0, threshold=128, denoise=0,
        sensitivity=0.5, mask_folder="", mask_index=None,
        preset="general", auto_preset=False,
        sharpen=0.6, enable_postprocess=True, postprocess_eval=False,
        poisson_blend=False, poisson_strength=0.0,
        texture_transfer=False, texture_amount=0.0,
        artifact_reduce=False, artifact_amount=0.0,
        superres=False, superres_amount=0.0,
        real_superres=False, real_scale=1, real_backend="auto",
        dual_run=None,
    )
    assert out["status"] == "STATUS-OK"
    assert out["out_path"] == str(tmp_path / "out.png")


def test_history_reprocess_unpacks_result_and_status_in_order(monkeypatch, tmp_path):
    """历史页「重新处理」同样按真实返回序解包，结果路径不得写成状态文案。"""
    import numpy as np
    from types import SimpleNamespace

    import ui.tab_history as th

    (tmp_path / "orig.png").write_bytes(b"x")
    rec = SimpleNamespace(
        original_path=str(tmp_path / "orig.png"), kind="image",
        original_name="orig.png", result_path="old.png", thumb_path=None,
        engine="opencv", status="success", params="",
    )

    class _Cache:
        def get(self, rec_id):
            return rec

        def update_record(self, r):
            pass

    frame = np.full((16, 16, 3), 128, np.uint8)
    real_out = str(tmp_path / "cleaned.png")
    monkeypatch.setattr(th, "_CACHE", _Cache())
    monkeypatch.setattr(th, "load_image", lambda p: frame)
    monkeypatch.setattr(th, "detect_watermark_mask",
                        lambda img, **k: SimpleNamespace(mask=np.full((16, 16), 255, np.uint8)))
    monkeypatch.setattr(th, "_build_editor_value", lambda img, mask: {})
    monkeypatch.setattr(th, "process_auto_repair",
                        lambda ev, **k: (frame, frame, frame, "STATUS-OK", real_out))
    monkeypatch.setattr(th, "make_thumbnail", lambda img, size=None: frame)
    monkeypatch.setattr(th, "save_thumbnail", lambda thumb, name: tmp_path / "thumb.png")
    monkeypatch.setattr(th, "load_gallery", lambda compare=False: ([], None))

    msg, _gallery = th.reprocess("rid", "opencv", 1.0, 0)
    assert rec.result_path == real_out, "记录的结果路径必须是真实产物，不是状态文案"
    assert "STATUS-OK" in msg
