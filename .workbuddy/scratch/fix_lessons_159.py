# -*- coding: utf-8 -*-
"""修正 LESSONS.md：把阶段 158 误插在中部的重复「58.」条目移到文件末尾并正确编号，
再把阶段 159 的条目追加在最后。先备份，纯字符串操作，不做删除。"""
import re

P = r"D:\work 111\2026-08-03-17-38-51\.workbuddy\memory\LESSONS.md"
BAK = r"D:\work 111\2026-08-03-17-38-51\.workbuddy\scratch\LESSONS.md.bak"

raw = open(P, "r", encoding="utf-8", newline="").read()
open(BAK, "w", encoding="utf-8", newline="").write(raw)
print("backup bytes:", len(raw))

marker = "58. **下载路径也是写入口"
i = raw.find(marker)
assert i != -1, "marker not found"

j = raw.rfind("\n\n", 0, i)
k = raw.find("\n\n", i)
assert j != -1 and k != -1, "paragraph bounds not found"
block = raw[j:k]
assert "下载路径也是写入口" in block and "（158）" in block, "block sanity failed"
assert block.count("下载路径也是写入口") == 1
print("removed block chars:", len(block))

raw2 = raw[:j] + raw[k:]

pat = re.compile(r"(?m)^(?:\W{0,4})(?:\*\*)?(\d{1,4})(?:\*\*)?\s*[.：]")
nums = [int(m.group(1)) for m in pat.finditer(raw2)]
mx = max(nums)
print("max lesson number:", mx, "| matched count:", len(nums))

n1 = mx + 1
n2 = mx + 2
l158 = (
    "- **" + str(n1) + "**：**下载路径也是写入口，先判定后落盘必须覆盖它；bool 返回值无法区分"
    "「同图跳过」与「路径冲突拒绝」（158）**：`collect_real_assets.cmd_download` 旧实现直接把文件"
    "下载到 `images/<fn>` 再交给 `_upsert` 判定 ⇒ 「已登记图被不同内容覆盖 + 判定拒绝」时磁盘已改、"
    "manifest 仍旧、退出码误报 0（与 `add --copy` 的 P0 同构）。修法同 152：先下临时文件、算 sha、"
    "判定**接受**后才 move 进 `images/`。配套：`_upsert` 返回 bool 时「跳过/拒绝/接受」坍缩成真值 ⇒ "
    "必须升级为可区分的状态字符串（`accepted/skipped/rejected`）。**凡「判定后可能拒绝」的写入口，"
    "返回值语义必须能区分「已存在跳过」与「冲突拒绝」，否则拒绝分支会被调用方误读为成功。**"
)
l159 = (
    "- **" + str(n2) + "**：**验证装置的裁决必须可证伪——退化样本（平局/缺失）不得计入一致性**（159）："
    "`validate_selector` 的一致性判定 `gt = \"opencv\" if do > dl else \"lama\"` 在平局时静默倒向 lama "
    "且**计入**，于是「指标零判别力」的退化样本被读成「指标与真值一致」——全平局时打印"
    "「一致性 4/4 = 100%」（零信息满分），把诚实的 0/2=0% 抬成 2/4=50%。判据：**凡「通过率/一致率」"
    "类裁决，必须把「无判别力（平局/退化）」与「数据缺失」单列且不计入**，否则验证装置会给出"
    "不可证伪的结论（同族：经验 7 度量会奖励劣化、15 跳过≠通过、22 缺数据须与零可区分）。"
)

out = raw2.rstrip("\n") + "\n\n" + l158 + "\n\n" + l159 + "\n"
open(P, "w", encoding="utf-8", newline="").write(out)
print("done. appended lessons #%d(158) #%d(159); new bytes=%d" % (n1, n2, len(out)))
