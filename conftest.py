# -*- coding: utf-8 -*-
"""
阶段 13 测试基础设施（conftest）。

职责：
1. 将项目根目录加入 sys.path，使 `import config` / `from core import plugins` 等工作。
2. 提供跨测试复用的 fixture：
   - synthetic_image_mask：一张带蓝色水印方块的测试图 + 对应二值掩码（纯 numpy，不落盘）。
   - restore_active_model：模型管理测试结束后，把「当前模型槽」还原，避免污染后续用例。
   - cli_run：以子进程方式调用 cli.py，用于集成测试。

设计约束（沙箱 safe-delete 注意）：
   本环境 Python 的 os.remove / shutil.rmtree 会被 safe-delete 钩子拦截，
   因此测试一律「不删除文件」——需要文件存在性时用 monkeypatch 模拟，
   端到端修复用纯 numpy（OpenCV 引擎，无需 torch）。
"""

import os
import sys
import subprocess

import pytest
import numpy as np

# 项目根目录（conftest.py 在仓库根，故 __file__ 的目录即根）。
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 运行 pytest 时 sys.executable 即托管 venv 的 python，子进程复用它，环境与测试一致。
PY = sys.executable


# --------------------------------------------------------------------------- #
# 会话级：把语言持久化文件重定向出真实目录（阶段 290 新增）
# --------------------------------------------------------------------------- #
def pytest_configure(config):
    """**在测试收集（collection）之前**把 i18n 状态文件重定向到临时目录。

    事故链（阶段 290 回归中实测，非推测）：

      `utils.i18n.apply_language()` 会同时 `set_language()`（进程全局）与
      `save_language()`（**落盘** `cache/i18n_state.json`）。而 `load_language()`
      的优先级是 `环境变量 > 持久化文件 > config > 默认`——于是持久化文件里的
      值会在**之后每一个全新 pytest 进程**生效，压过 `config.UI_LANGUAGE`。

      实测：该文件一旦是 `en-US`，宽回归即 **14 例「期望中文」全红**；改回
      zh-CN 后同批 144 例全绿。因果链由「投毒 → 跑 → 14 红 → 还原 → 全绿」
      双向闭合验证。

    为什么已有夹具挡不住（本轮定位的关键）：

      ① `_isolate_i18n_language`（阶段 78）只快照/还原**进程内** `_LANG`，
         完全不碰落盘文件；
      ② `_isolate_runtime_dirs`（阶段 148）确实重定向了 `CACHE_DIR`，但它**函数级**——
         而**模块级夹具与「收集期的模块级代码」都早于函数级夹具建立**。任何在
         收集期就调用 `t()` / `load_language()` 的测试模块，会在隔离生效**之前**
         读进真实文件，把 `_LANG` 污染成持久化值；此后第① 条夹具快照到的
         已是**被污染的值**，还原也还原不回`zh-CN`。

    修法：用 `i18n._STATE_FILE` 这个**阶段 249 就已备好的显式覆盖钩子**
    （`_state_path()` 里`_STATE_FILE is not None` 时直接返回），
    在 `pytest_configure`（早于 collection）把状态文件指到会话专属临时目录——
    于是**任何时刻、任何作用域**的用例都读不到真实持久化文件。
    """
    import tempfile

    try:
        from utils import i18n
    except Exception:  # noqa: BLE001 —— 依赖缺失时不影响用例
        return
    try:
        # 测试重现实开关（仅 inject-proof 用，正常跑恒为 unset，零行为变更）：
        # 设 WME_I18N_NO_REDIRECT=1 即跳过重定向，复现「修复前跨进程泄漏」，
        # 此时 test_i18n_state_leak_guard 必 FAIL —— 证守卫能捕获修复缺失。
        if os.environ.get("WME_I18N_NO_REDIRECT"):
            config._wme_i18n_state_patched = False
            return
        d = tempfile.mkdtemp(prefix="wme_i18n_state_")
        i18n._STATE_FILE = os.path.join(d, "i18n_state.json")
        config._wme_i18n_state_patched = True
    except Exception:  # noqa: BLE001
        config._wme_i18n_state_patched = False


def pytest_unconfigure(config):
    """还原 `i18n._STATE_FILE`，避免影响同一进程内的后续非测试行为。"""
    if not getattr(config, "_wme_i18n_state_patched", False):
        return
    try:
        from utils import i18n
        i18n._STATE_FILE = None
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
def synthetic_image_mask():
    """
    返回 (原图, 掩码) 的纯内存 numpy 数组。

    原图：64x64 浅灰底，中央 24x24 蓝色方块（模拟水印）。
    掩码：对应蓝色方块区域为 255，其余为 0。
    """
    h = w = 64
    img = np.full((h, w, 3), 240, np.uint8)
    img[20:44, 20:44] = [40, 40, 200]          # 蓝色水印方块
    mask = np.zeros((h, w), np.uint8)
    mask[20:44, 20:44] = 255                     # 二值掩码
    return img, mask


@pytest.fixture
def restore_active_model():
    """模型管理测试结束后，把 active 槽还原到进入测试前的状态（内存态，不落盘）。"""
    from core import model_manager as mm

    saved = mm.get_active_model()
    yield
    try:
        mm.set_active_model(saved, persist=False)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
def cli_run():
    """返回子进程调用 cli.py 的辅助函数，cwd 固定在项目根。"""

    def _run(*args, timeout: int = 240):
        return subprocess.run(
            [PY, "cli.py", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    return _run


@pytest.fixture(autouse=True)
def _isolate_i18n_language():
    """
    隔离 i18n 的全局语言状态（阶段 78 新增，autouse）。

    背景（阶段 78 审计副产品）：多个测试文件会调用 `utils.i18n.set_language(...)`
    验证双语输出，但**大多不还原**，语言状态于是在用例之间泄漏。
    跑全量时会被后面某个「设回 zh-CN」的文件碰巧掩盖，而**任意子集运行或顺序变化
    就会误报**——实测复现：`test_cache_disk`（设 en-US 不还原）→
    `test_history_filter`（断言中文文案）→ 2 例失败。

    本 fixture 在每个用例前后快照/还原 `i18n._LANG`，从根上消除这类跨用例污染。
    """
    try:
        from utils import i18n
    except Exception:  # noqa: BLE001 —— 依赖缺失时不影响用例
        yield
        return

    saved = getattr(i18n, "_LANG", None)
    try:
        yield
    finally:
        # 直接还原模块级变量（不做 set_language 的取值校验，因为只是恢复原状）。
        i18n._LANG = saved


#: 需要**重定向到用例专属临时目录**的运行时目录（`config` 属性名 → 子目录名）。
#: 刻意**不含** `MODEL_DIR` / `ASSETS_DIR` —— 它们是「读」的资源（模型权重、静态资源），
#: 隔离会让需要它们的用例失败；这里只隔离「写」的目录。
_RUNTIME_DIR_ATTRS = (
    ("OUTPUT_DIR", "outputs"),
    ("EXPORT_DIR", "exports"),
    ("CACHE_DIR", "cache"),
    ("TEMP_DIR", "temp"),
    ("LOG_DIR", "logs"),
)


@pytest.fixture(autouse=True)
def _isolate_runtime_dirs(tmp_path, monkeypatch):
    """
    把全部「写」类运行时目录重定向到**本用例专属**的临时目录（阶段 148，autouse）。

    背景（阶段 147 审计的延伸）：`cache/` 已累积 **11074** 个文件、`exports/` 451 个、
    `outputs/` 97 个 —— **每一次测试运行**都会经 `make_unique_path` 往真实目录
    写入并递增序号（`src(118).png` / `我的测试图(28).png`），后果有三：
      1. 工作区被测试产物污染（且**无限增长**）；
      2. 沙箱的「单轮批量删除」计数器被累积推高 ⇒ 门禁/自检跑到第 N 次被护栏拦截
         （阶段 146/147 两次全量失败的直接根因）；
      3. `make_unique_path` 的序号递增让「文件名」不可预测 ⇒ 依赖确定性的断言变脆。

    阶段 147 的 env 注入只覆盖了 `test_legacy_selfcheck` 的**子进程**；
    本 fixture 把隔离推广到**全部测试**（含同进程直接调函数的）。

    实现：`config` 的目录常量虽是模块级 `Final[Path]`，但**所有消费方都是运行时取**
    （`config.EXPORT_DIR` / `get_output_dir()` 等），故 `monkeypatch.setattr` 即可
    整体改向；用例结束后由 monkeypatch 自动还原，用例之间互不可见。

    ⚠ 子目录放在 `tmp_path/_wme_runtime/` **专属前缀**下 —— 首版直接用
    `tmp_path/outputs`，与 `test_path_helper_audit144` 里 `base = tmp_path / "outputs";
    base.mkdir()` 的既有约定**撞名**（`mkdir()` 无 `exist_ok` ⇒ `FileExistsError`，
    16 个用例全红）。专属前缀让隔离目录与测试自建的目录**互不可见**。
    """
    import config
    for attr, sub in _RUNTIME_DIR_ATTRS:
        d = tmp_path / "_wme_runtime" / sub
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, attr, d)
    yield


#: config 中**可变容器**类型（需深拷贝快照才能真正捕捉「原地修改」）
_MUTABLE_TYPES = (list, dict, set, bytearray)


@pytest.fixture(autouse=True)
def _isolate_config_globals():
    """隔离 `config` 模块的全局常量（阶段 248，autouse）。

    背景（阶段 248 运行期检测器实测）
    -------------------------------
    `tests/test_tab_settings_audit129.py` 调 `ui.tab_settings.apply_superres_config(...)`
    ——该函数会**直接写 `config.REAL_SUPERRES_ENABLED/SCALE/BACKEND`**；而用例的 `finally`
    只还原 logger 与（阶段 247 后）级别，**不还原 config** ⇒ 实测把
    `REAL_SUPERRES_SCALE` 2→4、`REAL_SUPERRES_BACKEND` esrgan→auto 泄漏给后续用例。
    `tests/test_settings_superres.py::test_register_and_refresh_superres_widgets`
    同款（写 3 个 config 全局量，`finally` 只 `clear_superres_widgets()`）。

    这类泄漏**不抛错**，只是让后续用例在**另一组配置**下运行 ⇒ 表现为
    「单跑绿、整批偶发红」，与 245/246/247 的全局状态污染同族。

    做法（与 `_isolate_i18n_language` 同款：快照 → 还原）
    -----------------------------------------------------
    每个用例前后快照/还原 `config` 的**全大写**属性。比「逐点加还原」更彻底：
      · 已有的 `monkeypatch.setattr(config, ...)` 不受影响（monkeypatch 先还原，
        本夹具再还原到同一初值，幂等）；
      · **忘记还原**的直接赋值会被自动纠正 ⇒ 从根上消除该类跨用例污染。

    判据细节
    --------
      · 不可变标量：按**身份**（`is not`）判断是否被替换 → 只对真正改过的回写；
      · 可变容器：快照用 `deepcopy`、按**值**比较 → 能捕捉「原地 append/更新」；
      · 任何一步异常都吞掉（夹具不应成为新的失败源）。

    ⚠ 局限：只覆盖 `config` 模块本身；其它单例（logger / i18n / model_manager）
      各有专项夹具或守卫（245/246/247 / `restore_active_model`）。
    """
    try:
        import config
    except Exception:                # noqa: BLE001
        yield
        return

    import copy as _copy

    saved: dict = {}
    for k in dir(config):
        if not k.isupper():
            continue
        try:
            v = getattr(config, k)
        except Exception:            # noqa: BLE001
            continue
        if isinstance(v, _MUTABLE_TYPES):
            try:
                saved[k] = _copy.deepcopy(v)
                continue
            except Exception:        # noqa: BLE001
                pass
        saved[k] = v

    yield

    for k, v in saved.items():
        try:
            cur = getattr(config, k)
        except Exception:            # noqa: BLE001
            continue
        try:
            if isinstance(v, _MUTABLE_TYPES):
                changed = (cur != v)
            else:
                changed = (cur is not v)
        except Exception:            # noqa: BLE001
            changed = False
        if changed:
            try:
                setattr(config, k, v)
            except Exception:        # noqa: BLE001
                pass
