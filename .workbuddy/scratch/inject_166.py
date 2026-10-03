# -*- coding: utf-8 -*-
"""Stage 166 注入式反例（#188 模式：newline='' + rb sha + 行尾感知锚点）。

移除 generate_pdf 的 records_per_page 下界校验（缺陷版）→ 守卫应红；逐字节还原应绿。
"""
import hashlib
import os
import subprocess
import sys
import tempfile as _tf
import uuid

PATH = "D:/work 111/2026-08-03-17-38-51/services/report_service.py"
PY = "C:/Users/HP/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
TESTS = ["D:/work 111/2026-08-03-17-38-51/tests/test_report_service_contract.py"]


def bt() -> str:
    return os.path.join(_tf.gettempdir(), f"wme166_{uuid.uuid4().hex[:8]}")


def raw_sha() -> str:
    return hashlib.sha256(open(PATH, "rb").read()).hexdigest()


def run() -> int:
    cmd = [PY, "-m", "pytest", *TESTS, "-q", f"--basetemp={bt()}",
           "-o", "tmp_path_retention_policy=all", "-p", "no:cacheprovider"]
    return subprocess.run(cmd, capture_output=True, text=True).returncode


def main():
    text = open(PATH, "r", encoding="utf-8", newline="").read()
    orig_sha = raw_sha()
    nl = "\r\n" if "\r\n" in text else "\n"
    print(f"[inject] 行尾={'CRLF' if nl == chr(13)+chr(10) else 'LF'}  orig raw sha={orig_sha[:16]}…")

    FIXED = nl.join([
        "    # 阶段 166：每页条数须 ≥ 1。此前 `per_page <= 0` 会让分页判据 `placed_on_page >= per_page`",
        "    # **恒真** ⇒ 分页静默退化成「一图一页」（负值同样）；数量类参数缺下界即显式拒绝",
        "    # （与阶段 161 queue_worker 的 retry 下界校验同族）。",
        "    if per_page < 1:",
        '        raise ValueError(f"records_per_page 必须 ≥ 1，得到 {per_page}")',
    ])
    assert FIXED in text, "FIXED 锚点未命中"

    open(PATH, "w", encoding="utf-8", newline="").write(text.replace(FIXED, "", 1))
    rc_red = run()
    print(f"[inject] 移除下界校验 → rc={rc_red}（期望非 0）")

    open(PATH, "w", encoding="utf-8", newline="").write(text)
    assert raw_sha() == orig_sha, "还原后字节不一致"
    rc_green = run()
    print(f"[inject] 还原（raw sha {raw_sha()[:16]}… 一致）→ rc={rc_green}（期望 0）")

    ok = (rc_red != 0) and (rc_green == 0) and (raw_sha() == orig_sha)
    print(f"\n[inject] 守卫有牙：{'YES' if ok else 'NO'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
