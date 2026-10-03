# Stage 127 — services/auto_pipeline.py 真值审计：manual 模式 NameError + 重复进度事件

> 审计方法：探针量化 → 分类（缺陷 vs by-design） → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 文档 → 记忆同步。
> 审计对象：`services/auto_pipeline.py`（自动处理管线核心服务，四入口共用：CLI / 界面 / api.py / server.py）。
> 荒漠度：`audit_selector.py --unaudited` 榜首 **7.8**（code 389 / refs 50 / files 4）。

## 1. 探针量化

探针 `s127_probe.py`（写 `%TEMP%`，项目零残留）实测：

| 项 | 实测 |
|---|---|
| `process_file(mode="manual", ...)` | **泄露 `NameError: name 'load_image_with_alpha' is not defined`**（D1 成立） |
| 源码中 `on_progress(0.92, "保存结果……")` 出现次数 | **2**（应为 1，D2 成立） |
| 对照：`process_file(mode="auto", ...)` | 正常（skipped，管线无异常） |

## 2. 两处真缺陷

### D1（严重，真缺陷）：manual 模式因缺失 import 完全不可用
- **症状**：`process_file` 的 manual 分支（第 443 行）调用 `load_image_with_alpha(mp)`，但第 56 行 import 只引入 `load_image_full, save_image`——`load_image_with_alpha` **从未导入**。任何 `mode="manual"` 的文件处理（CLI `--mode manual`、界面批量手动掩码、同名 `.mask.png` 自动发现）**调用即 `NameError` 崩溃**。
- **定性**：真缺陷（未完成的引用/导入，与 Stage 125 缺陷 C 同类）。manual 是文档化的核心模式，属「覆盖荒漠里的活体崩溃」。
- **为何长期没被发现**：`__main__` 自检**只覆盖 auto 模式**；既有测试亦未以真实掩码图跑通 manual 文件路径（auto 路径的正确性成了 manual 缺陷的掩护——经验 10）。
- **最小修复**：import 行补 `load_image_with_alpha`；并在 `__main__` 自检**补一条 manual 模式用例**（堵住自检盲区），子进程守卫随之覆盖该路径。

### D2（轻微，真缺陷）：保存阶段进度事件重复
- **症状**：保存前 `on_progress(0.92, "保存结果……")` 连写两遍（第 503–504 行），进度回调被触发两次（界面进度条抖动 / 重复日志）。
- **定性**：真缺陷（冗余复制粘贴），无功能危害但污染进度事件流。
- **最小修复**：删除重复的一行。

## 3. 守卫与注入式反例

- 回归守卫：`tests/test_auto_pipeline_audit127.py`（4 用例：manual 显式 mask_path / manual 同名掩码 / D2 进度事件唯一 / D1 静态邻位 import 复检）。
- 自检补强：`services/auto_pipeline.py` `__main__` 新增 manual 模式用例（原自检仅 auto）。
- 子进程守卫：`tests/test_legacy_selfcheck.py` 的 `MODULES` 新增 `("services/auto_pipeline.py", [])`（Stage 122 铁律）。
- 静态扫描：自研 `ast` 未定义名扫描器复检全模块，仅 `load_image_with_alpha` 一处真未定义（`__file__` 为误报）。
- 注入式反例：`s127_inject.py`（写 `%TEMP%`）——FIXED 守卫通过 → 写回 BUGGY（D1 去掉 import + D2 复制进度行）→ 守卫捕获回归 → 还原 FIXED，`sha256=fa182ee13898453608c202c57b28d54e655990ca6f1ca8d76bb2238ebf51958d` 字节一致。

## 4. 影响面与回归
- 修复 D1 后 manual 模式首次可用（探针实测 `status=success` 并产出结果文件）。
- 修复 D2 后运行时 `(0.92, 保存结果……)` 进度事件恰好 1 次。
- 本阶段全测：`test_auto_pipeline_audit127.py`(4) + `test_legacy_selfcheck[services/auto_pipeline.py]`(1) = **5 例 EXIT=0**。
