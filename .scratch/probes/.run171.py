# -*- coding: utf-8 -*-
"""直接调用 Stage 171 守卫测试模块里的全部 test_*。"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

FILES = ["tests/test_e2e_real_probe_exit_codes.py"]

passed = 0
failed = []
for rel in FILES:
    path = os.path.join(ROOT, rel)
    name = os.path.basename(rel)[:-3]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for attr in sorted(dir(mod)):
        if attr.startswith("test_") and callable(getattr(mod, attr)):
            try:
                getattr(mod, attr)()
                passed += 1
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{name}::{attr}: {type(exc).__name__}: {exc}")

result = [f"PASSED={passed} FAILED={len(failed)}"]
result += ["  - " + f.encode("ascii", "replace").decode("ascii") for f in failed]
with open(".run171.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(result) + "\n")
print("done")
