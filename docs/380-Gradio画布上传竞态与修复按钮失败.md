# 380 · Gradio 画布上传竞态：修复按钮偶发 UnidentifiedImageError（2026-11-05）

## 怎么发现的

1.0.5 发布后，用 PyPI 上的 wheel 做真机黄金路径复验：试示例 → 自动检测（7 个区域）
→ 一键修复，**后端在 preprocess 阶段就失败**：

```
gradio.exceptions.ComponentProcessingError: Could not preprocess input component
at index 0 (a `imageeditor` component) of the event handler (named "process_auto_repair")
...
Original error: UnidentifiedImageError: cannot identify image file
C:\...\Temp\gradio\<hash>\layer_0.png
```

第一次是 `background.png`，之后每次都是 `layer_0.png`；同一路径的文件在报错后
毫秒级内就能被 Pillow 正常打开（`WEBP/PNG 768x768`），说明**不是坏图**。

## 排查

- 不是 1.0.5 的数据目录改动：事件在进入我们的 handler 之前就失败；
- 不是图片格式：临时文件名为 `.png`、内容一度是 WebP；改成
  `ImageEditor(format="png")` 后文件确认变成真正的 PNG，**仍然失败**，故排除格式假设；
  该实验已撤回，避免无色差收益的格式变更；
- Gradio 6.31 前端在点击时重新上传画布背景与图层，随后立即 `queue/join`；
  后端 preprocess 可能在文件写完前 `PIL.Image.open` ⇒ 读竞态。
  这与「文件稍后可读、错误发生在 preprocess、与业务代码无关」三条证据一致。
- 早期一次成功、后来 9/9、3/3 失败，符合竞态在不同进程/调度下的表现差异。

## 修法

`ui/gradio_compat.py`：只包装 `ImageEditor.preprocess`，遇到异常按 0.15 秒间隔重试，
最多 6 次（总计约 0.9 秒）；成功立即返回，耗尽后**原样抛出**，不吞掉真正的坏图错误。
由 `build_app()` 装载一次（幂等），单图与自动检测两页的画布都受益。

## 验证

- 单元守卫 `tests/test_gradio_compat380.py`（4 条）：前两次失败第三次成功、
  耗尽后抛出原异常、装载幂等且 payload 原样传递、`build_app` 确实装载。
- 源码态真机复验（端口 7889，撤掉 format 实验后）：试示例 → 检测 7 区域 → 一键修复，
  日志 `UnidentifiedImageError` 出现 **0 次**，结果写入 `outputs/auto_180620_cleaned_180620.png`。
- 全量入口 `rc=0`（3448 passed / 37 skipped / 6 deselected，781.50s；
  当时另有真机服务在跑，耗时长于常态）。

## 边界

- 这是对上游竞态的**兼容层**，不是根治；Gradio 修复后应重新评估删除。
- 重试只在异常路径发生，正常请求无额外延迟；界面不会提示重试过程。
- 真正的坏图仍会在 6 次后报错，不会被静默吞掉。
