# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/app.py
 文件说明 : Gradio 应用骨架与启动入口。
            build_app()  负责组装界面（合规声明 + 多标签页布局）；
            launch_app() 负责按 config 启动本地网页服务（含端口自动探测、
            主题容错、异常捕获）。
            本文件是「界面层」的总装配点，被 main.py 的 launch_ui() 调用。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import json as _json
import os as _os
import socket as _socket
import sys as _sys

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import gradio as gr

import config
from services.file_service import ensure_dirs
from ui.components import render_scope_note, clear_superres_widgets
from ui.tab_single import build_single_tab
from ui.tab_auto import build_auto_tab
from ui.tab_batch import build_batch_tab
from ui.tab_media import build_media_tab
from ui.tab_history import build_history_tab
from ui.tab_settings import build_settings_tab
from ui.tab_models import build_models_tab
from ui.theme import (
    BRAND_THEME_NAME,
    build_app_css,
    build_app_theme,
    resolve_theme_name,
)
from utils import brand
from utils.logger import get_logger
from utils.i18n import t, t_in, load_language
from ui.i18n_rt import (
    apply_language,
    clear_registry,
    registered_components,
    current_language,
    register,
)

log = get_logger("ui.app")

# 主页签 elem_id —— 必须与 build_app() 中各 TabItem 的 elem_id 一一对应，
# 否则注入的 JS 找不到按钮、切换语言时标签标题不刷新（tests 有对账断言）。
_MAIN_TAB_IDS = ("single", "auto", "batch", "media", "history", "settings", "models")
# 媒体页子页签（elem_id = "media_" + 子页名）。
_MEDIA_SUB_TAB_IDS = ("pdf", "video")


def _compliance_brief() -> str:
    """
    生成界面顶部的精简版合规声明（完整版在控制台打印，见 main.py）。

    返回值：
        Markdown 字符串（随当前语言变化，注册进 i18n 运行时表以支持切换）。
    """
    return t("app.compliance")


def _on_lang(lang: str) -> list:
    """
    语言切换回调：刷新全部已注册组件，并给出切换反馈。

    参数说明：
        lang : 目标语言代码（zh-CN / en-US）。

    返回值：
        与 registered_components() 等长、同序的 [gr.update(...), ...]，
        末尾追加一条语言状态 Markdown 更新（作为事件 outputs 的一部分）。
    """
    updates = apply_language(lang)
    name = t("lang.zh") if lang == "zh-CN" else t("lang.en")
    applied = t("lang.applied", name=name)
    return updates + [gr.update(value=applied)]


def _tab_rename_map_json() -> str:
    """
    构建设标签标题映射（中文/英文）的 JSON 字符串，供注入 JS 在切换语言时
    重命名标签页按钮（Gradio 6 的 TabItem 不支持运行时 update 标题）。

    映射键即各 TabItem 的 elem_id；值为 {"zh-CN": ..., "en-US": ...}。

    说明：
        取译文统一走 `utils.i18n.t_in()`（与 `t()` 共用同一套「缺失即回退
        zh-CN、再回退键名」的守卫）。阶段 91 前此处直接下标
        `TRANSLATIONS[lang][...][tid]`——绕过了守卫，一旦键被改名会在
        build_app() 构建期抛 KeyError 使整个界面起不来，而不是降级。

    返回值：
        JSON 字符串。
    """
    data: dict = {}
    for tid in _MAIN_TAB_IDS:
        key = f"app.tab.{tid}"
        data[tid] = {
            "zh-CN": t_in("zh-CN", key),
            "en-US": t_in("en-US", key),
        }
    # 媒体页的两个子标签（PDF / 视频）。
    for sub in _MEDIA_SUB_TAB_IDS:
        key = f"media.{sub}.tab"
        data[f"media_{sub}"] = {
            "zh-CN": t_in("zh-CN", key),
            "en-US": t_in("en-US", key),
        }
    return _json.dumps(data, ensure_ascii=False)


def _build_lang_js() -> str:
    """
    构造语言切换事件的前端 JS：在 fn 执行前，按目标语言重命名所有标签页按钮
    （按钮 DOM id = elem_id + "-button"），并把语言代码透传给后端 fn。

    返回值：
        可直接传给 gr 事件 js= 参数的 JS 字符串。
    """
    tabmap = _tab_rename_map_json()
    return (
        "(lang) => {\n"
        "  const MAP = " + tabmap + ";\n"
        "  for (const id in MAP) {\n"
        "    const btn = document.getElementById(id + '-button');\n"
        "    if (btn) { btn.textContent = (MAP[id][lang] || btn.textContent); }\n"
        "  }\n"
        "  return lang;\n"
        "}"
    )


def _resolve_theme() -> str:
    """
    把 config.SERVER_THEME 解析为 Gradio 接受的主题标识。

    返回值：
        `ui.theme.BRAND_THEME_NAME`（使用品牌定制主题）或经校验的 Gradio
        内置主题名。非法值由 resolve_theme_name() 记录 warning 后回退品牌主题。

    说明：
        本函数是 config.SERVER_THEME 的**唯一消费入口**，由 launch_app() 调用。
        阶段 91 审计前它是死代码（全仓零调用点），实测 SERVER_THEME 取
        'soft' / 'monochrome' / 'not-a-theme' 三值时 launch 收到的 theme
        实参**完全相同**——配置项名存实亡，且白名单里的
        'gradio-dark' / 'huggingface' 在 Gradio 6.22 中并不存在。
    """
    return resolve_theme_name(config.SERVER_THEME)


# 拖拽浮层的样式模板：品牌色用占位符注入（阶段 91 前这里是 4 个硬编码颜色
# 字面量，与 ui/theme.py、ui/theme.py 的 CSS 各写一份，且带/不带空格两种变体）。
_OVERLAY_CSS_TEMPLATE = (
    "#global-drag-overlay{position:fixed;inset:0;z-index:9999;display:none;"
    "align-items:center;justify-content:center;background:__PRIMARY_RGBA_12__;"
    "backdrop-filter:blur(2px);border:4px dashed __PRIMARY__;box-sizing:border-box;"
    "pointer-events:none;}"
    "#global-drag-overlay.visible{display:flex;}"
    "#global-drag-overlay .hint{background:#fff;color:__PRIMARY_HOVER__;font-size:22px;"
    "font-weight:700;padding:18px 28px;border-radius:14px;"
    "box-shadow:0 6px 24px __PRIMARY_RGBA_35__;}"
)


def _overlay_css() -> str:
    """把拖拽浮层样式模板按当前 utils.brand 令牌展开（调用时读取）。"""
    return (
        _OVERLAY_CSS_TEMPLATE
        .replace("__PRIMARY_RGBA_12__", brand.rgba(brand.primary_rgb(), 0.12))
        .replace("__PRIMARY_RGBA_35__", brand.rgba(brand.primary_rgb(), 0.35))
        .replace("__PRIMARY_HOVER__", brand.PRIMARY_HOVER_HEX)
        .replace("__PRIMARY__", brand.PRIMARY_HEX)
    )


def _build_custom_js() -> str:
    """
    构造注入到页面的自定义 JavaScript（全局拖拽上传 + 键盘快捷键）。

    通过 launch(js=...) 注入到页面，仅在对应 config 开关为真时启用相应功能。
    所有逻辑都用 try/catch 包裹，任何异常都不会影响 Gradio 主体运行。

    依赖：各输入 / 按钮组件已在对应 tab 文件中设置 elem_id：
        · 输入：single_editor / auto_upload / batch_files / batch_folder /
                media_pdf / media_video
        · 按钮：single_run / auto_detect / auto_repair / batch_run /
                batch_cancel / history_refresh / history_clear

    说明：
        Gradio 6.x 已移除 gr.Blocks(js=...) 参数，但 launch(js=...) 仍保留，
        因此把脚本统一在启动期注入。脚本在 window 上挂监听，组件查找延迟到
        实际事件发生时（拖放 / 按键），天然兼容组件异步挂载。
        · 浮层配色取自 utils.brand（唯一真值来源），本函数不再内联颜色字面量。
    """
    drag_on = "true" if config.UI_GLOBAL_DRAGDROP else "false"
    shortcut_on = "true" if config.UI_SHORTCUTS_ENABLED else "false"
    return """
(function(){
  const DRAG_ENABLED = __DRAG__;
  const SHORTCUT_ENABLED = __SHORTCUT__;

  // 判断元素是否真正可见于页面（含 hidden 属性 / display:none / visibility:hidden）。
  function isVisible(el){
    if(!el) return false;
    let n = el;
    while(n && n !== document.body){
      if(n.hidden) return false;
      const s = getComputedStyle(n);
      if(s.display === 'none' || s.visibility === 'hidden') return false;
      n = n.parentElement;
    }
    return true;
  }
  // 点击组件内真正的 <button>（elem_id 打在外层 wrapper 上，需向内找 button）。
  function clickEl(el){
    const btn = el.querySelector('button') || el;
    btn.click();
  }
  function clickIfVisible(id){
    const el = document.getElementById(id);
    if(el && isVisible(el)){ clickEl(el); return true; }
    return false;
  }
  // 从候选 elem_id 列表中返回第一个当前可见的组件根节点。
  function firstVisible(ids){
    for(let i=0;i<ids.length;i++){
      const el = document.getElementById(ids[i]);
      if(el && isVisible(el)) return el;
    }
    return null;
  }

  // ============================ 全局拖拽上传 ============================
  if(DRAG_ENABLED){
    // 注入浮层样式（纯装饰，不影响功能）。
    const style = document.createElement('style');
    style.textContent = '__OVERLAY_CSS__';
    document.head.appendChild(style);
    const overlay = document.createElement('div');
    overlay.id = 'global-drag-overlay';
    overlay.innerHTML = '<div class="hint">📥 松手即可把文件填入当前页签的上传框</div>';
    document.body.appendChild(overlay);

    // Gradio 原生已支持拖拽上传的区域（落在这些区域上交给 Gradio 自己处理）。
    const UPLOAD_ZONE = 'input[type=file], .file-droparea, .upload-container, .gradio-image, .gradio-file, .image-editor';
    // 候选目标（按优先级）：批量优先填「多文件」，媒体按可见子页自动命中。
    const TARGETS = ['single_editor','auto_upload','batch_files','batch_folder','media_pdf','media_video'];

    function hasFiles(e){
      try{ return !!(e.dataTransfer && e.dataTransfer.types && Array.from(e.dataTransfer.types).indexOf('Files') !== -1); }
      catch(_){ return false; }
    }
    // 把拖入的文件转发到目标组件的原生 file input，并派发 change/input 触发上传。
    function forwardFiles(files){
      const target = firstVisible(TARGETS);
      if(!target) return;
      const input = target.querySelector('input[type=file]');
      if(!input) return;
      try{
        const dt = new DataTransfer();
        for(let i=0;i<files.length;i++){ if(files[i]) dt.items.add(files[i]); }
        input.files = dt.files;
        input.dispatchEvent(new Event('change', {bubbles:true}));
        input.dispatchEvent(new Event('input', {bubbles:true}));
      }catch(err){ console.warn('全局拖拽转发失败：', err); }
    }

    let depth = 0;
    window.addEventListener('dragenter', function(e){
      if(!hasFiles(e)) return;
      if(e.target.closest && e.target.closest(UPLOAD_ZONE)) return; // 组件自身已支持，不抢
      depth++; overlay.classList.add('visible'); e.preventDefault();
    });
    window.addEventListener('dragover', function(e){
      if(!hasFiles(e)) return;
      if(e.target.closest && e.target.closest(UPLOAD_ZONE)) return;
      e.preventDefault(); if(e.dataTransfer) e.dataTransfer.dropEffect='copy';
    });
    window.addEventListener('dragleave', function(e){
      if(!hasFiles(e)) return;
      depth = Math.max(0, depth-1);
      if(depth===0) overlay.classList.remove('visible');
    });
    window.addEventListener('drop', function(e){
      if(!hasFiles(e)) return;
      if(e.target.closest && e.target.closest(UPLOAD_ZONE)) return; // 交给组件原生处理
      e.preventDefault(); e.stopPropagation();
      overlay.classList.remove('visible'); depth=0;
      if(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) forwardFiles(e.dataTransfer.files);
    });
  }

  // ============================ 键盘快捷键 ============================
  // 仅在非输入框聚焦时生效，避免与打字冲突。
  if(SHORTCUT_ENABLED){
    window.addEventListener('keydown', function(e){
      const tag = (e.target && e.target.tagName) || '';
      if(tag === 'INPUT' || tag === 'TEXTAREA' || (e.target && e.target.isContentEditable)) return;
      const k = (e.key || '').toLowerCase();
      let handled = false;
      if(e.shiftKey && k === 'r'){ handled = clickIfVisible('single_run') || clickIfVisible('auto_repair'); }
      else if(e.shiftKey && k === 'd'){ handled = clickIfVisible('auto_detect'); }
      else if(e.shiftKey && k === 'b'){ handled = clickIfVisible('batch_run'); }
      else if(e.shiftKey && k === 'c'){ handled = clickIfVisible('batch_cancel'); }
      else if(e.shiftKey && k === 'f'){ handled = clickIfVisible('history_refresh'); }
      if(handled) e.preventDefault();
    });
  }
})();
""".replace("__DRAG__", drag_on).replace("__SHORTCUT__", shortcut_on).replace(
        "__OVERLAY_CSS__", _overlay_css()
    )


def _find_free_port(host: str, start: int, max_try: int) -> int | None:
    """
    从 start 端口起向后探测 max_try 个端口，返回第一个「空闲」端口。

    参数说明：
        host    : 监听地址（用于探测，0.0.0.0 时用 127.0.0.1 探测）。
        start   : 起始端口。
        max_try : 最大尝试次数。

    返回值：
        空闲端口号；若范围内均无空闲，返回 None。
    """
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "", None) else host
    for offset in range(max_try):
        port = start + offset
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            # connect_ex 返回 0 表示端口「已被占用」；非 0 表示空闲。
            if sock.connect_ex((probe_host, port)) != 0:
                return port
    return None


def build_app() -> gr.Blocks:
    """
    构建完整的 Gradio 应用（Blocks）。

    返回值：
        gr.Blocks 实例（尚未 launch）。

    说明：
        阶段 3 实现「单图处理（手动涂抹）」；阶段 4 实现「智能自动检测」；
        阶段 5 实现「批量处理队列」与「历史记录」两个标签页；
        阶段 6 统一视觉主题；阶段 8 增加「设置与导出」；
        阶段 10 增加「文档与视频」标签页（PDF 逐页、视频逐帧）；
        阶段 19 接入国际化：顶部语言切换器 + 全部标签页标题随语言切换。
        阶段 91：开构建前先清空 i18n 运行时注册表，避免重复构建时累积。
    """
    # 清空 i18n 运行时注册表。注册表是**进程级全局**，语言切换事件的
    # outputs 直接由它生成；若不清空，重复调用本函数会让注册表线性累积
    # （实测 288 → 576），使新事件的 outputs 里混入上一次构建、已废弃
    # Blocks 的组件。clear_registry() 的 docstring 早已声明此用途，
    # 但此前 build_app() 从未调用它（阶段 91 真值审计）。
    clear_registry()
    # 阶段 96：同上理，`_SUPERRES_WIDGETS` 也是构建期全局注册表
    # （设置页「应用真实超分」遍历它刷新所有功能页控件）。不清空则
    # 重复构建会让注册表线性累积（与 U3 同源），使「应用」指向已废弃的组件。
    clear_superres_widgets()

    # 确保工作目录存在（上传/缓存/历史）。
    ensure_dirs()

    # 按优先级（环境变量 > 持久化 > config）确定当前语言。
    load_language()

    # 注意：Gradio 6.x 已将 theme / css 等参数从 Blocks 构造移到 launch()，
    # 因此这里只传 title，主题在 launch_app() 中传入。
    with gr.Blocks(
        title=f"{config.APP_NAME} v{config.APP_VERSION}",
    ) as demo:
        gr.Markdown(f"# {config.APP_NAME}  ·  v{config.APP_VERSION}")
        compliance_md = gr.Markdown(_compliance_brief())
        # 合规声明随语言切换刷新（值由 t() 实时取，不冻结）。
        register(compliance_md, lambda: {"value": _compliance_brief()})
        # 适用范围提示（阶段 95）：紧挨合规声明——两者都是「用之前必须知道的事」。
        # 承诺面与类型名由 config 派生（见 ui.components.render_scope_note），
        # 因此 config 里改一处 committed，界面文案自动同步（不会留下人读副本）。
        scope_md = gr.Markdown(render_scope_note())
        register(scope_md, lambda: {"value": render_scope_note()})

        # ---- 顶部语言切换器 ----
        with gr.Row():
            lang_dropdown = gr.Dropdown(
                choices=[(t("lang.zh"), "zh-CN"), (t("lang.en"), "en-US")],
                value=current_language(),
                label=t("lang.label"),
                info=t("lang.info"),
                scale=2,
            )
            lang_status = gr.Markdown("")

        with gr.Tabs():
            with gr.TabItem(t("app.tab.single"), id="single", elem_id="single"):
                build_single_tab()
            with gr.TabItem(t("app.tab.auto"), id="auto", elem_id="auto"):
                build_auto_tab()
            with gr.TabItem(t("app.tab.batch"), id="batch", elem_id="batch"):
                build_batch_tab()
            with gr.TabItem(t("app.tab.media"), id="media", elem_id="media"):
                build_media_tab()
            with gr.TabItem(t("app.tab.history"), id="history", elem_id="history"):
                build_history_tab()
            with gr.TabItem(t("app.tab.settings"), id="settings", elem_id="settings"):
                build_settings_tab()
            with gr.TabItem(t("app.tab.models"), id="models", elem_id="models"):
                build_models_tab()

        # ---- 语言切换事件（此时注册表已填满全部标签页组件）----
        # js 在 fn 之前运行：先重命名标签页按钮，再把语言代码透传给 fn。
        lang_dropdown.change(
            fn=_on_lang,
            inputs=[lang_dropdown],
            outputs=registered_components() + [lang_status],
            js=_build_lang_js(),
        )

    log.info("应用界面构建完成。")
    return demo


def launch_app(app: gr.Blocks) -> int:
    """
    按 config 启动 Gradio 本地网页服务。

    参数说明：
        app : build_app() 返回的 Blocks 实例。

    返回值：
        进程退出码（0 正常；非 0 表示启动失败）。
    """
    host = config.SERVER_HOST

    # 端口策略：开启自动换端口时探测空闲端口；否则固定使用配置端口。
    if config.SERVER_AUTO_PORT:
        port = _find_free_port(host, config.SERVER_PORT, config.SERVER_PORT_MAX_TRY)
        if port is None:
            log.error(
                "在 %d~%d 范围内未找到可用端口。",
                config.SERVER_PORT,
                config.SERVER_PORT + config.SERVER_PORT_MAX_TRY - 1,
            )
            print(
                f">>> 找不到可用端口（已尝试 {config.SERVER_PORT_MAX_TRY} 个）。\n"
                f">>> 请修改 config.SERVER_PORT 为其他值，或关闭占用端口的程序后重试。"
            )
            return 1
        if port != config.SERVER_PORT:
            log.info("配置端口 %d 不可用，自动改用 %d。", config.SERVER_PORT, port)
    else:
        port = config.SERVER_PORT

    # 底部页脚链接：控制是否显示 Gradio 自带的「API」文档入口。
    # Gradio 6.x 用 footer_links 取代旧版的 show_api 参数。
    footer_links = ["gradio", "settings"]
    if config.SERVER_SHOW_API:
        footer_links.append("api")

    # 界面主题：config.SERVER_THEME 取 "brand" 时使用品牌定制主题对象；
    # 否则把经校验的 Gradio 内置主题名直接交给 launch()。非法值已在
    # resolve_theme_name() 中告警并回退品牌主题（不会走到联网下载）。
    theme_name = _resolve_theme()
    theme_arg = build_app_theme() if theme_name == BRAND_THEME_NAME else theme_name
    log.info("界面主题：%s（brand = 品牌定制主题）", theme_name)

    try:
        # launch 默认阻塞运行（prevent_thread_lock=False），直到用户 Ctrl+C。
        # 主题与自定义 CSS 在 Gradio 6.x 中通过 launch() 传入；CSS 在启动期
        # 由 build_app_css() 展开，品牌色取自 utils.brand 唯一真值来源。
        app.launch(
            server_name=host,
            server_port=port,
            share=config.SERVER_SHARE,
            inbrowser=config.SERVER_OPEN_BROWSER,
            max_threads=config.SERVER_MAX_THREADS,
            theme=theme_arg,
            css=build_app_css(),
            js=_build_custom_js(),
            footer_links=footer_links,
        )
        return 0
    except KeyboardInterrupt:
        log.info("收到中断信号，程序退出。")
        return 0
    except OSError as exc:
        # 端口在「探测」与「正式启动」之间可能又被占用。
        log.error("启动服务失败（端口相关）：%s", exc)
        print(
            f">>> 启动失败：{exc}\n"
            f">>> 请尝试修改 config.SERVER_PORT 为其他值，或关闭占用该端口的程序。"
        )
        return 1
    except Exception as exc:
        log.exception("启动 Gradio 服务时发生未预期异常")
        print(f">>> 启动界面失败：{exc}")
        return 1


if __name__ == "__main__":
    # 直接运行本文件也能启动界面（等价于 python main.py）。
    _app = build_app()
    raise SystemExit(launch_app(_app))
