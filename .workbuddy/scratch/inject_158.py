"""Stage 158 注入式反例：还原校验不『自比』。

流程：
1. 记录修复后文件的外部 sha256（fixed_sha）。
2. 把 cmd_download 的核心保护块替换为『先下载覆盖磁盘、再 _upsert、无条件 return 0』的
   缺陷版本（精确复现阶段 158 抓到的 P1 静默数据损坏）。
3. 跑守卫 → 必须变红（核心守卫 test_download_path_collision_rejected_not_overwritten 失败）。
4. 用注入前记录的 fixed_content 逐字节还原 → 校验 sha256 == fixed_sha（字节级一致）。
5. 再跑守卫 → 必须变绿（18 例全过）。

守卫有牙：移除保护即红，还原即绿。
"""
import hashlib
import os
import subprocess
import sys
import tempfile as _tf
import uuid

PATH = "D:/work 111/2026-08-03-17-38-51/scripts/collect_real_assets.py"
PY = "C:/Users/HP/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
TESTS = [
    "D:/work 111/2026-08-03-17-38-51/tests/test_collect_real_assets_158.py",
    "D:/work 111/2026-08-03-17-38-51/tests/test_collect_real_assets.py",
]


def bt() -> str:
    # 放到系统 temp 下、且每次换新路径，并用 tmp_path_retention_policy=all 关掉会话结束删除；
    # 整个过程零删除，规避沙箱对「项目根下批量删 >50 文件」的拦截。
    return os.path.join(_tf.gettempdir(), f"wme158_{uuid.uuid4().hex[:8]}")

FIXED = '''    manifest = _load_manifest()
    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(fn)[1])
        tmp_path = tmp.name
        tmp.close()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=args.timeout) as r:
            with open(tmp_path, "wb") as f:
                f.write(r.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(f"[error] 下载失败：{e}", file=sys.stderr)
        return 2
    entry = _build_entry(rel, ai_source=args.ai_source, style=args.style,
                         source="url", abs_src=tmp_path)
    status = _upsert(manifest, entry)
    if status == "accepted":
        os.makedirs(IMAGES_DIR, exist_ok=True)
        shutil.move(tmp_path, os.path.join(IMAGES_DIR, fn))
        _save_manifest(manifest)
        print(f"[add] {entry['id']}  {rel}  ← {url}")
        return 0
    # 未接受：绝不覆盖磁盘上已登记的图。
    if tmp_path and os.path.exists(tmp_path):
        os.remove(tmp_path)
    # skipped=同图已存在（幂等，视为成功）；rejected=路径冲突（视为失败，非 0）。
    return 0 if status == "skipped" else 3'''

# 缺陷版本：先下载覆盖磁盘（images/<fn>），再 _upsert，无条件 return 0 —— 精确复现 P1。
BROKEN = '''    manifest = _load_manifest()
    dst = os.path.join(IMAGES_DIR, fn)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=args.timeout) as r, open(dst, "wb") as f:
            f.write(r.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[error] 下载失败：{e}", file=sys.stderr)
        return 2
    rel = f"images/{fn}"
    entry = _build_entry(rel, ai_source=args.ai_source, style=args.style, source="url")
    if _upsert(manifest, entry) == "accepted":
        _save_manifest(manifest)
        print(f"[add] {entry['id']}  {rel}  ← {url}")
    return 0'''


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_tests() -> int:
    cmd = [PY, "-m", "pytest", *TESTS, "-q",
           f"--basetemp={bt()}", "-o", "tmp_path_retention_policy=all",
           "-p", "no:cacheprovider"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        fixed_content = f.read()
    fixed_sha = sha_of(fixed_content)
    print(f"[inject] fixed_sha = {fixed_sha[:16]}…")

    if FIXED not in fixed_content:
        print("[inject] ERROR: FIXED 锚点未在文件中找到，注入中止")
        sys.exit(2)

    # 1) 注入缺陷版本
    broken_content = fixed_content.replace(FIXED, BROKEN, 1)
    assert broken_content != fixed_content, "替换未发生"
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(broken_content)
    print("[inject] 已注入缺陷版本（先覆盖磁盘、无条件 return 0）")

    # 2) 跑守卫 → 必须红
    rc_red = run_tests()
    print(f"[inject] 注入后 pytest 退出码 = {rc_red}（期望非 0 → 红）")

    # 3) 逐字节还原
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(fixed_content)
    restored_sha = sha_of(open(PATH, "r", encoding="utf-8").read())
    print(f"[inject] 还原后 sha = {restored_sha[:16]}…")
    assert restored_sha == fixed_sha, "还原后字节不一致！注入式反例失败"
    print("[inject] 还原字节级一致 ✓")

    # 4) 再跑 → 必须绿
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
