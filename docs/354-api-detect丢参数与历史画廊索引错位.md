# 阶段 354 · 「收了参数却没用」与「索引与展示不同源」（第三轮审计最后两条）

> 日期 2026-10-27 · 性质：**产品缺陷修复**（第三轮审计 7 条中的第 4、7 条；第 5、6 条见 §四）

## 一、`/api/detect` 把文档承诺的参数静默丢掉

* 用法页写着：`POST /api/detect {"image": "<base64>", "preset": "default"}`；
* 调用点注释写着「preset/sensitivity 可选传递，缺省交给 api 补 config 默认」；
* 实际代码：`body` **读了**，但调用时恒传 `preset=None, sensitivity=None`
  ⇒ **客户端钉了预设，拿到的却是默认检测**，而且没有任何提示（不是报错，是"照做了但没用"）。

修法：`_detect_one_b64(image_b64, preset=None, sensitivity=None)` 把它们**真的传下去**，
处理器传 `body.get("preset")` / `body.get("sensitivity")`。

## 二、历史画廊的索引表与画廊**不同源**

`_HISTORY_ID_MAP` 原来按**全部**记录建，而画廊会 `continue` 掉「缩略图与结果都不存在」的记录
⇒ **格子数比索引表短** ⇒ 点第 N 格选中的是**另一条**记录；「删除所选」删的也是那一条
（数据破坏类，条件性触发：记录文件被删而索引仍在内存）。

修法：索引表在**画廊循环内部**追加 ⇒ 只有真正进了画廊的记录才进索引表 ⇒ 两者永远同长同序。

## 三、守卫（`tests/test_audit_fixes354.py`，3 例）

* `_detect_one_b64` 必须把 `preset`/`sensitivity` 透传给 `api.detect_watermark_bytes`；
* 处理器源码里必须出现 `body.get("preset")` / `body.get("sensitivity")`（防退回 None）；
* **索引表长度必须等于画廊格子数**，且第一条指向画廊里的第一条记录。

反例（修复前的 HEAD）⇒ **3 条全红**（实测输出）。

## 四、本轮审计**未修**的 2 条（登记）

| 条目 | 内容 | 为什么这轮没做 |
|---|---|---|
| 对比图导出丢失败项不报告 | `ui/tab_settings.py` 逐条 `except → continue`，消息只报成功数 ⇒ ZIP 比历史页少几张却没人知道 | 需要新增 i18n 键（`compare_zip_partial`）+ 改消息结构，与「保存为默认」那一条一起做更省事 |
| 「保存为默认」写盘失败不可见 | `config.save_user_overrides` 返回 False 只 `log.warning`，界面仍显示「✅ 已应用」（文案承诺"下次启动自动生效"） | 同上：需要新增键 + 把失败写进返回信息；且要动 `apply_superres_config` 的返回结构 |

两条都属"界面说一套、实际做另一套"，下一阶段连同新文案键一起收口。
