# -*- coding: utf-8 -*-
"""Stage 162 注入式反例：还原校验不『自比』。

把 tab_models._render_status_md 的分隔符改回硬编码全角「｜」（缺陷版），跑守卫应红；
再逐字节还原应绿。系统 temp + retention=all（零删除）。
"""
import hashlib
import os
import subprocess
import sys
import tempfile as _tf
import uuid

PATH = "D:/work 111/2026-08-03-17-38-51/ui/tab_models.py"
PY = "C:/Users/HP/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
TESTS = ["D:/work 111/2026-08-03-17-38-51/tests/test_tab_models_i18n.py"]


def bt() -> str:
    return os.path.join(_tf.gettempdir(), f"wme162_{uuid.uuid4().hex[:8]}")


FIXED = '''        # 阶段 162：分隔符必须**留在本地化模板内**（与 slot_line 的 zh「｜」/ en「|」一致），
        # 不得在 UI 代码里硬编码全角「｜」——否则 en-US 下仍显示全角分隔符（i18n 泄漏），
        # 且项目既有的 CJK-only i18n 守卫（只扫 U+4E00–U+9FFF）**结构上看不到**这类泄漏。
        t("models.status_device_line", line=dev_line, label=dev_label),'''

BROKEN = '''        f"- {dev_line} ｜ {dev_label}",'''


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
    print("[inject] 已注入缺陷版本（硬编码全角分隔符）")

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
