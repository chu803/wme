# 阶段 165 · `core/model_manager` 下载路径守卫

## 动机

覆盖度荒漠榜次席 = `core/model_manager`（499 代码 / 40 引用测试 / 比值 12.5）——**核心逻辑模块**
（模型注册表 / 切换 / 校验 / 下载 / 回滚）。既有 `tests/test_model_manager.py` 覆盖注册与校验；
**下载路径的「源解析 → 备份 → 校验 → 回滚 → 缓存」语义**此前无守卫。

## 探针（`probe_165.py`：隔离 tmp，桩 `download_to`）

抓到两处：

1. **（P1 · 静默装错权重）** 槽未提供 `urls` 时，`download_model` **回退到全局默认源**
   （`config.MODEL_DOWNLOAD_URLS` = **内置 lama 的 3 个地址**）⇒ 把 lama 权重下进**别的槽**路径；
   而该槽若无 `expected_size` / `sha256`，`verify_model` 会**静默通过** ⇒ 用户以为下载了目标模型，
   实际得到 lama 权重。实测：
   ```
   槽 urls = ()
   download_to 收到 urls = ['https://github.com/Sanster/…/big-lama.pt', 'https://ghproxy.net/…', 'https://huggingface.co/Carve/LaMa-ONNX/…']
   config.MODEL_DOWNLOAD_URLS = （同上）
   >>> 是否回退了全局（lama）地址：True
   ```
2. **（P2 · 缓存不对称）** 下载成功后**不丢弃该路径的旧缓存模型**，而 `rollback_model` 会
   `_CACHE.pop(dest)` ⇒ 已加载过旧模型时，`load_model` 继续命中旧对象，除手动热重载外
   **静默沿用旧权重**。实测：
   ```
   下载前 _CACHE = {…probe_slot.pt: OLD_MODEL_OBJ}
   下载成功，返回 …probe_slot.pt
   下载后 _CACHE = {…probe_slot.pt: OLD_MODEL_OBJ}     # 旧对象还在
   ```

## 最小修复

1. **无 urls 即明确失败**：`urls = list(slot.urls)`；空则 `raise ModelNotFoundError(...)`——
   不再回退到「别人的源」（内置 lama 自带 urls，本就不需要该回退）。
2. **下载成功后 `_CACHE.pop(dest, None)`**：与 `rollback_model` 对称，杜绝静默沿用旧权重。

## 守卫

`tests/test_model_manager_download.py`（**7 例**，隔离 tmp + 桩 `download_to`）：

- 无 urls ⇒ 抛 `ModelNotFoundError` 且**从不调用下载**；
- 自带 urls ⇒ 原样透传（不被全局源覆盖）；
- 下载成功 ⇒ **丢弃旧缓存**；`rollback` 丢弃缓存（对称性回归）且把备份写回；
- 下载失败 ⇒ **回滚到备份内容并向上抛异常**（不静默返回）；
- 下载成功 ⇒ 清理备份；
- 下载后体积校验不过 ⇒ 抛 `ModelCorruptedError` 且回滚。

## 注入式反例（守卫有牙）

`inject_165.py`（按经验 #188：`newline=''` 读写 + 原始字节 sha + 行尾感知锚点，本文件为 LF）：
分别回退两处修复，**各只红对应那一条**守卫：

```
回退修复①(无 urls 回退全局源) → rc=1 失败=['test_slot_without_urls_raises_and_never_downloads']
回退修复②(下载成功不清缓存)   → rc=1 失败=['test_download_success_drops_stale_cache']
两次还原均字节级一致 ✓ ；还原后 rc=0
```

## 回归

新 7 + `test_model_manager` + `test_core_audit89` + `test_ui_audit94` + `test_legacy_selfcheck`
= **126 例全绿**。

## 诚实边界

- 修复后「无下载源」由静默回退变为**显式报错**——这是**行为收紧**（更安全）：内置 lama 自带 urls，
  任何依赖「回退全局源」的配置槽都会立刻得到明确错误而非错误权重。
- 槽未登记 `sha256` 时，`verify_model` 仍无法识别「体积合理但内容错误」——本阶段从**源头**（urls）
  消除了主要致因，但「无 sha 即信任」仍是设计取舍（登记）。
- 既有自检的临时槽会写入 `_SLOTS`；测试须快照/还原注册表。**陷阱**：快照必须在 `_ensure_init()`
  **之后**——若在惰性初始化前快照（`_SLOTS` 为空），还原后 `_INITED` 仍为 True，后续用例的
  `_ensure_init()` 短路返回 ⇒ 注册表被清空且不再重建（本轮探针首版即踩此坑）。

## 产物

`docs/165-model_manager下载路径守卫.md`
