# -*- coding: utf-8 -*-
"""Stage 164 注入式反例（按经验 #188 写：读写均 newline=''，sha 对原始字节计算）。

把 tab_batch 的画廊说明改回硬编码中文（缺陷版），源码级 i18n 守卫应红；逐字节还原应绿。
"""
import hashlib
import os
import subprocess
import sys
import tempfile as _tf
import uuid

PATH = "D:/work 111/2026-08-03-17-38-51/ui/tab_batch.py"
PY = "C:/Users/HP/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
TESTS = ["D:/work 111/2026-08-03-17-38-51/tests/test_tab_batch_helpers.py"]


def bt() -> str:
    return os.path.join(_tf.gettempdir(), f"wme164_{uuid.uuid4().hex[:8]}")


def raw_sha() -> str:
    return hashlib.sha256(open(PATH, "rb").read()).hexdigest()


def run_tests() -> int:
    cmd = [PY, "-m", "pytest", *TESTS, "-q", f"--basetemp={bt()}",
           "-o", "tmp_path_retention_policy=all", "-p", "no:cacheprovider"]
    return subprocess.run(cmd, capture_output=True, text=True).returncode


def main():
    # 读写一律 newline=''，保留文件原有行尾（该文件为 CRLF）
    text = open(PATH, "r", encoding="utf-8", newline="").read()
    orig_sha = raw_sha()
    nl = "\r\n" if "\r\n" in text else "\n"
    print(f"[inject] 文件行尾 = {'CRLF' if nl == chr(13)+chr(10) else 'LF'}  orig raw sha = {orig_sha[:16]}…")

    FIXED = nl.join([
        '                        caption = t("batch.caption_quality", name=name,',
        '                                    score=q.score, icon=icon)',
    ])
    BROKEN = nl.join([
        '                        caption = f"{name} · 质量{q.score}分{icon}"',
    ])
    assert FIXED in text, "FIXED 锚点未命中"

    broken = text.replace(FIXED, BROKEN, 1)
    assert broken != text
    open(PATH, "w", encoding="utf-8", newline="").write(broken)
    print("[inject] 已注入缺陷版本（硬编码中文画廊说明）")

    rc_red = run_tests()
    print(f"[inject] 注入后 pytest 退出码 = {rc_red}（期望非 0 → 红）")

    open(PATH, "w", encoding="utf-8", newline="").write(text)   # 逐字节还原
    restored_sha = raw_sha()
    print(f"[inject] 还原后 raw sha = {restored_sha[:16]}…")
    assert restored_sha == orig_sha, "还原后原始字节不一致"
    print("[inject] 还原字节级一致 ✓（原始字节 sha 比对，非文本 sha 自比）")

    rc_green = run_tests()
    print(f"[inject] 还原后 pytest 退出码 = {rc_green}（期望 0 → 绿）")

    ok = (rc_red != 0) and (rc_green == 0) and (restored_sha == orig_sha)
    print("\n[inject] ===== 结论 =====")
    print(f"  注入缺陷 → 守卫变红 : {'OK' if rc_red != 0 else 'FAIL'}")
    print(f"  还原字节级一致      : {'OK' if restored_sha == orig_sha else 'FAIL'}")
    print(f"  还原后守卫变绿      : {'OK' if rc_green == 0 else 'FAIL'}")
    print(f"  守卫有牙            : {'YES' if ok else 'NO'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
