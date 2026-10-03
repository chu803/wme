# 阶段 157 · `build_real_ground_truth.cmd_auto` 写真值契约守卫

## 动机
`scripts/build_real_ground_truth.py` 三个子命令里，`list`/`validate` 只读或复用
`structural_problems`，唯有 **`cmd_auto` 是真正「写入」`benchmark_real/ground_truth.json`
的入口**（它把检测器输出落成真值候选，供用户再微调）。而 `ground_truth.json` 正是
阶段 149 发现被**模板化伪造**、阶段 150 已清空为 `entries:[]` 的那份真值文件 ——
写入口的任何静默失真，都会直接污染整个真实基准的可信度。

`audit_selector` 只扫 `ui/services/core/utils` 与顶层文件，**不覆盖 `scripts/`**；
`scripts/` 的审计是「按子命令/入口逐点刻意选点」。Stage 152 在 `collect_real_assets.cmd_add`
抓到过 P0 静默数据损坏，本阶段顺延审查另一个写入口 `cmd_auto`（其 `validate` 已在
阶段 153 审过、`auto` 零测试守护）。

## 探针（合成检测器输出直灌 `cmd_auto`，隔离 tmp 目录）
不动真实数据集、不跑真实检测器，用 `unittest.mock.patch` 替换
`core.detector.detect_watermark_mask`，喂构造区域，观察写入 `ground_truth.json` 的内容：

| 喂入 | 修复前行为 | 是否真值损坏 |
|---|---|---|
| 0 个区域 | 仍写 `{"watermarks": []}` 空条目 | ✗ `audit_truth` 判「空标注会被读成没水印」；且污染 `have_ids` 阻断后续自动重标、`cmd_list` 算成已标注 |
| 区域 `x+w > W`（越界） | 原样写 `[x,y,w,h]` | ✗ `audit_truth`「bbox 在图内」判据判错，但 `auto` 打印「预填完成」 |
| `w=0` / `h=0` / `x<0` / `y<0` | 原样写 | ✗ 同上，非正 bbox 判错 |
| 一个越界 + 一个有效 | 两者都写（含无效） | ✗ 无效 bbox 混进真值 |
| 已有真值（image_id 在 have_ids） | 跳过 | ✓ 正确 |
| 缺 id / 文件缺失 | 跳过 / failed | ✓ 正确 |

→ 抓到 **两类静默真值损坏**：①空区域仍写空条目；②越界/非正 bbox 原样写入。

## 抓到（P1 · 写入口未就地校验）
`cmd_auto` 把 `reg.x/y/w/h` 直接 `int()` 后拼成 bbox 落盘，**全程不校验这些候选能否
经得起 `audit_truth` 校验**（阶段 153 已把「bbox 非负 / 在图内」上收到 `audit_truth`，
但 `cmd_auto` 这侧不调、也不自校验）。这符合本项目反复出现的「打印契约 ≠ 强制契约」
（经验 186）：`auto` 打印「候选 bbox=N」给用户看，却没有把同一套合法性**强制**在持久化之前。

## 修法（写前就地校验，无效跳过）
```python
H, W = arr.shape[0], arr.shape[1]
watermarks = []
for reg in res.regions:
    x, y, w, h = int(reg.x), int(reg.y), int(reg.w), int(reg.h)
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > W or y + h > H:
        print(f"[skip] {iid} 候选 bbox 越界/非正 ({x},{y},{w},{h})，跳过", file=sys.stderr)
        continue
    style = reg.kind if reg.kind in STYLE_ENUM else "unknown"
    watermarks.append({"bbox": [x, y, w, h], "style": style, "note": ""})
if not watermarks:
    print(f"[auto] {iid}  无有效候选 bbox（跳过，不写空标注）")
    continue
truth["entries"].append({...})   # 仅当确有有效候选才落盘
added += 1
```
- 越界 / 非正 bbox：跳过（不写入会令 `validate` 失败的条目）。
- 一张图任何有效候选都没有：**不写空条目**（直接 `continue`），不污染 `have_ids`、不误导 `cmd_list`。
- `auto:` 来源标记不变 → 仍被 `structural_problems` 判为「循环论证、不可当承诺真值」，这是 by design。

## 守卫
新增 `tests/test_build_real_ground_truth_auto.py`（8 例），不依赖真实检测器、用独立 tmp 目录
隔离 `BENCH_DIR/MANIFEST_PATH/TRUTH_PATH/IMAGES_DIR`，绝不碰真实数据集：
1. `test_auto_writes_valid_bbox` —— 有效区域 → 写出、bbox/style/labeled_by 正确；
2. `test_auto_skips_already_labeled` —— 已有真值不覆盖；
3. `test_auto_empty_regions_no_entry` —— 0 区域 → **不写空条目**（缺陷修复点）；
4. `test_auto_skips_out_of_bounds_bbox` —— 越界 → 跳过；
5. `test_auto_skips_nonpositive_bbox` —— w=0/h=0/x<0/y<0 四种 → 跳过；
6. `test_auto_keeps_valid_when_mixed` —— 越界+有效混合 → 只留有效的；
7. `test_auto_handles_missing_file` —— 文件缺失 → failed、不写；
8. `test_auto_skips_missing_id` —— 缺 id → 跳过。

## 注入式反例（纯 Python 直调，避 pytest 沙箱）
沙箱拦截 `%TEMP%` 批量删除（pytest 清理 73 文件 > 50 阈值，用户已拒），故改用纯 Python 直调
`importlib.util.spec_from_file_location` 加载测试模块、逐个跑 `test_*`，不触发任何删除提示。
- 移除写前校验块（还原为「直接 `int()` 拼 bbox 并 `append`」）→ 守卫 **4 例红**
  （empty_regions / out_of_bounds / nonpositive / mixed 全失败），证明守卫有牙；
- 还原字节级 sha `6af23c9456fb662feabbbb1e078e6e7110399f2b015504a5e50e6d9e6241ac02`
  逐字节一致 → 8/8 绿。

## 回归
新 8 例全绿（纯 Python 直调验证；pytest 因沙箱拦截 `%TEMP%` 清理未在此会话跑，门禁脚本
`quality_gate/real_gate/run_real_benchmark` 等未被触碰，回归风险限于本文件）。
`cmd_validate`/`cmd_list` 逻辑未改，阶段 153 的 `structural_problems` 仍是唯一判据。

## 诚实边界
- 本修复**只强化 `cmd_auto` 的写入口校验**，不改变检测器行为；检测器若本就输出合法区域，
  行为不变。`auto:` 条目依旧被 `structural_problems` 判为循环论证（不可当承诺真值），这是设计如此。
- 修复后 `auto` 写出的条目若含真实有效候选，**仍需人工复核**——本守卫只保证「不至于写出
  结构性非法 / 空标注」，不保证标注语义正确。
- 沙箱限制：本会话 pytest 子进程被用户拒（`%TEMP%` 清理），守卫以纯 Python 直调等价验证；
  回归完整性以 CI 的 `pytest` 为准。

## 产物
- `scripts/build_real_ground_truth.py`（`cmd_auto` 写前校验）
- `tests/test_build_real_ground_truth_auto.py`（8 例守卫）
- `docs/157-cmd_auto写真值契约守卫.md`
