# Stage 128 — core/plugins.py 真值审计：保留名缺失 + 内建身份可伪造

> 审计方法：探针量化 → 分类（缺陷 vs by-design） → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 文档 → 记忆同步。
> 审计对象：`core/plugins.py`（插件 / 自定义修复引擎注册框架，阶段 11 新增，阶段 77 曾首次加固）。
> 荒漠度：`audit_selector.py --unaudited` 榜首 **7.6**（code 325 / refs 43 / files 4）。
> 注：阶段 77 已修 P1–P6 六项；本阶段发现的是**其加固未覆盖的两处新缺陷**。

## 1. 探针量化

探针 `s128_probe.py`（写 `%TEMP%`，快照/还原注册表，项目零残留）实测：

| 项 | 修复前 | 修复后 |
|---|---|---|
| 插件自设 `is_builtin=True` → `list_engines` 报告 `builtin` | **True（伪装内建）** | False |
| 该插件经 `reload_plugins()` 后是否仍存在 | **True（无法卸载）** | False |
| `register_engine(name="auto")` 返回值 | **True（被接受）** | False |
| `_REGISTRY["auto"]` | **插件实例（违反 docs/11）** | None |

## 2. 两处真缺陷

### D1（真缺陷）：内建身份依赖可被插件伪造的类属性
- **症状**：模块 docstring 第 37 行声称「**内建身份由实例类型判定**」，但实现在三处（`list_engines` 两处 + `reload_plugins`）用 `getattr(inst, "is_builtin", False)`。`InpaintEnginePlugin.is_builtin` 是**插件可自行设置的类属性**——插件写 `is_builtin = True` 即可：
  1. 在引擎清单里被标为 `builtin=True`（**显示欺骗**，正是 P1 想根除的那类问题）；
  2. 被 `reload_plugins()` 视为内建而**永不卸载**（开发期热加载对该插件静默失效）。
- **定性**：真缺陷（声明 ≠ 实现 + 可观测后果）。判据：框架用于区分「自己人 / 外来者」的标记若可由外来者设置，等于没有保护。
- **最小修复**：新增 `_BUILTIN_ENGINE_TYPES = (_BuiltinLama, _BuiltinOpenCV)` 与 `_is_builtin_engine(inst) = isinstance(...)`，三处判定改用类型判定；docstring 同步订正为「由实例类型判定（不再依赖 `is_builtin` 属性）」。

### D2（真缺陷）：保留名 `auto` 无保护
- **症状**：`"auto"` 是自动选择器、**从不进入 `_REGISTRY`**（docs/11 明文），因此 `register_engine` 里的「同名冲突」检查对它**永不触发**。插件只要 `name="auto"` 就被接受，被写进 `_REGISTRY["auto"]`；而 `list_engines()` 又**硬编码**跳过 `auto` ⇒ 注册表里是插件、清单里显示的却是硬编码的「自动选择 / 内建」——**注册表与清单脱节 + 引擎被隐藏**（P1 家族缺陷）。
- **定性**：真缺陷（违反 docs/11「auto 不进入 `_REGISTRY`」与 P1「清单 ≡ 注册表」不变量）。
- **最小修复**：新增保留名集合 `_RESERVED_ENGINE_NAMES = frozenset({"auto"})`，`register_engine` 在正则校验后显式拒绝。

### 已排除（by-design，不修）
- `has_engine("auto") == True` 而 `get_engine("auto") is None`：`auto` 不进注册表，调用方 `_run_engine_once` 判 None 抛可读错、`_resolve_dual_secondary` 先排除 `auto`——不对称被文档化且被调用方安全处理。
- `config.PLUGIN_DIRS/PLUGIN_ENABLED` 迭代：config 声明为 `list[str]`（`_env_list`），非字符串逐字符风险。

## 3. 守卫与注入式反例

- 回归守卫：`tests/test_plugins_audit128.py`（3 用例：保留名拒绝 / 属性不得伪装内建 / 伪装插件可被 reload 卸载）。
- 自检补强：`core/plugins.py` `__main__` 新增保留名与「按类型判定」用例。
- 子进程守卫：`core/plugins.py` 已在 `tests/test_legacy_selfcheck.py` 的 `MODULES` 中（阶段 13 起）。
- 注入式反例：`s128_inject.py`（写 `%TEMP%`）——FIXED 守卫通过 → BUGGY（删保留名检查 + 内建身份改回属性判定）→ 守卫捕获回归 → 还原 FIXED，`sha256=59c1e5b18eecd1d8f06f0af6867dfe84308b7a2c770d5b48d93cfd20f87133d3` 字节一致。

## 4. 影响面与回归
- 两处修复均为「收紧非法输入 + 订正判定依据」，不改合法插件行为：`test_plugin_hardening.py`(20) / `test_plugins.py`(4) / `test_dual_run.py` 全绿。
- 本阶段全测：`test_plugins_audit128.py`(3) + 既有插件测试 + 子进程自检 = **50 例 EXIT=0**。
