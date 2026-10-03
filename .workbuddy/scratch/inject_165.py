# -*- coding: utf-8 -*-
"""Stage 165 注入式反例（按 #188：newline='' 读写 + 原始字节 sha + 行尾感知锚点）。

分别回退两处修复，各跑一次守卫应红；两次都还原后应绿。
"""
import hashlib
import os
import subprocess
import sys
import tempfile as _tf
import uuid

PATH = "D:/work 111/2026-08-03-17-38-51/core/model_manager.py"
PY = "C:/Users/HP/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
TESTS = ["D:/work 111/2026-08-03-17-38-51/tests/test_model_manager_download.py"]


def bt() -> str:
    return os.path.join(_tf.gettempdir(), f"wme165_{uuid.uuid4().hex[:8]}")


def raw_sha() -> str:
    return hashlib.sha256(open(PATH, "rb").read()).hexdigest()


def run() -> tuple:
    cmd = [PY, "-m", "pytest", *TESTS, "-q", f"--basetemp={bt()}",
           "-o", "tmp_path_retention_policy=all", "-p", "no:cacheprovider"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    fails = [l.split("::")[-1].split()[0] for l in p.stdout.splitlines() if l.startswith("FAILED")]
    return p.returncode, fails


def main():
    text = open(PATH, "r", encoding="utf-8", newline="").read()
    orig_sha = raw_sha()
    nl = "\r\n" if "\r\n" in text else "\n"
    print(f"[inject] 行尾={'CRLF' if nl == chr(13)+chr(10) else 'LF'}  orig raw sha={orig_sha[:16]}…")

    FIX_A = nl.join([
        "    urls = list(slot.urls)",
        "    if not urls:",
        "        raise ModelNotFoundError(",
        '            f"模型槽 {slot.name} 未提供下载地址（urls 为空）：请手动放入权重文件，"',
        '            f"或在 config.MODEL_SLOTS 中为该槽登记 urls。"',
        "        )",
    ])
    BK_A = "    urls = list(slot.urls) if slot.urls else list(config.MODEL_DOWNLOAD_URLS)"
    FIX_B = nl.join([
        "        _CACHE.pop(dest, None)",
        '        log.info("模型 %s 下载并校验成功：%s", slot.name, path)',
        "        return path",
    ])
    BK_B = nl.join([
        '        log.info("模型 %s 下载并校验成功：%s", slot.name, path)',
        "        return path",
    ])
    assert FIX_A in text and FIX_B in text, "锚点未命中"

    def restore():
        open(PATH, "w", encoding="utf-8", newline="").write(text)

    # 注入 A（无 urls 回退全局源）
    open(PATH, "w", encoding="utf-8", newline="").write(text.replace(FIX_A, BK_A, 1))
    rcA, failsA = run()
    print(f"[inject] 回退修复①(无 urls 回退全局源) → rc={rcA} 失败={failsA}")
    assert raw_sha() != orig_sha
    restore()
    assert raw_sha() == orig_sha, "还原 A 后字节不一致"

    # 注入 B（下载成功不清缓存）
    open(PATH, "w", encoding="utf-8", newline="").write(text.replace(FIX_B, BK_B, 1))
    rcB, failsB = run()
    print(f"[inject] 回退修复②(下载成功不清缓存) → rc={rcB} 失败={failsB}")
    restore()
    assert raw_sha() == orig_sha, "还原 B 后字节不一致"
    print("[inject] 两次还原均字节级一致 ✓")

    rcG, failsG = run()
    print(f"[inject] 还原后 rc={rcG} 失败={failsG}")

    ok = (rcA != 0) and (rcB != 0) and (rcG == 0) and (raw_sha() == orig_sha)
    print("\n[inject] ===== 结论 =====")
    print(f"  回退修复① → 守卫红 : {'OK' if rcA != 0 else 'FAIL'}  {failsA}")
    print(f"  回退修复② → 守卫红 : {'OK' if rcB != 0 else 'FAIL'}  {failsB}")
    print(f"  还原后守卫绿       : {'OK' if rcG == 0 else 'FAIL'}")
    print(f"  守卫有牙           : {'YES' if ok else 'NO'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
