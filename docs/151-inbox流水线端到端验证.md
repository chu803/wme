# 阶段 151 · inbox 收编流水线端到端验证（Option 2 推荐方向）

> 目标：把「丢图 → 预检红灯 → 人工标注 → 预检转绿」的闭合路径**用真实脚本实测跑通**，
> 而非停留在假设；并把阶段 149/150 之后已陈旧的 `benchmark_real/inbox/README.md` 重写为操作手册。

## 方法

- 不动项目内 `benchmark_real/` 任何文件：用 `WME_BENCH_DIR` 环境变量把 `collect_real_assets.py` /
  `real_asset_preflight.py` / `real_gate.annotation_scope` 指向一份**隔离临时拷贝**。
- 在拷贝里：生成一张带角标水印的合成图 → `collect_real_assets.py add --style corner_logo --copy`
  登记 → A/B 对照「加标注前 / 加标注后」两次跑 `real_asset_preflight.py` + 直查 `annotation_scope`。

## 结果（PASS）

| 阶段 | `committed_missing` | `complete` | 预检退出码 | 预检结论 |
|---|---|---|---|---|
| 加标注前（红） | `['ai_logo']` | False | **1** | ❌「承诺类缺真值证据: ai_logo」+「hint↔真值矛盾」 |
| 加 1 条 `manual:measured` 角标标注后（绿） | `[]` | True | **0** | ✅ 预检通过 |

`annotation_scope` 直查（绿）：`committed_missing=[]`、`committed_labeled=['ai_logo']`、
`complete=True`、`labeled=['corner_logo']`。

## 关键结论（已写进 `benchmark_real/inbox/README.md` 操作手册）

1. **硬红灯解除阈值**：`ai_logo` 的 `committed_missing` 红灯在 **≥1 条 `manual:*` 角标标注**
   （style ∈ `corner_logo`/`logo_watermark`）时即解除。`real_gate` 只卡 `committed_missing`，
   不卡条数。
2. **软建议 ≥3 张**：预检仍会提示「真值图数 < 下限 3」——这是**推荐阈值**（建议补到 3~5 张再宣布
   「已充分验证」），**不是硬门禁**，≥1 张即绿。
3. **hint↔真值矛盾守卫**：若先用具体 style（如 `corner_logo`）登记、却尚未标注，预检会报
   「清单声明与真值矛盾」。推荐首次登记用 `--style unknown`，标注后再定稿，避免提前误报。
4. **`auto:*` 标注被硬拒**：`build_real_ground_truth.py auto` 预填的 bbox 带 `auto:<preset>` 前缀，
   属检测器自输出（循环论证），门禁会硬失败；必须改为 `manual:measured` 并人工核 bbox 才算数。
5. **收件箱查重**：`real_asset_preflight.py` 扫描 `inbox/` 并 sha256 查重（与已收编 30 张 + 内部自重复）。

## 实测命令序列（可复现）

```bash
# 1) 放图到 inbox/ 后登记（先 unknown 避矛盾）
python scripts/collect_real_assets.py add <path> --style unknown --copy

# 2) 编辑 ground_truth.json，新增一条（务必 manual:*）
#    { "image_id": "<上一步 id>", "watermarks":[{"bbox":[x,y,w,h],"style":"corner_logo"}],
#      "labeled_by":"manual:measured" }

# 3) 预检转绿
python scripts/real_asset_preflight.py        # exit 0, committed_missing=[]

# 4)（可选）补到 ≥3 张后刷新真实指标
python scripts/real_gate.py --update
python scripts/real_gate.py --strict-scope --presets general,ai_logo
```

## 产物

- 重写 `benchmark_real/inbox/README.md`：实测操作手册（5 步收编 + 收/不收规格 + 阶段背景）。
- 本次验证脚本与临时基准位于隔离目录
  `C:/Users/HP/.workbuddy/binaries/python/envs/default/proof_inbox/`（项目外、非 Temp，可随时删除）。

## 未改动

- 未修改项目核心代码 / 测试文件；仅文档变更（`inbox/README.md` + 本文件），无回归风险。
- 阶段 148/150 遗留项不变：① 历史产物清理（`exports/451`、`outputs/97`、`cache/11074`）待你确认；
  ② 真实水印图覆盖——你提供 3~5 张/类真实 AI 带水印图走 inbox 收编即可闭合各红灯。
