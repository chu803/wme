import hashlib, importlib.util

PATH = r"D:/work 111/2026-08-03-17-38-51/scripts/e2e_real_probe.py"

def load_mod():
    spec = importlib.util.spec_from_file_location("ep_ce", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

with open(PATH, "rb") as f:
    original = f.read()
sha_orig = hashlib.sha256(original).hexdigest()
print("original sha256:", sha_orig)

needle = '("removal", "down")'
text = original.decode("utf-8")
assert needle in text, "needle not found"

in_spec = [{"case": "corner_logo", "n": 3, "n_total": 3,
           "removal": 0.30, "collateral": 1.30, "overdraw": 1.10, "spill": 0.03}]

try:
    # 翻转红线方向：removal 由「守下界」变「守上界」=> in-spec(0.30>=0.25) 本应通过，
    # 翻转后被误判失败 => 守卫（test_gate_in_spec_passes 等）必红。
    broken = text.replace(needle, '("removal", "up")', 1)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(broken)
    mod = load_mod()
    fails_broken, _, _ = mod.evaluate_gate(in_spec, None)
    print("BROKEN evaluate_gate fails:", fails_broken)
    assert any("removal" in f for f in fails_broken), "GUARD FAILED TO CATCH broken direction"
    print("  -> guard catches breakage (broken -> red): OK")
finally:
    # 无论如何还原字节级原文件
    with open(PATH, "wb") as f:
        f.write(original)

sha_after = hashlib.sha256(open(PATH, "rb").read()).hexdigest()
assert sha_after == sha_orig, f"RESTORE NOT BYTE-IDENTICAL: {sha_after} != {sha_orig}"
print("restored sha256 :", sha_after, "(byte-identical: OK)")

mod2 = load_mod()
fails_ok, _, _ = mod2.evaluate_gate(in_spec, None)
print("RESTORED evaluate_gate fails:", fails_ok)
assert fails_ok == [], fails_ok
print("  -> restored behaves correctly (green): OK")
print("COUNTER-EXAMPLE OK: broken->red, restored->sha identical->green")
