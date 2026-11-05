# 383 · 隐私承诺要字面成立：默认关掉 Gradio 匿名统计（2026-11-05）

## 怎么发现的

复核首屏承诺时逐句对照实现，发现一句**在默认配置下不成立**：

> 🔒 本地处理、图片不上传：检测与修复都在本机完成，图片不会发送到任何服务器
> （**唯一联网是首次使用时的模型权重大小约 205 MB 的下载**）

"图片不会发送到任何服务器" 是真的（图片始终在本机处理）；
但"**唯一联网**"不是——Gradio 6.x 的匿名统计**默认开启**：

- `gradio/analytics.py:46`：`analytics_enabled()` 读 `GRADIO_ANALYTICS_ENABLED`，
  **缺省为 "True"**；
- `gradio/blocks.py:1396`：没有显式传 `analytics_enabled` 时按上面那个开关走；
- `gradio/blocks.py:1474`：`initiated_analytics(data)` 在启动时向
  **`https://api.gradio.app/`** 发一次"用了哪些组件类型"的统计
  （**不含图片内容**，但确实是一次外发请求）。

也就是说：用户装完第一次启动，除模型下载外还会被上报一次使用统计。

## 修法

1. `config.SERVER_ANALYTICS`（默认 **False**，可用 `WME_SERVER_ANALYTICS=1` 打开）。
2. `launch_app()` 把 `analytics_enabled=config.SERVER_ANALYTICS` 传给 Gradio；
   同时在 `ui/app.py` 模块加载时 `os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", ...)`，
   让 Gradio 内部其它读取该环境变量的路径也一致（用户显式设过则以用户为准）。
3. 首屏合规文案（zh/en）补一句「匿名使用统计默认关闭」，让用户知道自己处在哪种状态。

这样，除模型权重下载外，**默认配置下不再有任何出网请求**，承诺字面成立。

## 验证

- 新增守卫 `tests/test_privacy_analytics383.py`（4 条）：默认关闭且
  `gradio.analytics.analytics_enabled()` 实测为 False；环境变量可显式打开；
  `launch_app` 确实把开关透传（关/开两种取值都验）；中英文案都写明统计默认关闭。
- `gradio/analytics.py:88` 的 `version_check()` 在 6.31 里**没有调用点**
  （全仓仅定义、无引用），所以关掉 analytics 后 Gradio 侧不再有其它出网路径。

## 边界

- 只覆盖 Gradio 的匿名统计；`share=True`（公网分享）与模型下载仍是**用户主动**的出网行为，
  两者都在首屏/控制台写明。
- 打开 `WME_SERVER_ANALYTICS=1` 后，首屏那句"匿名使用统计默认关闭"仍描述的是**默认状态**，
  但用户应自行知情；如需更严格，可在打开该开关时由启动日志明确提示（本轮未做）。
- 未改动任何图片处理逻辑。
