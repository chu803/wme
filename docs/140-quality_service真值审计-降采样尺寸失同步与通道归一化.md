# 140 · services/quality_service 真值审计——降采样尺寸失同步与通道归一化

> 日期：2026-09-27 ｜ 对象：`services/quality_service.py`（荒漠度 3.6 = 149 代码行 ÷ 41 引用测试 / 4 文件）
> 方法：探针量化 → 分类 → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 登记
> 前置：阶段 87 已修 P0（`_ssim_channel` uint8 溢出）与状态/观测对齐（level 改写同步 detail），本次为**续审**。

## 一、探针量化

| # | 候选 | 探针结果 | 判定 |
|---|---|---|---|
| **D1** | `_downscale_for_eval` 声称「两图按**同一目标边长**等比缩放」，实测是**各按自身边长**独立缩放 | 原图 800²（未超限）+ 结果 1600² → 返回 `800²` 与 `1024²`，**尺寸不一致**；宽高比不同时 → `1024²` 与 `1024×853` | **defect（P1）** |
| **D2** | `evaluate_quality_arrays` 入参未做通道归一化 | 灰度 2D → `_ssim` 的 `img[:,:,c]` 处 `IndexError`；RGBA 4C → 不崩但第 4 通道混入 SSIM、`changed_ratio` 恒 ≈1.0 | **defect（P1）** |
| **D3** | 自检 4 个 `NamedTemporaryFile(delete=False)` 无 try/finally | 实测中途崩溃泄漏 4 个 `tmp*.png`（620 B ×2 + 946 B ×2） | **defect（P2，与 136 同族）** |
| — | 空图 0×0 输入 | `cv2.filter2D` 抛 OpenCV assertion | by design（上游 `load_image_with_alpha` 不产空图） |
| — | `_ssim_channel` uint8 溢出 / level-detail 一致性 | 阶段 87 已修，`tests/test_services_audit.py` 5 条钉住 | by design |
| 观察 | `scripts/real_benchmark_decision.py:159` 传 `evaluate_quality_arrays(img, img, compute_no_ref=True)` | `TypeError` → 被该函数 `except Exception` 吞成 `None` → NIQE 永远不可用（静默失效） | **登记未修**（跨模块接口幻觉，非本模块缺陷） |

### D1 危害量化（决定性证据）

同一对图，两种路径的分级结论：

| 场景 | 修复前尺寸一致? | 修复前 level/score | 修复后尺寸一致? | 修复后 level/score |
|---|---|---|---|---|
| 超分放大 1.5×（比例相同） | ✅ | good/100 | ✅ | good/99 |
| **超分放大且宽高比变化** | ❌ | good/96（SSIM 失真） | ✅ | good/99 |
| **仅结果图超上限** | ❌ | **warn/81** | ✅ | **good/99** |
| **原图更大、结果图更小** | ❌ | **warn/85** | ✅ | **good/97** |
| 完全同尺寸（对照） | ✅ | review/80 | ✅ | review/80 |

危害窗口 = `postprocess_service.evaluate_improvement`（真实超分放大后对比）以及任何「结果图尺寸 ≠ 原图」的调用方，**在默认配置 `QUALITY_MAX_EVAL_SIDE=1024` 下即触发**。失同步后下游那句 `cv2.resize(r_rgb, 原图尺寸)` 是**非等比**回缩 → 内容被拉伸/压扁 → SSIM 与 changed_ratio 双双不可信，且**值域仍落在 0~1，不报错**（经验 26：值域必须用探针直接断言）。

## 二、最小修复（services/quality_service.py）

1. **D1**：`_downscale_for_eval` 改为「先让结果图**等比回缩到原图尺寸**，再对两图施加**同一个目标尺寸**」。契约声明与实现对齐（经验 44：归一化只做一次、且在对齐唯一入口做）。docstring 记录来龙去脉与实测数字。`evaluate_quality_arrays` 内那句回缩保留为**防御性兜底**。
2. **D2**：新增 `_as_rgb3(arr)`（2D 灰度→三通道复制 / 4C RGBA→丢 alpha / 其余原样），在 `evaluate_quality_arrays` 入口对两图各调用一次。与 135 `services/file_service._as_rgb3` 同族同解。
3. **D3**：自检改为 `_tmp_paths` 集中登记 + `try/finally` 兜底清理；新增用例 4（降采样尺寸一致）与用例 5（灰度/RGBA 归一化）。

## 三、验证

| 门禁 | 结果 |
|---|---|
| 守卫测试 `tests/test_quality_service_audit140.py`（17 用例：尺寸同步×4 场景 / 上限契约 / 关闭降采样返回原对象 / 端到端 good / `_as_rgb3` 三分支 / 灰度不崩 / 灰度&RGBA 自比 changed=0 / 私有函数灰度现状登记 / 子进程自检零泄漏 / 自检覆盖声明 / finally 清理） | ✅ 17/17 绿 |
| quality 相关回归（`test_services_audit.py` + `test_perf2.py` + `test_services_audit88.py` + 本阶段） | ✅ 56/56 绿 |
| 子进程守卫登记 `test_legacy_selfcheck.py` MODULES += `("services/quality_service.py", [])` | ✅ 可登记 |
| 注入式反例（`s140_inject.py`：还原 `_rsz` 独立缩放 + 撤掉入口对齐） | BUGGY rc=1 **红**；还原 sha256=`360ee9ccd2c07cad…` **逐字节一致**；回绿 rc=0 |
| `audit_selector.py --check` | ✅ EXIT=0，登记 39 条 |
| `audit_selector.py --unaudited` | `services/quality_service` 已剔除；下一站 `utils/brand` 3.3 |

## 四、登记

- `AUDITED_SINCE` += `"services/quality_service": 140`。
- 新增登记未修：`scripts/real_benchmark_decision.py:159` 的 `compute_no_ref=True` 形参幻觉（被 `except` 吞成 `None`，属跨模块接口漂移）。
- `_changed_ratio` 仍硬依赖 3 通道（`.any(axis=2)`）——归一化只在 `evaluate_quality_arrays` 入口；守卫测试已钉住「直接传灰度会抛 AxisError」这一现状，便于未来改动时主动复核。
- 下一站（`--unaudited`）：`utils/brand` 3.3 → `core/inpainter` 3.3 → `core/detection_presets` 3.1。

## 五、全量回归捕获的跨阶段缺陷（连带修复）

跑全量 pytest 时套装**红灯**，归因后发现三处相互纠缠的跨阶段遗留问题，本轮一并修复：

| # | 缺陷 | 根因 | 修法 |
|---|---|---|---|
| **R1** | `test_i18n_coverage::test_all_referenced_keys_are_defined` 报 `app.tab` 未定义 | 阶段 137 我在 i18n 自检里加了 `t("app.tab")` 探针；该测试的正则会**扫描整个 `utils/i18n.py`（注释也扫）** → 自检探针被当成生产引用 | 改用该测试 `_ALLOWED_UNKNOWN` 已登记的专用探针键；注释里不再写完整 `t("字面量")` 形式 |
| **R2** | `test_i18n_audit137::test_t_dict_node_returns_key` 断言中文却拿到英文 | 同上用例依赖「本机语言 = zh-CN」这一**隐式全局状态** | 显式 `monkeypatch _LANG`（经验 122），并补英文侧同契约断言 |
| **R3** | **跑完测试套件后本机 `cache/i18n_state.json` 被改成 `en-US`** | `ui/i18n_rt.apply_language` 内部 `save_language(lang)` **真实写盘**；`tests/test_i18n.py` 的 `test_apply_language_*` 调它时**未隔离 `_state_path`** → 每次跑测试都污染生产状态 | 给 `test_i18n.py` 加 **autouse fixture** 全局隔离状态文件到 `tmp_path`（覆盖含未来新增的所有用例）；已把真实文件恢复为 `zh-CN` |

**R3 是本轮最值钱的发现**：它长期潜伏——在干净 CI 机上「恰好」不影响（顺序/隔离差异），但在任何**连续两次跑测试**的开发机上都会让依赖中文默认值的用例假红，且**污染是持久的**（写进磁盘），符合经验 122 的「环境相关断言」范式。

**新增守卫**：`tests/test_i18n_audit137.py::test_selfcheck_static_keys_are_allowlisted`
—— 对齐真实扫描器口径（不剥注释），断言 `utils/i18n.py` 全文里的静态 `t("字面量")` 要么在键表、要么已在 `_ALLOWED_UNKNOWN` 登记，防止 R1 复发。

## 六、最终门禁

| 门禁 | 结果 |
|---|---|
| 全量 pytest（第二次，含 R1/R2/R3 修复） | ✅ 全绿 |
| 跑完全量后 `cache/i18n_state.json` | ✅ 仍为 `zh-CN`（污染消除） |
| `tests/test_i18n*.py` + `test_quality_service_audit140.py` | ✅ 67/67 绿 |

