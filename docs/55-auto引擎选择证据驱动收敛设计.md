# 阶段 55 设计文档：修复引擎 `auto` 选择的证据驱动收敛

> ## ⚠️ 阶段 A 更正声明（2026-09-13）——本文档的**证据**已被证伪，但**结论**仍然保留
>
> 本文档据以决策的核心证据是「阶段 51 证明 LaMa 在合成数据上多数用例起负作用」。
> 该证据**已失效**：阶段 51 测的是一个**值域归一化错误**的 LaMa（掩码区饱和成白板，
> 详见 `docs/A3-LaMa与OpenCV真实图对决与决策.md`）。修复后同脚本重跑：
> **LaMa 3 / OpenCV 4 / 持平 1，全部差距 ≤4.3 dB**。
>
> **结论（默认 `opencv`）予以保留，但依据已更换**：不再是「LaMa 质量更差」，
> 而是**延迟（LaMa CPU 1024² 单图约 6s vs OpenCV 即时）与保守性
> （OpenCV 是非生成式的局部扩散，不会"脑补"出画面里没有的内容；
> 25#1 薰衣草图上 LaMa 凭空造出一条橙红色带即为典型幻觉风险）**。
> `_resolve_auto_engine()` 的 docstring 已同步改写。
>
> 附带说明：阶段 55 的收敛动作在工程上**是正确的**（把"有模型就无脑用 LaMa"的静默劣化消掉），
> 只是当时给出的理由恰好错了 —— 属于「结论对、证据错」的幸运情形，不能作为方法论范例。

> 关联代码：`core/inpainter.py`（`_resolve_auto_engine` / `inpaint` 的 `auto` 分支）、`config.py`（`INPAINT_AUTO_PREFER`）
> 关联测试：`tests/test_auto_engine.py`
> 证据来源：`scripts/run_benchmark.py` + `docs/51-LaMa与OpenCV公平对比设计.md`（阶段 51，合成数据）
> 前置阶段：阶段 51（LaMa vs OpenCV 公平对比）、阶段 49–54（检测器质量收敛）

---

## 1. 问题回顾

`config.INPAINT_ENGINE` 默认值就是 `"auto"`。旧逻辑（`core/inpainter.py`）：

```python
if engine == "auto":
    if is_model_available():
        engine = "lama"        # 只要有模型就无脑选 LaMa
    else:
        engine = "opencv"
```

而本机**已装 LaMa 权重**（torch 2.9.1+cpu + big-lama.pt，阶段 50 攻克）。这意味着：
**每一次默认修复都在用 LaMa**——但阶段 51 的公平 benchmark 已证明，在合成数据上
LaMa 在多数用例**起负作用**（ΔPSNR 低至 −25.19 dB，因大面积/渐变区域产生幻觉纹理），
OpenCV 在 8/8 用例占优。旧的 `auto` 逻辑等于"默认静默选更差的引擎"，影响面 100%。

---

## 2. 根因

`auto` 的偏好是**"有模型就上 LaMa"**，但没有任何证据支撑这一偏好——
阶段 51 的结论恰恰相反：合成数据上 OpenCV 全面占优、LaMa 常为负向。
偏好来源是"深度学习模型 = 效果更好"的直觉，而非实测。

---

## 3. 修复方案（收敛而非新增）

抽出纯函数 `_resolve_auto_engine(preferred, model_available) -> str`，逻辑：

- opencv **永远可用**（不需要模型），是大多数水印的稳妥选择、且不会幻觉；
- lama **仅在模型就绪时可用**，且在合成数据上多数用例为负向；
- 新增配置 `INPAINT_AUTO_PREFER`（阶段 55，默认 **"opencv"**）：
  - 默认偏好 opencv → 消除"有模型就无脑 LaMa"的静默劣化；
  - 设 `WME_INPAINT_AUTO_PREFER=lama` 可恢复旧行为（auto 有模型即走 LaMa）；
  - 也可**始终显式选 `engine="lama"`**（不受偏好影响）。
- 偏好 lama 但模型缺失 → 回退 opencv（保证总能修）。

`inpaint` 的 `auto` 分支改为 `engine = _resolve_auto_engine(config.INPAINT_AUTO_PREFER, is_model_available())`。

---

## 4. 为什么不直接做"双跑择优"

一个更"智能"的方案是：auto 时双跑 OpenCV 与 LaMa，用无参考质量指标（如掩码内/外纹理一致性）
择优。但这有两个风险，故阶段 55 **不做**、留待 P0 真实图验证后：

1. 无参考 inpainting 质量指标本身不可靠——在真实纹理背景上，LaMa 保留纹理可能是**对的**，
   而"惩罚高内部能量"的指标会误判其为幻觉，反而把对的选掉；
2. 双跑使每次修复耗时翻倍（LaMa CPU 单图 3~4s），且尚无真实图 ground truth 验证指标有效性。

阶段 55 采用**证据驱动的最保守正确解**：默认用 benchmark 证明更好的 OpenCV，
把 LaMa 留在显式 opt-in（同时也方便用户拿真实图做 P0 对比，验证 LaMa 是否真有优势）。

---

## 5. 回归护栏（`tests/test_auto_engine.py`，4 例全过）

1. `test_resolve_auto_engine_logic`：纯函数 4 组合（默认+模型/缺失、偏好 lama+模型/缺失、
   未知偏好）→ 行为正确。
2. `test_auto_uses_opencv_when_model_present`：集成，本机（模型就绪）上 `auto` 实际走 opencv
   且修复成功（直接证明静默劣化已消除）。
3. `test_auto_honors_lama_preference_when_model_present`：偏好 lama + 模型就绪时，auto 经
   LaMa 路径修复成功（skip 若模型缺失），验证 opt-in 接线正确。
4. `test_auto_falls_back_when_lama_preferred_but_model_missing`：monkeypatch 模型缺失，
   偏好 lama 仍回退 opencv 且修复成功。

---

## 6. 影响与边界

- 默认修复（auto）现在走 OpenCV：更快（纯 CPU、无模型推理）、不会幻觉、且 benchmark 证明更优。
- LaMa 仍完全可用：显式 `engine="lama"` 或在 `INPAINT_AUTO_PREFER=lama` 下经 auto 启用。
- 本修复只改引擎**选择**逻辑，未动任何修复算法本身；检测器（阶段 49–54）不受影响。
- **仍卡用户输入项（P0）**：3~5 张真实带水印 AI 图，跑 `--input-dir` 对比，才能定论
  LaMa 在真实照片上是否真有 OpenCV 没有的优势——那将是"双跑择优"方案的验证前提。
