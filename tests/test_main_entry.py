"""阶段 189 · 启动入口 `main.py` 的编排与冒烟守卫（承接 187 覆盖率发现）。

为什么需要（187 的实测证据）
--------------------------
覆盖率首次测量时 `main.py` = **0.00%（189 语句全部未执行）**：Gradio 入口的**装配与
编排段从未被任何测试 import**，「UI 能不能起来」只靠人工点开验证。
本文件把入口的行为锁住，其中最关键的一条是**编排顺序的设计意图**：

    「--selftest 必须在 `ensure_model_ready()` **之前**返回」
    理由见 main.py 注释：自测只需核心算法，若走到模型就绪检查会**触发网络下载**
    （可能长时间超时）。order 一旦被改坏，用户跑 `--selftest` 会莫名卡住——故用
    **间谍函数**钉死：selftest 路径下 `ensure_model_ready` **绝不允许被调用**。

设计要点
--------
· 用 `sys.modules` 注入**假 `ui.app`**（`build_app`/`launch_app`），既不启 Gradio 服务、
  也不受「本机是否装了 gradio」影响 ⇒ 结果确定性；
· 用 `sys.modules["gradio"] = None` 让 `import gradio` 抛 ImportError，
  以确定性覆盖「缺 Gradio 的友好提示」分支（CPython 对 None 条目的既有行为）；
· 只测**编排**，不真跑模型/超分/推理 ⇒ 秒级、无副作用。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config  # noqa: E402
import main as m  # noqa: E402


class _Args:
    """parse_args 结果的替身。

    阶段 202 修（**锚点腐化**，与 LESSONS 183 同族）：原实现只手抄 3 个属性
    （selftest / no_download / skip_check），而 D1-Stage 2 给 `main.py` 加了
    `--api` 分支后，`main()` 开始读 `args.api` / `args.api_host` / `args.api_port`
    ⇒ 用例抛 `AttributeError: '_Args' object has no attribute 'api'`。
    198 阶段没跑全库，所以这条腐化一直没被发现（LESSONS 208：改既有函数须全量回归）。

    ⚠ **不要在这里调 `m.parse_args()` 取基线**：本文件多处用 monkeypatch 把它替换成
    0 参 lambda（`lambda: _Args(...)`），桩构造时若反向调用它会
    `TypeError: <lambda>() takes 0 positional arguments but 1 was given`
    ——这是自引用，比原缺陷更糟。故此处**显式列出全部 6 个属性**，
    并由 `test_args_stub_covers_every_cli_flag` 守卫与 `main.py` 的参数集对账
    （期望值与真值来源对账，而非手抄后不管）。
    """

    def __init__(self, selftest=False, no_download=False, skip_check=False,
                 api=False, api_host=None, api_port=None):
        self.selftest = selftest
        self.no_download = no_download
        self.skip_check = skip_check
        # D1-Stage 2（`--api`）新增的三个字段，缺任一都会让 main() 抛 AttributeError
        self.api = api
        self.api_host = api_host
        self.api_port = api_port


def _fake_ui_app(build_ok=True, launch_rc=0):
    """构造一个假的 `ui.app` 模块（避免真启 Gradio）。"""
    mod = types.ModuleType("ui.app")

    def build_app():
        if not build_ok:
            raise ModuleNotFoundError("fake: gradio 缺失")
        return object()

    mod.build_app = build_app
    mod.launch_app = lambda app: launch_rc
    return mod


def _patch_pipeline(monkeypatch, calls, *, env_ok=True):
    """把 main 的编排依赖替换为记录调用的间谍。"""
    monkeypatch.setattr(m, "print_startup_banner", lambda: calls.append("banner"))
    monkeypatch.setattr(m, "run_environment_check",
                        lambda: (calls.append("env"), env_ok)[1])
    monkeypatch.setattr(m, "print_performance_report", lambda: calls.append("perf"))
    monkeypatch.setattr(m, "report_device_and_gpu_hint", lambda: calls.append("device"))
    monkeypatch.setattr(m, "ensure_model_ready", lambda: calls.append("model"))
    monkeypatch.setattr(m, "warm_up_model", lambda: calls.append("warmup"))
    monkeypatch.setattr(m, "launch_ui", lambda: (calls.append("launch"), 0)[1])


# ---------------------------------------------------------------------------
# 1 · 导入与轻量函数（187 的 0% 主体）
# ---------------------------------------------------------------------------

def test_module_import_is_side_effect_free():
    """模块级只依赖 config/logger；重依赖（cv2/torch/gradio）都在函数内惰性导入。"""
    src = (_ROOT / "main.py").read_text(encoding="utf-8")
    top = src.split("def ", 1)[0]
    for heavy in ("import cv2", "import torch", "import gradio", "import numpy"):
        assert heavy not in top, f"{heavy} 出现在模块级——导入即拖慢/可能失败"


def test_print_startup_banner_runs(capsys):
    m.print_startup_banner()
    out = capsys.readouterr().out
    assert out.strip(), "启动横幅不应为空（它还要承载合规声明）"
    assert config.APP_VERSION in out or config.APP_NAME in out


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py"])
    a = m.parse_args()
    assert (a.selftest, a.no_download, a.skip_check) == (False, False, False)


def test_parse_args_flags(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--selftest", "--no-download", "--skip-check"])
    a = m.parse_args()
    assert (a.selftest, a.no_download, a.skip_check) == (True, True, True)


# ---------------------------------------------------------------------------
# 2 · 编排顺序（核心：selftest 绝不触发模型下载）
# ---------------------------------------------------------------------------

def test_selftest_returns_before_model_steps(monkeypatch):
    """**本文件最重要的一条**：selftest 路径不得调用 ensure_model_ready / warm_up_model。"""
    calls = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(m, "parse_args", lambda: _Args(selftest=True, skip_check=True))
    monkeypatch.setattr(m, "run_selftest", lambda: (calls.append("selftest"), 7)[1])

    assert m.main() == 7
    assert "selftest" in calls
    assert "model" not in calls, "selftest 路径触发了模型就绪检查（会引发网络下载）"
    assert "warmup" not in calls
    assert "launch" not in calls


def test_normal_path_orchestration_order(monkeypatch):
    calls = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(m, "parse_args", lambda: _Args())

    assert m.main() == 0
    assert calls == ["banner", "env", "perf", "device", "model", "warmup", "launch"], calls


def test_fatal_env_check_aborts_before_launch(monkeypatch):
    calls = []
    _patch_pipeline(monkeypatch, calls, env_ok=False)
    monkeypatch.setattr(m, "parse_args", lambda: _Args())

    assert m.main() == 1
    assert "launch" not in calls and "model" not in calls


def test_selftest_ignores_failed_env_check(monkeypatch):
    """自测模式下环境自检失败**不**硬失败（交给 run_selftest 自己给友好提示）。"""
    calls = []
    _patch_pipeline(monkeypatch, calls, env_ok=False)
    monkeypatch.setattr(m, "parse_args", lambda: _Args(selftest=True))
    monkeypatch.setattr(m, "run_selftest", lambda: 0)

    assert m.main() == 0
    assert "selftest" not in calls  # run_selftest 被替换为 lambda 0，未记录
    assert "model" not in calls


def test_no_download_flag_flips_config_and_is_restored(monkeypatch):
    calls = []
    _patch_pipeline(monkeypatch, calls)
    monkeypatch.setattr(m, "parse_args", lambda: _Args(no_download=True))
    before = config.MODEL_AUTO_DOWNLOAD
    try:
        assert m.main() == 0
        assert config.MODEL_AUTO_DOWNLOAD is False, "--no-download 未生效"
    finally:
        config.MODEL_AUTO_DOWNLOAD = before


# ---------------------------------------------------------------------------
# 3 · launch_ui 的两条分支（成功 / 缺依赖友好提示）
# ---------------------------------------------------------------------------

def test_launch_ui_success_path_uses_ui_app(monkeypatch):
    monkeypatch.setitem(sys.modules, "ui.app", _fake_ui_app(launch_rc=5))
    assert m.launch_ui() == 5, "应把 ui.app.launch_app 的返回值原样传出"


def test_launch_ui_missing_gradio_gives_guidance(monkeypatch, capsys):
    """缺 UI 模块时：给安装指引，并在缺 Gradio 时明确「核心算法仍可跑」而不是崩溃。"""
    monkeypatch.setitem(sys.modules, "ui.app", _fake_ui_app(build_ok=False))
    monkeypatch.setitem(sys.modules, "gradio", None)   # 让 `import gradio` 抛 ImportError
    rc = m.launch_ui()
    out = capsys.readouterr().out
    assert rc == 1
    assert "requirements.txt" in out, "缺少依赖时必须给出安装指引"
    assert "核心算法" in out, "缺 Gradio 时应说明核心算法仍可用"


def test_launch_ui_falls_back_to_selftest_when_gradio_present(monkeypatch):
    """核心依赖与 Gradio 都在、但 ui.app 加载失败 ⇒ 退回自测（而非直接失败）。"""
    monkeypatch.setitem(sys.modules, "ui.app", _fake_ui_app(build_ok=False))
    monkeypatch.setattr(m, "run_selftest", lambda: 42)
    assert m.launch_ui() == 42


# ---------------------------------------------------------------------------
# 阶段 202：_Args 桩必须覆盖 main() 读取的**每一个**参数（防锚点腐化复发）
# ---------------------------------------------------------------------------

def test_args_stub_covers_every_cli_flag():
    """桩的属性集必须与 `main.py` 定义的全部 CLI 参数**对账一致**。

    为什么需要这条（LESSONS 183「有守卫≠有覆盖」的反面）：阶段 198 给 `main.py`
    加了 `--api` / `--api-host` / `--api-port`，而本文件的 `_Args` 桩只列了 3 个属性
    ⇒ `main()` 读 `args.api` 时抛 AttributeError。**198 没跑全库，所以没发现**。

    这里做的是**与真值来源对账**（解析 `main.py` 源码里的 `add_argument`），
    而不是「再手抄一份」——手抄的清单会随下次加参数再次腐化（同 LESSONS 196）。
    """
    import re as _re

    _src = Path(m.__file__).read_text(encoding="utf-8")
    flags = set(_re.findall(r'add_argument\(\s*[\'"](--[A-Za-z0-9_-]+)', _src))
    assert flags, "未从 main.py 解析出任何 CLI 参数（解析式已失效，需更新）"
    derived = {f[2:].replace("-", "_") for f in flags}
    stub = set(vars(_Args()))
    missing = derived - stub
    assert not missing, (
        f"_Args 桩缺少 main() 会读取的属性：{sorted(missing)}"
        f"（CLI 参数 {sorted(flags)}）——加参数后须同步补桩，否则用例抛 AttributeError"
    )
    extra = stub - derived
    assert not extra, (
        f"_Args 桩有 main.py 未定义的多余属性：{sorted(extra)}（桩已腐化）"
    )
