import sys

out = []
for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
    enc = getattr(stream, "encoding", None)
    enc = enc or "?"
    try:
        "✅❌⚠".encode(enc)
        able = "OK"
    except Exception as e:  # noqa: BLE001
        able = "FAIL:" + type(e).__name__
    out.append(f"{name}: encoding={enc} print_emoji={able}")
with open(".enc_result.txt", "w", encoding="ascii", errors="replace") as f:
    f.write("\n".join(out) + "\n")
