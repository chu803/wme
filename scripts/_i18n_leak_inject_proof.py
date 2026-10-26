"""i18n 跨进程泄漏修复的注入式反证（证明守卫能捕获「修复缺失」）。

做法：设 `WME_I18N_NO_REDIRECT=1`（conftest 跳过重定向，复现修复前状态），
再跑 `tests/test_i18n_state_leak_guard.py`。此时子进程会读进被投毒的真实
`cache/i18n_state.json`（en-US）⇒ 守卫必 FAIL（RC!=0）。

若本脚本断言「RC!=0」成立 ⇒ 守卫确实能捕获修复缺失，证明它是有效防线，而非恒绿假守卫。
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = dict(os.environ)
ENV["WME_I18N_NO_REDIRECT"] = "1"

proc = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_i18n_state_leak_guard.py", "-q"],
    cwd=REPO,
    env=ENV,
    capture_output=True,
    text=True, encoding="utf-8", errors="replace",
    timeout=300,
)
print(proc.stdout)
print(proc.stderr, end="")
assert proc.returncode != 0, (
    "反证失败：WME_I18N_NO_REDIRECT=1 下守卫仍绿 ⇒ 守卫无法捕获修复缺失（假守卫）"
)
print("[INJECT] 反证成功：修复缺失时守卫 FAIL（RC=%d），证明它是有效防线。" % proc.returncode)
sys.exit(0)
