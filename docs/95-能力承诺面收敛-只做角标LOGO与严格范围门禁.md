# 阶段 95 · 能力承诺面收敛（「只处理角标 LOGO 类图片」）与严格范围门禁

> 阶段 92 提出的缺口 S1（**标注覆盖面沉默**）收尾。阶段 93 给出两条出路，
> 本阶段用户拍定走第 ② 条：**不补四类真实图，而是把能力声明收敛到已验证范围**。
> 本阶段的任务就是把这句口头决定变成**单一真值来源 + 可判定门禁 + 双向文案守卫**。

---

## §0 结论先行

| 项 | 阶段 93 末 | 阶段 95 末 |
|---|---|---|
| `scripts/real_gate.py --strict-scope` | **结构性红灯**（30 张图只声明 catch-all ⇒ 必然失败） | ✅ **EXIT=0**，首次可长期为绿 |
| 能力声明的"承诺" | README 把 5 类都列在"支持"表里，靠逐行"未验证"免责 | **承诺 1 类**（`ai_logo`），其余 4 类**显式不承诺** |
| 承诺面真值来源 | 无（散落在 README 散文里） | `config.WATERMARK_TYPE_COMMITTED`（由 SCOPE 表**派生**） |
| "承诺类型缺真值" | 无此判定（可以承诺无边） | **始终硬失败**（不受 `--strict-scope` 控制） |
| 界面适用范围提示 | **不存在** | 顶部提示，由 config 派生、随语言切换刷新 |
| 类型名"人读副本" | 无守卫 | **i18n 模板禁止出现任何字面类型名**（注入式守卫） |

**核心机制**：把"我们承诺处理哪些类型"从一句话，变成 `config.WATERMARK_TYPE_SCOPE[*]["committed"]`
上的**布尔字段**。于是三类问题第一次能被机器分开：

| 状态 | 含义 | 门禁处置 |
|---|---|---|
| 承诺类 **有**真值 | 正常 | 通过 |
| 承诺类 **无**真值 | **承诺比证据多** | **始终硬失败**（可执行：补图 或 收回承诺） |
| 非承诺类**无**真值 | 明示的能力边界 | **记录但绝不失败** |

---

## §1 为什么这不是"改文档"而是"改语义"

阶段 93 的 `complete` 判据是 `unspecified_images == 0`，即"清单里每张图都要有具体风格声明"。
这个判据在**补图路线**下是对的；但在**收敛路线**下会变成一个错的判据：

- 30 张图的帧内容客观上可能确实混有多种样式，清单写 `mixed` 是**诚实**的；
- 而承诺面已经收窄到"只做角标"，此时 `recall` 度量的**正好是被承诺的那一类**——
  未承诺类型的召回"未知"这件事，已经由"不承诺"承担了，不需要再用红灯表达；
- 若继续让 catch-all 压成红灯，`--strict-scope` 就永远无法为绿 ⇒ **红灯失去信息量**
  （这正是阶段 92 批评"门禁全绿 ≠ 产品可用"的镜像：**门禁恒红 = 门禁无用**）。

于是本阶段把 `unspecified_images` 从**缺口**降级为**提示精度诊断**，
并把 `complete` 的判据换成"**承诺类无缺证**"。

> **单一规则，不是两套实现**：旧格式 scope（没有 `committed` 字段）按**最严**理解——
> 没说哪些被承诺，就当作全部都被承诺。于是阶段 93 的口径在旧格式下**完全不变**
> （`tests/test_watermark_scope95.py::test_undeclared_commitment_keeps_conservative_strict_behavior`）。

### 被改写的两条阶段 93 断言（有意为之，已注明理由）

| 断言 | 原口径 | 新口径 |
|---|---|---|
| `test_scope_detects_catch_all_gap` | `complete is False` | `unspecified_images == 2` **仍被记录** + `complete is True` |
| `test_scope_treats_empty_and_unknown_as_catch_all` | `complete is False` | 同上 |

改写后的断言**保留了原意**（catch-all 必须被显式记录，不许静默丢弃），
只是把"记录"与"判缺口"两件事分开——判"是不是缺口"现在由承诺面决定。

---

## §2 单一真值来源：`config.WATERMARK_TYPE_SCOPE` 扩成四字段

```python
WATERMARK_TYPE_SCOPE = {
    "ai_faint_tiled": {"truth_styles": ("translucent", "overlay"),
                       "committed": False, "label_zh": "平铺文字", "label_en": "tiled text"},
    "ai_logo":        {"truth_styles": ("corner_logo", "logo_watermark"),
                       "committed": True,  "label_zh": "角标 LOGO", "label_en": "corner logo"},
    ...
}

# 承诺面 = 派生，刻意不另写一份（避免「同一语义两套实现」）
WATERMARK_TYPE_COMMITTED = frozenset(n for n, e in WATERMARK_TYPE_SCOPE.items()
                                     if e.get("committed"))
```

| 字段 | 唯一消费者 |
|---|---|
| `truth_styles` | 真值 style 名映射（阶段 93） |
| `committed` | 门禁承诺面判定 + UI/README 承诺级别 |
| `label_zh` / `label_en` | **UI 适用范围提示**（`ui/components.py:render_scope_note`） |

**新增配置自检（检查 17）**：承诺面为空 → `validate_config()` 报问题。
理由：`committed_missing` 用**空集合**遍历时循环体永不执行 ⇒ 判定无事可守却照常报绿
（"遍历式守卫空转"，经验 54）。这一层把结构性退化拦在配置自检里。

---

## §3 门禁三层判定（阶段 95 后的全貌）

```
0)  数据完整性        实际评估图数 == 标注图数
0b) 标注覆盖面 + 承诺面
      · 数据矛盾 / 枚举外 style   → 始终硬失败（标注错误）
      · 承诺类型缺真值证据        → 始终硬失败（承诺比证据多；可执行）
      · 清单只有 catch-all        → 只告警，--strict-scope 可升级
1)  绝对红线          general 预设 7 项（召回 / 精确率 / 区域IoU / 掩码IoU / 覆盖率 / 过检倍率）
2)  基线漂移          只约束"变坏"方向，方向从红线方向派生
```

### `annotation_scope()` 新增字段

| 字段 | 含义 |
|---|---|
| `committed` / `uncommitted` | 承诺面 / 明示不承诺面（由 config 派生） |
| `committed_labeled` / `committed_missing` | 承诺类中有 / 无真值 |
| `uncommitted_labeled` / `uncommitted_missing` | 非承诺类中有 / 无真值 |
| `complete` | 无矛盾 ∧ 无枚举外 ∧ **承诺类无缺证**（catch-all 不再参与） |
| `unspecified_images` | **降级为诊断项**：清单提示精度 |

`config` 的导入**刻意不做 try/except 兜底**：若承诺面取不到就退回"无承诺面"，
`committed_missing` 会变成空集、门禁对着空集合静默报绿。宁可抛 ImportError。

### 横幅（`render_scope_banner`）的出现规则

| 情形 | 是否出现 |
|---|---|
| scope 为空（无覆盖面信息） | ❌ 不出现（不凭空断言，也不凭空告警） |
| **承诺面已声明** | ✅ **无条件出现**（哪怕 `complete=True`） |
| 旧格式（无 `committed`）且完整 | ❌ 不出现（阶段 93 规则不变） |

"承诺面已声明即无条件出现"是本阶段的关键取舍：既然承诺了"只做角标"，
那么表格里那个 `recall = 1.0000` 就必须**永久带着**"仅代表承诺类型"这句话走，
否则把数字贴进文档的人会把它读成全类型结论——这正是阶段 93 建立横幅的初衷。

真实运行输出（现场实测）：

```
| 预设 | n | 精确率 | 召回 | 区域IoU | 掩码IoU | 掩码覆盖率 | 过检倍率 | 契约 |
|---|---|---|---|---|---|---|---|---|
| general | 30 | 0.0385 | 1.0000 | 0.7573 | 0.0635 | 0.1618 | 16.62× | 红线 |

⚠ 标注覆盖面 1 类：corner_logo
  · **承诺处理**：ai_logo（角标 LOGO）——缺真值证据将直接判失败（承诺面唯一真值来源 `config.WATERMARK_TYPE_COMMITTED`）
  · **明确不承诺**：ai_faint_tiled（平铺文字）、ai_gradient（全局渐变）、ai_grid（网格点阵）、ai_text_heavy（边缘小字）——预设仍可选用，但效果不作承诺，也不参与本门禁结论
  · 因此本次 recall/precision **仅代表上述承诺类型**；未承诺类型的真实指标是**未知**，不是 100%
  · 提示精度：清单中 30 张图只声明了 catch-all 风格（mixed/unknown）……但引用数字时须同时说明「仅上述承诺类型」

✅ 真实图质量门禁通过        （--strict-scope，EXIT=0）
```

---

## §4 界面侧：适用范围提示

新增 `ui/components.py:render_scope_note(lang)`，挂在 `ui/app.py` 顶部**紧挨合规声明**
（两者都是"用之前必须知道的事"），并注册进 i18n 运行时表以随语言切换刷新。

```
⚠️ **适用范围**：本工具**承诺处理** 角标 LOGO（`ai_logo`）；**明确不承诺**
平铺文字（`ai_faint_tiled`）、全局渐变（`ai_gradient`）、网格点阵（`ai_grid`）、
边缘小字（`ai_text_heavy`）等样式——后者对应预设仍可选用，但效果不保证，
也不计入质量门禁的结论。
```

**关键设计**：i18n 模板里**不写任何类型名**，只留两个占位符 `{types}` / `{others}`，
名字由 config 注入。这样"界面上承诺了哪些类型"永远等于 `config.WATERMARK_TYPE_COMMITTED`：
改 config 一处，界面自动跟着变。

> 这是经验 51「人读副本」的**通解**：副本可以存在，但必须有机器可判定的守卫把它钉回
> 单一真值来源。守卫 = `test_scope_note_template_has_no_literal_type_names`
> （模板不得出现任何已登记类型名）+ `test_scope_note_puts_each_type_on_the_right_side`
> （逐类型断言它只在正确的**那一段**出现——把类型写错一侧会被抓住）。

---

## §5 README 能力表改「承诺级别」两分

新表结构（`类型 | 预设名 | 说明 | 承诺级别 | 验证状态`）：

| 类型 | 预设名 | 承诺级别 | 验证状态 |
|---|---|---|---|
| 角落 LOGO | `ai_logo` | ✅ **承诺处理** | 合成用例 + **真实图 30 张（60 个标注）已验证** |
| 半透明平铺文字 | `ai_faint_tiled` | ⚪ 不承诺 | 合成用例已验证；真实图**未验证** |
| 底部/边缘小字 | `ai_text_heavy` | ⚪ 不承诺 | 合成用例已验证；真实图**未验证** |
| 网格点阵 | `ai_grid` | ⚪ 不承诺 | 合成用例已验证；真实图**未验证** |
| 全局渐变叠层 | `ai_gradient` | ⚪ 不承诺 | **未验证**：无合成用例、无真实标注 |

守卫（`tests/test_watermark_scope95.py`）：

1. 承诺的行必须写「承诺处理」**且不得出现"未验证"**（承诺却有未验证 = 承诺比证据多）；
2. 非承诺的行必须写「不承诺」；
3. 表中出现的预设名集合必须**恰好等于**登记表键集合（不多不少）；
4. 正文里"当前承诺 **N** 类"这个**数字**必须等于 `len(WATERMARK_TYPE_COMMITTED)`
   ——数字是最容易过期的"人读副本"，故让它是可判定的。

> 阶段 93 的 `test_readme_marks_unvalidated_types`（缺证据的行必须出现"未验证"）
> **保持原样不改**：新表里缺证据的行仍然写着"未验证"。两个守卫分工——
> 旧守卫管"必须有标记"，新守卫管"标记的词必须对"。

---

## §6 验证

### 6.1 新增测试 `tests/test_watermark_scope95.py`（27 例）

| 组 | 覆盖 |
|---|---|
| A 承诺面（5） | 承诺面 == {ai_logo}；派生而非副本；字段齐备且标签唯一；**空承诺面被 validate_config 拦住**；标签缺失回退预设名 |
| B 门禁语义（9） | 真实数据集承诺面；**`--strict-scope` 为绿（验收）**；承诺缺真值硬失败（含可执行措辞）；非承诺缺真值严格模式也通过；catch-all 不再挡严格模式；旧格式仍按最严；指标口径零变化 |
| C 横幅（5） | 承诺面声明后无条件出现；类型名与中文名由 config 派生；缺证时 ✖ 报出；两种"不出横幅"情形；`to_markdown` 数字与限定同框 |
| D 文案（6） | 模板无字面类型名；中/英各自含全部承诺类型；**逐类型左右侧分段断言**；标签改动文案跟着变；随语言切换 |
| D2 README（2） | 承诺级别列与承诺面双向一致；"当前承诺 N 类"数字一致 |
| E 界面接线（1） | 真构建一次 `build_app`：提示在场，且切英文后**载荷**里出现英文提示 |

### 6.2 注入式反例 **8/8 全部捕获**

| # | 注入 | 被哪条守卫抓住 |
|---|---|---|
| I1 | 承诺面改成手写字面集合 | `test_commitment_is_derived_from_scope_table` 等 |
| I2 | `complete` 退回旧口径（catch-all 仍算缺口） | `test_catch_all_hint_no_longer_blocks_strict` 等 |
| I3 | 摘掉「承诺类型缺真值」硬失败 | `test_committed_missing_is_hard_failure_without_strict` |
| I4 | 横幅在 `complete` 时提前返回 | `test_banner_always_renders_once_commitment_declared` |
| I5 | i18n 模板手抄类型名 | `test_scope_note_template_has_no_literal_type_names` |
| I6 | README 把 `ai_grid` 标成「承诺处理」 | `test_readme_commitment_column_matches_promise_face` |
| I7 | 把 `ai_logo` 的 `committed` 改 False | `test_committed_face_is_exactly_corner_logo` 等 |
| I8 | 文案把承诺/不承诺两侧写反 | `test_scope_note_puts_each_type_on_the_right_side` |

- 预检：涉及的 10 条守卫测试**注入前全绿**（否则"失败"可能是本来就坏）；
- 每个注入：外部备份 + sha256，还原后**逐字节比对**（自比无效）；
- 4 个被改文件（`config.py` / `scripts/real_gate.py` / `utils/i18n.py` / `README.md`）全部还原一致。

> 注入脚本自身出过一次错（I8 锚点漏掉行首 `⚠️ **适用范围**：`，导致"锚点未命中"）。
> 这正是经验 55 的场景：**注入未被捕获时，先怀疑注入本身**。
> 脚本对"锚点未命中"与"守卫未捕获"分别报错，就是为了不把这两件事混为一谈（经验 64）。

### 6.3 本阶段自己踩的三个坑（都是抓我，不是抓代码）

| # | 症状 | 真因 | 修法 |
|---|---|---|---|
| 1 | I8 报"未捕获" | 注入脚本锚点凭记忆写了行的后半段，**注入根本没写进文件** | 脚本区分「锚点未命中」与「守卫未捕获」（经验 64） |
| 2 | 文案位置断言失效 | i18n 模板里 `{others}` 排在"不做承诺"**之前**，而断言假设它在之后 | 文案改成「承诺处理 X；明确不承诺 Y」的显式分段结构，断言改为**切段后逐类型判侧** |
| 3 | 接线用例**顺序依赖**：单跑绿、与 `test_i18n.py` 同跑红 | i18n 语言是**进程级全局**（且持久化到磁盘），上个文件把它留在 `en-US` | 用例开头显式 `set_language + save_language` **自己建场景**（经验 65："快照/还原"只防污染别人，还得不依赖别人） |

### 6.4 回归与门禁（全部现场实测）

| 项 | 结果 |
|---|---|
| 全量测试 | **1304 例 / 87 文件，0 失败**（7 例环境前提 skip） |
| 合成门禁 `scripts/quality_gate.py` | **EXIT=0**，零回归 |
| 真实门禁 `scripts/real_gate.py` | **EXIT=0**，指标与阶段 86/93 完全一致（P 0.0385 / R 1.0000 / IoU 0.7573 / mask 0.1618 / 16.62×） |
| 真实门禁 `--strict-scope` | **EXIT=0** ← 本阶段验收指标 |
| 选点工具 `scripts/audit_selector.py --check` | **EXIT=0** |

---

## §7 登记不改（本轮确认过的边界）

1. **`benchmark_real/manifest.json` 不改**：30 张图的 `watermark_style_hint` 仍是 `mixed`。
   用户说的是"**只承诺**角标"，这是**产品承诺面**的声明，不是"这 30 张图只含角标"的
   **数据集事实**——把 hint 改成 `corner_logo` 等于凭空断言。故保留 `mixed`，
   由横幅与 `unspecified_images` 把它作为"提示精度诊断"显式记录。
2. **`WATERMARK_TYPE_COMMITTED` 是导入期派生常量**：运行时改 `WATERMARK_TYPE_SCOPE`
   不会让它跟着变。当前无人做这件事（该表不在 `user_overrides.json` 的 11 键白名单内），
   故不引入动态派生（那会带来"读的时候才变"的新不确定性）。**若日后要支持运行时改承诺面，
   必须同时改这里的派生方式并补测试。**
3. **检测侧默认预设不变**：`auto → opencv`、`general` 预设语义与全部红线**未动**。
   承诺面只影响"我们对外说什么"与"门禁怎么判"，**不影响算法行为**——
   非承诺类型的预设仍可正常选用。
4. **`ai_gradient` 仍是"无合成用例"的已知缺口**（`WATERMARK_TYPE_KNOWN_SYNTHETIC_GAP`）：
   本阶段只把它登记为"不承诺"，没有为它补用例。
5. **`test_readme_marks_unvalidated_types`（阶段 93）保持原样**：新旧守卫分工见 §5。

---

## §8 缺口台账（docs/92 的四个缺口）

| 缺口 | 状态 |
|---|---|
| **S1** 标注覆盖面沉默 | ✅ **闭环**（阶段 93 显式化 + 阶段 95 收敛承诺面；`--strict-scope` EXIT=0） |
| **S2** 过检面积无红线 | ✅ 闭环（阶段 93：三条面积红线 + 方向派生） |
| **S3** 端到端真实链路未验证 | ✅ 闭环（阶段 93） |
| **S4** 能力声明与门禁自述矛盾 | ✅ 闭环（阶段 93 建单一真值来源；阶段 95 升级为**承诺级别 + 双向守卫**） |

---

## §9 文件清单

| 文件 | 改动 |
|---|---|
| `config.py` | `WATERMARK_TYPE_SCOPE` 增 `committed` / `label_zh` / `label_en`；新增派生 `WATERMARK_TYPE_COMMITTED` 与 `watermark_type_label()`；`validate_config()` 增检查 17（承诺面非空）；附录 A 注释扩写 |
| `scripts/real_gate.py` | `annotation_scope()` 增承诺面字段、`complete` 换判据；`evaluate_real()` 增"承诺类缺证"硬失败；`render_scope_banner()` 重写（含无条件出现规则、类型名由 config 派生）；模块 docstring 增 0b 段与用法 |
| `utils/i18n.py` | 新增 `scope.note` 双语键（模板**不含类型名**，只留 `{types}` / `{others}`）；`__main__` 自检增占位符断言 |
| `ui/components.py` | 新增 `render_scope_note(lang)`（由 config 派生类型名与承诺面） |
| `ui/app.py` | 顶部新增适用范围提示组件 + 注册进 i18n 运行时表 |
| `README.md` | §1 能力表增「承诺级别」列 + 说明块改写；§2.43 阶段小节；§4 计数 |
| `tests/test_watermark_scope.py` | 2 条断言按新口径改写（注明理由，保留原意） |
| `tests/test_watermark_scope95.py` | **新增 27 例** |
| `docs/95-*.md` | 本文件 |
| `TODO.md` | 选项 0 更新为"已选择：只做角标" |
