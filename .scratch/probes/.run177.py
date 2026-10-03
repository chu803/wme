# -*- coding: utf-8 -*-
"""直接调用指定测试模块的 test_*（无参 / monkeypatch 替身 / 参数化手动展开）。"""
import importlib.util
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
FILES = ["tests/test_real_gate.py", "tests/test_watermark_scope.py",
         "tests/test_watermark_scope95.py", "tests/test_baseline_guard_audit146.py",
         "tests/test_scripts_console_encoding.py"]


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


passed, failed, skipped = 0, [], []
for rel in FILES:
    spec = importlib.util.spec_from_file_location(
        os.path.basename(rel)[:-3], os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in sorted(dir(mod)):
        if not (name.startswith("test_") and callable(getattr(mod, name))):
            continue
        fn = getattr(mod, name)
        try:
            params = [p for p in inspect.signature(fn).parameters]
        except (TypeError, ValueError):
            params = []
        if params:
            if len(params) == 1 and params[0] == "monkeypatch":
                mp = MP()
                try:
                    fn(mp)
                    passed += 1
                except Exception as exc:  # noqa: BLE001
                    failed.append(f"{name}: {type(exc).__name__}: {exc}")
                finally:
                    mp.undo()
            else:
                skipped.append(f"{name}({','.join(params)})")
            continue
        try:
            fn()
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: {type(exc).__name__}: {exc}")

res = [f"PASSED={passed} FAILED={len(failed)} SKIPPED={len(skipped)}"]
res += ["  - FAIL " + f.encode("ascii", "replace").decode("ascii") for f in failed]
res += ["  - SKIP " + s.encode("ascii", "replace").decode("ascii") for s in skipped]
with open(".run177.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(res) + "\n")
print(res[0])
