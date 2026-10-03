# -*- coding: utf-8 -*-
"""
阶段 89 · core 设备/模型管理真值审计测试。

审计对象（按「模块行数 ÷ 测试函数数」修正后的选点指标）：
  · core/model_manager.py（725 行 / 13 个测试函数）—— 主战场
  · core/device_manager.py（383 行 / 6 个测试函数）—— 复核 + 一处资源泄漏

本文件钉住的缺陷：
  A  verify_model 把「SHA256 算不出来」（文件被占用/权限不足）覆盖成
     「SHA256 校验失败（文件可能被篡改/损坏）」—— 精确原因被泛化结论覆盖，
     且覆盖后的措辞把「读不了」断言成「被改过」。
  B  verify_model / slot_detail / api.verify_model 的返回字典在分支间
     schema 不一致（4 键 vs 8/13 键），而 api 的 docstring 承诺「含 size_mb 等」。
  C  _init_discover 用「文件名 stem」去比「槽 key」，注释声称「内置槽的文件名已注册，
     跳过」实际没跳 —— 内置权重被再注册成第二个槽（本机实测：lama 与 big-lama
     指向同一份 big-lama.pt，模型列表出现重复项）。
  E  _query_free_vram_mb 只在成功路径 nvmlShutdown()，异常路径漏关（NVML 停在
     「已初始化未关闭」态）——「静默容错把资源泄漏藏起来」。

同时把「已验证为非缺陷」的两项钉住，防止后人以「去重/优化」名义改坏：
  · load_model 内联的 fp16 判据**不能**改成 device_manager.use_fp16()；
  · select_device 的缓存复用语义（auto 复用已定下来的设备）是声明过的行为。
"""

import inspect
import sys

import pytest

import config
from core import device_manager as dm
from core import model_manager as mm


# ------------------------------------------------------------------------------
# fixture：隔离 model_manager 的模块级可变状态
# ------------------------------------------------------------------------------
@pytest.fixture
def isolate_registry():
    """快照 / 还原 model_manager 的全局注册表与缓存（避免用例间污染）。"""
    saved_slots = dict(mm._SLOTS)
    saved_active = mm._ACTIVE
    saved_inited = mm._INITED
    cache_ref = mm._CACHE
    saved_cache = dict(cache_ref)
    try:
        yield mm
    finally:
        mm._SLOTS.clear()
        mm._SLOTS.update(saved_slots)
        mm._ACTIVE = saved_active
        mm._INITED = saved_inited
        # clear_cache() 会重新绑定 _CACHE，故先还原对象引用再回填内容。
        mm._CACHE = cache_ref
        cache_ref.clear()
        cache_ref.update(saved_cache)


def _patch_file_present(monkeypatch, size=1000):
    """模拟「文件存在且体积足够」——本环境禁用真实落盘/删除，一律用 monkeypatch。"""
    monkeypatch.setattr(mm._os.path, "exists", lambda p: True)
    monkeypatch.setattr(mm._os.path, "getsize", lambda p: size)


# ==============================================================================
# 缺陷 B：返回字典的键集合必须与分支无关
# ==============================================================================
def test_verify_keys_constant_matches_verify_model(isolate_registry):
    """对账：blank_verify_result 的键集合 == _VERIFY_KEYS == verify_model 正常分支。"""
    blank = mm.blank_verify_result("whatever")
    assert set(blank.keys()) == set(mm._VERIFY_KEYS)

    mm.register_model_slot("a89_ok", filename="a89_ok.pt")
    try:
        v = mm.verify_model("a89_ok")
        assert set(v.keys()) == set(mm._VERIFY_KEYS)
    finally:
        mm._SLOTS.pop("a89_ok", None)


def test_verify_model_key_set_is_branch_independent(isolate_registry, monkeypatch):
    """verify_model 的「槽不存在 / 文件不存在 / 校验通过」三个分支键集合必须一致。"""
    mm.register_model_slot("a89_nofile", filename="a89_nofile_noexist.pt")
    try:
        v_unknown = mm.verify_model("a89_totally_unknown")
        monkeypatch.setattr(mm._os.path, "exists", lambda p: False)
        v_nofile = mm.verify_model("a89_nofile")
        _patch_file_present(monkeypatch)
        v_ok = mm.verify_model("a89_nofile")
    finally:
        mm._SLOTS.pop("a89_nofile", None)

    assert v_unknown["exists"] is False and v_nofile["exists"] is False
    assert v_ok["ok"] is True
    keys = set(mm._VERIFY_KEYS)
    assert set(v_unknown.keys()) == keys, (
        f"「槽不存在」分支缺键：{sorted(keys - set(v_unknown.keys()))}"
    )
    assert set(v_nofile.keys()) == keys
    assert set(v_ok.keys()) == keys


def test_slot_detail_key_set_is_branch_independent(isolate_registry, monkeypatch):
    """slot_detail 的「槽不存在」分支必须给出与「存在」分支相同的键（UI 直接取 size_mb）。"""
    mm.register_model_slot("a89_det", filename="a89_det.pt")
    try:
        d_unknown = mm.slot_detail("a89_totally_unknown")
        _patch_file_present(monkeypatch)
        d_ok = mm.slot_detail("a89_det")
    finally:
        mm._SLOTS.pop("a89_det", None)

    missing = sorted(set(d_ok.keys()) - set(d_unknown.keys()))
    assert not missing, f"slot_detail 的缺失槽分支缺键：{missing}"
    # 这些键 UI 详情面板会无保护地读取，必须存在。
    for k in ("display_name", "path", "mtime", "sha_computed", "loaded", "size_mb"):
        assert k in d_unknown, f"缺失槽分支必须有 {k}，否则 UI 取详情会 KeyError"


def test_api_verify_fallback_key_set_matches(monkeypatch):
    """api.verify_model 的异常兜底分支键集合必须与 model_manager 一致（docstring 承诺 size_mb）。"""
    import api

    def _boom(name=None):
        raise RuntimeError("模拟校验异常")

    monkeypatch.setattr(mm, "verify_model", _boom)
    out = api.verify_model("whatever")
    missing = sorted(set(mm._VERIFY_KEYS) - set(out.keys()))
    assert not missing, f"api 兜底分支缺键：{missing}"
    assert out["ok"] is False and "校验异常" in out["reason"]


# ==============================================================================
# 缺陷 A：精确原因不得被泛化结论覆盖；「算不出来」≠「算出来不匹配」
# ==============================================================================
def test_sha_computation_failure_keeps_precise_reason(isolate_registry, monkeypatch):
    """SHA256 计算抛 PermissionError 时，日志/返回值必须说「无法读取」，不能说「被篡改」。"""
    mm.register_model_slot("a89_sha", filename="a89_sha.pt",
                           expected_size=1000, sha256="deadbeef" * 8)

    def _boom(path, chunk=8 * 1024 * 1024):
        raise PermissionError("拒绝访问：文件被其他进程占用")

    try:
        _patch_file_present(monkeypatch)
        monkeypatch.setattr(mm, "_sha256_of_file", _boom)
        v = mm.verify_model("a89_sha")
    finally:
        mm._SLOTS.pop("a89_sha", None)

    assert v["ok"] is False
    assert v["sha_error"], "必须把「计算失败」的原始异常记录下来（缺数据 ≠ 校验不通过）"
    assert "PermissionError" in v["sha_error"] and "拒绝访问" in v["sha_error"]
    assert "无法读取" in v["reason"], f"原因应指向读取失败，实际：{v['reason']!r}"
    assert "篡改" not in v["reason"], (
        f"读不了文件却被断言成「可能被篡改」，诊断方向错误：{v['reason']!r}"
    )


def test_sha_mismatch_still_reports_tamper(isolate_registry, monkeypatch):
    """
    对照组：SHA256 **算出来了但不匹配**时，仍应给出「可能被篡改/损坏」的结论。

    这条与上一条共同界定语义：修复不能让「真的不匹配」也变成「无法读取」。
    """
    mm.register_model_slot("a89_mis", filename="a89_mis.pt",
                           expected_size=1000, sha256="00" * 32)
    try:
        _patch_file_present(monkeypatch)
        monkeypatch.setattr(mm, "_sha256_of_file", lambda p, chunk=0: "ff" * 32)
        v = mm.verify_model("a89_mis")
    finally:
        mm._SLOTS.pop("a89_mis", None)

    assert v["ok"] is False
    assert v["sha_error"] == "", "计算成功时 sha_error 必须为空"
    assert "篡改" in v["reason"], f"真实不匹配应保留篡改结论，实际：{v['reason']!r}"


def test_old_reason_logic_would_misdiagnose(isolate_registry, monkeypatch):
    """注入式反例：把修复前的 reason 覆盖顺序搬回来，它必须产生误诊。"""
    mm.register_model_slot("a89_old", filename="a89_old.pt",
                           expected_size=1000, sha256="deadbeef" * 8)

    def _old_reason(size_ok, sha_ok):
        # 修复前：结尾按 size_ok → sha_ok 顺序无条件覆写 reason，
        # 于是「SHA256 计算失败」这条精确原因被「校验失败（可能被篡改）」取代。
        if size_ok and sha_ok:
            return "校验通过"
        if not size_ok:
            return "体积异常"
        return "SHA256 校验失败（文件可能被篡改/损坏）"

    try:
        _patch_file_present(monkeypatch)

        def _boom(path, chunk=8 * 1024 * 1024):
            raise PermissionError("拒绝访问")

        monkeypatch.setattr(mm, "_sha256_of_file", _boom)
        v_new = mm.verify_model("a89_old")
        assert _old_reason(True, False) == "SHA256 校验失败（文件可能被篡改/损坏）"
        assert v_new["reason"] != _old_reason(True, False), (
            "修复后不应再产出旧实现那种误诊结论"
        )
    finally:
        mm._SLOTS.pop("a89_old", None)


# ==============================================================================
# 缺陷 C：自动发现不得为「已登记的文件」再造一个槽
# ==============================================================================
class _FakeModelDir:
    """只实现 _init_discover 真正用到的接口：exists / is_dir / str / __truediv__。"""

    def __init__(self, names):
        self._names = list(names)

    def exists(self):
        return True

    def is_dir(self):
        return True

    def __truediv__(self, other):
        return "F:/fake_models/" + str(other)

    def __str__(self):
        return "F:/fake_models"


def test_discover_skips_already_registered_filename(isolate_registry, monkeypatch):
    """
    内置槽的文件名已登记 → 自动发现必须跳过该文件（修复点）。

    修复前：判重只比「文件名 stem」与「槽 key」，而内置槽 key=lama、
    文件名=big-lama.pt，两者不等 → 又注册一个 big-lama 槽指向同一文件。
    """
    mm.register_model_slot("a89_builtin", display_name="内置", filename="a89_w.pt")
    monkeypatch.setattr(mm.config, "MODEL_AUTO_DISCOVER", True)
    monkeypatch.setattr(mm.config, "MODEL_DIR", _FakeModelDir(["a89_w.pt", "a89_new.pt"]))
    monkeypatch.setattr(mm._os, "listdir", lambda p: ["a89_w.pt", "a89_new.pt"])
    try:
        mm._init_discover()
        assert "a89_w" not in mm._SLOTS, (
            "a89_w.pt 已由内置槽登记，不应被自动发现再造一个槽（会与内置槽指向同一文件）"
        )
        assert "a89_new" in mm._SLOTS, "未登记过的新文件仍应被发现"
        assert mm._SLOTS["a89_new"].filename == "a89_new.pt"
        assert mm._SLOTS["a89_new"].builtin is False
    finally:
        mm._SLOTS.pop("a89_new", None)
        mm._SLOTS.pop("a89_w", None)
        mm._SLOTS.pop("a89_builtin", None)


def test_real_registry_has_no_duplicate_paths():
    """
    真实注册表里不得有两个槽指向同一份权重文件（本机实测修复前为 lama + big-lama）。
    """
    by_path = {}
    for s in mm.list_model_slots():
        by_path.setdefault(str(s.path), []).append(s.name)
    dups = {p: names for p, names in by_path.items() if len(names) > 1}
    assert not dups, f"存在指向同一权重文件的重复槽：{dups}"


def test_old_discover_logic_would_duplicate(isolate_registry, monkeypatch):
    """注入式反例：修复前「只比 key」的判重逻辑，必须产生重复槽。"""

    class _Slot:
        def __init__(self, name, filename):
            self.name = name
            self.filename = filename

    def _old_discover(slots, files):
        slots = dict(slots)
        for fname in files:
            stem = fname.rsplit(".", 1)[0].lower()
            if stem in slots:            # ← 只比 key
                continue
            slots[stem] = _Slot(stem, fname)
        return slots

    slots = {"a89b": _Slot("a89b", "a89_w.pt")}          # 类似内置 lama
    out = _old_discover(slots, ["a89_w.pt", "a89_new.pt"])
    assert "a89_w" in out, "旧逻辑确实会把已登记的文件再注册一次"
    assert out["a89_w"].filename == "a89_w.pt"


# ==============================================================================
# 缺陷 E：NVML 必须成对 init/shutdown（异常路径也要关）
# ==============================================================================
class _FakeNvml:
    def __init__(self, fail_at=None, free=4 * 1024 * 1024):
        self.fail_at = fail_at
        self.free = free
        self.inits = 0
        self.shutdowns = 0

    def nvmlInit(self):
        self.inits += 1
        if self.fail_at == "init":
            raise RuntimeError("init 失败")

    def nvmlDeviceGetHandleByIndex(self, index):
        if self.fail_at == "handle":
            raise RuntimeError("拿不到句柄")
        return "handle"

    def nvmlDeviceGetMemoryInfo(self, handle):
        if self.fail_at == "mem":
            raise RuntimeError("读不到显存")
        return type("M", (), {"free": self.free})()

    def nvmlShutdown(self):
        self.shutdowns += 1


@pytest.mark.parametrize("fail_at", [None, "handle", "mem"])
def test_vram_query_pairs_init_and_shutdown(monkeypatch, fail_at):
    """成功与两种异常路径都必须恰好 shutdown 一次。"""
    fake = _FakeNvml(fail_at=fail_at)
    monkeypatch.setitem(sys.modules, "pynvml", fake)

    got = dm._query_free_vram_mb(0)
    assert fake.inits == 1
    assert fake.shutdowns == 1, (
        f"NVML 必须成对关闭（fail_at={fail_at}），实际 shutdown {fake.shutdowns} 次"
    )
    if fail_at is None:
        assert got == fake.free // (1024 * 1024)
    else:
        assert got is None, "查不到显存应返回 None，交由调用方保守处理"


def test_old_vram_query_leaks_nvml_on_error(monkeypatch):
    """注入式反例：修复前「只在成功路径 shutdown」的写法在异常时会漏关。"""
    fake = _FakeNvml(fail_at="handle")
    monkeypatch.setitem(sys.modules, "pynvml", fake)

    def _old_impl(index):
        try:
            import pynvml

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            pynvml.nvmlShutdown()
            return int(mem.free / (1024 * 1024))
        except Exception:
            return None

    assert _old_impl(0) is None
    assert fake.inits == 1 and fake.shutdowns == 0, "旧实现异常路径确实漏关 NVML"
    assert dm._query_free_vram_mb(0) is None
    assert fake.shutdowns == 1, "新实现把漏掉的那次关闭补上了"


# ==============================================================================
# 已验证为非缺陷：写测试钉住，防止后人以「去重/优化」名义改坏
# ==============================================================================
def test_forced_cuda_does_not_silently_degrade(monkeypatch):
    """DEVICE_MODE=cuda 且无显卡时必须明确报错，绝不静默降级（否则用户以为在用显卡）。"""
    monkeypatch.setattr(dm, "_cached_device", None)
    monkeypatch.setattr(dm, "_detect_cuda", lambda: None)
    with pytest.raises(RuntimeError):
        dm.select_device("cuda")


def test_select_device_auto_reuses_settled_choice(monkeypatch):
    """auto 模式复用「已定下来」的设备，是 docstring 声明的语义（非缺陷）。"""
    monkeypatch.setattr(dm, "_cached_device", None)
    h1 = dm.select_device("cpu")
    assert h1.info.kind == "cpu"
    h2 = dm.select_device("auto")
    assert h2 is h1, "auto 应复用已缓存的设备，不应重新探测"


def test_load_model_fp16_judgment_uses_passed_device():
    """
    load_model 的 fp16 判据必须看「调用方传入的 device」，不能改用全局 use_fp16()。

    两者作用对象不同：use_fp16() 看全局 active 设备，load_model 看传入句柄。
    合并会让「显式传 CPU 句柄」也按 fp16 加载（配置开着时）。
    """
    src = inspect.getsource(mm.load_model)
    assert "device.is_gpu" in src, "load_model 应按传入 device 判断 fp16"
    assert "use_fp16" not in src, (
        "load_model 不应改用 device_manager.use_fp16()：它读的是全局 active 设备，"
        "与传入 device 可能不同，合并会导致误开半精度"
    )


def test_register_slot_normalizes_note(isolate_registry):
    """note 与其他字段一样做规整：传 None 不能把 None 存进元数据（UI 会显示 'None'）。"""
    mm.register_model_slot("a89_note", filename="a89_note.pt", note=None)
    try:
        slot = mm.get_model_slot("a89_note")
        assert slot.note == ""
        assert slot.to_dict()["note"] == ""
    finally:
        mm._SLOTS.pop("a89_note", None)


def test_get_model_slot_empty_string_is_boundary(isolate_registry):
    """
    边界登记：get_model_slot("") 与 get_model_slot(None) 一样返回当前 active 槽。

    docstring 只声明了 None 的行为；空字符串被当成「未指定」而非「名为空的槽」。
    UI 一律传 `name or None`，故当前不可达；此处钉住现状，改语义需先评估调用方。
    """
    active = mm.get_active_model()
    assert getattr(mm.get_model_slot(""), "name", None) == active
    assert getattr(mm.get_model_slot(None), "name", None) == active
    assert mm.get_model_slot("a89_definitely_absent") is None
    assert config.ACTIVE_MODEL  # 仅确保 config 已导入，避免误判为未使用
