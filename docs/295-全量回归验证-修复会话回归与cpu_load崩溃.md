# 阶段 295 · 全量回归验证：修复 5 处真实缺陷（4 会话回归 + 1 潜伏崩溃）

> 践行 294 沉淀的「阶段收尾应跑更广回归」——这是自 `acd5f4e`（290-b）后**首次全量回归**，
> 覆盖本会话 291–294 全部改动。结果：抓到并修复 **5 处真实缺陷**，并把 full-batch 的
> **16 个失败全部隔离证实为沙箱删除护栏 artifact**（非产品缺陷）。

## 一、结论先行

全量回归（218 文件 / 5 批）暴露的问题分两类：
- **5 处真实缺陷**（4 处我会话改动的跨阶段一致性遗漏 + 1 处阶段 224 潜伏 ctypes 崩溃）——**全部修复**。
- **16 个 full-batch 失败**——**全部是沙箱删除护栏 artifact**：无一例外都是 delete/cleanup/purge/rotation 测试，逐个单跑均 **RC=0**，同轮多文件累积删除即触发护栏（SIGTERM/拦截）。属项目阶段 183/201/252/254/289 已记载的环境 artifact。

## 二、5 处真实缺陷（修复 + 证实）

| # | 缺陷 | 来源 | 修复 | 证实 |
|---|---|---|---|---|
| 1 | docs/292/293/294 未登记 INDEX | 我会话遗漏 | INDEX 补「291–294」节 | v3 B2 rc=0 |
| 2 | `docker_static` 默认步骤未同步 run_all_checks 3 断言 | C5 遗漏 | 改 3 处（skip 测试改为对步骤集合免疫） | v3 B4 rc=0 |
| 3 | 同 #2（gates 清单断言） | C5 遗漏 | 断言加 docker_static | v3 B4 rc=0 |
| 4 | 同 #2（all-skipped 返回码） | C5 遗漏 | runner 改「一律返 2」 | v3 B4 rc=0 |
| 5 | **`cpu_load.process_busy_pct` 调 `GetProcessTimes` 只传 3 个 FILETIME（API 需 4）** ⇒ 第 4 次写入（user 时间）落未控地址 ⇒ **偶发栈损坏崩溃 0xC0000409**，拖垮整个 B1 批 | 阶段 224 潜伏 ctypes 缺陷 | 传齐 4 出参 + 求和改 kernel+user + 加 argtypes（参数个数错则大声报错防复发） | `test_cpu_load_probe` 单跑 RC=0；v3 B1 不再崩溃 |

缺陷 5 是本次最有价值的发现：一个「跨验证专用、返回 0 兜底」的探针助手，因 ctypes 少传一个出参而**非确定性地硬崩整个解释器**——290-b 时栈布局侥幸没崩，本次崩。它不在我会话改动内，是全量回归翻出的存量缺陷。

## 三、16 个 full-batch 失败 = 沙箱删除护栏 artifact（逐个隔离证实）

v3 full-batch 失败清单（B1×3 / B2×1 / B3×8 / B5×4）全部落在 delete/cleanup/purge/rotation 测试：
`test_cache_disk`(prune) · `test_cache_service_audit132`(delete-thumb×2) · `test_history_batch_delete`(delete-thumb) · `test_legacy_selfcheck[api.py]` · `test_model_manager_download`(removes_backup) · `test_orphan_cleanup`(purge×6) · `test_utils_audit90`(rotation×4)。

**隔离证明（逐个单跑，一文件一轮，删除量重置）**：以上每一文件单独 `pytest` 均 **RC=0**；而同一轮跑多个删重型文件即被 **SIGTERM**（护栏按「轮」累计删除 >50 触发）。判据：
1. 16/16 全是删文件类测试；
2. 我会话改动**零触及删除逻辑**（只动 docs / 打包测试 / cpu_load 探针 / .gitignore / 记忆）；
3. 逐个单跑全过、同轮多跑被 SIGTERM（护栏累积触发的直接证据）；
4. 与项目 183/201/252/254/289 对「沙箱删除护栏」的既有记载同类。

⇒ **非产品缺陷，是本沙箱对批量删除的限制**；不影响 CI（CI 每次全新 checkout，无累积删除）。

## 四、附带修复

- `.scratch/run_batched.py`：给每批传**唯一项目内 basetemp**，消除 %TEMP% 清理撞护栏的 teardown artifact（B5 早期 RC=1 假象的来源）。

## 五、验证

- v3 全量：B4 rc=0（会话修复确认）；B1 不再崩溃（cpu_load 修复）；残余失败 16 个逐个隔离 RC=0（artifact）。
- `test_cpu_load_probe` / `test_orphan_cleanup` / `test_cache_disk` / `test_history_batch_delete` / `test_model_manager_download` / `test_legacy_selfcheck` / `test_cache_service_audit132` / `test_utils_audit90` 均单跑 RC=0。

## 六、自反证伪

- 「16 个是 artifact」以逐个单跑 RC=0 + 同轮 SIGTERM + 零删除逻辑改动三重证明，非感觉。
- 「5 处已修」以 v3 对应批 rc=0 / 单跑 RC=0 证明（B2/B4 干净、cpu_load 单跑过）。
- 未验证：full-batch 在**无沙箱删除护栏**的机器上是否 16 个全绿（本机无法构造该条件；但逐个单跑已等价证明逻辑正确）。
