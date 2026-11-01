# 阶段 366 · `evaluate=true` 的 `/api/remove` 从阶段 337 起就是坏的——而唯一会跑它的那条自检被豁免了

> 日期 2026-11-01 · 性质：**P0 级 REST 缺陷 + 覆盖结构性缺口**

## 一、它是怎么被撞出来的

阶段 365 给 `server.py` 的自检加了用例6，于是第一次（自 337 以来第一次）完整跑了一遍
`python server.py` ⇒ 跑到用例2 直接崩：

```
AttributeError: 'QualityReport' object has no attribute 'detail'
  server.py:173  "quality": _quality_to_dict(qr)
  server.py:133      "detail": qr.detail
→ 客户端侧表现：http.client.RemoteDisconnected: Remote end closed connection without response
```

⇒ 真实后果：**任何 REST 客户端只要开 `evaluate=true`，拿到的不是错误 JSON，而是连接被掐断**。
`_process_one_b64` 外层那句 `except Exception: return {"status":"failed", ...}`
（注释写着"单张出错不影响服务可用性"）**没覆盖到 `return` 里的响应组装**——
承诺的边界恰好漏了出错的那一行。

## 二、为什么它能潜伏 4 个多月

| 防线 | 为什么没拦住 |
|---|---|
| `tests/test_server_endpoints.py` 测过 `/api/remove` | remove 用例**从不传 `evaluate`** ⇒ `qr is None` ⇒ `_quality_to_dict` 第一行短路返回，组装那行从未执行 |
| `python server.py` 自检**会**传 `evaluate=True`（用例2） | 但 `server.py` 在自检台账里是**豁免**的（`NOT_REGISTERED_BY_DESIGN`："B 启动服务"）⇒ 没有任何自动通道跑它 |
| 337 的守卫 `test_quality_i18n337.py` | 它钉的是"服务层不再产句子"和 UI/CLI 的渲染，**漏了 REST 序列化这一处消费者** |

⇒ 三条合起来是同一句话：**从未被执行的路径 = 未验证**（LESSONS 221），
而"豁免清单"可以把**唯一**那条覆盖藏起来——豁免从来不是免费的。

## 三、修法（三层一起做，缺一层就会再来一次）

1. **修缺陷**：`server._quality_to_dict` 不再读已删属性——
   `"detail": QS.detail_text(qr)`（对外契约仍是"一句话"，但跟随当前语言、由唯一实现渲染）
   + 新增机器可读的 `"detail_key": qr.detail_key`，让客户端可以自己翻译。
2. **补常驻覆盖**（`tests/test_server_endpoints.py` 两条）：
   - `test_remove_with_evaluate_returns_quality_payload`：走**真 HTTP + 生产服务器类**，
     `evaluate=true` ⇒ 断言 `status=success`、`quality` 是字典、`score/level/detail/detail_key` 类型与取值正确；
   - `test_quality_to_dict_never_touches_removed_attributes`：用**真的 `QualityReport` 对象**过一遍序列化
     （结构守卫：改了 dataclass 字段就要在这里露出来，不用等某条路径被执行）。
3. **取消豁免**：`server.py` 登记进自检台账 `MODULES`（实测 CI 等价环境
   Python 3.11 + 只装 min/dev 下 rc=0、约 20 秒、不需要网络与权重——EasyOCR/torch 缺失只走降级分支）。
   豁免理由里那句"启动服务"其实站不住：**它用的是随机端口、跑完自己 shutdown**。

## 四、反例（证明新用例有牙齿）

把 `"detail": QS.detail_text(qr)` 改回旧的 `qr.detail` ⇒ 两条新用例**同时变红**
（HTTP 那条报 `AttributeError`/连接被掐），复原后 9 例全绿。

## 五、顺带修掉的编号自伤

先把自己的自检用例写成"用例5"，与既有用例5重号；挪到末尾又漏改数字，跑自检时看到两行"用例5通过"才发现。
⇒ 自检输出里**每一条用例的编号都必须唯一**（它是一份可核对的清单，不是散文）。已改成用例6。

## 六、诚实边界

- 只修了 `_quality_to_dict` 这一处消费者；全仓 `\.detail\b` 复扫：其余命中都是
  i18n 键名（`history.detail.*`、`models.detail`）、`WMEError.detail`（另一套结构）与
  注释/历史文档 ⇒ 无第二处同类。
- 响应新增 `detail_key` 属**追加字段**，老客户端不受影响；`detail` 的语义（一句话）保持不变。
- 本次没动 `_process_one_b64` 的 try 边界。更彻底的做法是把响应组装也纳入兜底
  （"宁可返回 degraded，也不让连接死掉"），那属于接口容错策略变更，另案。
