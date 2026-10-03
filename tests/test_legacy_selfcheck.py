# -*- coding: utf-8 -*-
"""遗留模块 __main__ 自检回归（阶段 13）。

把所有分散在各模块的 `python xxx.py` 自检收敛到 pytest 统一管理：
- 离线、无需 torch：只挑不依赖模型权重下载的模块。
- 每个模块作为独立子进程运行，断言退出码为 0（与 CI 行为一致）。
- 标记为 slow 的用例（如 detector）在 CI 中仍会运行（它离线可用），
  但若节奏需要可单独 `-m "not slow"` 跳过。

约束：不删除任何文件；部分模块自检可能向 temp/ 写入临时图，
      由阶段收尾的清理步骤统一处理。

阶段 147 加固（本轮）
--------------------
审计发现本守卫自身有三个结构性问题（都用**枚举**而非回忆定位）：

① **重复登记** `ui/components.py` 出现两次（第 35 行与第 46 行）⇒
   `ids=[m[0] for m in MODULES]` 产生重复 id、该模块自检被跑两遍。
   ⇒ 新增 `test_selfcheck_registry_has_no_duplicates` 防回归。

② **断言过弱：只查 `rc == 0`，不看输出** ⇒ 结构上无法发现「哑巴自检」
   （自检什么都不做、只 `print` 一句，rc 同样是 0）。
   ⇒ 补 `stdout 非空` 断言。

③ **没有任何机制检查自检「有没有判定能力」** ⇒ 这是「哑巴自检」能长期存在的
   根本原因。实测 `config.py` 的自检有 **21 条语句 / 26 次调用、却 0 个 `assert`**：
   它确实调用了 `validate_config()` 并打印问题清单，但**判定结果不影响退出码**
   ⇒ 本守卫永远绿。这是最隐蔽的形态（比「只 print」充实得多，因此更难看穿）。
   ⇒ 新增 `test_selfcheck_modules_have_decision_power`（AST 递归判定 + 显式白名单）。
"""

import ast
import collections
import os
import subprocess
import sys

import pytest

# 项目根（本文件在 tests/ 下）。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (模块相对路径, 额外标记)
MODULES = [
    ("config.py", []),
    ("utils/brand.py", []),
    ("core/plugins.py", []),
    ("core/model_manager.py", []),
    ("core/inpainter.py", []),
    ("core/image_utils.py", []),
    ("core/mask_generator.py", []),
    ("api.py", []),  # Stage 125：补子进程守卫，捕获自检崩溃（此前 REAL_SUPERRES 下用例2 形状断言崩）
    ("core/detector.py", ["slow"]),
    ("ui/theme.py", []),
    ("ui/compare_view.py", []),  # Stage 126：补子进程守卫（回调纯函数自检）
    ("services/auto_pipeline.py", []),  # Stage 127：补子进程守卫（含新增 manual 模式用例）
    ("services/validators.py", []),  # Stage 130：补子进程守卫（自检用例4/5 曾因存在性检查先行而崩）
    ("services/pdf_service.py", []),  # Stage 131：补子进程守卫（整份 PDF 流程自检）
    ("services/cache_service.py", []),  # Stage 132：自检已隔离到临时目录，可安全登记
    ("utils/exceptions.py", []),  # Stage 133：自检已补断言（树完整性 + 路径抽取），可安全登记
    ("services/export_service.py", []),  # Stage 134：自检非破坏性（写 EXPORT_DIR 临时产物后清理），可安全登记
    ("services/file_service.py", []),  # Stage 135：自检非破坏性（写 uploads/outputs 运行时目录、make_unique_path 不覆盖、只删自身 tmp），可安全登记
    ("services/video_export.py", []),  # Stage 136：自检隔离在 mkdtemp 且 finally 自清理（阶段 136 修复泄漏），rc=0 约 4.4s，可安全登记
    ("utils/i18n.py", []),  # Stage 137：自检纯内存断言 + 临时状态文件（mkdtemp 即删），rc=0 秒级，可安全登记
    ("ui/components.py", []),  # Stage 138：自检包 Blocks 上下文真正构造组件（修复哑巴自检），纯内存无文件写入，可安全登记
    ("ui/tab_history.py", []),  # Stage 139：自检纯内存（工具函数 + 桩缓存，跑完恢复），rc=0 秒级，可安全登记
    ("services/quality_service.py", []),  # Stage 140：自检写 4 个临时 png（阶段 140 补 try/finally 清理），纯本地可安全登记
    ("core/detection_presets.py", []),  # Stage 143：自检纯内存（与 _BUILTIN_PRESETS 集合对账 + 注册校验断言），无文件写入，rc=0 秒级，可安全登记
    ("utils/path_helper.py", []),  # Stage 144：自检纯内存（文件名清洗 / 模板替换 / 格式白名单断言），无文件写入，rc=0 秒级，可安全登记
    ("scripts/baseline_guard.py", []),  # Stage 146：自检纯内存（护栏方向/容差/边界 + 非法 direction 抛错），无文件写入，rc=0 秒级
    ("scripts/undef_scan.py", []),  # Stage 146：自检纯内存（用内嵌探针串验证扫描器有区分力且不误报 f-string / with-as），无文件写入
    # --- Stage 147：由**磁盘枚举**发现的 6 个「离线可跑、有实质断言」却漏登记的模块 ---
    #     实测：rc=0，耗时 0.25~3.73s，输出非空，**关键目录文件数零变化**（无副作用）。
    ("core/mask_editor.py", []),  # Stage 147：自检 51 语句 / 19 assert，rc=0 约 0.4s
    ("core/preset_recommender.py", []),  # Stage 147：自检 15 语句 / 2 assert，rc=0 约 0.6s
    ("services/postprocess_service.py", []),  # Stage 147：自检 50 语句 / 12 assert，rc=0 约 3.7s
    ("services/queue_worker.py", []),  # Stage 147：自检 13 语句 / 2 assert，rc=0 约 1.9s
    ("utils/format_utils.py", []),  # Stage 147：自检 16 语句 / 15 assert，rc=0 约 0.3s
    ("utils/recommend_view.py", []),  # Stage 147：自检 26 语句 / 10 assert，rc=0 约 0.4s
]

# ---------------------------------------------------------------------------
# Stage 147 · 「含 __main__ 但**不登记**为子进程守卫」的**显式**清单
# ---------------------------------------------------------------------------
# 为什么需要它：`MODULES` 是一份手写的「已登记」清单，而**磁盘上含 `__main__`
# 的模块远多于它**。若不给「未登记」也留一份显式名单，那么「新增一个模块」
# 与「忘了登记」在数据上**长得一模一样**（经验 167：留白会被读成「忘了」）。
# 下面这份清单与 `test_selfcheck_coverage_is_accounted_for` 一起工作：
# 磁盘上凡含 `__main__` 的模块，必须**要么**在 MODULES、**要么**在这里 —— 否则测试变红。
#
# 分类（由阶段 147 的静态风险探针产出，非人工拍脑袋）：
#   B  依赖重 / 有外部副作用（torch / gradio / 网络 / 模型下载 / 启动服务）
#   W  写文件（需逐个确认是否非破坏性，本轮未确认）
#   S  scripts/ 下的一次性门禁 / 诊断脚本（`__main__` 仅转发 `main()`；
#      多数耗时长或写文件，**不属于本守卫「运行时模块自检」的语义范围**）
NOT_REGISTERED_BY_DESIGN: dict = {
    # --- B：依赖重 / 有外部副作用（离线守卫跑不了）---
    "main.py": "B Gradio 入口，import torch + gradio + 网络",
    "server.py": "B 启动服务，含网络与模型下载路径",
    "cli.py": "B CLI 入口，`__main__` 仅转发，含模型下载目录创建",
    "download_model.py": "B 专职下载模型，含网络",
    "core/device_manager.py": "B import torch，含网络与模型下载",
    "core/model_loader.py": "B 依赖模型权重",
    "core/superres.py": "B import torch，且创建目录",
    "services/compare_service.py": "B 依赖 gradio",
    "services/video_service.py": "B import torch，含网络",
    "ui/app.py": "B Gradio 应用装配 + launch",
    "utils/progress.py": "B 依赖 gradio",
    # --- W：写文件（未逐个确认非破坏性）---
    "services/report_service.py": "W 写报告文件 + 创建目录",
    "utils/logger.py": "W 创建日志目录",
    # --- S：scripts/ 下的门禁 / 诊断脚本（非运行时模块）---
    "scripts/audit_selector.py": "S 审计选点工具",
    "scripts/build_real_ground_truth.py": "S 真值构建",
    "scripts/cascade_robustness.py": "S 鲁棒性诊断",
    "scripts/collect_real_assets.py": "S 资产收集（含网络）",
    "scripts/detector_diag.py": "S 检测器诊断",
    "scripts/detector_mask_dilate_audit.py": "S 掩码膨胀审计",
    "scripts/e2e_real_probe.py": "S 端到端修复侧门禁（慢）",
    "scripts/jpeg_robustness.py": "S JPEG 鲁棒性诊断",
    "scripts/make_demo_assets.py": "S 合成资产生成",
    "scripts/make_real_compare_crops.py": "S 对比图裁剪",
    "scripts/mask_param_audit.py": "S 掩码参数标定审计",
    "scripts/prompted_area_gate.py": "S 次级档面积门禁（真实跑 20 图×4 预设 ≈171s）",
    "scripts/quality_gate.py": "S 合成质量门禁",
    "scripts/real_asset_preflight.py": "S 真实资产入库预检",
    "scripts/real_benchmark_decision.py": "S 真实基准决策",
    "scripts/real_gate.py": "S 真实图质量门禁",
    "scripts/run_benchmark.py": "S 合成基准跑分",
    "scripts/run_real_benchmark.py": "S 真实基准跑分",
    "scripts/scale_robustness.py": "S 尺度鲁棒性诊断",
    "scripts/validate_selector.py": "S 选点校验工具",
    # --- 阶段 183 补登记（此前 180/181 新增后漏登记 ⇒ 「含 __main__ 未登记」守卫常红）---
    "scripts/clean_bench_probe.py": "S 干净图误报门禁（检测器推理，慢；语义同 real_gate 一族）",
    "scripts/_verify_clean_probe.py": "S clean_bench_probe 的本地等价验证器（写并清理 .verify_tmp/）",
    # ⚠ 阶段 190：`benchmark_real/**` 已在 `SKIP_DIRS` 内（它是**数据集**目录，不是源码），
    #   其下的 `clean/_gen_synthetic.py` 因此不再进入审计视野 ⇒ 原豁免条目已成幽灵，移除。
    #   （若将来要把数据集目录内的脚本重新纳入审计，应先把该目录移出 SKIP_DIRS 再登记。）
    # ⚠ 阶段 188/190：根目录点文件已归档进 `.scratch/`（该目录现由 SKIP_DIRS 跳过），
    #   故原先的 `.probe181.py` / `.probe183_holdout.py` 两条豁免**已失效并移除**
    #   —— 它们会变成「豁免清单里的幽灵条目」，由 stale 用例守住。
    # --- 阶段 184/186 ---
    "scripts/run_all_checks.py": "S 统一验证入口（会递归拉起 pytest 子进程，超出本守卫 300s 超时，"
                                 "且会被 pytest 套件内再调 pytest 递归占用）",
    "scripts/perf_probe.py": "S 性能探针（跑真实检测推理，10 次；语义同门禁一族）",
    "scripts/a4_gradient_proof.py": "S ai_gradient 真渐变证伪实验脚本（诊断用；需真实图输入，默认无输入 rc=2，不登记为运行时自检）",
    "scripts/clean_artifacts.py": "S 仓库产物清理脚本（运维用；默认 dry-run 只读，非运行时模块自检）",
}


#: 需要**重定向到临时目录**的运行时目录（环境变量名 → 默认子目录名）。
#: `config._resolve_dir(env_key, default_name)` 读的是 `WME_<env_key>`，
#: 故这里注入同名环境变量即可整体改向 —— 见 `test_module_selfcheck` 的说明。
_RUNTIME_DIR_ENVS = (
    ("WME_OUTPUT_DIR", "outputs"),
    ("WME_EXPORT_DIR", "exports"),
    ("WME_CACHE_DIR", "cache"),
    ("WME_TEMP_DIR", "temp"),
    ("WME_LOG_DIR", "logs"),
)


@pytest.mark.parametrize(
    "mod,marks",
    MODULES,
    ids=[m[0] for m in MODULES],
)
# ⚠ 阶段 191：本用例给子进程传 `timeout=300`，与 pytest.ini 的全局 `--timeout=300`
#   相等 ⇒ 外层不严格大于内层时会出现"谁先到点"的竞态。此处显式抬高外层。
@pytest.mark.timeout(420)
def test_module_selfcheck(mod, marks, request, tmp_path):
    """运行模块自带自检，断言「退出码为 0」、「有可见输出」且**不污染真实运行时目录**。

    阶段 147：
      · 补 `stdout 非空` —— 只断 `rc == 0` 时，一个「什么都不做」的空自检
        同样能通过（哑巴自检的基本形态）。
      · **把全部运行时目录重定向到本用例专属的临时目录**（见下）。
    """
    for m in marks:
        request.node.add_marker(getattr(pytest.mark, m))

    # ------------------------------------------------------------------
    # 阶段 147 · **元级隔离**：一次覆盖全部被登记模块，而不是逐个改自检
    # ------------------------------------------------------------------
    # 实测问题：多个模块的自检会**写真实的运行时目录再删掉产物**
    # （`services/export_service.py` 写 `exports/`、`api.py` 写 `outputs/`…）。
    # 看似干净，实则**非幂等** —— 删除动作会累积进沙箱的「单轮批量删除」计数器
    # （`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，阈值 50），跑到第 N 次就被护栏拦截
    # ⇒ 同一份代码，前面几次绿、后面开始红。
    #
    # 逐个修自检是**治标**（而且我第一版只发现了 `export_service`，漏了 `api.py`
    # —— 又是「枚举优于记忆」的教训，经验 178）。这里改为**在守卫层统一改向**：
    # `config` 的每个目录都由 `_resolve_dir(env_key, default)` 从 `WME_<key>` 读取，
    # 于是只要给子进程注入这几个环境变量，**全部模块**都会写到本用例的临时目录，
    # 既不再污染项目目录，也不会跨用例累积删除计数。
    env = dict(os.environ)
    for var, sub in _RUNTIME_DIR_ENVS:
        d = tmp_path / sub
        d.mkdir(parents=True, exist_ok=True)
        env[var] = str(d)

    r = subprocess.run(
        [sys.executable, mod],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert r.returncode == 0, (
        f"{mod} 自检失败（退出码 {r.returncode}）\n"
        f"--- stdout ---\n{r.stdout}\n"
        f"--- stderr ---\n{r.stderr}"
    )
    # 阶段 147：输出非空（防「静默通过」的空自检）。
    assert (r.stdout or "").strip(), (
        f"{mod} 自检退出码为 0 但**没有任何输出** —— "
        f"无法判断它究竟跑了什么（空自检与真自检在 rc 上无法区分）。\n"
        f"--- stderr ---\n{r.stderr}"
    )


# ---------------------------------------------------------------------------
# 阶段 147 · 守卫自身的元检查
# ---------------------------------------------------------------------------

def test_selfcheck_registry_has_no_duplicates():
    """登记表不得有重复项（重复会让同一模块被跑两遍、且 id 冲突）。"""
    names = [m[0] for m in MODULES]
    dup = [k for k, v in collections.Counter(names).items() if v > 1]
    assert not dup, f"MODULES 含重复登记：{dup}"


def test_selfcheck_registry_paths_exist():
    """登记的每个路径都必须真实存在（防改名后登记失效）。"""
    missing = [m for m, _ in MODULES if not os.path.isfile(os.path.join(ROOT, m))]
    assert not missing, f"MODULES 登记了不存在的文件：{missing}"


#: 枚举时跳过的目录 —— **直接导入** `scripts/undef_scan` 的唯一真值来源
#: （阶段 190：此前两边各写一份、注释却称「同一口径」，实测已漂移）。
from scripts.undef_scan import SKIP_DIRS as _SKIP_DIRS          # noqa: E402
from scripts.undef_scan import SKIP_DIR_PREFIXES as _SKIP_PREFIXES  # noqa: E402


def _enumerate_modules_with_main() -> list:
    """从磁盘**枚举**所有含 `if __name__ == "__main__"` 的模块（排除 tests/ 自身）。

    这是本组用例的**真值来源** —— 刻意不用手写清单（经验 167/178：
    手抄清单的失效方式是**静默的**）。
    """
    import re as _re
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith(_SKIP_PREFIXES)]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT).replace("\\", "/")
            if rel.startswith("tests/"):
                continue
            try:
                src = open(os.path.join(dirpath, fn), encoding="utf-8").read()
            except OSError:
                continue
            if _re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', src):
                found.append(rel)
    return sorted(found)


def test_artifact_dirs_are_skipped_by_the_enumerator():
    """产物目录（build/dist/.scratch/htmlcov 等）**不得**进入审计视野。

    实测代价（阶段 190）：未跳过时 `build/lib/**` 的 52 个源码副本被误报为
    「含 `__main__` 却未登记」⇒ 守卫变红，而它们只是构建产物。
    """
    found = _enumerate_modules_with_main()
    leaked = [p for p in found
              if p.split("/")[0] in {"build", "dist", "htmlcov"} or p.startswith(".scratch/")]
    assert not leaked, f"产物目录里的 .py 被当成源码审计了：{leaked[:6]}"


def test_selfcheck_coverage_is_accounted_for():
    """磁盘上每个含 `__main__` 的模块，必须**要么已登记、要么有显式豁免理由**。

    这是把「漏登记」从**静默**变成**机械可发现**的那一步：
    新增一个带自检的模块却两边都没记 ⇒ 本用例变红。
    """
    on_disk = set(_enumerate_modules_with_main())
    covered = {m for m, _ in MODULES} | set(NOT_REGISTERED_BY_DESIGN)
    unaccounted = sorted(on_disk - covered)
    assert not unaccounted, (
        f"以下模块含 `__main__` 但既未登记为子进程守卫、也不在豁免清单里：\n"
        f"  {unaccounted}\n"
        f"处置：能离线安全跑 ⇒ 加进 MODULES（并跑一次确认 rc=0 + 无副作用）；"
        f"否则加进 NOT_REGISTERED_BY_DESIGN 并写明理由。"
    )


def test_selfcheck_exemption_list_has_no_stale_entries():
    """豁免清单不得残留「已经不存在」或「其实已登记」的条目。"""
    on_disk = set(_enumerate_modules_with_main())
    registered = {m for m, _ in MODULES}
    ghosts = sorted(set(NOT_REGISTERED_BY_DESIGN) - on_disk)
    both = sorted(set(NOT_REGISTERED_BY_DESIGN) & registered)
    assert not ghosts, f"豁免清单含已不存在的模块：{ghosts}"
    assert not both, f"以下模块同时在登记表与豁免清单里（互相矛盾）：{both}"


def test_selfcheck_exemption_entries_all_have_reasons():
    """豁免清单的每条都必须**有理由**（空理由会被读成「忘了」）。"""
    empty = [k for k, v in NOT_REGISTERED_BY_DESIGN.items() if not (v or "").strip()]
    assert not empty, f"豁免清单里这些条目没有理由：{empty}"


def test_selfcheck_registry_is_a_nonempty_real_subset():
    """登记表必须非空，且其成员确实含 `__main__`（防「登记了却没有自检」）。"""
    assert len(MODULES) >= 30, f"登记表意外缩小到 {len(MODULES)} 条"
    on_disk = set(_enumerate_modules_with_main())
    without_main = sorted({m for m, _ in MODULES} - on_disk)
    assert not without_main, f"以下模块被登记但磁盘上没有 `__main__` 自检：{without_main}"


# ---------------------------------------------------------------------------
# Stage 147 · 静态未定义名（全仓）
# ---------------------------------------------------------------------------

def test_no_undefined_names_repo_wide():
    """全仓不得有「引用了但从未定义」的名字。

    为什么必须**静态**查：`from __future__ import annotations` 会让**注解里的
    未导入名**完全静默 —— 所有注解延迟为字符串、永不求值 ⇒ 运行时**永远不会**报错。
    实测（本用例上线时）抓到 3 处：

      · `core/detector.py`   —— 注解用了 `Dict[...]`，只导入了 `List, Optional, Tuple`
      · `services/validators.py` —— 注解用了 `_os.PathLike`，只 `import os`
      · `ui/tab_history.py`  —— 注解用了 `Optional[str]`，**完全没导入 typing**

    它们只有 `typing.get_type_hints()` / 文档生成 / 类型检查器会炸。
    ⇒ 静态枚举（AST）是**唯一**能发现这类缺陷的手段（经验 180）。
    """
    from scripts import undef_scan
    hits = {}
    for rel in undef_scan._all_py(ROOT):
        missing, err = undef_scan.scan_file(os.path.join(ROOT, rel))
        if err or missing:
            hits[rel] = missing or [err]
    assert not hits, (
        f"以下文件含「引用了但从未定义」的名字（注解里的也要修）：\n"
        + "\n".join(f"  {k}: {v}" for k, v in sorted(hits.items()))
    )


def _main_block(tree: ast.AST) -> "list | None":
    """返回 `if __name__ == "__main__":` 的 body（无则 None）。"""
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                for c in node.test.comparators:
                    if isinstance(c, ast.Constant) and c.value == "__main__":
                        return node.body
    return None


def _module_functions(tree: ast.AST) -> dict:
    """模块级函数名 → 函数节点（用于递归追踪自检块引用的本模块函数）。"""
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _decision_evidence(mod_rel: str) -> "tuple[bool, list]":
    """AST 判断：自检的**执行路径上**是否存在能导致非零退出码的构造。

    判据 = `__main__` 块 ∪ 「它直接调用的**本模块函数**」体里存在
    `assert` / `raise` / `SystemExit` / `sys.exit`。

    设计要点（两条，都是被实测教训逼出来的）：
      · **必须递归到被调用的本模块函数**：`ui/tab_history.py` 把断言放在
        `_run_selfcheck()` 里，只看 `__main__` 块会**误报**它为哑巴
        （首版探针正是这么误报的 —— 经验 174/177 的「作用域」问题）。
      · **跨模块调用刻意不计入**：`config.py` 的 `__main__` 调用了
        `validate_config()`，但那只是**打印结果**、不影响退出码 ⇒
        **不计入**正是要抓的形态（把判定权交给别人、自己永远 0 退出）。

    返回 (是否具备判定能力, 证据列表)。
    """
    path = os.path.join(ROOT, mod_rel)
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (OSError, SyntaxError) as exc:                       # noqa: BLE001
        return False, [f"无法解析：{exc}"]
    body = _main_block(tree)
    if body is None:
        return False, ["没有 if __name__ == '__main__' 块"]

    funcs = _module_functions(tree)
    # 收集 `__main__` 块里直接调用的本模块函数名
    called = set()
    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                nm = fn.id if isinstance(fn, ast.Name) else None
                if nm and nm in funcs:
                    called.add(nm)
    targets = list(body) + [funcs[n] for n in sorted(called)]

    evidence = []
    for node in targets:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assert):
                evidence.append(f"assert@L{sub.lineno}")
            elif isinstance(sub, ast.Raise):
                evidence.append(f"raise@L{sub.lineno}")
            elif isinstance(sub, ast.Call):
                f = sub.func
                nm = (f.attr if isinstance(f, ast.Attribute) else
                      (f.id if isinstance(f, ast.Name) else ""))
                if nm in ("exit", "SystemExit"):
                    evidence.append(f"{nm}()@L{sub.lineno}")
    return bool(evidence), evidence


#: 允许「自检无判定能力」的**显式白名单**：模块 → 理由。
#: 空白项必须为空 —— 每个例外都要有人为它负责（留白会被读成「忘了」）。
SELFCHECK_NO_DECISION_ALLOWED: dict = {}


@pytest.mark.parametrize("mod", [m for m, _ in MODULES],
                         ids=[m for m, _ in MODULES])
def test_selfcheck_modules_have_decision_power(mod):
    """自检必须**有能力失败**：断言 / 抛异常 / 非零退出，至少一个。

    只断 `rc == 0` 的守卫永远发现不了「哑巴自检」；本用例从**静态结构**上挡住它。
    """
    if mod in SELFCHECK_NO_DECISION_ALLOWED:
        pytest.skip(f"已登记为允许无判定：{SELFCHECK_NO_DECISION_ALLOWED[mod]}")
    ok, evidence = _decision_evidence(mod)
    assert ok, (
        f"{mod} 的自检**没有任何判定能力**（既无 assert、也无 raise / SystemExit）："
        f"它无论发现什么问题都会以 0 退出，本守卫因此永远绿。\n"
        f"修法二选一：① 在自检里加 assert 或在发现问题时 `raise SystemExit(1)`；"
        f"② 若确实只需打印，显式登记进 `SELFCHECK_NO_DECISION_ALLOWED` 并写明理由。"
    )
