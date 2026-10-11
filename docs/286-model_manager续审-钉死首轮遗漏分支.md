# 286 · core/model_manager 续审 — 钉死首轮遗漏的 verify/active 分支不变量

> 5j 深度复审链第 11 站。阶段 89（设备与模型管理真值审计）的 `model_manager` 子模块的二次复审。
> 荒漠指标：838 行 / 22 引用 = **比 38.1**（本轮候选最高），故取为本站。

## 结论
**零真缺陷**。该模块在首轮（阶段 89 真值审计 + 阶段 165 下载路径守卫）已把全部 P0 修扎实：
- url 无源不得回退全局源（静默装错权重）
- 下载/回滚必须对称丢弃旧缓存（静默沿用旧权重）
- 惰性初始化置位顺序（半初始化固化）
- `verify_model` / `slot_detail` 分支键集合一致（`_VERIFY_KEYS` 契约）

本轮补的是**首轮未机控的分支不变量**。探针 27 项实跑全部正确，新增 18 例守卫全绿，全回归 **109 例全绿**（含 model_manager×3 + core_audit89 + tab_models×2 + api 只读端点 251），EXIT=0、无夹具泄漏。

## 覆盖清单（18 例守卫 + 探针 27 项）
| 类别 | 钉死的不变量 |
|---|---|
| `verify_model` sha256 | 不匹配 → `sha_ok=False` + reason 含「篡改」；**SHA 算不出（不可读）→ `sha_ok=False` + `sha_error` 非空 + reason 归因「无法读取文件」（而非「篡改」）** |
| 体积 0.9× 边界 | 文件恰好 `0.9×expected` → `size_ok=True`；差 1 字节 → `False`（`verify_model` 与 `is_model_available` 同口径） |
| `set_active_model` | 不存在槽 → `False` 且 active 不变；成功**清空权重缓存**（副作用，防误用旧权重）；`persist=True` 写状态文件 `{active:key}`；**`persist` 写失败仍返回 `True`（切换已生效，写盘非致命）** |
| `get_model_slot(None)` | 返回当前 active 槽 |
| `register_model_slot` | 无 `filename` → 默认 `key+".pt"` 且 path 落到 `MODEL_DIR`；重复注册 → 后者覆盖前者 |
| `_sha256_of_file` | 指向不存在路径 → **抛异常**（不静默返回空串），调用方据此判失败 |
| `rollback_model` | `copy2` 抛异常 → 返回 `False`（不冒泡） |
| `blank_verify_result` | 键集 `== _VERIFY_KEYS`（声明与实现一致，防调用方 KeyError） |
| `slot_detail` | 带 sha 且匹配 → `sha_computed` 正确 + `sha_ok=True`；无 sha → `sha_computed=""` 且 `sha_ok=True`（「无需校验→通过」） |

## 归因 / 重点
最值得指出的是 **`verify_model` 的两类 sha 失败分支被首轮完全漏测**：
- 前者（不匹配）指向「文件可能被篡改/损坏」；
- 后者（算不出，如被占用/权限不足）指向「无法读取文件，并非校验不匹配」。
- 二者 reason 文案**方向不同**，错误归因会误导用户（把「权限问题」当成「文件被改」）。本轮显式钉死两条分支各自的字段与文案。

另：`set_active_model` 的「persist 写失败仍返回 True」是**有意的容错语义**（内存切换成功、重启不持久化），但首轮无测试断言，可能被后人误改成「写失败即返回 False」而掩盖切换已发生的真相。本轮钉死其返回 `True` 且 active 已切换。

## 交付与落盘
- 守卫：`tests/test_model_manager_branches_audit.py`（18 例）
- 探针：`.scratch/probe_model_manager_reaudit.py`（27 项全 OK）
- 文档：`docs/286-model_manager续审-钉死首轮遗漏分支.md`
- 登记/记忆：`audit_selector.py` 补 286；`MEMORY.md`（264-286）+ `2026-10-10.md`
- 提交：**286**（6 files）
- 离线包：`.scratch/wme.bundle`（含本提交 + 完整历史）

## 下一站候选（5j 续审链）
已做 11 站：276–280 零缺陷、281 修复 1 缺陷、282–284 零缺陷、285 修复 3 缺陷、**286 零缺陷**。
余下候选：`device_manager`(89) / `logger`(90) / `theme`·`app`(91)。
