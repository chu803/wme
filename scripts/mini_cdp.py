# -*- coding: utf-8 -*-
"""最小 CDP 客户端（纯标准库 WebSocket）——驱动 Chrome 无头实例做真实 UI 操作与截图。

用途：把"用户会看到什么"截成真图（首屏 / 自动检测页 / 检测结果 / 修复结果），
并支持真鼠标事件（切页签、点按钮）与真文件上传。

只依赖标准库：握手 + 帧编解码（客户端帧必须掩码）。

用法（先起界面）：
    python scripts/capture_ui_screenshots.py
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request


class WS:
    """够用的 WebSocket 客户端（文本/二进制帧，客户端掩码）。"""

    def __init__(self, url: str, timeout: float = 60.0):
        assert url.startswith("ws://")
        rest = url[5:]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("握手失败")
            buf += chunk
        if b"101" not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"握手被拒：{buf[:120]!r}")
        self._buf = buf.split(b"\r\n\r\n", 1)[1]

    # ---- 帧 ----
    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("连接关闭")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        head = bytearray([0x81])
        n = len(payload)
        if n < 126:
            head.append(0x80 | n)
        elif n < (1 << 16):
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(head) + masked)

    def recv(self) -> str:
        while True:
            b0, b1 = self._recv_exact(2)
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            masked = b1 & 0x80
            mask = self._recv_exact(4) if masked else b""
            data = self._recv_exact(length)
            if masked:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                raise RuntimeError("对端关闭")
            if opcode in (0x1, 0x2):
                return data.decode("utf-8", "replace")
            # ping/pong/续帧：忽略并继续

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


class CDP:
    def __init__(self, ws_url: str):
        self.ws = WS(ws_url)
        self._id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def eval(self, expr: str):
        r = self.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True})
        res = r.get("result", {})
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"])[:300])
        return res.get("value")

    def shot(self, path: str, full: bool = False) -> str:
        params = {"format": "png"}
        if full:
            params["captureBeyondViewport"] = True
        data = self.call("Page.captureScreenshot", params, timeout=120)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    def click_xy(self, x: float, y: float) -> None:
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", {
                "type": kind, "x": x, "y": y, "button": "left",
                "clickCount": 1 if kind == "mousePressed" else 0,
                "buttons": 1 if kind == "mousePressed" else 0,
            })

    def close(self) -> None:
        self.ws.close()


def launch_chrome(port: int = 9333, width: int = 1440, height: int = 1800) -> subprocess.Popen:
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    profile = os.path.join(os.environ.get("TEMP", "."), "wme_cdp_profile")
    os.makedirs(profile, exist_ok=True)
    args = [
        chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        f"--window-size={width},{height}", "about:blank",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_target(port: int = 9333, timeout: float = 30.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                targets = json.loads(r.read().decode("utf-8"))
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    raise TimeoutError("Chrome 调试端口没起来")


if __name__ == "__main__":
    proc = launch_chrome()
    try:
        url = wait_target()
        cdp = CDP(url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:7860/"})
        time.sleep(12)
        print("标题：", cdp.eval("document.title"))
        print("截图：", cdp.shot(r"D:\qoder\watermark-workbench\.scratch\ui_cdp_home.png"))
        cdp.close()
    finally:
        proc.terminate()
