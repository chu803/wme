# -*- coding: utf-8 -*-
"""Stage 161 注入式反例：还原校验不『自比』。

把 BatchQueue.__init__ 的参数处理替换回 `or` 版本（缺陷版：显式 0 被静默吞掉 +
retry 无下界校验），跑守卫应红；再逐字节还原应绿。系统 temp + retention=all（零删除）。
"""
import hashlib
import os
import subprocess
import sys
import tempfile as _tf
import uuid

PATH = "D:/work 111/2026-08-03-17-38-51/services/queue_worker.py"
PY = "C:/Users/HP/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
TESTS = ["D:/work 111/2026-08-03-17-38-51/tests/test_queue_worker_contract.py"]


def bt() -> str:
    return os.path.join(_tf.gettempdir(), f"wme161_{uuid.uuid4().hex[:8]}")


FIXED = '''        # 阶段 161：四个参数统一用 `is not None` 判定，不再用 `or`——`or` 会把调用方
        # **显式传入的 0 静默吞掉**、回退到 config（「静默覆盖调用方意图」）。此前只有
        # `retry` 用了正确写法，是同一风险面的两种写法（家族：同值不同源 / 兜底绕过）。
        # 改为：缺省（None）才取 config；显式非法值直接抛 ValueError（**显式失败**优于
        # 「静默用别的值」或「交给下游崩溃 / 卡在 RUNNING」）。
        self.max_concurrency = (max_concurrency if max_concurrency is not None
                                else config.BATCH_CONCURRENCY)
        self.max_size = max_size if max_size is not None else config.QUEUE_MAX_SIZE
        self.retry = retry if retry is not None else config.QUEUE_TASK_RETRY
        self.timeout = timeout if timeout is not None else config.QUEUE_TASK_TIMEOUT

        # 阶段 161：下界校验。`retry < 0` 会让 `attempts = retry + 1 <= 0` ⇒ `for` 循环
        # 一次都不执行 ⇒ 任务**永远停在 RUNNING**（既不 DONE/FAILED/CANCELLED），
        # 破坏 run() 的终态不变量；`max_concurrency < 1` 会让 ThreadPoolExecutor 抛
        # ValueError；`max_size < 1` 则使队列无法容纳任何任务。均在构造期显式拒绝。
        if self.max_concurrency < 1:
            raise ValueError(f"max_concurrency 必须 ≥ 1，得到 {self.max_concurrency}")
        if self.max_size < 1:
            raise ValueError(f"max_size 必须 ≥ 1，得到 {self.max_size}")
        if self.retry < 0:
            raise ValueError(f"retry 必须 ≥ 0，得到 {self.retry}")
'''

BROKEN = '''        self.max_concurrency = max_concurrency or config.BATCH_CONCURRENCY
        self.max_size = max_size or config.QUEUE_MAX_SIZE
        self.retry = retry if retry is not None else config.QUEUE_TASK_RETRY
        self.timeout = timeout or config.QUEUE_TASK_TIMEOUT
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
    print("[inject] 已注入缺陷版本（or 判定 + 无下界校验）")

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
