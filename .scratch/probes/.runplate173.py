# -*- coding: utf-8 -*-
"""直接调用 tests/test_corner_plate.py 的全部用例（含参数化与 monkeypatch 替身）。"""
import importlib.util
import inspect
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location(
    "test_corner_plate", os.path.join(ROOT, "tests", "test_corner_plate.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class MP:
    def __init__(self):
        self._u = []

    def setattr(self, target, name, value=None, raising=True):
        self._u.append((target, name, getattr(target, name, "__MISSING__")))
        setattr(target, name, value)

    def undo(self):
        for t, n, o in reversed(self._u):
            if o == "__MISSING__":
                try:
                    delattr(t, n)
                except Exception:  # noqa: BLE001
                    pass
            else:
                setattr(t, n, o)
        self._u.clear()


PARAMS = {
    "test_plate_detected_on_both_dark_and_bright_backgrounds":
        [(mod._DARK_BG, True), (mod._BRIGHT_BG, False)],
}
NEEDS_MP = {"test_plate_channel_disabled_by_switch",
            "test_plate_channel_covers_all_real_white_logos",
            "test_plate_closing_rejoins_ink_fragmented_plate_on_dark_bg"}

passed, failed = 0, []
for name in sorted(dir(mod)):
    if not (name.startswith("test_") and callable(getattr(mod, name))):
        continue
    fn = getattr(mod, name)
    if name in PARAMS:
        for p in PARAMS[name]:
            try:
                fn(*p)
                passed += 1
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}{p}: {type(exc).__name__}: {exc}")
    elif name in NEEDS_MP:
        mp = MP()
        try:
            fn(mp)
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
        finally:
            mp.undo()
    else:
        try:
            fn()
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: {type(exc).__name__}: {exc}")

res = [f"PASSED={passed} FAILED={len(failed)}"]
res += ["  - " + f.encode("ascii", "replace").decode("ascii") for f in failed]
with open(".runplate173.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(res) + "\n")
print(res[0])
