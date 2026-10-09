# 阶段 245 · utils/logger 测试的全局注册表泄漏（234 段 rotation flaky 的真根因）

> 承接阶段 244：244 的全量回归暴露 `tests/test_utils_audit90.py` 的 4 条 rotation 用例
> 在批次内反复失败。此前它们被登记为「**环境负载型 flaky，非代码缺陷，断言不削弱**」
> （阶段 234/239）。本阶段证明：**这个定性是错的**——它是一个真实的**测试卫生缺陷**。

---

## 1. 结论先行

| 项 | 结论 |
|---|---|
| 根因 | `test_stale_multi_handler_would_break_rotation` 把 handler 挂在**全局** `logging` 注册表上，`finally` 只 `close()` 句柄、**不摘 handler、不还原 `propagate`** ⇒ 泄漏跨用例存活 |
| 证据 | 守卫在修复前实测到 `logging.getLogger("t90.old1")` 残留 **3 个 handler**（含已关闭的 `RotatingFileHandler`）且 `propagate=False` |
| 后果 | 后续任何 `getLogger("t90.old1").warning(...)` 会写向**已关闭**的流 ⇒ 抛 `ValueError: I/O operation on closed file` 或打印 `--- Logging error ---`，把**无关用例**搞红 |
| 修复 | 反例改为「先存快照 → `finally` 里摘 handler + 还原 propagate/level」 |
| 守卫 | `tests/test_logging_global_leak245.py`（自建隔离，**不依赖执行顺序**） |
| 验证 | 双向：修复前守卫 FAIL（3 handlers泄漏）→ 修复后 PASS；两种文件顺序均 EXIT=0；相邻 logger/i18n **154 例全绿** |

---

## 2. 缺陷细节

`tests/test_utils_audit90.py::test_stale_multi_handler_would_break_rotation`
（阶段 90 的注入式反例，用途是「手工给两个 logger 各挂一个独立 handler，模拟旧实现，
必须复现轮转失败」）：

```python
h1, h2 = standalone(), standalone()
lg1 = logging.getLogger("t90.old1")      # ← 全局单例，跨用例存活
lg2 = logging.getLogger("t90.old2")
for lg, h in ((lg1, h1), (lg2, h2)):
    lg.handlers.clear()
    lg.propagate = False# ← 全局状态，未还原
    lg.setLevel(logging.WARNING)
    lg.addHandler(h)                      # ← 挂上，随后只 close 不摘
...
finally:
    sys.stderr = old_stderr
    h1.close(); h2.close()                # ← 只关句柄，handler 仍挂在全局注册表
```

该文件的 `fresh_logger` 夹具只还原 `L._logger_cache` / `L._shared_handlers` /
`L._initialized`，**完全不碰全局 `logging` 注册表** ⇒ 泄漏不会被夹具回收。

两个 logger 因此永久残留在 `logging.Logger.manager.loggerDict` 里，带着
一个**已关闭**的 handler 与 `propagate=False`。这既解释了 rotation 用例的间歇性失败，
也违反项目铁律「注入式反例必须自带还原」——反例只负责复现失败，不该留残留状态。

---

## 3. 修复

```python
saved = [(lg, list(lg.handlers), lg.propagate, lg.level)
         for lg in (lg1, lg2)]
...                                        # 原反例逻辑不变
finally:
    sys.stderr = old_stderr
    for lg in (lg1, lg2):
        lg.handlers.clear()                # 先摘除（含已关闭的 h1/h2）
    h1.close(); h2.close()
    for lg, handlers, propagate, level in saved:
        lg.handlers.extend(handlers)       # 还原原 handler 集合
        lg.propagate = propagate           # 还原传播标志
        lg.setLevel(level)                 # 还原级别
```

**反例的核心行为（必须复现轮转失败）一字未改** —— 只补了还原，不削弱断言。

## 4. 守卫

`tests/test_logging_global_leak245.py` 断言「跑完反例后全局 logger 必须干净」。

设计上有一个刻意的选择：**守卫自己完整复刻反例逻辑**（用 `monkeypatch` + `tmp_path`
自建等价隔离，因为 `fresh_logger` 是 `test_utils_audit90.py` 的**文件内**夹具、跨文件
不可见），而**不是**依赖「先跑原反例、再断言」的顺序。理由：顺序依赖的守卫在单独跑时
会给出假绿/假红，**不可靠**。

它同时是一条**回归防线**：若有人日后把 `test_utils_audit90.py` 里的还原逻辑删掉，
守卫会立刻变红。

---

## 5. 对既有结论的订正（诚实登记）

| 阶段 | 原登记 | 本阶段订正 |
|---|---|---|
| 234 / 239 | `test_utils_audit90` rotation 4 条=「**环境负载型 flaky、非代码缺陷**，隔离复跑全绿、断言不削弱」 | ❌ **定性错误**。隔离复跑确实会绿（因为泄漏仅在**同进程内后续用例**才发作，单跑该文件时残留 logger 无人使用），但它是**真实的全局状态泄漏缺陷**，且会把同批次内后续无关用例拖红。本阶段已修根因。 |

**为什么此前反复误判为「环境 flaky」**：泄漏的杀伤力只在「**同一进程内、泄漏之后
还有别的用例去用那个 logger 名**」时才显现。单跑该文件时，残留的 `t90.old1` /
`t90.old2` 无人使用 ⇒ 全绿；只有当整批文件里恰好有别的用例触发相关 logger 时才炸，
表现为「有时绿有时红」⇒ 极像环境抖动。

**教训**：**「隔离复跑全绿」不等于「不是缺陷」**。flaky 的定性必须给出**机制**，
否则「偶发绿」会被当成免责。定位flaky 根因时，除了「能否稳定复现」，还要问
**「它在什么条件下会咬人」**——本例的答案是「同进程内有其它用例使用同名 logger」。

---

## 6. 遗留

- 阶段 234/239 登记的其余 flaky 归类未在本阶段复查。
- 本阶段只修了这**一个**已证实的泄漏；其它测试文件若也有「往全局注册表挂东西却不还原」
  的模式，需另行审计（本阶段未做全库排查）。