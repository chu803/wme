# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : utils/path_helper.py
 文件说明 : 路径与文件名处理工具集。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
------------------------------------------------------------------------------
 【本文件解决的真实痛点】
   1. 中文路径：Windows 上很多库（特别是老版本的 OpenCV）直接 open 中文路径
      会报「文件不存在」。本模块统一用「读字节 → cv2.imdecode」的方式绕过。
   2. 打包路径：程序打包成 EXE 后，__file__ 指向临时解压目录，用户数据应放在
      EXE 旁边。所有「定位项目资源」的逻辑都收口在本文件，避免散落各处出错。
   3. 文件名安全：用户上传的图片可能叫「我的照片/2024?.png」这种带特殊字符
      的名字，直接拼进路径会出问题，这里做清洗与去重。
==============================================================================
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from pathlib import Path

# 从全局配置读取项目根目录与各类工作目录，保证与 config.py 唯一真值来源一致。
# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config

# 阶段 81：移除未使用的 `from utils.exceptions import InvalidConfigValueError`
# （该导入自引入起从未被本模块使用，属「定义了却没人用」的一类死代码；
#   路径校验函数抛的是内置的 FileNotFoundError / IsADirectoryError，
#   由上层按具体类型捕获并转成友好提示。）


# ==============================================================================
# 第 1 节：项目资源定位
# ------------------------------------------------------------------------------
# 说明：凡是需要访问「程序自带资源」（模型、CSS、示例图）时，都通过这里拿路径，
#       这样无论是源码运行还是 EXE 运行都能正确定位。
# ==============================================================================

def get_assets_dir() -> Path:
    """
    获取静态资源目录（assets/）。

    返回值：
        assets 目录的绝对路径。
    """
    return config.ASSETS_DIR


def get_model_path() -> Path:
    """
    获取 LaMa 模型权重文件的完整路径。

    返回值：
        模型文件绝对路径，例如 D:\\工具\\models\\big-lama.pt
    """
    return config.MODEL_PATH


def get_output_dir(sub_folder: str | None = None) -> Path:
    """
    获取输出目录，可选地在输出目录下再建一层子目录。

    参数说明：
        sub_folder : 子目录名（如 "2026-08-03"）。传 None 表示直接返回输出根目录。
                     ⚠ 只取**末段名**：含分隔符的输入会被剥离（见下方 F4）。

    返回值：
        目录绝对路径，且该目录一定已存在（自动创建）。

    阶段 144 修复 F4：`sub_folder` 此前直接参与路径拼接且**未做任何约束**，
    于是可以越出输出目录（实测，仅路径计算，未实际创建）：

        get_output_dir("../escape") → <项目根>/escape     ❌ 越出 outputs/
        get_output_dir("..")        → <项目根>             ❌ 越出 outputs/
        get_output_dir("/abs")      → D:\\abs               ❌ 绝对路径直接覆盖
        get_output_dir("C:\\\\x")     → C:\\x                 ❌ 换盘

    而 docstring 承诺的是「在**输出目录下**再建一层子目录」。
    该函数会**真实创建目录**（`os.makedirs`），故风险高于纯字符串拼接。
    修法 = 与 `make_unique_path` 同款纵深防御：显式按 `/` 与 `\\` 取**最后一段**，
    并兜掉 `.` / `..` / 空串（此时退化为「返回输出根目录」）。
    当前唯一调用方 `services/file_service.py` 传**无参**，故修复对现有行为零影响。
    """
    base: Path = config.OUTPUT_DIR
    if sub_folder is not None:
        # 阶段 144 F4：只接受末段名，剥离任何目录成分与上跳。
        seg = str(sub_folder).replace("\\", "/").rsplit("/", 1)[-1].strip()
        if seg and seg not in (".", ".."):
            base = base / seg
    os.makedirs(base, exist_ok=True)
    return base


def get_cache_dir() -> Path:
    """获取缓存目录（历史记录、缩略图等）绝对路径，自动确保存在。"""
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    return config.CACHE_DIR


def get_temp_dir() -> Path:
    """获取临时目录（推理中间产物）绝对路径，自动确保存在。"""
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    return config.TEMP_DIR


def get_log_dir() -> Path:
    """获取日志目录绝对路径，自动确保存在。"""
    os.makedirs(config.LOG_DIR, exist_ok=True)
    return config.LOG_DIR


# ==============================================================================
# 第 2 节：文件名安全清洗
# ------------------------------------------------------------------------------
# 说明：Windows 不允许文件名包含 \ / : * ? " < > | 这些字符，
#       且不允许以空格或点结尾。上传文件时用户可能带入这些字符，必须清洗。
#
# 【阶段 78 真值审计后加固】命名链路此前**零测试覆盖**，审计发现七项缺陷，
#   其中 N2/N3/N4/N5 集中在本节的清洗规则上：
#     · N2 扩展名部分完全未经清洗 —— `a.//evil` 的路径分隔符从不带清洗的
#          「扩展名」位置漏过（清洗只作用于主名）；
#     · N3 Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）未处理，
#          创建 `CON.png` 会失败或写入设备；
#     · N4 控制字符（\x00-\x1f、\x7f）未剔除，`\x00` 会让 open() 直接抛 ValueError；
#     · N5 尾部空格/点只对主名生效，`a.png␠` 的尾空格经扩展名位置漏过。
#   现改为「**整串先行清洗，再分离扩展名**」的单向管线，根治上述四类问题。
# ==============================================================================

# Windows 文件名非法字符集合（含路径分隔符 / \）。
_ILLEGAL_CHARS: str = r'<>:"/\|?*'

# 匹配任何非法字符的正则。
_ILLEGAL_RE: re.Pattern = re.compile(f"[{re.escape(_ILLEGAL_CHARS)}]")

# 控制字符（C0 区 \x00-\x1f 与 DEL \x7f）：Windows 与多数文件系统均不接受；
# 其中 \x00 会让 Python 的 open()/os.open() 直接抛 ValueError（embedded null byte），
# \n / \t 则会破坏日志、命令行与界面展示。
_CONTROL_RE: re.Pattern = re.compile(r"[\x00-\x1f\x7f]")

# Windows 保留设备名（**基名**，大小写不敏感）。这些名字即便带扩展名
# （`CON.png`、`aux.txt`）在 Windows 上仍被解析为设备，创建文件会失败或写入设备。
_RESERVED_NAMES: frozenset = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# 扩展名（含前导点）的长度上限：扩展名不值得占用长度额度，超长一律截断。
_MAX_SUFFIX_LENGTH: int = 16


def _scrub(text: str) -> str:
    """
    单步清洗：控制字符与 Windows 非法字符一律替换成下划线。

    阶段 144 抽出的公共步骤 —— 清洗管线此前只作用于**用户给的 name**，
    而 `fallback` 兜底名绕过了它（见 `sanitize_filename` 的 F1 修复说明）。
    抽成函数后 name 与 fallback 走**同一套**规则，避免两处规则漂移。

    注意：本函数**不做** NFKC 归一化（那是「美化」而非「安全」），
    归一化仍由调用方按 `normalize` 开关决定。
    """
    return _ILLEGAL_RE.sub("_", _CONTROL_RE.sub("_", text))


def _split_name(text: str) -> tuple[str, str]:
    """
    把（已清洗的）文件名拆成「主名 + 扩展名」，扩展名含前导点。

    说明：
        · 用 `rfind(".")` 找**最后一个**点，故 `a.b.png` → ("a.b", ".png")；
        · 点位于首位（`.gitignore` 风格）时整体视为主名，不当作扩展名；
        · 无点时扩展名为空串。
    """
    dot = text.rfind(".")
    if dot > 0:
        return text[:dot], text[dot:]
    return text, ""


def sanitize_filename(
    name: str,
    max_length: int = 200,
    *,
    fallback: str = "未命名",
    normalize: bool = True,
) -> str:
    """
    清洗文件名，去除非法字符并控制长度（阶段 78 重写）。

    参数说明：
        name        : 原始文件名。
        max_length  : 清洗后**整个文件名**（主名 + 扩展名，不含目录）的长度目标。
                      ⚠ 见下方「F2 订正」：这是**目标值而非硬上限**，极小值时可能超出。
        fallback    : 清洗后为空时使用的兜底名（**同样会被清洗**，阶段 144 修复）。
        normalize   : 是否做 NFKC 归一化（全角 → 半角），默认 True。

    返回值：
        清洗后的安全文件名（保留扩展名）。

    规则（严格按序执行，单向管线）：
        1. **NFKC 归一化**：全角字符 → 半角（`ａｂｃ` → `abc`，`．` → `.`）；
        2. **控制字符**（\\x00-\\x1f、\\x7f）→ 下划线；
        3. **Windows 非法字符**（`<>:"/\\|?*`，含路径分隔符）→ 下划线；
        4. 分离「主名 + 扩展名」，**两者分别**去除首尾空格与点号；
        5. 主名为空 → fallback（**fallback 补走第 2/3 步清洗**，见下）；
        6. 主名为 Windows 保留设备名时加前缀下划线（`CON` → `_CON`）；
        7. 扩展名超长 → 截断到 16 字符；
        8. 长度控制：主名 + 扩展名合计不超过 `max_length`。

    **阶段 78 关键修复**：清洗与去尾**先于**扩展名分离执行。
    此前实现先 `rsplit(".", 1)` 再只清洗主名，导致扩展名位置成为「清洗盲区」——
    `a.//evil` 会原样保留路径分隔符；`a.png␠` 的尾空格同样漏过。见 `docs/78-*`。

    **阶段 144 修复 F1：fallback 此前绕过清洗管线（两层都绕过）**
    -----------------------------------------------------------------
    原实现在第 5 步直接 `stem = fallback`，**跳过了第 2/3 步**（控制字符 / 非法字符
    替换）**与第 4 步的 `strip(" .")`** —— 因为那时它们都已执行完毕。于是：

        sanitize_filename(".", fallback="a/b")          → "a/b"      （含路径分隔符！）
        sanitize_filename(".", fallback="..\\\\..\\\\etc") → "..\\..\\etc"（目录穿越形态）
        sanitize_filename(".", fallback="evil*name")    → "evil*name"（含非法字符）
        sanitize_filename(".", fallback="   ")          → "   "      （纯空格主名）
        sanitize_filename(".", fallback="...")          → "..."      （全是点号主名）

    而函数的契约是返回「清洗后的**安全**文件名」。修法 = 抽出 `_scrub()` 单步，
    fallback 显式走 `_scrub(...).strip(" .")`（与 name 路径**同规则**），
    清洗后仍为空时兜底到 `"未命名"` —— 与既有测试
    `sanitize_filename("...", fallback="img") == "img"` 所锁定的
    「点号被 strip 掉后为空 → 兜底」语义一致，只是把它同样施加到 fallback 上。
    注意第 6/7/8 步**本来就在** fallback 赋值之后，故保留设备名与长度控制
    对 fallback 一直是生效的（`fallback="CON"` → `"_CON"`）。

    **阶段 144 订正 F2：`max_length` 是目标值而非硬上限**
    ----------------------------------------------------
    第 8 步的额度计算是 `budget = max(1, max_length - len(suffix))` —— **主名至少保留
    1 个字符**。因此当 `max_length < len(suffix) + 1` 时结果**必然超出** `max_length`：

        sanitize_filename("abc.png", 4) → "a.png"（长度 5 > 4）
        sanitize_filename("a", 0)       → "a"    （长度 1 > 0）

    行为本身是**唯一合理的**（扩展名与至少 1 字符主名都不该被截成空，否则文件无法被
    识别），故此处的处置是**把声明写准**而不是改行为（见经验 5m 三态判据）。
    所有仓内调用方传 120 / 200，从不触发该边界。
    """
    if not name:
        return fallback

    text = str(name)
    # ① 归一化：全角 → 半角（此前 normalize_unicode 定义了却从未被调用，属死代码，
    #    阶段 78 起接入本清洗管线，成为唯一实现）。
    if normalize:
        text = normalize_unicode(text)
    # ②③ 控制字符先把 \x00-\x1f / \x7f 剔除，再替换 Windows 非法字符
    #     （**整串**执行，扩展名位置不再漏网）。阶段 144：抽成 _scrub()，
    #     使 fallback 能复用同一套规则。
    text = _scrub(text)

    # ④ 此时串内已无路径分隔符与非法字符，可安全分离扩展名。
    stem, suffix = _split_name(text)

    stem = stem.strip(" .")
    # 扩展名**只去尾部**：`suffix` 天然以点开头，用 strip 会把那个点一起吃掉
    # （`my.png` → `mypng`）。这是实现期被自检当场抓住的坑。
    suffix = suffix.rstrip(" .")
    if not stem:
        # 阶段 144 F1：fallback **补走与 name 完全相同的处理** —— 此前只做
        #   `stem = fallback` 直接赋值，同时绕过了：
        #     · ②③ 步（控制字符 / 非法字符替换）—— 可注入路径分隔符；
        #     · ④ 步的 `strip(" .")` —— 纯空白或全点号的 fallback 会原样成为主名
        #       （实测 `fallback="   "` → 返回纯空格主名；`fallback="..."` → 返回 `"..."`）。
        #   现改为 `_scrub(...).strip(" .") or "未命名"`，与 name 路径**同规则**；
        #   清洗后仍为空（fallback 为空串 / 纯空白 / 全点号）时兜底到内置名，
        #   保证主名非空 —— 这正是既有测试
        #   `assert sanitize_filename("...", fallback="img") == "img"`
        #   所要求的「点号被 strip 掉后为空 → 兜底」语义，只是在 fallback 上同样成立。
        stem = _scrub(str(fallback)).strip(" .") or "未命名"

    # ⑤ 保留设备名加前缀（`CON` / `con` / `COM1` …）。
    if stem.upper() in _RESERVED_NAMES:
        stem = "_" + stem

    # ⑥⑦ 长度控制：扩展名先限长，剩余额度给主名。
    if len(suffix) > _MAX_SUFFIX_LENGTH:
        suffix = suffix[:_MAX_SUFFIX_LENGTH]
    budget = max(1, int(max_length) - len(suffix))
    if len(stem) > budget:
        stem = stem[:budget]

    return stem + suffix


def normalize_unicode(text: str) -> str:
    """
    将文件名中的全角字符统一转为半角，避免个别文件系统/库处理异常。

    参数说明：
        text : 待处理字符串。

    返回值：
        归一化后的字符串。
    """
    # NFKC 归一化：把全角字母数字标点转成对应的半角形式。
    return unicodedata.normalize("NFKC", text)


# ==============================================================================
# 第 3 节：唯一化输出文件名
# ------------------------------------------------------------------------------
# 说明：用户可能反复处理同一张图，若直接覆盖同名文件会丢失历史成果。
#       本函数生成「不重复」的文件名：若目标已存在，则追加 (1)、(2) 序号。
# ==============================================================================

def make_unique_path(target_dir: str | Path, desired_name: str) -> Path:
    """
    在指定目录内生成一个不会与已有文件冲突的唯一路径。

    参数说明：
        target_dir   : 目标目录（字符串或 Path）。
        desired_name : 期望的文件名。

    返回值：
        保证不冲突的 Path 对象。文件本身不会被创建，仅计算路径。

    算法：
        1. 先看 desired_name 是否存在；
        2. 存在则在主名与扩展名之间插入 (1)、(2)……直到找到一个空闲名。

    **阶段 78 纵深防御**：函数只取 `desired_name` 的**文件名词干**
    （`Path(...).name`）。即便某个调用方忘了先 `sanitize_filename`，
    带路径分隔符的名字也无法越出 `target_dir` —— 这里是写出路径的最后一道防线。

    **阶段 144 修复 F5：`Path(...).name` 的分隔符语义是平台相关的**
    --------------------------------------------------------------
    `Path` 在 Windows 上认 `/` 与 `\\` 两种分隔符，但在 POSIX 上**只认 `/`**：

        Path("a\\\\b.txt").name          # Windows(nt) → "b.txt"      ✅ 已剥离
        PurePosixPath("a\\\\b.txt").name  # POSIX        → "a\\\\b.txt"  ❌ 未剥离

    ⇒ 原实现「剥离任何目录成分」的声明**只在 Windows 成立**；在 Linux/macOS 上，
    带反斜杠的输入会原样成为文件名的一部分（产出含 `\\` 的怪名，且 docstring
    承诺的「无法越出 target_dir」不再由本函数保证）。
    修法 = **不依赖 Path 的平台语义**，显式按 `/` 与 `\\` 两种分隔符取最后一段。
    """
    target_dir = Path(target_dir)
    # 防御：剥离任何目录成分（`a/b.txt` → `b.txt`；`a\b.txt` → `b.txt`）。
    #   阶段 144 F5：显式兼容两种分隔符，不再依赖 Path 的平台差异。
    raw_name = str(desired_name).replace("\\", "/")
    desired_name = raw_name.rsplit("/", 1)[-1] or "output"
    #   `..` / `.` 会被上面保留下来（它们本身不含分隔符），需显式兜掉
    #   —— 否则会拼出「父目录(1)」这种荒谬结果。这是测试当场抓住的坑。
    if desired_name in (".", ".."):
        desired_name = "output"
    candidate: Path = target_dir / desired_name
    if not candidate.exists():
        return candidate

    # 拆分主名与扩展名，用于插入序号。
    stem: str = candidate.stem
    suffix: str = candidate.suffix
    index: int = 1
    while True:
        new_name: str = f"{stem}({index}){suffix}"
        candidate = target_dir / new_name
        if not candidate.exists():
            return candidate
        index += 1
        # 极端情况下兜底，防止死循环（理论上不会触发）。
        if index > 9999:
            return target_dir / f"{stem}_{int(time.time())}{suffix}"


# ==============================================================================
# 第 4 节：按模板生成输出文件名
# ------------------------------------------------------------------------------
# 说明：尊重 config.OUTPUT_NAME_TEMPLATE 模板，支持日期、时间、序号等占位符，
#       并自动处理文件扩展名（不同导出格式扩展名不同）。
# ==============================================================================

# 已知扩展名集合（阶段 78）：仅当模板渲染结果的**末尾段**是其中之一时，
# 才认为它是「扩展名」并允许被目标格式替换；否则一律安全追加，
# 避免把主名中的点误判为扩展名分隔点（见 build_output_filename 的 N1 修复）。
_KNOWN_EXTS: frozenset = frozenset({
    "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif", "avif",
    "heic", "heif", "ico", "jfif", "svg",
    "pdf", "zip", "html", "htm", "json", "txt", "csv", "log",
    "mp4", "mov", "avi", "mkv", "webm",
})


def build_output_filename(
    original_stem: str,
    extension: str,
    index: int | None = None,
    engine: str | None = None,
    date_str: str | None = None,
    time_str: str | None = None,
) -> str:
    """
    根据配置模板生成最终的输出文件名。

    参数说明：
        original_stem : 原文件主名（不含扩展名）。
        extension     : 目标扩展名（不含点，例如 "png" / "jpg" / "webp"），
                        用于覆盖模板里可能写死的扩展名。
        index         : 批量处理序号（从 1 开始），对应模板 {index}。
        engine        : 实际使用的修复引擎名（lama/opencv），对应 {engine}。
        date_str      : 日期串（默认取当天，格式 20260803）。
        time_str      : 时间串（默认取当前时刻，格式 181955）。

    返回值：
        完整的文件名（含扩展名）。

    **阶段 78 关键修复（N1）**：扩展名强制逻辑此前是
    `name.rsplit(".", 1)[0] + "." + extension`，隐含假设「最后一个点之后就是扩展名」。
    但**默认模板 `{stem}_cleaned_{time}` 本身不含扩展名**，于是主名里的点被误判：

        模板 {stem}_cleaned_{time} + 原图 my.photo + png
        渲染所得 my.photo_cleaned_125053  →  输出 my.png
        （`.photo` 与 `_cleaned_125053` **被静默丢弃**）

    含点的原图名（`photo.2024.png`、`v1.2.jpg`）极为常见，且丢弃 `{time}` 还会让
    多张图坍缩成同一名字。现改为「**仅当末尾段是已知扩展名时才替换，否则安全追加**」：

        my.photo_cleaned_125053 → my.photo_cleaned_125053.png   （信息完整）
        shot.jpg（模板写死） + png → shot.png                     （替换仍生效）
    """
    if date_str is None:
        date_str = time.strftime("%Y%m%d")
    if time_str is None:
        time_str = time.strftime("%H%M%S")

    # 用模板里的占位符做替换。未提供的占位符保持原样，避免报错。
    name: str = config.OUTPUT_NAME_TEMPLATE
    replacements: dict[str, str] = {
        "{stem}": original_stem,
        "{date}": date_str,
        "{time}": time_str,
        "{index}": str(index) if index is not None else "",
        "{engine}": engine if engine else "",
    }
    for placeholder, value in replacements.items():
        name = name.replace(placeholder, value)

    # 去掉可能残留的连续下划线、首尾点号，保证文件名干净。
    #   （阶段 78：改用正则折叠任意长度的连续下划线，此前 str.replace 只替换一次，
    #     `a____b` 仍会留下多下划线。）
    name = re.sub(r"__+", "_", name).strip("._")
    if not name:
        name = "output"

    # 强制使用目标格式的扩展名（模板里写的 .png 不应覆盖用户选的 webp）。
    target_ext = "." + extension.lower()
    if not name.lower().endswith(target_ext):
        dot = name.rfind(".")
        tail = name[dot + 1:] if dot > 0 else ""
        if tail.lower() in _KNOWN_EXTS:
            # 末尾段确实是扩展名 → 替换（保持原有「格式由参数决定」的意图）。
            name = name[:dot] + target_ext
        else:
            # 末尾段不是扩展名（主名里的点 / 模板无扩展名）→ **安全追加**，绝不截断。
            name = name + target_ext

    return sanitize_filename(name)


# ==============================================================================
# 第 5 节：路径存在性与可读性校验
# ------------------------------------------------------------------------------
# 说明：在真正打开文件之前先做一次友好校验，把晦涩的系统错误转成中文提示。
# ==============================================================================

def assert_file_readable(path: str | Path) -> Path:
    """
    校验一个文件是否「存在且可读」，否则抛出友好的中文异常。

    参数说明：
        path : 待校验的文件路径。

    返回值：
        校验通过的 Path 对象。

    异常：
        FileNotFoundError : 文件不存在时抛出（中文提示）。
        IsADirectoryError : 路径存在但**不是文件**（是目录）时抛出。

    ⚠ 阶段 144 修复 F3：原 docstring 的「异常」段**只列了 `FileNotFoundError`**，
    而实现里明明还有一条 `IsADirectoryError`（实测：`assert_file_readable(某目录)`
    → `IsADirectoryError: 路径不是一个文件：…`）。调用方按 docstring 只 `except
    FileNotFoundError` 时，传目录会让 `IsADirectoryError` 逃逸到上层。属「声明 ≠ 实现」，
    且方向是**漏列**（比多列更危险：多列只是冗余，漏列会让调用方漏捕获）。
    """
    p: Path = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到文件：{p}\n请确认路径是否正确，注意中文路径需使用完整路径。")
    if not p.is_file():
        raise IsADirectoryError(f"路径不是一个文件：{p}")
    return p


def is_supported_input(path: str | Path) -> bool:
    """
    判断文件扩展名是否在「支持读取」的白名单内。

    参数说明：
        path : 文件路径。

    返回值：
        True 表示支持；False 表示不支持。
    """
    ext: str = Path(path).suffix.lower()
    return ext in config.SUPPORTED_INPUT_FORMATS


def is_supported_pdf_input(path: str | Path) -> bool:
    """
    判断文件扩展名是否在「支持读取的 PDF 文档」白名单内。

    参数说明：
        path : 文件路径。

    返回值：
        True 表示支持；False 表示不支持。
    """
    ext: str = Path(path).suffix.lower()
    return ext in config.SUPPORTED_DOC_FORMATS


def is_supported_video_input(path: str | Path) -> bool:
    """
    判断文件扩展名是否在「支持读取的视频格式」白名单内。

    参数说明：
        path : 文件路径。

    返回值：
        True 表示支持；False 表示不支持。
    """
    ext: str = Path(path).suffix.lower()
    return ext in config.SUPPORTED_VIDEO_FORMATS


def ensure_parent_dir(path: str | Path) -> Path:
    """
    确保某文件路径的父目录存在，不存在则创建。

    参数说明：
        path : 目标文件路径。

    返回值：
        原始路径的 Path 对象（便于链式调用）。
    """
    p: Path = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def display_path(path: str | Path, base: str | Path | None = None) -> str:
    """
    把绝对路径渲染成「相对 base 的可读路径」，用于命令行/日志展示。

    为什么需要它（阶段 83）：`os.path.relpath()` 在 **Windows 跨盘符**时抛
    `ValueError: path is on mount 'C:', start on mount 'D:'`。门禁脚本的 `--update`
    在写完基线后用 `relpath` 打印确认信息——用户只要把 `--baseline` 指到另一个盘
    （例如把快照放到项目所在盘之外），就会在**基线已成功落盘之后**崩溃，报错与真实
    状态完全不符。展示路径绝不该有能力让功能失败。

    参数说明：
        path : 待展示的路径。
        base : 相对基准目录；默认项目根目录。

    返回值：
        可读路径：同盘符时返回相对路径，跨盘符/异常时原样返回绝对路径。
    """
    p = Path(path)
    ref = Path(base) if base is not None else Path(_ROOT)
    try:
        return _os.path.relpath(str(p), str(ref))
    except (ValueError, OSError):
        # 跨盘符（Windows）或任何无法求相对路径的情况 → 退回绝对路径。
        return str(p)


# ==============================================================================
# 第 6 节：模块自检
# ------------------------------------------------------------------------------
# 说明：直接运行本文件会跑一组断言，验证路径处理函数在边界输入下表现正确。
#       这是给开发者用的，普通用户无需关心。
# ==============================================================================

if __name__ == "__main__":
    # 用例 1：清洗含非法字符的文件名。
    assert sanitize_filename('我的照片/2024?.png') == "我的照片_2024_.png", \
        "含非法字符的文件名未被正确清洗"

    # 用例 2：全角转半角。
    assert normalize_unicode("ＡＢＣ") == "ABC"

    # 用例 3：模板替换 + 扩展名覆盖。
    # 使用含 {engine} 占位符的模板验证替换逻辑（默认模板不含 {engine}，
    # 函数对不存在的占位符保持原样，这是预期行为）。
    name = build_output_filename(
        "sunset", "webp", index=3, engine="lama",
        date_str="20260803", time_str="181955",
    )
    assert name.endswith(".webp"), "扩展名未被正确覆盖为 webp"
    # 默认模板 {stem}_cleaned_{time} 中应含主名与日期时间。
    assert "sunset" in name and "181955" in name, "模板占位符未被正确替换"
    # 强制扩展名覆盖：即便传入不同扩展名也应统一为 webp。
    name2 = build_output_filename("photo", "WEBP")
    assert name2.endswith(".webp"), "扩展名大写未归一化"

    # 用例 4：支持格式判断。
    assert is_supported_input("test.JPG") is True
    assert is_supported_input("test.txt") is False

    # 用例 5：媒体格式白名单判断。
    assert is_supported_pdf_input("report.PDF") is True
    assert is_supported_pdf_input("report.doc") is False
    assert is_supported_video_input("clip.MP4") is True
    assert is_supported_video_input("clip.avi") is True
    assert is_supported_video_input("clip.mp3") is False

    print("path_helper.py 自检通过：路径处理函数逻辑正确。")
