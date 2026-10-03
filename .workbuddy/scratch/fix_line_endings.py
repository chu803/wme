# -*- coding: utf-8 -*-
"""修复阶段 158-163 注入脚本造成的行尾回归：文本模式写回把 LF 规范成了 CRLF
（LESSONS #127 明确警告过，本轮违规）。把 6 个被注入文件转回 LF——转换后其
原始字节 sha256 应当**恰好等于**各阶段文档记录的 sha（它们本是 LF 归一化文本 sha），
以此作为「恢复到签收状态」的硬校验。"""
import hashlib

ROOT = "D:/work 111/2026-08-03-17-38-51/"
EXPECTED = {
    "scripts/collect_real_assets.py": "16238c60cfc371cc",
    "scripts/validate_selector.py": "076ba405986ae35e",
    "scripts/make_real_compare_crops.py": "60e882cf2bab7fc3",
    "services/queue_worker.py": "24478f059213dec7",
    "ui/tab_models.py": "d8a0cc94fd13f6c2",
    "ui/tab_auto.py": "2294337a9ea0b2c1",
}

ok = True
for rel, want in EXPECTED.items():
    p = ROOT + rel
    raw = open(p, "rb").read()
    lf = raw.replace(b"\r\n", b"\n")
    got = hashlib.sha256(lf).hexdigest()
    match = got.startswith(want)
    ok &= match
    if raw != lf:
        open(p, "wb").write(lf)
    print(f"{rel:42} 转LF后 sha={got[:16]} 期望={want}  {'OK' if match else 'MISMATCH'}")

# 复核：磁盘上现在应无 CRLF，且原始字节 sha == 记录 sha
print()
for rel, want in EXPECTED.items():
    b = open(ROOT + rel, "rb").read()
    crlf = b.count(b"\r\n")
    got = hashlib.sha256(b).hexdigest()
    good = (crlf == 0) and got.startswith(want)
    ok &= good
    print(f"{rel:42} 复核 CRLF={crlf}  sha={got[:16]}  {'OK' if good else 'FAIL'}")

print("\n结论:", "全部恢复到各阶段签收的字节状态" if ok else "存在未恢复项，需人工核查")
