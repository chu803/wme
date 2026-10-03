# 阶段 158 · `collect_real_assets` 其余未测子命令守卫

## 动机

阶段 152 只审计了 `scripts/collect_real_assets.py` 的 `cmd_add`（抓到 P0 静默数据损坏），
但 `cmd_scan` / `cmd_download` / `cmd_list` 与辅助 `_upsert` / `_infer_id_from_filename`
仍**零专属测试**。本轮顺延审计这些未覆盖入口，探针量化每种子命令的静默数据损坏 / 契约风险，
分类（defect vs by-design），最小修复，补守卫，并以注入式反例验证守卫有牙。

## 探针（隔离 tmp，脚本 `probe_158.py`）

逐入口直灌隔离 tmp，量化结果：

| 入口 | 发现 | 分类 |
|------|------|------|
| `cmd_download`（冲突路径） | 下载直接覆盖 `images/<fn>` → 再被 `_upsert` 拒绝（sha 不一致）→ **磁盘文件被换成 C2、manifest 仍记 S1、退出码误报 0** | **P1 静默数据损坏** |
| `cmd_download`（失败） | `rc=2`、无残留空文件 | by design 正确 |
| `cmd_scan`（同内容异名） | 两张同 sha 图 → 只登记 1 条（dedup by content） | by design 正确 |
| `_upsert`（旧 bool 返回值） | `skip`/`reject` 无法区分，调用方无法给出正确退出码 | 设计缺陷（支撑 P1） |
| `_infer_id_from_filename` | `Foo Bar`→`foo_bar`、`MyImage!!.JPG`→`myimage`、`""`→`img`、超长截断 24、冲突 `_N` | by design 正确 |

冲突下载的实测证据：
```
[probe] S1=0118f144 S2=1bee1b90
[probe] 磁盘文件 sha=1bee1b90 (期望保持 S1=0118f144)
[probe] manifest sha=0118f144 (期望 S1)
[probe] 磁盘被覆盖? True  退出码误报成功? True
```
即「manifest 的 sha256 与磁盘文件静默失同步 + 退出码误报成功」——与阶段 152 `add --copy`
的「先覆盖、后拒绝」**同一种缺陷**（打印契约 ≠ 强制契约，经验 186/189），只是出现在下载路径上。

## 缺陷分类

- **P1（defect）· `cmd_download` 先下载覆盖、后判定**：下载直接落到 `images/<fn>` 并覆盖同名已登记文件，
  待 `_upsert` 判定拒绝时已无法挽回（且退出码仍是 0）。
- 其余入口（`cmd_scan` 的 dedup / 重扫保留元数据、`cmd_list`、`_infer_id_from_filename`）经探针验证均 **by design 正确**，
  本轮不改动，仅补回归护栏。

## 最小修复

1. **`_upsert` 由 `bool` 升级为状态字符串**：返回 `"accepted"` / `"skipped"` / `"rejected"`，
   使调用方能区分「同图跳过（幂等）」与「路径冲突（失败）」，从而给出正确退出码。
2. **`cmd_download` 改为「先判定、后落盘」**（与阶段 152 `cmd_add --copy` 同构）：
   先下到临时文件 → 算 sha256 → `_upsert` 判定 → **接受后才 `shutil.move` 进 `images/`**；
   拒绝/跳过则清理临时文件，**绝不覆盖磁盘上已登记的图**。
3. 同步把 `cmd_scan` / `cmd_add`（两处 copy / 非 copy）的接受判定从 `if _upsert(...):`
   改为 `if _upsert(...) == "accepted":`（字符串非空，旧 `if` 在升级后会恒真 → 正是本轮要堵的回归）。
4. 顶部补 `import shutil` / `import tempfile`（移除 `cmd_add` 内的内联 `import shutil`）。

## 守卫

`tests/test_collect_real_assets_158.py`（**14 例**，tmp 隔离、不碰真实数据集）：

- **scan**：新增+保存 manifest / 同 sha 跳过 / 路径冲突拒绝且**不改写已登记 sha** /
  重扫**保留手工 ai_source·style_hint** / `images/` 目录缺失返回 1；
- **`_upsert`** 状态字符串三态；
- **`_infer_id_from_filename`** 清洗·截断·冲突 `_N`；
- **download**：非法 scheme 返回 1 / 失败返回 2 且无残留 / 成功登记+保存+落盘 /
  **路径冲突拒绝且不覆盖磁盘（核心守卫）** / 同 sha 幂等视为成功；
- **`cmd_list`**：空 manifest 占位提示 / 打印已登记行。

守卫口径同阶段 152：**断言磁盘字节与 manifest 的 sha256 一致**（缺陷本质即「打印成功、磁盘却是另一回事」）。

## 注入式反例（守卫有牙）

脚本 `inject_158.py`：记录修复后文件外部 sha256 → 把 `cmd_download` 核心保护块替换为
「先下载覆盖磁盘、再 `_upsert`、无条件 `return 0`」的缺陷版本 → 跑守卫 → 断言变红 →
用注入前记录的原文**逐字节还原** → 校验 sha 一致 → 再跑守卫 → 断言变绿。

> 沙箱对「项目根下批量删 >50 文件」拦截（用户拒），故反例改放**系统 temp** + `tmp_path_retention_policy=all`
> 以**零删除**完成验证，不再触发删除提示。

结果：
```
[inject] 注入后 pytest 退出码 = 1（期望非 0 → 红）
[inject] 还原字节级一致 ✓  (sha = 16238c60cfc371cc…)
[inject] 还原后 pytest 退出码 = 0（期望 0 → 绿）
守卫有牙 : YES
```
核心守卫 `test_download_path_collision_rejected_not_overwritten` 在缺陷版本下失败（磁盘被覆盖 / 退出码误报 0），
还原后通过——守卫精准命中、还原字节级一致。

## 回归

新 14 例 + 既有 `test_collect_real_assets.py` 4 例 = **18 例全绿**。

## 诚实边界

- **`_load_manifest` 解析损坏时返回空清单并随后覆盖写**：若 `manifest.json` 因手抖 JSON 损坏，
  `cmd_scan`/`cmd_add`/`cmd_download` 仍以「重新扫描」覆盖写，会**静默丢失手工 `ai_source`/`style_hint`
  元数据**。阶段 158 **未修**此边界（属覆盖面变更、无真实损坏样本验证，保守不动），记为已知边界，
  后续可加「损坏不覆盖、先备份」保护。
- `cmd_scan` 每次 `_save_manifest` 以 2 空格缩进重写——健康重扫保留全部字段（仅重排格式），无数据丢失。
- 同内容重下载：缺陷修复后 `_upsert` 返回 `skipped` → 临时文件被清理、不重复登记（幂等），
  退出码由旧版「覆盖写后跳过=0」变为「skipped=0」，字节结果一致。

## 产物

`docs/158-collect_real_assets未测子命令守卫.md`
