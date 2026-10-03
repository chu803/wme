"""Stage 159 注入式反例：还原校验不『自比』。

把 validate_selector.main() 的一致性判定块替换回「平局倒向 lama、且计入一致性」的
缺陷版本（复现阶段 159 抓到的「退化样本被读成一致」），跑守卫应红；再逐字节还原应绿。
沙箱禁「项目根批量删 >50 文件」，故 basetemp 放系统 temp + retention=all（零删除）。
"""
import hashlib
import os
import subprocess
import sys
import tempfile as _tf
import uuid

PATH = "D:/work 111/2026-08-03-17-38-51/scripts/validate_selector.py"
PY = "C:/Users/HP/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
TESTS = ["D:/work 111/2026-08-03-17-38-51/tests/test_validate_selector.py"]


def bt() -> str:
    return os.path.join(_tf.gettempdir(), f"wme159_{uuid.uuid4().hex[:8]}")


FIXED = '''    agree = 0
    total = 0
    ties = 0
    invalid = 0
    print("\\n" + "=" * 96)
    print("无参考指标 vs ground-truth ΔPSNR 一致性校验")
    print("（★ 一致 / ✗ 相悖 / = 平局·退化 / — 数据缺失；平局与缺失均**不计入**一致性）")
    print("| 数据 | 用例 | OCV ΔPSNR | LaMa ΔPSNR | OCV分 | LaMa分 | 一致 |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        do = r.get("dpsnr_opencv")
        dl = r.get("dpsnr_lama")
        so = r.get("score_opencv")
        sl = r.get("score_lama")
        # 只有两引擎都有效才计入一致性。
        if do is None or dl is None or so is None or sl is None:
            flag = "—"
            invalid += 1
        elif do == dl or so == sl:
            # 阶段 159：退化（真值或指标无判别力，平局）。平局不携带方向信息，
            # 若计入一致性会凭空抬高一致率（极端：全平局 ⇒ 100% 却零信息），
            # 与「证明指标方向未被写反」的脚本目的相悖。故单列、不计入。
            flag = "="
            ties += 1
        else:
            gt = "opencv" if do > dl else "lama"
            mt = "opencv" if so > sl else "lama"
            ok = (gt == mt)
            flag = "★" if ok else "✗"
            if ok:
                agree += 1
            total += 1
        print(f"| {r['data']} | {r['case_cn']} | {do} | {dl} | {so} | {sl} | {flag} |")

    print("-" * 96)
    if total:
        print(f"一致性：{agree}/{total} = {agree / total * 100:.1f}%"
              f"（平局·退化 {ties} 例、数据缺失 {invalid} 例，未计入）")
    else:
        print(f"⚠ 无携带方向信息的有效样本（平局·退化 {ties} 例、数据缺失 {invalid} 例）"
              f"—— 无法评估一致性，切勿读成「一致」。")
    print("=" * 96)'''

BROKEN = '''    agree = 0
    total = 0
    print("\\n" + "=" * 96)
    print("无参考指标 vs ground-truth ΔPSNR 一致性校验（★ = 指标与真值选同一引擎）")
    print("| 数据 | 用例 | OCV ΔPSNR | LaMa ΔPSNR | OCV分 | LaMa分 | 一致 |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        do = r.get("dpsnr_opencv")
        dl = r.get("dpsnr_lama")
        so = r.get("score_opencv")
        sl = r.get("score_lama")
        # 只有两引擎都有效才计入一致性。
        if do is None or dl is None or so is None or sl is None:
            flag = "—"
        else:
            gt = "opencv" if do > dl else "lama"
            mt = "opencv" if so > sl else "lama"
            ok = (gt == mt)
            flag = "★" if ok else "✗"
            if ok:
                agree += 1
            total += 1
        print(f"| {r['data']} | {r['case_cn']} | {do} | {dl} | {so} | {sl} | {flag} |")

    print("-" * 96)
    if total:
        print(f"一致性：{agree}/{total} = {agree / total * 100:.1f}%")
    print("=" * 96)'''


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_tests() -> int:
    cmd = [PY, "-m", "pytest", *TESTS, "-q", f"--basetemp={bt()}",
           "-o", "tmp_path_retention_policy=all", "-p", "no:cacheprovider"]
    return subprocess.run(cmd, capture_output=True, text=True).returncode


def main():
    fixed_content = open(PATH, "r", encoding="utf-8").read()
    fixed_sha = sha_of(fixed_content)
    print(f"[inject] fixed_sha = {fixed_sha[:16]}…")
    if FIXED not in fixed_content:
        print("[inject] ERROR: FIXED 锚点未找到")
        sys.exit(2)

    broken_content = fixed_content.replace(FIXED, BROKEN, 1)
    assert broken_content != fixed_content
    open(PATH, "w", encoding="utf-8").write(broken_content)
    print("[inject] 已注入缺陷版本（平局倒向 lama 且计入一致性）")

    rc_red = run_tests()
    print(f"[inject] 注入后 pytest 退出码 = {rc_red}（期望非 0 → 红）")

    open(PATH, "w", encoding="utf-8").write(fixed_content)
    restored_sha = sha_of(open(PATH, "r", encoding="utf-8").read())
    print(f"[inject] 还原后 sha = {restored_sha[:16]}…")
    assert restored_sha == fixed_sha, "还原后字节不一致"
    print("[inject] 还原字节级一致 ✓")

    rc_green = run_tests()
    print(f"[inject] 还原后 pytest 退出码 = {rc_green}（期望 0 → 绿）")

    ok = (rc_red != 0) and (rc_green == 0)
    print("\n[inject] ===== 结论 =====")
    print(f"  注入缺陷 → 守卫变红 : {'OK' if rc_red != 0 else 'FAIL'}")
    print(f"  还原字节级一致      : {'OK' if restored_sha == fixed_sha else 'FAIL'}")
    print(f"  还原后守卫变绿      : {'OK' if rc_green == 0 else 'FAIL'}")
    print(f"  守卫有牙            : {'YES' if ok else 'NO'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
