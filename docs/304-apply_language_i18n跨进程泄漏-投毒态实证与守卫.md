# 304 · apply_language i18n 状态文件跨进程泄漏 —— 投毒态实证与守卫（2026-10-19）

> 用户从「5j 深度复审链耗尽后的待复审候选」中择定本项：apply_language() 经
> `cache/i18n_state.json` 向磁盘泄漏 en-US，污染后续期望 zh-CN 的 pytest 进程。
> 阶段 290 已实现治本（conftest 会话级重定向 `_STATE_FILE`），但**从未被投毒态实证**。
> 本阶段完成实证 + 注入式反例守卫，待复审项收口。

## 1. 结论（TL;DR）

- **根因**：`utils.i18n.apply_language()` 同时 `set_language()`（进程全局）与
  `save_language()`（落盘 `cache/i18n_state.json`）。`load_language()` 优先级为
  `env > 持久化文件 > config.UI_LANGUAGE(=zh-CN) > DEFAULT_LANG(=zh-CN)`。一旦磁盘文件是
  `en-US`，**之后每一个全新 pytest 进程**都会读进 en-US，压过 config，导致「期望中文」用例全红。
- **既有修复（290）**：`conftest.pytest_configure`（早于 collection）把 `i18n._STATE_FILE`
  重定向到会话临时目录 ⇒ 任何作用域的用例都读不到真实持久化文件。
- **本阶段实证**：用「投毒态重跑证伪」证明修复有效，并加守卫：
  - 守卫 `tests/test_i18n_state_leak_guard.py`：把真实文件写成 en-US（投毒）→ 拉起全新 pytest
    子进程 → 断言 `get_language()=="zh-CN"`；并反例闭合确认「毒」确实写入生效。
  - 注入式反证 `scripts/_i18n_leak_inject_proof.py`：设 `WME_I18N_NO_REDIRECT=1`
    （conftest 跳过重定向，复现修复前）→ 守卫必 FAIL（RC=1），证守卫能捕获修复缺失。
- **结论**：修复有效，待复审项收口；新增零副作用测试重现实开关（`WME_I18N_NO_REDIRECT`，
  正常跑恒为 unset，零行为变更）。

## 2. 验证（量化证据）

| 场景 | 命令 | 结果 |
|---|---|---|
| 投毒态 + 修复在位 | `pytest tests/test_i18n_state_leak_guard.py` | **RC=0**（子进程回落 zh-CN） |
| 投毒态 + 修复缺失（反证） | `WME_I18N_NO_REDIRECT=1 pytest tests/test_i18n_state_leak_guard.py` | **RC=1**（守卫捕获：`_STATE_FILE is None`） |
| 更广回归 | i18n 4 件套 + app_branches_audit | **RC=0 全绿** |

## 3. 归因（为什么之前测不出来）

- 早期夹具 `_isolate_i18n_language`（阶段 78）只快照/还原**进程内** `_LANG`，不碰落盘文件；
- `_isolate_runtime_dirs`（阶段 148）虽重定向 `CACHE_DIR`，但是**函数级**——模块级夹具与
  「收集期模块级代码」早于函数级夹具建立，会在隔离生效前读进真实文件，把 `_LANG` 污染成
  持久化值，导致第①条夹具还原也还原不回 zh-CN。
- 治本点在 `pytest_configure`（早于 collection）改模块级全局 `_STATE_FILE`，覆盖任意作用域。

## 4. 守卫设计要点

- 子进程重跑是「跨进程泄漏」唯一可信证法：单进程内 monkeypatch 无法复现「新进程读旧盘文件」。
- 反例闭合（确认毒确实写入）与正面断言（回落 zh-CN）**双向**齐备，避免「毒没生效却绿」的伪证。
- `WME_I18N_NO_REDIRECT` 是测试重现实开关，非产品行为；未设时 conftest 照常重定向。

## 5. 未决 / 注意

- 生产端 `apply_language()` 仍会写真实 `cache/i18n_state.json`——这是**产品预期行为**
  （记住用户语言偏好），不是缺陷；本修复只隔离测试进程，不动生产写盘。
- 真实文件当前不存在（仓库干净）；历史污染曾令宽回归 14 例全红，现已由 conftest 防线阻断。
