# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/model_manager.py
 文件说明 : 模型注册表与多模型管理（阶段 12 新增）。

 设计目标
 --------------------------------------------------------------------------
 阶段 11 支持了「插件引擎」（换算法）。阶段 12 进一步支持「多模型权重切换」：
 同一套 LaMa TorchScript 接口（输入 image+mask，输出修复图），可以挂多个
 不同的 .pt 权重文件，并在它们之间随时切换。

 核心概念：模型槽（ModelSlot）
 --------------------------------------------------------------------------
 每个权重文件是一个「模型槽」，包含：
   · name        唯一键（小写），如 "lama" / "lama_fp32"
   · display_name 界面显示名
   · path        权重文件完整路径（落在 MODEL_DIR 下）
   · expected_size 预期字节数（用于下载后校验，0 表示不校验）
   · sha256      SHA256 校验值（可选）
   · urls        下载地址列表（可选）
   · builtin     是否内置槽（lama 为 True）

 注册来源（按优先级，后者不覆盖前者已注册的）：
   1. 内置槽 lama（指向 config.MODEL_PATH）；
   2. config.MODEL_SLOTS 显式登记的槽；
   3. 若开启 MODEL_AUTO_DISCOVER，扫描 MODEL_DIR 下的 .pt/.onnx 自动发现。

 当前模型（active）
 --------------------------------------------------------------------------
 所有 LaMa 引擎修复都加载「当前模型槽」的权重。切换 active 即用新权重，
 不需改任何调用方代码。active 持久化到 MODEL_STATE_FILE，重启仍生效。

 关键约定
 --------------------------------------------------------------------------
 · 本模块属于 core 层，严禁 import gradio / 任何界面组件。
 · 零 torch 依赖：torch 仅在 load_model 内部延迟导入。
 · 下载/覆盖前自动备份（path + MODEL_BACKUP_SUFFIX），校验失败自动回滚。
 · 加载/下载失败只抛明确异常（来自 utils.exceptions），不静默吞掉。
==============================================================================
"""

from __future__ import annotations

import hashlib
import json
import os as _os
import sys as _sys
from dataclasses import dataclass, field
from typing import Callable, Optional

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config
from utils.exceptions import (
    ModelCorruptedError,
    ModelLoadError,
    ModelNotFoundError,
)
from utils.logger import get_logger
from utils.path_helper import non_ascii_path_warning

log = get_logger("core.model_manager")

# 进度回调类型：callback(downloaded: int, total: int, speed_kbps: float) -> None
DownloadProgress = Callable[[int, int, float], None]


# ==============================================================================
# 第 1 节：模型槽数据类
# ==============================================================================
@dataclass
class ModelSlot:
    """一个权重文件的描述（模型槽）。"""

    name: str
    display_name: str = ""
    filename: str = ""          # 落在 MODEL_DIR 下的文件名
    path: object = field(default=None)  # pathlib.Path，绝对路径
    expected_size: int = 0      # 预期字节数，0 表示不校验体积
    sha256: str = ""            # SHA256 校验值，空表示不校验
    urls: tuple = field(default_factory=tuple)  # 下载地址列表
    note: str = ""
    builtin: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "filename": self.filename,
            "path": str(self.path) if self.path else "",
            "expected_size": self.expected_size,
            "expected_mb": round(self.expected_size / 1e6, 1) if self.expected_size else 0,
            "sha256": self.sha256,
            "urls": list(self.urls),
            "note": self.note,
            "builtin": self.builtin,
        }


# ==============================================================================
# 第 2 节：注册表（进程内单例）
# ==============================================================================
_SLOTS: dict[str, ModelSlot] = {}
_ACTIVE: str = "lama"
_INITED: bool = False
# 模型缓存：路径 -> torch 模型对象（与 model_loader 的单例语义一致，但按路径区分）。
_CACHE: dict[str, object] = {}

#: **已知 fp16（半精度）不安全的模型** → 加载时强制保持 float32。
#:
#: 实测依据（阶段 207 · RTX 5060 + torch 2.9.1+cu128）：
#:   · LaMa 的 FFC（Fast Fourier Convolution）使用**复数运算**，而 PyTorch 的
#:     `ComplexHalf` 至今是实验性（运行时告警 “ComplexHalf support is experimental
#:     and many operators don't support it yet”）⇒ fp16 前向**输出全 NaN**
#:     （实测 2048×2048 图：12,582,912 / 12,582,912 个元素全为 NaN，值域 [nan, nan]）。
#:   · 后果是**静默毁图**：`tensor_to_rgb` 的 `clamp(0,1)` 拦不住 NaN，
#:     `astype(np.uint8)` 把 NaN 变 0 ⇒ 修复区域被填成黑色块。
#:     实测掩码区均值 172.2 → **5.65**，近黑像素占比 **73.9%**（fp32 下为 161.96 / 0.0%）。
#:   · 人眼复核：fp16 输出上出现大片黑色斑点 + 水印处一块纯黑矩形，而 API 仍报 success。
#: ⇒ 该缺陷此前从未被发现，因为全部质量验证（172/177 real_gate）都跑在 CPU/fp32 上，
#:   而 GPU 路径直到阶段 207 才第一次真正跑起来。
_FP16_UNSAFE_MODELS = frozenset({"lama"})


def _is_fp16_unsafe(name: str) -> bool:
    """该模型是否**已知**不能安全使用 fp16（纯函数，便于测试）。"""
    return str(name).lower() in _FP16_UNSAFE_MODELS


def _resolve_path(filename: str):
    """把文件名解析为 MODEL_DIR 下的绝对路径。"""
    return config.MODEL_DIR / filename


# ==============================================================================
# 第 3 节：初始化（惰性，仅一次）
# ==============================================================================
def _init_builtin() -> None:
    """注册内置 lama 槽（指向 config.MODEL_PATH）。"""
    p = str(config.MODEL_PATH)
    _SLOTS["lama"] = ModelSlot(
        name="lama",
        display_name="LaMa（默认）",
        filename=config.MODEL_FILENAME,
        path=config.MODEL_PATH,
        expected_size=int(config.MODEL_EXPECTED_SIZE),
        sha256=config.MODEL_SHA256.strip(),
        urls=tuple(config.MODEL_DOWNLOAD_URLS),
        note="内置 LaMa TorchScript 权重，效果最好",
        builtin=True,
    )


def _init_config_slots() -> None:
    """从 config.MODEL_SLOTS 显式登记额外槽。"""
    slots = config.MODEL_SLOTS
    if not isinstance(slots, list):
        return
    for item in slots:
        if not isinstance(item, dict) or not item.get("name") or not item.get("filename"):
            continue
        name = str(item["name"]).lower()
        if name in _SLOTS:
            continue
        _SLOTS[name] = ModelSlot(
            name=name,
            display_name=str(item.get("display_name") or item["name"]),
            filename=str(item["filename"]),
            path=_resolve_path(str(item["filename"])),
            expected_size=int(item.get("expected_size") or 0),
            sha256=str(item.get("sha256") or ""),
            urls=tuple(item.get("urls") or ()),
            note=str(item.get("note") or ""),
            builtin=False,
        )


def _init_discover() -> None:
    """
    扫描 MODEL_DIR 下的 .pt/.onnx 文件，自动注册为槽（不覆盖已有槽）。

    注意：判重必须同时看**槽 key**与**已登记的文件名**。
    只比 key 的话，内置槽（key=lama，文件名= big-lama.pt）的文件会被再注册成
    一个名为 big-lama 的槽，与 lama 指向同一份权重——模型列表里出现重复项，
    用户可对其分别下载/回滚同一个文件，却没有第二份权重。
    """
    if not config.MODEL_AUTO_DISCOVER:
        return
    d = config.MODEL_DIR
    # 「自动发现」是**可选增强**：目录探测的 I/O 失败（目录被删/权限/TOCTOU）
    # 不得让整个注册表初始化失败——否则内置槽 lama 都可能拿不到，
    # 而 `_ensure_init` 当时已经把「已初始化」置位，注册表就被永久固化在半初始化态
    # （阶段 94 D1）。这里就地降级：告警 + 跳过发现，注册表仍可用。
    try:
        if not d.exists() or not d.is_dir():
            return
        entries = sorted(_os.listdir(str(d)))
    except OSError as exc:
        log.warning("扫描模型目录失败，跳过自动发现（不影响内置槽）：%s", exc)
        return
    taken_keys = set(_SLOTS.keys())
    taken_files = {str(s.filename).lower() for s in _SLOTS.values() if s.filename}
    for fname in entries:
        if not (fname.endswith(".pt") or fname.endswith(".onnx")):
            continue
        stem = _os.path.splitext(fname)[0].lower()
        # 已注册的槽 key 或已登记的文件名，都算「这个文件已经有人管了」。
        if stem in taken_keys or fname.lower() in taken_files:
            continue
        _SLOTS[stem] = ModelSlot(
            name=stem,
            display_name=stem,
            filename=fname,
            path=d / fname,
            expected_size=0,
            sha256="",
            urls=(),
            note="自动发现于模型目录",
            builtin=False,
        )
        taken_keys.add(stem)
        taken_files.add(fname.lower())


def _load_state() -> None:
    """从 MODEL_STATE_FILE 读取持久化的 active 模型（若存在且合法）。"""
    global _ACTIVE
    try:
        if config.MODEL_STATE_FILE.exists():
            data = json.loads(config.MODEL_STATE_FILE.read_text(encoding="utf-8"))
            active = str(data.get("active", "")).lower()
            if active and active in _SLOTS:
                _ACTIVE = active
                return
    except Exception as exc:  # noqa: BLE001
        log.debug("读取模型状态文件失败，使用默认 active：%s", exc)
    # 未读到有效状态：回退到配置默认值。
    default = str(config.ACTIVE_MODEL).lower()
    _ACTIVE = default if default in _SLOTS else "lama"


def _ensure_init() -> None:
    """惰性初始化注册表（仅一次）。

    ⚠ `_INITED` **必须在全部初始化步骤成功之后**才置位（阶段 94 D1 修正）。
    此前实现是「先置位、再初始化」，于是中途一旦抛异常，`_INITED` 永远为 True，
    后续调用直接短路返回 —— 注册表被**永久固化在半初始化状态**且不再重试，
    异常却仍冒给调用方（同族：阶段 79「容错成为错误的藏身处」）。

    重入性：下面四个初始化函数都只写本模块状态、不回调公开 API，
    故把置位移到末尾不会引入递归。
    """
    global _INITED
    if _INITED:
        return
    _init_builtin()
    _init_config_slots()
    _init_discover()
    _load_state()
    _INITED = True


# ==============================================================================
# 第 4 节：注册与查询
# ==============================================================================
def register_model_slot(
    name: str,
    display_name: str = "",
    filename: str = "",
    expected_size: int = 0,
    sha256: str = "",
    urls: tuple = (),
    note: str = "",
    builtin: bool = False,
) -> ModelSlot:
    """
    注册（或覆盖）一个模型槽。

    参数说明：
        name         : 槽唯一键（小写）。
        display_name : 界面显示名。
        filename     : 权重文件名（落在 MODEL_DIR 下）；缺省用 name + 推断扩展名。
        expected_size: 预期字节数（0 不校验）。
        sha256       : SHA256 校验值（空不校验）。
        urls         : 下载地址列表。
        note         : 备注。
        builtin      : 是否内置槽。

    返回值：
        注册好的 ModelSlot 实例。
    """
    _ensure_init()
    key = str(name).lower()
    if not filename:
        filename = key + ".pt"
    slot = ModelSlot(
        name=key,
        display_name=display_name or key,
        filename=filename,
        path=_resolve_path(filename),
        expected_size=int(expected_size or 0),
        sha256=str(sha256 or ""),
        urls=tuple(urls or ()),
        note=str(note or ""),
        builtin=builtin,
    )
    _SLOTS[key] = slot
    log.info("已注册模型槽：%s（显示名：%s，路径：%s）", key, slot.display_name, slot.path)
    return slot


def list_model_slots() -> list[ModelSlot]:
    """返回全部模型槽（含内置、配置、自动发现）。"""
    _ensure_init()
    return list(_SLOTS.values())


def list_models() -> list[dict]:
    """返回全部模型槽的字典形式（供 UI / API 展示）。"""
    return [s.to_dict() for s in list_model_slots()]


def get_model_slot(name: Optional[str] = None) -> Optional[ModelSlot]:
    """按名称取模型槽；name 为 None 时返回当前（active）槽。"""
    _ensure_init()
    if not name:
        return _SLOTS.get(_ACTIVE)
    return _SLOTS.get(str(name).lower())


def get_active_model() -> str:
    """返回当前模型槽名称。"""
    _ensure_init()
    return _ACTIVE


def set_active_model(name: str, persist: bool = True) -> bool:
    """
    切换当前模型槽。

    参数说明：
        name    : 目标槽名称（必须已注册）。
        persist : 是否持久化到 MODEL_STATE_FILE（重启仍生效）。

    返回值：
        True 表示切换成功；False 表示槽不存在或名称非法。
    """
    _ensure_init()
    key = str(name).lower()
    if key not in _SLOTS:
        log.warning("切换当前模型失败：未找到模型槽 %s", key)
        return False
    global _ACTIVE
    _ACTIVE = key
    # 切换模型应清空权重缓存，避免误用旧权重。
    clear_cache()
    if persist:
        try:
            config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
            config.MODEL_STATE_FILE.write_text(
                json.dumps({"active": key}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info("已将当前模型持久化为：%s", key)
        except Exception as exc:  # noqa: BLE001
            log.warning("持久化当前模型失败（不影响本次运行）：%s", exc)
    return True


# ==============================================================================
# 第 5 节：存在性与校验
# ==============================================================================
def _sha256_of_file(path: str, chunk: int = 8 * 1024 * 1024) -> str:
    """分块计算大文件 SHA256，避免一次性读入内存。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def is_model_available(name: Optional[str] = None) -> bool:
    """
    判断指定（或当前）模型槽的权重是否已就位（不加载，只检查存在性与体积）。

    返回值：
        True 表示存在且体积合理（或未知预期体积时只要存在即认为可用）。
    """
    slot = get_model_slot(name)
    if slot is None or slot.path is None:
        return False
    p = str(slot.path)
    if not _os.path.exists(p):
        return False
    if slot.expected_size and slot.expected_size > 0:
        if _os.path.getsize(p) < int(slot.expected_size * 0.9):
            return False
    return True


# verify_model / slot_detail / api.verify_model 对外承诺的键集合。
# 这三处的返回字典会被「按固定键读取」的调用方消费（ui 详情面板直接取
# size_mb / mtime；api.verify_model 的 docstring 明写「含 size_mb 等」），
# 因此**键集合必须与分支无关**：少一个键就是调用方的一次 KeyError。
# 此前 verify_model 的「槽不存在」分支只给 4 个键、正常分支给 8 个；
# slot_detail 的「槽不存在」分支只给 4 个、正常分支给 13 个。
_VERIFY_KEYS: tuple = (
    "name", "exists", "size_mb", "expected_mb",
    "size_ok", "sha_ok", "sha_error", "ok", "reason",
)


def blank_verify_result(name: Optional[str], reason: str = "未找到模型槽") -> dict:
    """
    返回与 verify_model 完全同构的「无法校验」结果。

    用途：统一「槽不存在」「未放权重」「API 兜底」等早返回分支的形状，
    避免调用方在不同的失败分支上遇到不同的键集合。
    """
    return {
        "name": str(name or _ACTIVE),
        "exists": False,
        "size_mb": 0.0,
        "expected_mb": 0.0,
        "size_ok": False,
        "sha_ok": False,
        "sha_error": "",
        "ok": False,
        "reason": reason,
    }


def verify_model(name: Optional[str] = None) -> dict:
    """
    对指定（或当前）模型槽做完整性校验（存在性 + 体积 + 可选 SHA256）。

    返回值：
        字典：{name, exists, size_mb, expected_mb, size_ok, sha_ok, sha_error,
        ok, reason}
        —— 键集合与分支无关（见 _VERIFY_KEYS 的说明），任何分支都返回全部键。

    说明：
        「SHA256 算不出来」（文件读不了，如被占用 / 权限不足）与
        「SHA256 算出来不匹配」（文件确实被改过）是两回事：
        前者记进 sha_error 并原样写进 reason，不会被套上「文件可能被篡改/损坏」
        这种指向错误方向的结论。
    """
    slot = get_model_slot(name)
    if slot is None:
        return blank_verify_result(name, "未找到模型槽")
    p = str(slot.path)
    info = blank_verify_result(slot.name)
    info["expected_mb"] = round(slot.expected_size / 1e6, 1) if slot.expected_size else 0.0
    info["sha_ok"] = True  # 未登记 sha256 时视为「无需校验 → 通过」
    if not _os.path.exists(p):
        info["reason"] = f"权重文件不存在：{p}"
        return info
    size = _os.path.getsize(p)
    info["exists"] = True
    info["size_mb"] = round(size / 1e6, 1)
    if slot.expected_size and slot.expected_size > 0:
        info["size_ok"] = size >= int(slot.expected_size * 0.9)
    else:
        info["size_ok"] = True
    if slot.sha256:
        try:
            actual = _sha256_of_file(p)
            info["sha_ok"] = actual.lower() == slot.sha256.lower()
        except Exception as exc:  # noqa: BLE001
            info["sha_ok"] = False
            info["sha_error"] = f"{type(exc).__name__}: {exc}"
    info["ok"] = bool(info["exists"] and info["size_ok"] and info["sha_ok"])
    if info["ok"]:
        info["reason"] = "校验通过"
    elif not info["exists"]:
        info["reason"] = f"权重文件不存在：{p}"
    elif not info["size_ok"]:
        info["reason"] = f"体积异常：{info['size_mb']}MB / 预期约 {info['expected_mb']}MB"
    elif info["sha_error"]:
        info["reason"] = (
            f"SHA256 计算失败（无法读取文件，并非校验不匹配）：{info['sha_error']}"
        )
    else:
        info["reason"] = "SHA256 校验失败（文件可能被篡改/损坏）"
    return info


# ==============================================================================
# 第 6 节：加载（委托给 torch.jit.load，按槽路径）
# ==============================================================================
def load_model(
    name: Optional[str] = None,
    device=None,
    force_reload: bool = False,
):
    """
    加载指定（或当前）模型槽的权重，返回单例（已设为 eval 模式）。

    参数说明：
        name         : 槽名称；None 表示当前 active 槽。
        device       : 目标设备句柄（core.device_manager._DeviceHandle）；None 自动选择。
        force_reload : 强制重新加载（忽略缓存）。

    返回值：
        已加载的 torch.jit 模型对象（可直接调用 model(image, mask)）。

    异常：
        ModelNotFoundError / ModelCorruptedError / ModelLoadError
    """
    slot = get_model_slot(name)
    if slot is None or slot.path is None:
        raise ModelNotFoundError(f"未找到模型槽：{name or _ACTIVE}")
    model_path = str(slot.path)
    cache_key = model_path

    # 命中缓存且非强制刷新：直接返回。
    if not force_reload and cache_key in _CACHE:
        return _CACHE[cache_key]

    # 先做存在性 / 完整性校验，给出友好提示。
    v = verify_model(slot.name)
    if not v["exists"]:
        raise ModelNotFoundError(
            f"未找到模型权重：{model_path}\n"
            f"请执行 download_model.py 自动下载，或手动放入 models/ 目录。"
        )
    if not v["size_ok"]:
        raise ModelCorruptedError(
            f"模型文件体积异常：{v['size_mb']} MB，预期约 {v['expected_mb']} MB。"
            f"\n文件可能下载不完整，请删除后重新下载。"
        )
    if not v["sha_ok"]:
        raise ModelCorruptedError(
            f"模型文件 SHA256 校验失败（文件可能被篡改/损坏）：{model_path}"
        )

    # 解析目标设备。
    if device is None:
        from core.device_manager import select_device

        device = select_device()
    torch_device = device.torch_device

    try:
        import torch

        log.info("正在加载模型 %s：%s（映射至 %s）", slot.name, model_path, torch_device)
        model = torch.jit.load(model_path, map_location=torch_device)
        model.eval()
        if device.is_gpu and config.DEVICE_USE_FP16 and not _is_fp16_unsafe(slot.name):
            try:
                model = model.half()
                log.info("已启用半精度（float16）推理。")
            except Exception as exc:
                log.warning("半精度转换失败，回退 float32：%s", exc)
        elif device.is_gpu and config.DEVICE_USE_FP16 and _is_fp16_unsafe(slot.name):
            log.info("模型 %s 已知 fp16 不安全，保持 float32（见 _FP16_UNSAFE_MODELS 的实测依据）。",
                     slot.name)
        _CACHE[cache_key] = model
        log.info("模型 %s 加载完成。", slot.name)
        return model
    except ModelNotFoundError:
        raise
    except ModelCorruptedError:
        raise
    except Exception as exc:
        # 阶段 314：非 ASCII 路径的失败长得像「文件缺失」（WinError 127），
        # 若不点明，用户会去重下权重而不是改路径。
        _path_hint = non_ascii_path_warning(model_path)
        raise ModelLoadError(
            f"模型 {slot.name} 加载失败：{exc}\n"
            f"可能原因：PyTorch 版本过低、模型文件损坏、或设备不可用。"
            + (f"\n{_path_hint}" if _path_hint else "")
        ) from exc


def clear_cache() -> None:
    """清空模型权重缓存并释放显存。"""
    global _CACHE
    _CACHE = {}
    try:
        from core.device_manager import empty_cache

        empty_cache()
    except Exception:  # noqa: BLE001
        pass


def cache_size() -> int:
    """返回当前进程内已缓存（已加载到内存）的模型数量。"""
    return len(_CACHE)


def reload_active(device=None) -> dict:
    """
    清空当前 active 模型的内存缓存并强制重新加载。

    应用场景：用户在程序运行期间手动替换 / 重新下载了某个权重文件，
    而该文件路径与缓存键相同（旧内存模型仍驻留），调用此函数即可
    「热重载」而无需重启应用。

    返回值：
        {ok, name, loaded, error}
    """
    name = _ACTIVE
    try:
        clear_cache()
        load_model(None, device, force_reload=True)
        return {"ok": True, "name": name, "loaded": True, "error": ""}
    except Exception as exc:  # noqa: BLE001
        log.warning("重新加载模型 %s 失败：%s", name, exc)
        return {"ok": False, "name": name, "loaded": False, "error": str(exc)}


def slot_detail(name: Optional[str] = None) -> dict:
    """
    返回单个模型槽的详尽元数据（供 UI 详情面板）。

    在 verify_model 的基础上补充：display_name、path、mtime（本地修改时间）、
    sha_computed（实测 SHA256，仅当槽登记了 sha256 时计算）、loaded（是否已在内存）。

    返回值：
        字典（含 name/display_name/path/exists/size_mb/expected_mb/mtime/
        sha256/sha_computed/sha_ok/size_ok/loaded/ok/reason）。
    """
    slot = get_model_slot(name)
    if slot is None:
        # 与「存在」分支保持同一键集合（见 _VERIFY_KEYS 注释）：
        # 否则调用方读 size_mb / mtime 会 KeyError。
        detail = blank_verify_result(name, "未找到模型槽")
        detail.update({
            "display_name": str(name or _ACTIVE),
            "path": "",
            "mtime": "",
            "sha_computed": "",
            "loaded": False,
        })
        return detail
    p = str(slot.path)
    detail = dict(verify_model(slot.name))  # 复用完整性校验结果
    detail["display_name"] = slot.display_name or slot.name
    detail["path"] = p
    detail["mtime"] = ""
    detail["sha_computed"] = ""
    if _os.path.exists(p):
        try:
            st = _os.stat(p)
            import time as _time

            detail["mtime"] = _time.strftime(
                "%Y-%m-%d %H:%M:%S", _time.localtime(st.st_mtime)
            )
        except Exception:  # noqa: BLE001
            pass
        if slot.sha256:
            try:
                detail["sha_computed"] = _sha256_of_file(p)
            except Exception:  # noqa: BLE001
                detail["sha_computed"] = ""
    detail["loaded"] = (p in _CACHE) if p else False
    return detail


def get_model_info() -> dict:
    """返回模型管理整体状态，供 UI / API 展示（兼容旧 get_model_info 字段）。"""
    from core.device_manager import get_device_info

    slot = get_model_slot(None)
    di = get_device_info()
    base = {
        "active_model": _ACTIVE,
        "models": list_models(),
        "device": di.kind,
        "device_name": di.name,
        "device_is_gpu": di.is_gpu,
        "vram_mb": di.vram_mb,
        "fp16": bool(config.DEVICE_USE_FP16 and di.is_gpu),
        "cache_count": len(_CACHE),
    }
    if slot is not None and slot.path is not None:
        p = str(slot.path)
        base.update(
            {
                "exists": _os.path.exists(p),
                "path": p,
                "size_mb": round(_os.path.getsize(p) / 1e6, 1) if _os.path.exists(p) else 0,
                "loaded": str(slot.path) in _CACHE,
            }
        )
    else:
        base.update({"exists": False, "path": "", "size_mb": 0, "loaded": False})
    return base


# ==============================================================================
# 第 7 节：下载（带备份 + 校验 + 失败回滚）
# ==============================================================================
def _backup_path(slot: ModelSlot) -> str:
    """返回该槽权重的备份文件路径（原路径 + MODEL_BACKUP_SUFFIX）。"""
    return str(slot.path) + config.MODEL_BACKUP_SUFFIX


def rollback_model(name: Optional[str] = None) -> bool:
    """
    从备份恢复指定（或当前）模型槽的权重。

    返回值：
        True 表示恢复成功；False 表示无备份或恢复失败。
    """
    slot = get_model_slot(name)
    if slot is None or slot.path is None:
        return False
    bak = _backup_path(slot)
    if not _os.path.exists(bak):
        log.warning("回滚失败：未找到备份文件 %s", bak)
        return False
    try:
        import shutil

        shutil.copy2(bak, str(slot.path))
        log.info("已从备份恢复模型 %s：%s", slot.name, slot.path)
        # 恢复后清空缓存，确保下次用新（恢复后的）权重。
        _CACHE.pop(str(slot.path), None)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("回滚失败：%s", exc)
        return False


def download_model(
    name: Optional[str] = None,
    progress_cb: Optional[DownloadProgress] = None,
    auto_pick: bool = True,
) -> str:
    """
    下载指定（或当前）模型槽的权重，带备份 + 校验 + 失败回滚。

    参数说明：
        name       : 槽名称；None 表示当前 active 槽。
        progress_cb: 进度回调 (downloaded, total, speed_kbps)；None 用文本进度条。
        auto_pick  : 是否按扩展名筛选下载源。

    返回值：
        下载完成的权重文件绝对路径。

    说明：
        · 覆盖前先把现有权重备份为「原路径.bak」；
        · 下载完成后校验（体积 / SHA256），失败则自动回滚备份并抛异常。
    """
    slot = get_model_slot(name)
    if slot is None or slot.path is None:
        raise ModelNotFoundError(f"未找到模型槽：{name or _ACTIVE}")

    from download_model import download_to

    dest = str(slot.path)
    # 阶段 165：槽必须**自带** urls。此前「槽无 urls ⇒ 回退全局默认源」会把**内置 lama**
    # 的下载地址用到**别的槽**上：权重下进该槽路径，而该槽若无 expected_size / sha256，
    # 校验会**静默通过**（探针实测：无 urls 的槽收到 lama 的 3 个地址）——等于静默装错权重。
    # 无下载源是**明确失败**，不是回退到别人的源。
    urls = list(slot.urls)
    if not urls:
        raise ModelNotFoundError(
            f"模型槽 {slot.name} 未提供下载地址（urls 为空）：请手动放入权重文件，"
            f"或在 config.MODEL_SLOTS 中为该槽登记 urls。"
        )

    # 覆盖前备份现有权重（若有）。
    if _os.path.exists(dest):
        try:
            import shutil

            shutil.copy2(dest, _backup_path(slot))
            log.info("已备份现有权重：%s", _backup_path(slot))
        except Exception as exc:  # noqa: BLE001
            log.warning("备份现有权重失败（继续下载）：%s", exc)

    try:
        path = download_to(
            dest=dest,
            urls=urls,
            progress_cb=progress_cb,
            auto_pick=auto_pick,
            expected_size=slot.expected_size or None,
            sha256=slot.sha256 or "",
            timeout=config.MODEL_DOWNLOAD_TIMEOUT,
            retry=config.MODEL_DOWNLOAD_RETRY,
        )
        # 下载后再次确认校验通过（download_to 内部已校验，这里双保险）。
        v = verify_model(slot.name)
        if not v["ok"]:
            raise ModelCorruptedError(f"下载后校验未通过：{v['reason']}")
        # 校验通过则删掉备份（已无用），释放磁盘。
        bak = _backup_path(slot)
        if _os.path.exists(bak):
            try:
                _os.remove(bak)
            except OSError:
                pass
        # 阶段 165：下载成功必须**丢弃该路径的旧缓存模型**——否则已加载过的旧模型会继续被
        # `load_model` 命中（除用户手动热重载外，静默沿用旧权重）。`rollback_model` 早已
        # pop 该键，此处补齐对称（同一风险面两种处理方式）。
        _CACHE.pop(dest, None)
        log.info("模型 %s 下载并校验成功：%s", slot.name, path)
        return path
    except Exception as exc:  # noqa: BLE001
        # 下载/校验失败：尝试回滚到备份，保证「升级翻车」可还原。
        log.warning("模型 %s 下载失败：%s，尝试回滚备份。", slot.name, exc)
        if rollback_model(slot.name):
            log.info("已自动回滚到上一版权重。")
        else:
            log.warning("无可用备份，权重可能处于不完整状态，请重新下载。")
        raise


# ==============================================================================
# 第 8 节：自检
# ==============================================================================
if __name__ == "__main__":
    # 验证注册表 + active 切换 + 校验逻辑。
    _ensure_init()
    assert get_model_slot("lama") is not None, "内置 lama 槽应存在"
    assert is_model_available("lama") in (True, False), "is_model_available 应可调用"
    # 注册一个临时槽并切换 active。
    register_model_slot("__selftest__", display_name="自检槽", filename="__selftest__.pt")
    assert set_active_model("__selftest__", persist=False), "应能切换到临时槽"
    assert get_active_model() == "__selftest__", "当前模型应为临时槽"
    # 切回 lama 并清理临时槽（从注册表移除，仅自检用）。
    set_active_model("lama", persist=False)
    _SLOTS.pop("__selftest__", None)
    ver = verify_model("__selftest__")
    assert ver["exists"] is False, "缺失槽校验应返回 exists=False"
    print("core/model_manager.py 自检通过：注册表 + active 切换 + 校验 均正常。")
