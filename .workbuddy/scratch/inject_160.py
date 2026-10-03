# -*- coding: utf-8 -*-
"""Stage 160 注入式反例：还原校验不『自比』。

把 make_real_compare_crops 的取景夹紧（eff_view = max(view, pad)）+ 告警替换回
`eff_view = args.view`（缺陷版：view<pad 时 crop 落在 mask 内、且无告警），跑守卫应红；
再逐字节还原应绿。系统 temp + retention=all（零删除）。
"""
import hashlib
import os
import subprocess
import sys
import tempfile as _tf
import uuid

PATH = "D:/work 111/2026-08-03-17-38-51/scripts/make_real_compare_crops.py"
PY = "C:/Users/HP/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
TESTS = ["D:/work 111/2026-08-03-17-38-51/tests/test_make_real_compare_crops.py"]


def bt() -> str:
    return os.path.join(_tf.gettempdir(), f"wme160_{uuid.uuid4().hex[:8]}")


FIXED = '''    # 阶段 160：取景必须**完整覆盖被修复区域**（mask = bbox ± pad）。
    # 旧实现 crop = bbox ± view 且 view/pad 相互独立；当 view < pad（而 --pad 帮助文本
    # 又写着「掩码与取景同步」，易诱导）时，crop 会**整幅落在 mask 内** ⇒ 对比图里既没有
    # 水印也没有上下文，肉眼复核被静默误导，而退出码仍是 0。探针实测：pad=40,view=10 时
    # crop 内落在 mask 的列比例 = 1.00（正常应 < 1，两翼各留原图上下文）。
    # 现强制 eff_view = max(view, pad)，保证 crop ⊇ mask；被夹紧时显式告警。
    eff_view = max(args.view, args.pad)
    if args.view < args.pad:
        print(f"[warn] --view({args.view}) < --pad({args.pad})：取景已夹紧到 {eff_view}px，"
              f"以完整覆盖被修复区域（否则对比图看不到水印与上下文）。", file=sys.stderr)
'''

BROKEN = '''    eff_view = args.view
'''


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
    assert FIXED in fixed_content, "FIXED 锚点未找到"

    broken_content = fixed_content.replace(FIXED, BROKEN, 1)
    assert broken_content != fixed_content
    open(PATH, "w", encoding="utf-8").write(broken_content)
    print("[inject] 已注入缺陷版本（eff_view = args.view，无夹紧、无告警）")

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
