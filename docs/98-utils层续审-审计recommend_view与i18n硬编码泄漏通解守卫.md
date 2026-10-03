# 阶段 98 · `utils/recommend_view` 审计（i18n 硬编码泄漏通解守卫 + 分值钳制 + SSOT 漂移）

> 阶段 96 审 `ui/tab_auto`、阶段 97 审 `ui/tab_batch` 之后，`audit_selector.py` 实时排名
> **未审计模块第一荒漠** 换成 `utils/recommend_view`（152 行 ÷ 8 引用 = **19.0**）。本阶段审它。

---

## §0 结论先行

`utils/recommend_view` 245 行，是「推荐理由 → 人类可读文案」的**唯一渲染层**，被
`ui/tab_auto`（智能推荐按钮）、`ui/tab_batch`（批量日志/画廊）、`ui/tab_media`、
`services/report_service`（HTML/PDF 报告）四处共用。

抓到 **1 个真缺陷 + 1 个一致性缺陷 + 1 个边界**：

| 编号 | 类型 | 一句话 | 后果 |
|---|---|---|---|
| **D1** | **真缺陷（可达）** | 渲染器内嵌硬编码中文：`、`（分隔符）、`：`（行标点）、`（无）`（空态）、`无推荐结果`（exc） | **英文界面必漏中文**；违反模块 docstring「翻译统一走 i18n，绝不暴露裸键」 |
| **D2** | 一致性缺陷（低危） | `bars_html` 不钳制分值，`_char_bar` 钳制 | 上游出现 >1 分 ⇒ `style="width:150%"` 溢出容器 |
| **D3** | 边界（漂移风险） | `_FEATURE_KEYS` 是 `core.preset_recommender` 的 `scores` 键硬编码副本（当前一致） | 推荐器改键 ⇒ 展示层静默漏特征 |

**这一阶段最有价值的产出不是 D1 的四处替换，而是把「i18n 硬编码泄漏」这类缺陷变成机器可判定的通解守卫**（I4）：`utils/` 模块函数体的字符串字面量一律不得含中文，docstring 与 `__main__` 自检除外。同类缺陷下一次会在别的模块被写下，但不会再有第二次机会逃过 CI。

---

## §1 选点：为什么是 `utils/recommend_view`

**选点时刻**（审计动作发生之前）：

```
python scripts/audit_selector.py --top 5
    19.5 | ui/tab_batch          （阶段 97 已审）
    19.0 | utils/recommend_view  ← 本阶段：当时未审计模块中的第一名
    18.9 | services/queue_worker
    18.7 | ui/tab_models         （阶段 94 已审）
    17.1 | core/model_manager    （阶段 89 已审）
```

**审计完成后的实时排名**（同一条命令，同一份代码）：

```
python scripts/audit_selector.py --top 8
    19.9 | ui/tab_auto           | 718 | 36 | 4   ← 阶段 96 已审，但榜单不区分
    18.9 | services/queue_worker | 264 | 14 | 1   ← 未审计中的第一名
    18.7 | ui/tab_models         | 281 | 15 | 2
    17.4 | ui/tab_batch          | 644 | 37 | 4
    14.0 | core/preset_recommender| 377 | 27 | 3
    13.7 | ui/tab_single         | 437 | 32 | 3
    13.4 | core/mask_editor      | 174 | 13 | 1
    12.8 | ui/tab_media          | 702 | 55 | 5
```

`utils/recommend_view` **已从榜单消失**：`--module` 查得 `代码行=163 / 引用用例=18 / ratio=9.1`。

> **观察者效应（阶段 94 的老问题换了张脸）**：本阶段新增的 13 个审计用例**自己就成了这个模块的引用**，
> 分母从 8 涨到 18，比值从 19.0 掉到 9.1。**「审完即掉榜」是这套量具的固有性质，不是改好了**。
> 所以引用荒漠榜时必须说清「这是审计前还是审计后的数」——否则会得出「一次审计让覆盖率翻了倍」的假结论。

> **另一个边界（本阶段新登记）**：榜单**不区分已审 / 未审**。`KNOWN_AUDITED` 只用于口径自检
> （断言登记过的模块引用数 > 0），不参与排序过滤。所以阶段 96 的 `ui/tab_auto`（19.9）依旧排第一，
> 尽管它刚被审过。**按「未审计」筛出的真正下一站是 `services/queue_worker`（264 / 14 = 18.9）**
> ——它与阶段 89 审过的 `core/model_manager` 是**同一个怪现象**：行数不多、引用不少，
> 但因为落在并发/资源管理这类「难以穷举分支」的代码上，比值反而不低。见 §7。

选它的第二个理由：它是 **utils 层唯一的「文案生成」模块**，而阶段 91 刚把品牌色 / 标签映射的「人读副本」问题定性为跨层通病（经验 51）。这里正好是同一家族的下一个受害者。

---

## §2 探针量化：三个发现

### D1 · i18n 硬编码泄漏（真缺陷，可达）

模块 docstring 第 20~21 行明确写着：

> 翻译统一走 i18n（`detect.preset.<name>.title` / `detect.recommend.*`），键值缺失时回退到原始名，**绝不暴露裸键**。

但实现里有 **4 处**绕过 i18n 的中文：

| 位置 | 原代码 | 泄漏物 |
|---|---|---|
| `_feats_text` L101 | `return "、".join(...)` | 中文顿号分隔符 |
| `markdown` L177 | `t("detect.recommend.msg.fail", exc="无推荐结果")` | 中文文案当 **参数** 传入（`t()` 只管模板，不管入参） |
| `markdown` L199 | `f"  - {feature_label(k)}：{_char_bar(v)} **{v:.2f}**"` | 中文全角冒号 |
| `markdown` L202 | `lines.append("  - （无）")` | 中文括号空态 |

**可达性验证**（不是「理论可能」）：`core.preset_recommender.recommend_preset` 在
`config.RECOMMEND_ENABLED=False` 时直接 `return Recommendation(..., scores={}, ...)`
（L364-371），此时 `markdown()` 的 `shown` 恒为 `False` ⇒ **`（无）` 分支必被走到**。
而 `msg.fail` 的 `exc` 在所有 `markdown(None)` / 空 preset 调用里都会被渲染。

> 关键区分：`t()` 在缺键时返回 **key 本身**（降级，不崩），所以「缺键」不是崩溃级问题。
> D1 的性质不同——**它是「有键不用，写死中文」**，比缺键更隐蔽：中文界面下完全看不出问题，
> 只有切到 `en-US` 才暴露。所以本阶段第一件事是把「英文界面」变成断言对象，而不是靠肉眼审。

### D2 · 两条分数条钳制口径不一致（一致性，低危）

```python
# _char_bar（markdown 用）—— 钳制
fill = max(0.0, min(1.0, float(fill)))

# bars_html —— 不钳制
w = int(round(v * 100))
```

`core.preset_recommender` 的 8 个分值里，`grad_act`（`explained`）、`period_strength`、
`regularity` 靠子函数自我钳制，其余走 `min(1.0, …)`。当前实现下 **没有 >1 的分值**，
所以 D2 是「当前不可触发、但两条通路已经分叉」的缺陷——**任一侧未来改了钳制规则，另一侧不会跟着改**。

### D3 · SSOT 漂移（边界）

```python
# core.preset_recommender.py L422-431
scores = {"gradient": ..., "grid": ..., "tiled": ..., "translucent": ...,
          "solid_logo": ..., "text": ..., "signature": ..., "regularity": ...}

# utils/recommend_view.py L45-48（硬编码副本）
_FEATURE_KEYS = ("gradient", "grid", "tiled", "translucent",
                 "solid_logo", "text", "signature", "regularity")
```

当前 **同名同序**，但没有任何东西保证它继续一致。**为什么不直接 import**：`utils/` 层
按分层依赖不得依赖 `core/`（模块 docstring 第 18~19 行：「只依赖 utils.i18n，不引入 gradio /
torch / 任何界面库」），`core.preset_recommender` 间接牵扯 torch/opencv，import 会破坏分层。
所以守卫只能放 **测试侧**（测试可以自由 import 两侧）。

---

## §3 修复

### D1 修复 · i18n 补 4 键（中英双全）+ 替换 4 处硬编码

`utils/i18n.py` **zh-CN**（L127 / L132-134）与 **en-US**（L859 / L864-866）各补：

```python
"msg": {..., "no_result": "无推荐结果" / "no recommendation result"},
"list_sep":   "、"  / ", ",
"score_line": "  - {name}：{bar} **{val}**"  / "  - {name}: {bar} **{val}**",
"scores_none": "（无）" / "(none)",
```

`utils/recommend_view.py`：

```python
# L106  _feats_text
return t("detect.recommend.list_sep").join(feature_label(k) for k in triggered)

# L185-186  markdown 空态
return t("detect.recommend.msg.fail", exc=t("detect.recommend.msg.no_result"))

# L209-210  markdown 分数行
lines.append(t("detect.recommend.score_line",
               name=feature_label(k), bar=_char_bar(v), val=f"{v:.2f}"))

# L213  markdown 全零空态
lines.append("  - " + t("detect.recommend.scores_none"))
```

**为什么用 `score_line` 整行模板而不是只把 `：` 换成变量**：英文的标点约定不只是冒号不同
（`：`→`: `），还牵涉后随空格；把整行的「标点骨架」交给译文，等于把排版决定权交给语言。
`{bar}` 里是 `█`/`░`，`{val}` 是 `0.71` 这样的纯数字，都不含 `{`/`}`，不会撞 `.format`。

**i18n 自检同步登记**：`utils/i18n.py` 的推荐键守卫列表（L1701-1703）补进 4 个新键，
并在 `en-US` 段（L1733-1739）加「英文侧同样必须有」的反向断言——这是阶段 95
「承诺面派生而非副本」思路在 i18n 层的复用：**新增键只加一边**会立即红。

### D2 修复 · 条宽与 `_char_bar` 对齐

```python
# utils/recommend_view.py L150-152
# 阶段 98：宽度必须钳制到 [0,1]，与 _char_bar 保持一致；否则上游出现 >1
# 的分值时 style="width:150%" 会溢出容器。展示值仍用原始 v（与 markdown 一致）。
w = int(round(max(0.0, min(1.0, v)) * 100))
```

刻意 **只钳宽度、不钳展示值**——`markdown` 的 `**{v:.2f}**` 显示的是原始分，
两侧口径必须继续一致（否则又制造出新的分叉）。

### D3 处置 · 加漂移守卫而非改依赖方向

新增守卫 I3（见 §4），用 `ast` 从 `core/preset_recommender.py` **源码** 提取 `scores`
字典字面量的键，与 `_FEATURE_KEYS` 断言**同名同序**。

用 ast 而非「调 `recommend_preset()` 读返回值」的原因：`RECOMMEND_ENABLED=False` 时返回值
`scores={}`，守卫会**假绿**。读源码字面量则与运行期开关完全解耦。

---

## §4 验证

### 新增 `tests/test_utils_audit98.py`（13 例）

| 编号 | 用例 | 性质 |
|---|---|---|
| **I1** | `test_i1_en_renderers_have_no_cjk` | **注入式反例**：`en-US` 下 4 种输入（正常 / 全零 / 多特征 / None）× 3 个渲染器，输出一律不得含 CJK 字符 |
| I1 | `test_i1_zh_still_chinese_and_separators_localized` | **反向守卫**：`zh-CN` 侧仍必须是中文（防「把中文改成英文常量」式假修复）；并断言分隔符双语各自生效、`en-US` 下不残留 `、` |
| I1 | `test_i1_referenced_keys_exist_in_both_languages` | 从 `recommend_view.py` 源码 ast 提取**它真正引用到的** `detect.recommend.*` 常量键（+ f-string 拼出的 `feature.*`），逐键断言中英双全 |
| **I2** | `test_i2_bars_html_width_is_clamped` | **注入反例**：分值 `1.8` ⇒ 必须 `width:100%`，且不得出现 `width:180%` |
| I2 | `test_i2_bars_html_display_value_is_raw` | 展示值仍为 `1.80`（钳制只作用于条宽） |
| I2 | `test_i2_char_bar_and_bars_html_same_clamp_rule` | 两条分数条对同一超界输入的钳制结果必须一致（防再分叉） |
| **I3** | `test_i3_feature_keys_match_recommender_scores` | **SSOT 漂移守卫**：ast 提取推荐器 `scores` 键，与 `_FEATURE_KEYS` 断言同名同序；并要求恰好找到 1 处 `scores` 字面量（否则守卫本身失效） |
| I3 | `test_i3_every_score_key_has_i18n_label` | 每个展示键都必须有中英特征文案 |
| **I4** | `test_i4_no_cjk_literals_in_module_body` | **通解守卫**：`recommend_view.py` 函数体的字符串字面量不得含中文（ast 剔除 docstring 与 `__main__` 子树的全部节点） |
| I4 | `test_i4_guard_itself_detects_a_planted_leak` | **守卫自检**：向临时文件植入 `无推荐结果`，判据必须抓出来——否则守卫是摆设 |

### 注入式反例 4/4 全部捕获（实测）

| 注入动作 | 命中用例 | 结果 |
|---|---|---|
| 把 `list_sep` 改回硬编码 `"、"` 且 `en-US` 输出该串 | I1（CJK 扫描） | ✅ 红 |
| `bars_html` 去掉 `max/min` 钳制 | I2（`width:180%`） | ✅ 红 |
| 推荐器 `scores` 增删一个键 | I3（同名同序断言） | ✅ 红 |
| 在函数体写一句中文文案 | I4（ast 字面量扫描） | ✅ 红（并由 I4 自检用例证明判据有效） |

### 门禁与全量回归

| 项目 | 结果 |
|---|---|
| `quality_gate.py` | **EXIT=0**；误报 0.78% · P 51.8% · R 81.9% · IoU 49.7% · ΔPSNR +12.49 dB · 交付链 +11.31 dB（掩码区最差 −0.01 dB）——**与阶段 86/93/95/96/97 逐项一致，零回归** |
| `real_gate.py --strict-scope` | **EXIT=0**；general P 0.0385 / R 1.0000 / IoU 0.7573 / mask IoU 0.0635 / coverage 0.1618 / 16.62× |
| `utils/recommend_view.py` 自检 | 5 用例通过（新增「用例5：英文侧无中文残留」） |
| `utils/i18n.py` 自检 | 通过 |
| 全量 pytest | **1325 例 / 90 文件**零失败（1312 → +13） |

---

## §5 新增经验（72~74）

**经验 72 · i18n 泄漏 ≠ 缺键，它的判据是「英文界面」而不是「键表」**
`t()` 缺键时返回 key 本身（降级不崩），所以「键表完整性」检查抓不到「有键不用」——
本模块 4 处硬编码中文，在 zh-CN 下 **100% 正常**。唯一有效的判据是把输出**真的切到 `en-US`
再扫 CJK 码点**。凡「文案生成层」，其回归测试必须包含一次语言切换，否则测的是中文界面的中文。

**经验 73 · 同一可视化语义的两条实现，钳制口径必须写进断言**
`_char_bar` 钳、`bars_html` 不钳——当前无可触发差异（上游无 >1 分），所以肉眼审、甚至
跑全量测试都发现不了。这类「**潜伏分叉**」的判据不是「现在对不对」，而是「两条通路对同一
极端输入的输出是否一致」。修复时也只钳**该钳的那一半**（宽度），保留另一半（展示值）
与 markdown 一致——**不然修一个分叉会造出另一个**。

**经验 74 · 硬编码文案的通解守卫 = AST 扫字面量，而不是 grep 关键词**
grep 中文会被注释与 docstring 淹没（本模块注释里就有大量中文）；正确做法是
`ast.parse` 后取 `ast.Constant` 中的 `str`，**剔除 docstring 节点**与 `__main__` 子树，
再扫 CJK 码点。这样守卫对**尚未出现的第 5 处、第 50 处**同样有效——grep 只能守住你
已经想到的那几个词。并且守卫自己要有「植入一个泄漏必须变红」的自检用例（I4 第二条），
否则它可能因为 `skip` 集合写错而永久绿。

---

## §6 产出文件

| 文件 | 变更 |
|---|---|
| `utils/recommend_view.py` | D1 四处去硬编码 + D2 钳制 + docstring/版本 1.0.0→1.1.0 + 自检新增用例5 |
| `utils/i18n.py` | zh/en 各补 4 键；自检守卫登记（zh + en 双向断言） |
| `tests/test_utils_audit98.py` | 新增，13 例（I1 6 / I2 3 / I3 2 / I4 2） |
| `tests/test_recommend_view.py` | L76 断言从钉死中文字面量改为取当前语言译文 |
| `docs/98-utils层续审-审计recommend_view与i18n硬编码泄漏通解守卫.md` | 本文 |
| `README.md` §2.46 + §4 | 阶段条目 + 规模计数（1325 例 / 90 文件 / 101 份文档） |
| `TODO.md` P1.46 + 荒漠榜 | 阶段记录 + 下一荒漠换为 `services/queue_worker` |
| `.workbuddy/memory/` | `STAGES.md` / `LESSONS.md`(72~74) / `MEMORY.md` / `2026-09-18.md` |

---

## §7 边界与剩余

1. **`ui/` 层仍有两个大模块未审**：`ui/tab_media`（1008 行 / 已审 16 引用）与
   `ui/tab_settings`。前者行数最大、连通 PDF/视频两条媒体链路，是下一个「发现密度」
   可能同样高的区域。
2. **`services/` 仍有 9 个模块未单独审计**（13 个中只审 4 个），
   `services/queue_worker`（264 / 14 = 18.9）已是实时排名第二，且位于批量任务
   并发链路的关键位置。
3. **I4 通解守卫目前只作用于 `utils/recommend_view.py`**。本轮刻意不扩散到整个
   `utils/`——但「函数体不得内嵌中文文案」这条判据对 `utils/` 全部文案模块
   （如 `recommend_view` 的兄弟）都成立，建议下一轮把它提升为 `utils/` 目录级守卫
   （需先清点现存中文文案，避免一次引入大量红灯）。
4. **`_FEATURE_KEYS` 的 SSOT 守卫落在测试侧**是分层依赖下的最优解；若未来
   `core/` 与 `utils/` 的依赖方向松动，可考虑把键表下沉到一个不牵扯 torch 的
   `core/_preset_keys.py` 常量模块，由两侧共同 import。**本阶段不动**——为了一个
   当前一致的副本去改分层结构，收益为负。
5. **D2 的上游钳制仍不统一**：`core.preset_recommender` 里
   `grad_act` / `period_strength` / `regularity` 靠子函数自我钳制，其余走 `min(1.0, …)`。
   本阶段只在**展示层**加了护栏（最后一道防线），**没有改推荐器**——理由是它属检测侧
   核心链路，任何改动都必须同时跑两条门禁；而展示层钳制已足以消除溢出症状。
   登记为观察项。
