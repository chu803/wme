# 阶段 114 · `cli.py` 真值审计

> 选点：由 `scripts/audit_selector.py --unaudited` 给出（榜首 `cli` 10.5，485 代码行 / 46 测试引用 / 4 个测试文件）。
> 审计口径：逐条核对「CLI 声明（argparse / docstring）」与「实际接线」是否一致；对「疑似缺陷」先复测判定条件再定夺（经验 119）。

## 1. 模块概览

`cli.py`（入口层）是无需浏览器、在终端/服务器批量去水印的命令行入口，复用
`services.auto_pipeline.process_file`（图片）、`services.pdf_service.process_pdf`（PDF）、
`services.video_service.process_video`（视频）与 `services.quality_service`，零 gradio 依赖。

结构：`_kind_of`（按扩展名判类型）→ `_collect_inputs`（收集输入）→ `_build_arg_parser`（argparse）
→ `_run_one` / `_run_pdf` / `_run_video`（三个分派执行体）→ `_dispatch`（按类型路由）
→ `_print_result`（唯一拼装用户可见输出行的出口）→ `main`（流程编排）。

## 2. 探针与方法

既有 2 个测试文件（`test_cli_real_superres` / `test_services_audit88`）**只覆盖 argparse 与真实超分透传**；
`_kind_of` / `_collect_inputs` / `_dispatch` / `_print_result` / `_run_one` 的展示键路径**零覆盖**。
本轮据此补两条探针：

1. **AST 契约探针**（`%TEMP%`，项目内零残留）：解析 `add_argument` 的 dest 集合 ↔ `args.*` 读取集合 ↔ 下游调用 kwarg；
2. **经验探针**：实测 `_kind_of` 各扩展名、`_collect_inputs` 的「文件/文件夹 × 递归 × 含媒体」四象限、`_dispatch` 路由、`_print_result` 五分支。

### 探针结论（多为「实证正确」）

| 探针对象 | 结果 |
|---|---|
| argparse dest ↔ `args.*` 读取 | 无未声明读取、无 inert 选项（`dual_run` 经 `getattr` 读取，探针误报已澄清） |
| 下游 kwarg 对齐（`process_file`/`process_pdf`/`process_video`） | 传入 kwarg **全部被接受**（27/22/23 全覆盖） |
| `_kind_of` 大小写与未知类型 | 正确（`.JPEG`/`.WebP` 归一为 image，无扩展/未知为 unknown） |
| `_collect_inputs` 四象限 + 单文件任意类型 | 正确（非递归只顶层、递归含子目录、`--include-media` 才收 PDF/视频） |
| `_dispatch` 按类型路由 | 正确 |
| `_print_result` 五分支（image/pdf/video success + skipped + failed） | 图标/名字/计数均正确 |
| `--detect-preset` 帮助列出的 10 个预设名 | 与 `core.detection_presets` 注册表**逐名一致** |
| 质量等级 `good/warn/review`（CLI 汇总 Counter 的键） | 与 `QualityResult.level` 取值一致 |
| `--serve` docstring「默认 127.0.0.1:7861」 | 与 `config.API_HOST/API_PORT`、`start_server` 回退一致 |

## 3. 真实缺陷（F2）

### 3.1 症状

`--superres` / `--superres-amount`（后处理深化组）此前**只透传给图片**（`_run_one`），
**PDF 路径 `_run_pdf` 完全未传**——但 `services/pdf_service.process_pdf` **明确接受并在逐页处理中使用**
`superres` / `superres_amount`（`pdf_service.py:333-334, 473-474`）。

后果：用户执行 `python cli.py -i 合同.pdf --superres` 时，**该标志被静默忽略**，
PDF 不会做轻量超分，而 CLI 帮助对此**没有任何「仅图片」的说明** ⇒ 属「声明的开关对该输入类型静默失效」。

### 3.2 判定条件复测（经验 119）

- `process_pdf` **确实消费** `superres`（不是死参数）⇒ 修复有实际效果；
- 该标志为 `store_true`（默认 False）⇒ 不传时行为零变化，**仅在用户显式传入时生效**；
- UI 的 PDF 路径（`ui/tab_media`）也未暴露轻量超分 ⇒ 属前端遗漏，非服务层限制。
判别：**服务支持 + 用户显式开启 + 帮助无例外说明 = 真实缺陷**（而非「看似缺陷」）。

### 3.3 修复

`_run_pdf` 的 `process_pdf(...)` 调用补传两参数：

```python
            artifact_amount=args.artifact_amount,
            superres=args.superres,              # ← 新增
            superres_amount=args.superres_amount,  # ← 新增
            real_superres=args.real_superres,
```

> 边界：`process_video` **不接受** `superres`（视频逐帧轻量超分未实现），故视频仍只有 `--real-superres`。此边界显式登记。

## 4. 加固（F1，潜伏期）

### 4.1 症状

`_print_result` 拼输出行时按 `original_name → input_path` 取名字：

```python
name = rd.get("original_name") or _os.path.basename(str(rd.get("input_path", "")))
```

二者**皆缺**时 `name` 为空串 ⇒ 打印出「`❌  失败：…`」这种**无文件名的行**（实测复现，见下）。
`_run_one` 此前只 `setdefault("kind","image")`，**未自保证**这两个展示键；它依赖 `process_file`
「每个分支都恰好带这两键」这一跨模块隐含契约。

### 4.2 判定条件复测

`process_file` 当前 4 个 return 分支（不存在 / 找不到掩码 / skipped / success）**都带** `original_name` + `input_path`
⇒ 该缺陷**当前不可达**（潜伏期）。但 CLI 侧未自保证，一旦 `process_file` 新增一个不带这两键的分支，
或 `_dispatch` 引入新的结果生产者，CLI 就会**静默打印空文件名**。属「键集依赖未在生产者侧收口」（技能候选 #5）。

### 4.3 修复

`_run_one` 用 setdefault 自保证展示键（与既有 `setdefault("kind","image")` 同构、**不覆盖**下游已给的值）：

```python
    rd.setdefault("kind", "image")
    rd.setdefault("original_name", _os.path.basename(path))  # ← 新增
    rd.setdefault("input_path", path)                        # ← 新增
    return rd
```

> 单真值来源：下游带了名字时**不覆盖**（`setdefault` 语义），仅在下游缺失时补位。

## 5. 守卫与注入式反例

新增 `tests/test_cli_audit114.py`（**9 例**）钉死上述契约：`_kind_of` 大小写/未知、
`_collect_inputs` 四象限 + 单文件任意类型、`_dispatch` 路由、`_print_result` 五分支与计数、
「评估只对图片」、F1 展示键自保证（含「下游已给不覆盖」）、F2 轻量超分透传。

注入式反例（回退两处修复）：

```
注入前 sha256 = b6e83619…
临时回退 F1+F2 → pytest EXIT=1，判据命中：
  FAILED test_run_one_always_supplies_display_keys
  FAILED test_run_pdf_threads_light_superres
还原 → sha256 逐字节一致（b6e83619…）
```

两处守卫均**真会失败**（非哑巴守卫）。

## 6. 验证

受影响面（cli 相关 + 注册表）：

```
pytest tests/test_cli_audit114.py tests/test_cli_real_superres.py \
       tests/test_services_audit88.py tests/test_audit_selector.py
→ EXIT=0（全绿）
```

## 7. 注册与交付

- `scripts/audit_selector.py`：`AUDITED_SINCE["cli"] = 114`，并补 provenance 注释。
- 新增 `tests/test_cli_audit114.py`（9 例）。
- `cli.py` 两处修复（`_run_pdf` 补传轻量超分；`_run_one` 自保证展示键）。
- README §2.61 / TODO P1.62 更新；LESSONS 追加 122；工作记忆追加本阶段条目。

## 8. 下一步（未审榜）

`core/superres` 10.5 → `services/postprocess_service` 10.5 → `core/mask_generator` 10.5 → `services/compare_service` 10.3。
