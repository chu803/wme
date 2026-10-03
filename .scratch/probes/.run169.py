# -*- coding: utf-8 -*-
"""Stage 169：以编程方式跑守卫测试并把结果写成 ASCII 文件。"""
import io
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import pytest

args = [
    "tests/test_report_service_i18n.py",
    "tests/test_i18n.py",
    "tests/test_i18n_coverage.py",
    "tests/test_i18n_audit137.py",
    "-p", "no:cacheprovider",
    "--basetemp=.pytest_tmp169",
    "-o", "addopts=",
    "-q", "--tb=short",
]

buf = io.StringIO()
_old = sys.stdout, sys.stderr
sys.stdout = sys.stderr = buf
try:
    code = pytest.main(args)
finally:
    sys.stdout, sys.stderr = _old

out = buf.getvalue()
ascii_out = out.encode("ascii", "replace").decode("ascii")
with open(".pytest169.result.txt", "w", encoding="utf-8") as f:
    f.write(f"EXIT={code}\n")
    f.write(ascii_out)
    f.write("\n")
print(f"EXIT={code}")
