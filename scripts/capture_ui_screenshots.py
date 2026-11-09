# -*- coding: utf-8 -*-
"""真实界面捕获：自动检测页 + 上传 + 检测 + 修复，逐步截图（给用户看"实际长什么样"）。

用 `scripts/mini_cdp.py`（纯标准库的最小 CDP 客户端）驱动 Chrome 无头实例：
真鼠标事件、真文件上传、真点按钮 ⇒ 截图反映的是真实交互，不是拼出来的示意图。

前置：先起界面 `python main.py --no-download`（默认 7860 端口）。
用法：`python scripts/capture_ui_screenshots.py`（截图落在 docs/assets/ui/）。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from mini_cdp import CDP, launch_chrome, wait_target  # noqa: E402

OUT = ROOT / "docs" / "assets" / "ui"
OUT.mkdir(parents=True, exist_ok=True)
IMG = ROOT / "benchmark_real" / "images" / "ai_logo_02_snow_mountain_lake.jpg"


def find_by_text(cdp: CDP, text: str, tag: str = "*"):
    """返回页面上文字等于 text 的元素的中心坐标（真实鼠标事件用）。"""
    expr = f"""
    (() => {{
      const nodes = Array.from(document.querySelectorAll('{tag}'));
      const exact = nodes.filter(n => (n.textContent || '').trim() === {json.dumps(text)});
      const all = exact.length ? exact : nodes.filter(
        n => (n.textContent || '').includes({json.dumps(text)}));
      const cands = all.filter(n => {{
        const r = n.getBoundingClientRect();
        return n.offsetParent !== null && r.width > 4 && r.height > 4;
      }});
      // 取**面积最大**的那个可见节点（避免点到内层 span）
      let best = null, bestArea = 0;
      for (const n of cands) {{
        const r = n.getBoundingClientRect();
        const area = r.width * r.height;
        if (area > bestArea) {{ best = n; bestArea = area; }}
      }}
      if (!best) return null;
      best.scrollIntoView({{block: 'center', inline: 'center'}});
      const r = best.getBoundingClientRect();
      return {{x: r.left + r.width / 2, y: r.top + r.height / 2,
               w: r.width, h: r.height, tag: best.tagName}};
    }})()
    """
    return cdp.eval(expr)


def upload_first_file(cdp: CDP, path: pathlib.Path) -> bool:
    """把文件塞进页面里第一个可见的 file input（触发 Gradio 的上传）。"""
    # 只找**可见**面板里的那个 input（页签切换后 inactive 面板是隐藏的）
    node_id = cdp.eval("""
    (() => {
      const ins = Array.from(document.querySelectorAll('input[type=file]'));
      // Gradio 的原生 file input 永远是隐藏的 ⇒ 用"所在面板是否被隐藏"来区分页签
      const inHiddenPanel = (el) => {
        let p = el.parentElement;
        for (let i = 0; i < 12 && p; i++, p = p.parentElement) {
          const r = p.getBoundingClientRect();
          if (r.height === 0 && r.width === 0) return true;
          if (p.style && p.style.display === 'none') return true;
        }
        return false;
      };
      const live = ins.filter(i => !inHiddenPanel(i));
      const node = live[0] || ins[ins.length - 1];
      if (!node) return null;
      node.setAttribute('data-wme-target', '1');
      return 'ok';
    })()
    """)
    doc = cdp.call("DOM.getDocument")
    root = doc["root"]["nodeId"]
    q = cdp.call("DOM.querySelector", {"nodeId": root, "selector": "input[data-wme-target]"})
    if not q.get("nodeId"):
        return False
    cdp.call("DOM.setFileInputFiles", {
        "files": [str(path)], "nodeId": q["nodeId"]})
    return True


def status_text(cdp: CDP) -> str:
    return cdp.eval("""
    (() => {
      const ta = Array.from(document.querySelectorAll('textarea'));
      for (const t of ta) { if ((t.value || '').trim().length > 3) return t.value.trim(); }
      return '';
    })()
    """) or ""


def main() -> None:
    proc = launch_chrome(port=9334)
    try:
        cdp = CDP(wait_target(port=9334))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("DOM.enable")
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:7860/"})
        time.sleep(14)
        print("① 首屏：", cdp.shot(str(OUT / "01-首屏-单图处理.png")))

        # 切到「智能自动检测」页签
        pos = find_by_text(cdp, "智能自动检测")
        print("   页签坐标：", pos)
        if pos:
            cdp.click_xy(pos["x"], pos["y"])
            time.sleep(3)
        print("② 自动检测页：", cdp.shot(str(OUT / "02-智能自动检测页.png")))

        # 上传一张真实角标图
        print("   上传：", IMG.name, upload_first_file(cdp, IMG))
        time.sleep(8)
        print("③ 上传后：", cdp.shot(str(OUT / "03-已上传待检测.png")))

        # 点「② 自动检测水印」
        pos = find_by_text(cdp, "② 自动检测水印")
        if pos:
            time.sleep(1.5)
            pos = find_by_text(cdp, "② 自动检测水印")
            cdp.click_xy(pos["x"], pos["y"])
            print("   已点自动检测")
        time.sleep(30)
        print("   状态：", status_text(cdp)[:200])
        print("④ 检测结果：", cdp.shot(str(OUT / "04-检测结果.png")))

        # 点「④ 一键修复」
        pos = find_by_text(cdp, "④ 一键修复")
        if pos:
            time.sleep(1.5)
            pos = find_by_text(cdp, "④ 一键修复")
            cdp.click_xy(pos["x"], pos["y"])
            print("   已点一键修复")
        time.sleep(35)
        print("   状态：", status_text(cdp)[:300])
        cdp.eval("window.scrollTo(0, 0)")
        time.sleep(2)
        print("⑤ 修复结果：", cdp.shot(str(OUT / "05-修复结果.png")))
        cdp.eval("window.scrollTo(0, 0)")
        time.sleep(1)
        print("⑥ 整页：", cdp.shot(str(OUT / "06-整页.png"), full=True))
        cdp.close()
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
