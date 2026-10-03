import sys
sys.path.insert(0, r"D:/work 111/2026-08-03-17-38-51")
import scripts.e2e_real_probe as ep

def mk(case, n=3, n_total=3, removal=0.30, collateral=1.30, overdraw=1.10, spill=0.03):
    return {"case": case, "n": n, "n_total": n_total,
            "removal": removal, "collateral": collateral, "overdraw": overdraw, "spill": spill}

print("T1 in-spec corner_logo (baseline=None) -> expect NO fail")
agg = [mk("corner_logo")]
f, n, s = ep.evaluate_gate(agg, None)
print("  fails:", f, "| notes:", n, "| skipped:", s)

print("T2 removal BELOW redline (0.20<0.25) -> expect fail")
agg = [mk("corner_logo", removal=0.20)]
f, n, s = ep.evaluate_gate(agg, None)
print("  fails:", f)

print("T3 collateral ABOVE redline (1.50>1.40) -> expect fail")
agg = [mk("corner_logo", collateral=1.50)]
f, n, s = ep.evaluate_gate(agg, None)
print("  fails:", f)

print("T4 overdraw ABOVE redline (1.30>1.16) -> expect fail")
agg = [mk("corner_logo", overdraw=1.30)]
f, n, s = ep.evaluate_gate(agg, None)
print("  fails:", f)

print("T5 spill ABOVE redline (0.06>0.05) -> expect fail")
agg = [mk("corner_logo", spill=0.06)]
f, n, s = ep.evaluate_gate(agg, None)
print("  fails:", f)

print("T6 n=0 -> expect fail")
agg = [mk("corner_logo", n=0, n_total=3)]
f, n, s = ep.evaluate_gate(agg, None)
print("  fails:", f)

print("T7 missing case -> expect fail")
agg = [mk("nonexistent_case")]
f, n, s = ep.evaluate_gate(agg, None)
print("  fails:", f)

print("T8 baseline engine mismatch -> expect fail")
agg = [mk("corner_logo")]
baseline = {"engine": "opencv", "side": 640, "aggregate": []}
f, n, s = ep.evaluate_gate(agg, baseline)
print("  fails:", f)

print("T9 drift: removal DROPS below baseline-tol -> expect [drift] fail")
agg = [mk("corner_logo", removal=0.10)]  # baseline 0.30, tol=max(0.15*0.30,0)=0.045 -> 0.10<0.255 -> fail
baseline = {"engine": "lama", "side": 640,
            "aggregate": [{"case": "corner_logo", "n": 3, "removal": 0.30, "collateral": 1.30, "overdraw": 1.10, "spill": 0.03}]}
f, n, s = ep.evaluate_gate(agg, baseline)
print("  fails:", f)
