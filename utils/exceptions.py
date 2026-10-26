# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : utils/exceptions.py
 文件说明 : 自定义异常体系。全项目所有可预见的错误都在此定义为专门的异常类型。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
------------------------------------------------------------------------------
 【合规强制声明】
   本工具专用于去除使用者本人生成的 AI 图片上的可见水印 / 署名标识。
   严禁使用本工具清除他人享有著作权作品上的水印、署名或版权标识。
   依据《中华人民共和国著作权法》第五十三条，故意删除或改变作品的
   权利管理电子信息，须承担相应民事责任乃至行政处罚，使用者风险自负。
------------------------------------------------------------------------------
 【为什么要自己定义异常？零基础用户请看这里】

   Python 自带的报错长这样：
       RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB ...

   普通用户看到这行字，完全不知道该怎么办。

   本文件的作用，就是把每一种可能出错的情况，都包装成一个
   「说人话 + 给出具体解决办法」的异常对象，最终在界面上显示成：

       ❌ 显卡显存不足
       原因：处理这张 4096×4096 的图片需要约 7.5GB 显存，
             但你的显卡当前只剩 2.1GB 可用。
       建议：
         1. 关闭其他占用显卡的程序（游戏、视频剪辑软件、浏览器硬件加速）
         2. 在设置页把「设备模式」改为 CPU（速度慢但一定能跑通）
         3. 在设置页把「分块尺寸」从 512 调小到 256

   这就是「工程级异常设计」与「随手抛个错」的区别。
------------------------------------------------------------------------------
 【异常继承树】

   WatermarkEraserError                所有自定义异常的共同祖先
   │
   ├── ConfigError                     配置相关
   │   └── InvalidConfigValueError     配置项取值非法
   │
   ├── ModelError                      模型相关
   │   ├── ModelNotFoundError          模型文件不存在
   │   ├── ModelCorruptedError         模型文件损坏或不完整
   │   ├── ModelLoadError              模型能读到但加载失败
   │   └── ModelDownloadError          模型下载失败
   │
   ├── ImageError                      图片相关
   │   ├── UnsupportedFormatError      格式不在白名单
   │   ├── CorruptedImageError         文件损坏无法解码
   │   ├── ImageTooLargeError          尺寸或体积超限
   │   ├── EmptyMaskError              掩码全黑，没有要修复的区域
   │   ├── InvalidMaskError            掩码形态无法解析 / 内容非法
   │   └── DetectionError              智能检测过程异常（输入非法 / 算法不可用）
   │
   ├── MediaError                      媒体文档（PDF / 视频）相关
   │   ├── UnsupportedMediaError       媒体格式不在白名单
   │   ├── CorruptedMediaError         媒体文件损坏，无法解码
   │   └── MediaTooLargeError          媒体体积超限
   │
   ├── ResourceError                   系统资源相关
   │   ├── OutOfVRAMError              显存不足
   │   ├── OutOfMemoryError            内存不足
   │   └── DiskFullError               磁盘空间不足
   │
   ├── NetworkError                    网络相关
   │   └── DownloadTimeoutError        下载超时
   │
   ├── TaskError                       任务队列相关
   │   ├── QueueFullError              队列已满
   │   ├── TaskTimeoutError            单任务超时
   │   └── TaskCancelledError          用户主动取消
   │
   └── ServerError                     服务启动相关
       └── PortInUseError              端口被占用
==============================================================================
"""

from __future__ import annotations

from typing import Any

# ------------------------------------------------------------------------------
# 阶段 168：异常文案国际化（i18n）收口
# ------------------------------------------------------------------------------
# 背景（经验 #197）：此前异常的标题 / 建议 / 结构标签全部硬编码中文，经
#   `format_exception_for_ui` → `t("single.msg.fail", exc=...)` 直接拼进英文界面，
#   造成「外层模板翻译了、内层异常仍是中文」的泄漏。
# 修复：标题与建议的默认文案走 `_PHRASE_EN` 译文表（与各异常类的 `default_title`
#   / `default_suggestions` 中文原文一一对应）；结构标签（原因/诊断/建议/底层异常
#   及 Markdown / 字典键）走 `_STRUCT_LABELS` 双语表。三个渲染方法新增 `lang`
#   形参，默认取全局语言 `get_language()`；en-US 下整段输出英文。
# 守卫（tests/test_exceptions_i18n.py）：① 所有子类 default 文案必须在 `_PHRASE_EN`
#   有对应键（漏翻立即红）；② en-US 渲染时结构标签/标题/建议全为英文、无中文残留。
try:  # i18n 属工具层；异常模块须能在无 i18n 环境下独立加载
    from utils.i18n import get_language as _get_language
except Exception:  # noqa: BLE001
    def _get_language() -> str:
        return "zh-CN"


def _effective_lang(lang: str | None) -> str:
    """渲染语言：显式传入优先，否则取全局当前语言。"""
    return lang or _get_language()


# 标题 / 建议默认文案的英文译文表。键必须与各异常类的 `default_title` /
# `default_suggestions` 中文原文**逐字一致**（守卫测试会校验覆盖）。
_PHRASE_EN: dict[str, str] = {
    # ---- 标题 ----
    "发生未知错误": "Unknown error",
    "配置错误": "Configuration error",
    "配置项取值非法": "Invalid configuration value",
    "模型错误": "Model error",
    "找不到 LaMa 模型文件": "LaMa model file not found",
    "模型文件损坏或下载不完整": "Model file corrupted or incomplete download",
    "模型加载失败": "Model failed to load",
    "模型下载失败": "Model download failed",
    "图片处理错误": "Image processing error",
    "不支持的图片格式": "Unsupported image format",
    "图片文件已损坏": "Image file is corrupted",
    "图片尺寸超出处理上限": "Image size exceeds limit",
    "未标记任何水印区域": "No watermark region marked",
    "水印掩码无效": "Invalid watermark mask",
    "智能检测失败": "Smart detection failed",
    "媒体文件处理错误": "Media file processing error",
    "不支持的媒体格式": "Unsupported media format",
    "媒体文件已损坏": "Media file is corrupted",
    "媒体文件体积超出处理上限": "Media file size exceeds limit",
    "系统资源不足": "Insufficient system resources",
    "显卡显存不足": "GPU out of memory",
    "系统内存不足": "System out of memory",
    "磁盘空间不足": "Insufficient disk space",
    "网络连接异常": "Network connection error",
    "下载超时": "Download timed out",
    "任务执行失败": "Task execution failed",
    "任务队列已满": "Task queue is full",
    "任务处理超时": "Task processing timed out",
    "任务已取消": "Task cancelled",
    "服务启动失败": "Service failed to start",
    "端口被占用": "Port already in use",
    # ---- 建议（基类）----
    "请查看 logs/error.log 中的详细日志": "Check logs/error.log for detailed information",
    "如问题持续存在，请携带日志文件反馈": "If the problem persists, report it with the log file",
    # ---- 建议（配置）----
    "检查项目根目录下的 .env 文件是否有填写错误": "Check the .env file in the project root for mistakes",
    "直接运行 python config.py 可以做一次配置自检": "Run `python config.py` to perform a configuration self-check",
    "删除 .env 文件可恢复为出厂默认配置": "Deleting the .env file restores factory defaults",
    # ---- 建议（模型）----
    "在项目根目录执行 python download_model.py 重新下载模型": "Run `python download_model.py` in the project root to re-download the model",
    "确认 models 目录下存在 big-lama.pt 文件且大小约为 200MB": "Make sure models/big-lama.pt exists and is about 200MB",
    "临时方案：在设置页把「修复引擎」切换为 opencv，无需模型即可使用": "Workaround: switch the repair engine to OpenCV in Settings; no model needed",
    "在项目根目录双击运行 download_model.py，或在命令行执行：python download_model.py": "Double-click download_model.py in the project root, or run: python download_model.py",
    "若已在别处下载过 big-lama.pt，可把它复制到项目的 models 目录下": "If you already downloaded big-lama.pt elsewhere, copy it into the project's models/ directory",
    "临时方案：程序会自动降级为 OpenCV 修复引擎，仍可处理细小水印": "Workaround: the app auto-falls back to the OpenCV engine and can still handle small watermarks",
    "删除 models 目录下的 big-lama.pt，然后重新执行 python download_model.py": "Delete models/big-lama.pt, then run python download_model.py again",
    "下载过程中请勿中断网络或强制关闭程序": "Do not interrupt the network or force-close the app during download",
    "若多次下载均失败，可能是网络不稳定，建议更换网络环境后重试": "If downloads keep failing, the network may be unstable; try a different network",
    "确认已正确安装 PyTorch：在命令行执行 python -c \"import torch; print(torch.__version__)\"": "Verify PyTorch is installed: run `python -c \"import torch; print(torch.__version__)\"`",
    "若使用显卡模式，尝试在设置页切换为 CPU 模式后重试": "If using GPU mode, try switching to CPU mode in Settings and retry",
    "PyTorch 版本与模型不匹配时也会报此错，建议按 requirements.txt 重装依赖": "A PyTorch/model version mismatch can also cause this; reinstall dependencies per requirements.txt",
    "检查网络连接是否正常，能否正常访问网页": "Check whether the network works and you can open web pages",
    "国内用户如访问 GitHub 缓慢，可尝试开启网络代理后重试": "In China, if GitHub is slow, try enabling a network proxy and retry",
    "手动下载方式：见 README.md 中「手动安装模型」章节": "Manual download: see the \"Manual model install\" section in README.md",
    "临时方案：不下载模型也能使用，程序会自动降级为 OpenCV 引擎": "Workaround: you can use the app without downloading; it auto-falls back to the OpenCV engine",
    # ---- 建议（图片）----
    "本工具支持的格式：PNG、JPG、JPEG、WebP、BMP、TIFF": "Supported formats: PNG, JPG, JPEG, WebP, BMP, TIFF",
    "可用系统自带的「画图」或「照片」应用把图片另存为 PNG 后再试": "Use the built-in Paint or Photos app to save the image as PNG and try again",
    "注意：GIF 动图、SVG 矢量图、PSD 分层文件均不受支持": "Note: GIF animations, SVG vectors, and PSD layered files are not supported",
    "尝试用系统「照片」应用打开该图片，确认它本身能否正常显示": "Try opening the image with the Photos app to confirm it displays correctly",
    "若文件是从网络下载的，可能下载不完整，请重新下载": "If downloaded, the file may be incomplete; re-download it",
    "批量处理时，损坏的图片会被自动跳过，不影响其他图片的处理": "During batch processing, corrupted images are skipped automatically without affecting others",
    "先用图片压缩工具把图片缩小到 8000 万像素以内（约 8900×8900）": "Shrink the image below 80 million pixels (about 8900x8900) with a compressor first",
    "若确实需要处理超大图，可在 .env 中调大 WME_MAX_IMAGE_PIXELS 的值": "If you must process a huge image, raise WME_MAX_IMAGE_PIXELS in .env",
    "警告：处理超大图极易耗尽内存导致电脑卡死，调整前请确认内存充足": "Warning: huge images can exhaust memory and freeze your PC; ensure enough RAM first",
    "手动模式：请用画笔在图片上涂抹要擦除的水印，或用矩形工具框选水印": "Manual mode: paint the watermark to erase, or use the rectangle tool to select it",
    "自动模式：若自动检测没有找到水印，请尝试调高「检测敏感度」滑块": "Auto mode: if detection finds nothing, raise the Detection Sensitivity slider",
    "自动模式检测不到时，也可切换到手动模式自行标记": "If auto detection fails, switch to manual mode and mark it yourself",
    "请确认图片组件类型为 numpy，并重新涂抹水印区域": "Make sure the image component is a numpy array and re-paint the watermark area",
    "若界面由旧版 Gradio 升级而来，请刷新页面后重试": "If the UI was upgraded from an older Gradio version, refresh the page and retry",
    "请确认上传的是合法图片（JPG/PNG/WebP）": "Make sure you uploaded a valid image (JPG/PNG/WebP)",
    "可切换到「单图处理（手动涂抹）」标签页手动标记水印": "Switch to the \"Single image (manual)\" tab to mark the watermark manually",
    "若提示缺少 OpenCV，请执行：pip install -r requirements.txt": "If OpenCV is missing, run: pip install -r requirements.txt",
    # ---- 建议（媒体）----
    "PDF 仅支持 .pdf；视频支持 MP4/AVI/MOV/MKV/WebM/FLV/WMV/M4V": "PDF supports .pdf only; video supports MP4/AVI/MOV/MKV/WebM/FLV/WMV/M4V",
    "请确认上传的是本项目支持的文档或视频，而非音频或压缩包": "Make sure you uploaded a supported document or video, not audio or an archive",
    "不支持的文件可先用格式转换工具转为受支持的格式后重试": "Convert unsupported files to a supported format with a converter, then retry",
    "尝试用 PDF 阅读器或视频播放器打开该文件，确认它本身能否正常加载": "Try opening the file with a PDF reader or video player to confirm it loads",
    "损坏的文件无法安全处理，请更换源文件后再试": "Corrupted files cannot be processed safely; use a different source file",
    "PDF 默认上限 500MB、视频默认上限 2GB，超大文件会拖垮内存与磁盘": "Default limits: 500MB for PDF, 2GB for video; oversized files strain memory and disk",
    "可先拆分 PDF 章节，或用剪辑软件裁剪视频片段后再处理": "Split PDF chapters, or trim video clips with an editor before processing",
    "若确有必要，可在 .env 中调大 WME_MAX_PDF_FILE_MB / WME_MAX_VIDEO_FILE_MB": "If necessary, raise WME_MAX_PDF_FILE_MB / WME_MAX_VIDEO_FILE_MB in .env",
    # ---- 建议（系统资源）----
    "关闭其他占用显卡的程序：游戏、视频剪辑软件、开启了硬件加速的浏览器": "Close other GPU-heavy apps: games, video editors, browsers with hardware acceleration",
    "在设置页把「分块尺寸」从 512 调小到 256，可显著降低显存占用": "In Settings, lower the tile size from 512 to 256 to cut VRAM usage",
    "在设置页把「设备模式」改为 CPU，速度较慢但一定能跑通": "In Settings, switch Device Mode to CPU; slower but always works",
    "开启「半精度推理」开关，可让显存占用减半": "Enable the half-precision inference switch to halve VRAM usage",
    "关闭其他占用内存的程序，尤其是浏览器的大量标签页": "Close other memory-heavy programs, especially browser tabs",
    "在设置页把「批量并发数」调整为 1，避免同时处理多张图片": "In Settings, set batch concurrency to 1 to avoid processing multiple images at once",
    "先把待处理图片缩小尺寸后再处理": "Shrink the images before processing",
    "重启程序可释放累积的内存碎片": "Restarting the app releases accumulated memory fragmentation",
    "清理输出目录 outputs 下不再需要的旧图片": "Clean up old images you no longer need under the outputs directory",
    "清理缓存目录 cache，删除它不会丢失已导出的成品图": "Clear the cache directory; deleting it will not lose exported results",
    "在 .env 中设置 WME_OUTPUT_DIR 把输出目录改到空间充足的其他磁盘": "Set WME_OUTPUT_DIR in .env to move output to a disk with free space",
    # ---- 建议（网络 / 任务 / 服务）----
    "检查电脑是否已正常联网": "Check that your computer is connected to the internet",
    "若使用了代理软件，请确认代理工作正常": "If you use a proxy, make sure it works",
    "稍后重试，或更换网络环境（如切换到手机热点）": "Retry later, or switch networks (e.g. to a phone hotspot)",
    "当前网络速度过慢，建议更换网络环境后重试": "The network is too slow; try a different network and retry",
    "在 .env 中调大 WME_MODEL_DOWNLOAD_TIMEOUT 的值（单位：秒），例如设为 600": "Raise WME_MODEL_DOWNLOAD_TIMEOUT in .env (seconds), e.g. 600",
    "程序支持断点续传，重新执行下载会从中断处继续，不会浪费已下载的部分": "The app supports resume; re-running continues from where it stopped",
    "等待当前批次处理完成后再添加新任务": "Wait for the current batch to finish before adding new tasks",
    "分批处理：一次导入的图片数量建议不超过 500 张": "Process in batches: import no more than 500 images at a time",
    "在 .env 中调大 WME_QUEUE_MAX_SIZE 可提高队列容量上限": "Raise WME_QUEUE_MAX_SIZE in .env to increase queue capacity",
    "该图片可能尺寸过大，建议先缩小后再处理": "The image may be too large; shrink it first",
    "CPU 模式处理大图确实较慢，可在 .env 中调大 WME_QUEUE_TASK_TIMEOUT": "CPU mode is slow for large images; raise WME_QUEUE_TASK_TIMEOUT in .env",
    "若电脑有 NVIDIA 显卡，切换到显卡模式可提速数倍至数十倍": "If you have an NVIDIA GPU, switching to GPU mode speeds things up many times over",
    "这是您主动取消的操作，无需处理。": "This operation was cancelled by you; no action needed.",
    "在 .env 中设置 WME_SERVER_PORT=7861 换一个端口号": "Set WME_SERVER_PORT=7861 in .env to use a different port",
    "或关闭占用该端口的其他程序后重试": "Or close the other program using that port and retry",
    "查看端口占用情况：在命令行执行 netstat -ano | findstr :7860": "To check port usage, run: netstat -ano | findstr :7860",
}

# 结构标签双语表（原因 / 诊断 / 建议 / 底层异常，及 Markdown / 字典键）。
# 注意：en-US 侧不出现任何 CJK 字符（含全角标点），由守卫测试校验。
_STRUCT_LABELS: dict[str, dict[str, str]] = {
    "zh-CN": {
        "title_wrap": "【{title}】",
        "reason": "原因：",
        "diag": "诊断：",
        "diag_sep": "，",
        "suggest": "建议：",
        "cause": "底层异常：",
        "trunc": "（已截断）",
        "md_title": "### ❌ {title}",
        "md_reason": "**原因**",
        "md_diag": "**诊断信息**",
        "md_suggest": "**解决建议**",
        "kv_sep": "：",
        "dict_type": "异常类型",
        "dict_title": "标题",
        "dict_reason": "原因",
        "dict_diag": "诊断",
        "dict_suggest": "建议",
        "dict_cause": "底层异常",
    },
    "en-US": {
        "title_wrap": "[{title}]",
        "reason": "Cause: ",
        "diag": "Diagnosis: ",
        "diag_sep": ", ",
        "suggest": "Suggestions: ",
        "cause": "Underlying exception: ",
        "trunc": " (truncated)",
        "md_title": "### ❌ {title}",
        "md_reason": "**Cause**",
        "md_diag": "**Diagnosis**",
        "md_suggest": "**Suggested fix**",
        "kv_sep": ": ",
        "dict_type": "Type",
        "dict_title": "Title",
        "dict_reason": "Cause",
        "dict_diag": "Diagnosis",
        "dict_suggest": "Suggestions",
        "dict_cause": "Underlying exception",
    },
}


def _localize_phrase(text: str, lang: str) -> str:
    """标题 / 建议文案按译文表本地化；无对应译文（如调用方显式传入的自定义文案）
    则原样返回，避免误译。"""
    if lang == "en-US" and text in _PHRASE_EN:
        return _PHRASE_EN[text]
    return text


def _labels(lang: str) -> dict[str, str]:
    """取指定语言的结构标签表，未知语言回退 zh-CN。"""
    return _STRUCT_LABELS.get(lang, _STRUCT_LABELS["zh-CN"])


# ==============================================================================
# 第 1 节：异常基类
# ==============================================================================

class WatermarkEraserError(Exception):
    """
    本项目所有自定义异常的共同基类。

    设计要点：
        每个异常携带三段信息，缺一不可：
          1. title      —— 一句话标题，例如「显卡显存不足」
          2. reason     —— 出错的具体原因，要带上实际数值，例如「需要 7.5GB，仅剩 2.1GB」
          3. suggestions—— 可操作的建议清单，用户照着做就能解决

    为什么这么设计？
        因为界面层拿到异常后，可以直接把这三段渲染成结构化的提示框，
        而不需要在界面代码里写一大堆 if 判断去猜「这个错该提示什么」。
    """

    # 默认标题。子类应当覆盖它。
    default_title: str = "发生未知错误"

    # 默认建议清单。子类应当覆盖它。
    default_suggestions: tuple[str, ...] = (
        "请查看 logs/error.log 中的详细日志",
        "如问题持续存在，请携带日志文件反馈",
    )

    def __init__(
        self,
        reason: str = "",
        *,
        title: str | None = None,
        suggestions: tuple[str, ...] | list[str] | None = None,
        detail: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """
        构造一个异常对象。

        参数说明：
            reason      : 出错的具体原因描述。建议带上实际数值，越具体越好。
            title       : 一句话标题。不传则使用类的 default_title。
            suggestions : 解决建议清单。不传则使用类的 default_suggestions。
            detail      : 附加的结构化诊断数据，会原样写入日志，便于排查。
                          例如 {"图片尺寸": "4096x4096", "可用显存MB": 2100}
            cause       : 引发本异常的底层原始异常。保留它是为了在日志里
                          打印完整的调用堆栈，不丢失技术细节。
        """
        # 标题：优先用调用方传的，否则用类默认值。
        self.title: str = title if title else self.default_title

        # 原因：允许为空（有些错误光看标题就够清楚了）。
        self.reason: str = reason

        # 建议：统一转成元组，避免调用方后续误改列表内容。
        if suggestions is None:
            self.suggestions: tuple[str, ...] = self.default_suggestions
        else:
            self.suggestions = tuple(suggestions)

        # 附加诊断数据。为 None 时用空字典代替，避免后续取值时还要判空。
        self.detail: dict[str, Any] = detail if detail is not None else {}

        # 底层原始异常。
        self.cause: BaseException | None = cause

        # 调用父类构造，把可读文本作为标准的异常消息。
        # 这样即使某处代码直接 print(异常对象)，输出的也是人话而非对象地址。
        super().__init__(self.to_text())

    # --------------------------------------------------------------------------
    # 输出格式化方法
    # --------------------------------------------------------------------------

    def to_text(self, lang: str | None = None) -> str:
        """
        把异常渲染成纯文本，用于控制台打印与日志记录。

        参数：
            lang : 渲染语言；为 None 时取全局当前语言（get_language）。
                   en-US 下标题 / 建议 / 结构标签全部走英文译文表。

        返回值：
            多行字符串。
        """
        lang = _effective_lang(lang)
        L = _labels(lang)
        title = _localize_phrase(self.title, lang)
        lines: list[str] = [L["title_wrap"].format(title=title)]

        if self.reason:
            lines.append(f"{L['reason']}{self.reason}")

        if self.detail:
            # 把诊断数据拼成「键=值」形式，单行展示。
            detail_parts: list[str] = [f"{k}={v}" for k, v in self.detail.items()]
            lines.append(L["diag"] + L["diag_sep"].join(detail_parts))

        sugg = [_localize_phrase(s, lang) for s in self.suggestions]
        if sugg:
            lines.append(L["suggest"])
            for index, item in enumerate(sugg, start=1):
                lines.append(f"  {index}. {item}")

        if self.cause is not None:
            # 只记录底层异常的类型与简短消息，完整堆栈由日志模块负责打印。
            cause_type: str = type(self.cause).__name__
            cause_msg: str = str(self.cause)
            # 底层报错信息可能极长（例如 CUDA 报错），截断到 300 字符防止刷屏。
            if len(cause_msg) > 300:
                cause_msg = cause_msg[:300] + " ..." + L["trunc"]
            lines.append(f"{L['cause']}{cause_type}: {cause_msg}")

        return "\n".join(lines)

    def to_markdown(self, lang: str | None = None) -> str:
        """
        把异常渲染成 Markdown，用于 Gradio 界面上的错误提示框。

        参数：
            lang : 渲染语言；为 None 时取全局当前语言。

        返回值：
            Markdown 格式字符串。
        """
        lang = _effective_lang(lang)
        L = _labels(lang)
        title = _localize_phrase(self.title, lang)
        lines: list[str] = [L["md_title"].format(title=title), ""]

        if self.reason:
            lines.append(f"{L['md_reason']}{L['kv_sep']}{self.reason}")
            lines.append("")

        if self.detail:
            lines.append(f"{L['md_diag']}{L['kv_sep']}")
            lines.append("")
            for key, value in self.detail.items():
                lines.append(f"- {key}{L['kv_sep']}`{value}`")
            lines.append("")

        sugg = [_localize_phrase(s, lang) for s in self.suggestions]
        if sugg:
            lines.append(f"{L['md_suggest']}{L['kv_sep']}")
            lines.append("")
            for index, item in enumerate(sugg, start=1):
                lines.append(f"{index}. {item}")
            lines.append("")

        return "\n".join(lines)

    def to_dict(self, lang: str | None = None) -> dict[str, Any]:
        """
        把异常转成字典，用于写入任务失败日志或 JSON 序列化。

        参数：
            lang : 渲染语言；为 None 时取全局当前语言（影响标题 / 建议的译文）。

        返回值：
            包含全部字段的字典。
        """
        lang = _effective_lang(lang)
        L = _labels(lang)
        return {
            L["dict_type"]: type(self).__name__,
            L["dict_title"]: _localize_phrase(self.title, lang),
            L["dict_reason"]: self.reason,
            L["dict_diag"]: self.detail,
            L["dict_suggest"]: [_localize_phrase(s, lang) for s in self.suggestions],
            L["dict_cause"]: (
                f"{type(self.cause).__name__}: {self.cause}" if self.cause else ""
            ),
        }


# ==============================================================================
# 第 2 节：配置类异常
# ==============================================================================

class ConfigError(WatermarkEraserError):
    """配置相关错误的基类。"""

    default_title = "配置错误"
    default_suggestions = (
        "检查项目根目录下的 .env 文件是否有填写错误",
        "直接运行 python config.py 可以做一次配置自检",
        "删除 .env 文件可恢复为出厂默认配置",
    )


class InvalidConfigValueError(ConfigError):
    """某个配置项的取值不合法。"""

    default_title = "配置项取值非法"


# ==============================================================================
# 第 3 节：模型类异常
# ==============================================================================

class ModelError(WatermarkEraserError):
    """模型相关错误的基类。"""

    default_title = "模型错误"
    default_suggestions = (
        "在项目根目录执行 python download_model.py 重新下载模型",
        "确认 models 目录下存在 big-lama.pt 文件且大小约为 200MB",
        "临时方案：在设置页把「修复引擎」切换为 opencv，无需模型即可使用",
    )


class ModelNotFoundError(ModelError):
    """模型权重文件不存在。"""

    default_title = "找不到 LaMa 模型文件"
    default_suggestions = (
        "在项目根目录双击运行 download_model.py，或在命令行执行：python download_model.py",
        "若已在别处下载过 big-lama.pt，可把它复制到项目的 models 目录下",
        "临时方案：程序会自动降级为 OpenCV 修复引擎，仍可处理细小水印",
    )


class ModelCorruptedError(ModelError):
    """模型文件存在但损坏、不完整或校验不通过。"""

    default_title = "模型文件损坏或下载不完整"
    default_suggestions = (
        "删除 models 目录下的 big-lama.pt，然后重新执行 python download_model.py",
        "下载过程中请勿中断网络或强制关闭程序",
        "若多次下载均失败，可能是网络不稳定，建议更换网络环境后重试",
    )


class ModelLoadError(ModelError):
    """模型文件读取正常，但加载到内存/显存时失败。"""

    default_title = "模型加载失败"
    default_suggestions = (
        "确认已正确安装 PyTorch：在命令行执行 python -c \"import torch; print(torch.__version__)\"",
        "若使用显卡模式，尝试在设置页切换为 CPU 模式后重试",
        "PyTorch 版本与模型不匹配时也会报此错，建议按 requirements.txt 重装依赖",
    )


class ModelDownloadError(ModelError):
    """模型下载过程失败。"""

    default_title = "模型下载失败"
    default_suggestions = (
        "检查网络连接是否正常，能否正常访问网页",
        "国内用户如访问 GitHub 缓慢，可尝试开启网络代理后重试",
        "手动下载方式：见 README.md 中「手动安装模型」章节",
        "临时方案：不下载模型也能使用，程序会自动降级为 OpenCV 引擎",
    )


# ==============================================================================
# 第 4 节：图片类异常
# ==============================================================================

class ImageError(WatermarkEraserError):
    """图片处理相关错误的基类。"""

    default_title = "图片处理错误"


class UnsupportedFormatError(ImageError):
    """图片格式不在支持白名单内。"""

    default_title = "不支持的图片格式"
    default_suggestions = (
        "本工具支持的格式：PNG、JPG、JPEG、WebP、BMP、TIFF",
        "可用系统自带的「画图」或「照片」应用把图片另存为 PNG 后再试",
        "注意：GIF 动图、SVG 矢量图、PSD 分层文件均不受支持",
    )


class CorruptedImageError(ImageError):
    """图片文件损坏，无法正常解码。"""

    default_title = "图片文件已损坏"
    default_suggestions = (
        "尝试用系统「照片」应用打开该图片，确认它本身能否正常显示",
        "若文件是从网络下载的，可能下载不完整，请重新下载",
        "批量处理时，损坏的图片会被自动跳过，不影响其他图片的处理",
    )


class ImageTooLargeError(ImageError):
    """图片尺寸或文件体积超出安全上限。"""

    default_title = "图片尺寸超出处理上限"
    default_suggestions = (
        "先用图片压缩工具把图片缩小到 8000 万像素以内（约 8900×8900）",
        "若确实需要处理超大图，可在 .env 中调大 WME_MAX_IMAGE_PIXELS 的值",
        "警告：处理超大图极易耗尽内存导致电脑卡死，调整前请确认内存充足",
    )


class EmptyMaskError(ImageError):
    """掩码全黑，即用户没有标记任何需要修复的区域。"""

    default_title = "未标记任何水印区域"
    default_suggestions = (
        "手动模式：请用画笔在图片上涂抹要擦除的水印，或用矩形工具框选水印",
        "自动模式：若自动检测没有找到水印，请尝试调高「检测敏感度」滑块",
        "自动模式检测不到时，也可切换到手动模式自行标记",
    )


class InvalidMaskError(ImageError):
    """掩码形态无法解析或内容非法（例如 ImageEditor 返回值格式不被支持）。"""

    default_title = "水印掩码无效"
    default_suggestions = (
        "请确认图片组件类型为 numpy，并重新涂抹水印区域",
        "若界面由旧版 Gradio 升级而来，请刷新页面后重试",
    )


class DetectionError(ImageError):
    """智能水印自动检测过程中出现的异常（输入非法、算法不可用等）。"""

    default_title = "智能检测失败"
    default_suggestions = (
        "请确认上传的是合法图片（JPG/PNG/WebP）",
        "可切换到「单图处理（手动涂抹）」标签页手动标记水印",
        "若提示缺少 OpenCV，请执行：pip install -r requirements.txt",
    )


# ==============================================================================
# 第 4.5 节：媒体类异常（PDF / 视频，阶段 44 新增）
# ------------------------------------------------------------------------------
# 说明：图片类错误统一挂在 ImageError 下，PDF 与视频这类「媒体文档」输入在
#       格式白名单、解码、体积上限上各有独立规则，新增独立的 MediaError 子树，
#       便于校验层抛出、UI 层按类型捕获并给出贴合媒体场景的上屏提示。
# ==============================================================================

class MediaError(WatermarkEraserError):
    """媒体（PDF / 视频）处理相关错误的基类。"""

    default_title = "媒体文件处理错误"


class UnsupportedMediaError(MediaError):
    """媒体文件格式不在支持白名单内。"""

    default_title = "不支持的媒体格式"
    default_suggestions = (
        "PDF 仅支持 .pdf；视频支持 MP4/AVI/MOV/MKV/WebM/FLV/WMV/M4V",
        "请确认上传的是本项目支持的文档或视频，而非音频或压缩包",
        "不支持的文件可先用格式转换工具转为受支持的格式后重试",
    )


class CorruptedMediaError(MediaError):
    """媒体文件损坏，无法正常解码（PDF 无法打开 / 视频无法读取头信息）。"""

    default_title = "媒体文件已损坏"
    default_suggestions = (
        "尝试用 PDF 阅读器或视频播放器打开该文件，确认它本身能否正常加载",
        "若文件是从网络下载的，可能下载不完整，请重新下载",
        "损坏的文件无法安全处理，请更换源文件后再试",
    )


class MediaTooLargeError(MediaError):
    """媒体文件体积超出安全上限。"""

    default_title = "媒体文件体积超出处理上限"
    default_suggestions = (
        "PDF 默认上限 500MB、视频默认上限 2GB，超大文件会拖垮内存与磁盘",
        "可先拆分 PDF 章节，或用剪辑软件裁剪视频片段后再处理",
        "若确有必要，可在 .env 中调大 WME_MAX_PDF_FILE_MB / WME_MAX_VIDEO_FILE_MB",
    )


# ==============================================================================
# 第 5 节：系统资源类异常
# ==============================================================================

class ResourceError(WatermarkEraserError):
    """系统资源不足类错误的基类。"""

    default_title = "系统资源不足"


class OutOfVRAMError(ResourceError):
    """显卡显存不足。"""

    default_title = "显卡显存不足"
    default_suggestions = (
        "关闭其他占用显卡的程序：游戏、视频剪辑软件、开启了硬件加速的浏览器",
        "在设置页把「分块尺寸」从 512 调小到 256，可显著降低显存占用",
        "在设置页把「设备模式」改为 CPU，速度较慢但一定能跑通",
        "开启「半精度推理」开关，可让显存占用减半",
    )


class OutOfMemoryError(ResourceError):
    """系统内存（RAM）不足。"""

    default_title = "系统内存不足"
    default_suggestions = (
        "关闭其他占用内存的程序，尤其是浏览器的大量标签页",
        "在设置页把「批量并发数」调整为 1，避免同时处理多张图片",
        "先把待处理图片缩小尺寸后再处理",
        "重启程序可释放累积的内存碎片",
    )


class DiskFullError(ResourceError):
    """磁盘可用空间不足，无法保存结果。"""

    default_title = "磁盘空间不足"
    default_suggestions = (
        "清理输出目录 outputs 下不再需要的旧图片",
        "清理缓存目录 cache，删除它不会丢失已导出的成品图",
        "在 .env 中设置 WME_OUTPUT_DIR 把输出目录改到空间充足的其他磁盘",
    )


# ==============================================================================
# 第 6 节：网络类异常
# ==============================================================================

class NetworkError(WatermarkEraserError):
    """网络相关错误的基类。"""

    default_title = "网络连接异常"
    default_suggestions = (
        "检查电脑是否已正常联网",
        "若使用了代理软件，请确认代理工作正常",
        "稍后重试，或更换网络环境（如切换到手机热点）",
    )


class DownloadTimeoutError(NetworkError):
    """下载超时。"""

    default_title = "下载超时"
    default_suggestions = (
        "当前网络速度过慢，建议更换网络环境后重试",
        "在 .env 中调大 WME_MODEL_DOWNLOAD_TIMEOUT 的值（单位：秒），例如设为 600",
        "程序支持断点续传，重新执行下载会从中断处继续，不会浪费已下载的部分",
    )


# ==============================================================================
# 第 7 节：任务队列类异常
# ==============================================================================

class TaskError(WatermarkEraserError):
    """任务处理相关错误的基类。"""

    default_title = "任务执行失败"


class QueueFullError(TaskError):
    """任务队列已满。"""

    default_title = "任务队列已满"
    default_suggestions = (
        "等待当前批次处理完成后再添加新任务",
        "分批处理：一次导入的图片数量建议不超过 500 张",
        "在 .env 中调大 WME_QUEUE_MAX_SIZE 可提高队列容量上限",
    )


class TaskTimeoutError(TaskError):
    """单个任务处理超时。"""

    default_title = "任务处理超时"
    default_suggestions = (
        "该图片可能尺寸过大，建议先缩小后再处理",
        "CPU 模式处理大图确实较慢，可在 .env 中调大 WME_QUEUE_TASK_TIMEOUT",
        "若电脑有 NVIDIA 显卡，切换到显卡模式可提速数倍至数十倍",
    )


class TaskCancelledError(TaskError):
    """任务被用户主动取消。"""

    default_title = "任务已取消"
    default_suggestions = ("这是您主动取消的操作，无需处理。",)


# ==============================================================================
# 第 8 节：服务启动类异常
# ==============================================================================

class ServerError(WatermarkEraserError):
    """网页服务相关错误的基类。"""

    default_title = "服务启动失败"


class PortInUseError(ServerError):
    """监听端口已被其他程序占用。"""

    default_title = "端口被占用"
    default_suggestions = (
        "在 .env 中设置 WME_SERVER_PORT=7861 换一个端口号",
        "或关闭占用该端口的其他程序后重试",
        "查看端口占用情况：在命令行执行 netstat -ano | findstr :7860",
    )


# ==============================================================================
# 第 9 节：异常包装辅助函数
# ==============================================================================

def _error_file_location(error: BaseException) -> str:
    """取异常关联的路径文本：优先 `error.filename`，其次异常消息，最后「未知路径」。

    阶段 133 修复：本项目自抛的 FileNotFoundError / PermissionError 多为「消息型」
    （如 `FileNotFoundError("找不到图片文件：x")`），此时 `error.filename` 为 None，
    旧写法 `error.filename or '未知路径'` 会丢弃消息里的真实路径、只报「未知路径」。
    """
    return getattr(error, "filename", None) or str(error) or "未知路径"


def wrap_unknown_error(
    error: BaseException,
    context: str = "",
) -> WatermarkEraserError:
    """
    把一个未预料到的原生异常，包装成本项目的标准异常对象。

    参数说明：
        error   : 捕获到的原始异常对象。
        context : 出错时正在做什么，例如「正在加载模型」「正在保存图片」。

    返回值：
        WatermarkEraserError 实例。

    使用场景：
        在最外层的兜底 try/except 中调用。作用是保证界面层永远只需要
        处理一种异常类型，不必为每个原生异常单独写展示逻辑。

    智能识别：
        本函数会尝试从原生异常的文本中识别出常见错误，
        自动转换为更精确的异常类型。例如包含 "out of memory" 字样的
        RuntimeError 会被识别为显存不足，从而给出针对性建议。
    """
    # 先取出异常的文本内容，统一转小写便于匹配关键词。
    text: str = str(error).lower()
    error_type_name: str = type(error).__name__

    # ---------- 智能识别 1：显存不足 ----------
    # PyTorch 显存不足的典型报错含有 "cuda out of memory"。
    if "out of memory" in text and ("cuda" in text or "gpu" in text):
        return OutOfVRAMError(
            reason="显卡显存已被占满，无法为本次推理分配所需空间。",
            detail={"操作阶段": context or "模型推理", "原始异常": error_type_name},
            cause=error,
        )

    # ---------- 智能识别 2：系统内存不足 ----------
    if isinstance(error, MemoryError) or "cannot allocate memory" in text:
        return OutOfMemoryError(
            reason="系统可用内存不足，无法完成本次操作。",
            detail={"操作阶段": context or "图像处理", "原始异常": error_type_name},
            cause=error,
        )

    # ---------- 智能识别 3：磁盘空间不足 ----------
    # Windows 上磁盘满的 errno 是 28（ENOSPC）。
    if isinstance(error, OSError) and getattr(error, "errno", None) == 28:
        return DiskFullError(
            reason="目标磁盘已没有可用空间，文件无法写入。",
            detail={"操作阶段": context or "文件保存", "原始异常": error_type_name},
            cause=error,
        )

    # ---------- 智能识别 4：文件不存在 ----------
    if isinstance(error, FileNotFoundError):
        loc = _error_file_location(error)
        return WatermarkEraserError(
            reason=f"找不到需要的文件：{loc}",
            title="文件不存在",
            suggestions=(
                "确认文件路径是否正确，是否被移动或删除",
                "若路径中含有中文或空格，尝试把文件移到纯英文路径下再试",
            ),
            detail={"操作阶段": context or "文件读取", "文件路径": loc},
            cause=error,
        )

    # ---------- 智能识别 5：权限不足 ----------
    if isinstance(error, PermissionError):
        loc = _error_file_location(error)
        return WatermarkEraserError(
            reason=f"没有权限访问：{loc}",
            title="文件权限不足",
            suggestions=(
                "确认该文件没有被其他程序打开占用",
                "尝试以管理员身份运行本程序",
                "避免把项目放在 C:\\Program Files 等受系统保护的目录下",
            ),
            detail={"操作阶段": context or "文件访问", "文件路径": loc},
            cause=error,
        )

    # ---------- 智能识别 6：网络类错误 ----------
    network_keywords: tuple[str, ...] = (
        "connection", "timeout", "timed out", "network",
        "unreachable", "resolve", "ssl", "certificate",
    )
    if any(keyword in text for keyword in network_keywords):
        return NetworkError(
            reason="网络请求失败，可能是断网、超时或被防火墙拦截。",
            detail={"操作阶段": context or "网络请求", "原始异常": error_type_name},
            cause=error,
        )

    # ---------- 兜底：确实无法识别的未知错误 ----------
    return WatermarkEraserError(
        reason=f"程序在「{context or '运行过程'}」中遇到未预期的错误。",
        title="未知错误",
        suggestions=(
            "请查看 logs/error.log 获取完整的技术细节",
            "重启程序后重试，多数偶发问题可自行恢复",
            "若问题稳定复现，请携带 error.log 文件反馈",
        ),
        detail={"操作阶段": context or "未知", "原始异常类型": error_type_name},
        cause=error,
    )


# ==============================================================================
# 第 9 节：界面呈现入口（阶段 81 新增）
# ------------------------------------------------------------------------------
# 【为什么需要它】审计发现：本模块精心设计了结构化异常（title / reason /
#   suggestions / detail）与两个呈现/包装能力，但**使用侧完全脱节**——
#     · `to_markdown()`（文档写明「用于 Gradio 界面上的错误提示框」）生产代码**零调用**；
#     · `wrap_unknown_error()` 生产代码**零调用**；
#     · UI 层 13 个文件**零引用**本模块基类，40 处错误提示全部走 i18n 的
#       `exc` 形参（默认渲染为 `str(exc)` 纯文本），靠 `str(exc)` 输出**纯文本**。
#   结果是「三段式结构化提示」只落地了「文本拼接」这一层，
#   界面里看不到标题层级与建议列表。
#
# 【本函数的作用】给界面层一个**统一入口**：把任意异常渲染成适合 Markdown
#   组件显示的字符串。设计要点：
#     · 自定义异常 → `to_markdown()`，获得标题 + 原因 + 建议的完整结构；
#     · 其他异常 → **原样返回 `str(exc)`**，保持既有提示措辞不退化（零回归）；
#     · 自身**绝不抛异常**——它处在错误处理路径上，再抛就会掩盖原始错误。
# ==============================================================================

def format_exception_for_ui(exc: BaseException) -> str:
    """
    把异常渲染成可直接放进 Markdown 组件的字符串（界面层统一入口）。

    参数说明：
        exc : 捕获到的任意异常对象。

    返回值：
        · 若 `exc` 是 `WatermarkEraserError` 子类 → 其 `to_markdown()` 输出
          （标题 + 原因 + 诊断 + 建议列表）；
        · 其他异常 → `str(exc)`，与既有的 `exc=exc` 写法结果**完全一致**；
        · 渲染过程出错 → 退回 `str(exc)`，绝不向调用方抛出新异常。

    用法（替换 UI 中原来的 `exc=exc`）：
        t("single.msg.fail", exc=format_exception_for_ui(exc))
    """
    try:
        if isinstance(exc, WatermarkEraserError):
            return exc.to_markdown()
    except Exception:  # noqa: BLE001 —— 渲染分支自身异常不得掩盖原始错误
        pass
    try:
        return str(exc)
    except Exception:  # noqa: BLE001 —— 连 str() 都失败的极端对象（如自定义 __str__ 抛错）
        return exc.__class__.__name__


# ==============================================================================
# 第 10 节：模块自测入口
# ==============================================================================

if __name__ == "__main__":
    # 阶段 333：自测文本含 ❌（GBK 编不出）⇒ 中文 Windows 控制台下「自测通过」会被读成 rc=1。
    # 本模块是纯标准库依赖，故只在这块里就地取 os/sys，不污染库面。
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from utils.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    print("=" * 78)
    print("  异常体系自测")
    print("=" * 78)
    print()

    # 测试 1：直接构造一个显存不足异常，检查文本渲染效果。
    print("【测试 1】显存不足异常的文本渲染")
    print("-" * 78)
    _err1 = OutOfVRAMError(
        reason="处理 4096×4096 图片需要约 7.5GB 显存，当前仅剩 2.1GB 可用。",
        detail={"图片尺寸": "4096x4096", "需要显存MB": 7680, "可用显存MB": 2150},
    )
    print(_err1.to_text())
    print()

    # 测试 2：智能识别功能，模拟 PyTorch 显存报错。
    print("【测试 2】智能识别 —— 把原生 RuntimeError 转为显存不足异常")
    print("-" * 78)
    _raw = RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
    _err2 = wrap_unknown_error(_raw, context="LaMa 模型推理")
    print(f"识别结果类型：{type(_err2).__name__}")
    print(_err2.to_text())
    print()

    # 测试 3：智能识别功能，模拟网络超时。
    print("【测试 3】智能识别 —— 把网络异常转为 NetworkError")
    print("-" * 78)
    _raw2 = OSError("Connection timed out after 120 seconds")
    _err3 = wrap_unknown_error(_raw2, context="下载模型文件")
    print(f"识别结果类型：{type(_err3).__name__}")
    print(_err3.to_text())
    print()

    # 测试 4：字典序列化，用于写入失败日志。
    print("【测试 4】异常转字典（用于 JSON 日志）")
    print("-" * 78)
    _err4 = ModelNotFoundError(
        reason="models/big-lama.pt 文件不存在。",
        detail={"预期路径": "D:\\项目\\models\\big-lama.pt"},
    )
    for _k, _v in _err4.to_dict().items():
        print(f"  {_k}: {_v}")
    print()

    # ---- 断言（阶段 133 补强）：自检必须「能失败」----
    #   此前本自检只有 print、零断言 ⇒ 无论渲染对不对都 exit 0（哑巴自检，
    #   子进程守卫也抓不到任何东西）。以下断言把关键契约钉住。
    assert "显存不足" in _err1.to_text() and "建议：" in _err1.to_text()
    assert _err1.title == "显卡显存不足"
    assert isinstance(_err2, OutOfVRAMError), "CUDA OOM 应被识别为 OutOfVRAMError"
    assert isinstance(_err3, NetworkError), "网络超时应被识别为 NetworkError"
    assert _err4.to_dict()["异常类型"] == "ModelNotFoundError"
    assert "底层异常" in _err2.to_text(), "带 cause 时应渲染底层异常"

    # D1：继承树必须收录全部异常类（防再次过期）。
    _all_cls = {n for n, o in globals().items()
                if isinstance(o, type) and issubclass(o, WatermarkEraserError)
                and o is not WatermarkEraserError}
    _missing = sorted(n for n in _all_cls if n not in (__doc__ or ""))
    assert not _missing, f"模块 docstring 继承树未收录：{_missing}"

    # D2：消息型 FileNotFoundError 不得丢失路径。
    _w = wrap_unknown_error(FileNotFoundError("找不到图片文件：foo.png"), context="读取")
    assert "foo.png" in _w.reason, f"路径丢失：{_w.reason!r}"
    print("断言全部通过（渲染 + 智能识别 + 继承树 + 路径抽取）。")
    print()

    print("=" * 78)
    print("  异常体系自测结束，全部渲染正常。")
    print("=" * 78)
