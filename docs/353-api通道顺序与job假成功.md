# 阶段 353 · api 字节入口的通道顺序 + job 的「假成功」（第三轮审计）

> 日期 2026-10-27 · 性质：**产品缺陷修复**（第三轮审计 7 条中的 3 条已修，其余登记为开放项）

## 一、审计结论有一条被我**实测推翻**（方向相反）

审计说「`api._encode_bytes` 只对 TIFF/AVIF 红蓝互换，PNG/JPG/WEBP 正常」。
它的隔离实验喂的是 **BGR 数组**；而管线交出来的是 **RGB**（`auto_pipeline` 契约 + 函数 docstring）。
**实测**（纯红 RGB → 各格式 → 解码看通道）：

| 格式 | 解码结果 | 结论 |
|---|---|---|
| PNG / JPG | BGR=[200,0,0] ⇒ 变成蓝 | ❌ **红蓝互换**（这是**主路径**！） |
| TIFF | BGR=[0,0,200] ⇒ 仍是红 | ✅ 正确 |

⇒ 错的是 `cv2.imencode` 那条（它按 **BGR** 解释数组），对的是走 `save_image` 的 TIFF/AVIF。
**教训**：审计的结论要用**真实输入**复核；隔离实验喂错语义 ⇒ 结论整个反过来。

## 二、三条修复

1. **编码侧**（`api._encode_bytes`）：入参是 RGB ⇒ `cv2.imencode` 前显式 `RGB2BGR`。
2. **解码侧**（`api` 的三个字节入口）：`cv2.imdecode` 给的是 **BGR**，而核心
   （`process_image` / `detect_watermark_mask` / `inpaint`）只认 **RGB**
   ⇒ 补 `BGR2RGB`。**这是阶段 338 修的 PDF/视频那个病在 api 层的同一份**：
   同一个文件，REST 入口与文件/界面路径给的**不是同一个结果**（审计实测：纯红像素的灰度值 76 vs 29）。
3. **job 的假成功**（`job_manager`）：PDF/视频服务**失败时不抛异常**，只把结果对象的 `status` 设成
   `"failed"`；而 worker 不看 status ⇒ 照样把作业标成 `success`
   ⇒ 客户端 `POST /api/jobs` → 轮询拿到 `{"status":"success"}` → `GET …/file` 却 404。
   修法：拿到结果后先查 `status`，不是 success 就抛异常 ⇒ 走 worker 的失败分支（记 `error`）。

## 四、守卫（`tests/test_api_channel_and_job_status353.py`，4 例）

* 纯红 RGB 经 `PNG`/`JPG` 编码后**解码仍是红**（不是蓝）；
* **`api.detect_watermark_bytes` 与「读文件 → detect_watermark_mask」必须给出同一个掩码**
  （比的是结果、不是颜色 ⇒ 这就是「同一张图不同入口不同结果」的可判定形式）；
* 坏输入的 PDF 作业最终状态必须是 `failed` 且 `out_path` 为空。

反例（修复前的 HEAD）：坏 PDF 作业被判成 `'success'`（实测断言输出）⇒ 守卫有牙齿。

## 五、本轮审计**未修**的其余 4 条（登记为开放项）

| 审计条目 | 内容 | 状态 |
|---|---|---|
| `/api/detect` 忽略 body 里的 `preset` / `sensitivity` | 文档与代码注释都声称可传，实际没读 ⇒ 客户端钉了预设却拿到默认检测 | **未修**（要改 server 的请求体解析 + 守卫） |
| 对比图导出丢失败项不报告 | `ui/tab_settings.py` 逐条 `except → continue`，消息只报成功数 ⇒ ZIP 比历史页少几张但没说 | **未修** |
| 「保存为默认」写盘失败不可见 | `config.save_user_overrides` 返回 False 只 log.warning，界面仍显示「✅ 已应用」 | **未修**（条件性：需写盘失败） |
| 历史画廊索引映射可能指错记录 | `_HISTORY_ID_MAP` 按**全部**记录建，而画廊会跳过缺文件的记录 ⇒ 索引错位时「删除所选」可能删错记录 | **未修**（条件性：需记录文件被删而索引仍在内存） |

未修的理由：后三条都是**条件性**或**小范围**改动，值得单独一步做；先把这三条「无脑即错」的主路径修干净。
