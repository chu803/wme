# 阶段 376 · README 的"展示图"是破链；而且 README 还是 PyPI 正文

> 日期 2026-11-04 · 性质：**对外展示修复 + 一条会腐烂的文档守卫**

## 一、问题（两层，第二层更隐蔽）

§2.2「新增配置项」下面挂着四张"四联对比图"：

```markdown
| ![角落LOGO](docs/assets/demo_corner_logo_opencv.png) | ![底部小字](docs/assets/demo_edge_text_opencv.png) |
```

1. **文件根本不存在**：`docs/assets/` 里只有 `bench_*.png` 与 json，**没有任何 `demo_*.png`**
   ⇒ 在 GitHub 上就是四张破图。
2. **README 同时是 PyPI 项目页的正文**：PyPI 只渲染 Markdown，**不会把相对路径重写成仓库地址**
   ⇒ 就算文件在，PyPI 上也加载不出来。"展示能力"的那一段对两类读者都是坏的。

## 二、改法

- 改指**已入库**的四张 `bench_*_seed1.png`（同样是"原图 / 带水印 / 检测掩码 / 修复结果"的四联图，
  由 `scripts/run_benchmark.py` 生成并随仓库提交）；
- 全部改成**绝对 raw URL**：
  `https://raw.githubusercontent.com/chu803/wme/main/docs/assets/...`
  —— GitHub 与 PyPI 都能渲染；
- 顺带把模型下载失败提示补全：明确"手动把权重放到哪个路径（`config.MODEL_PATH`），
  或启动时加 `--no-download` 只用 OpenCV"。

## 三、守卫：`tests/test_readme_assets.py`

- 解析 README 全部 `![alt](url)`；
- 指向本仓库 `docs/assets/` 的，**必须是绝对 raw URL 且文件存在**；
- 相对路径一律判红并说明原因（PyPI 解析不到仓库）；
- **反例**：注入一条指向不存在文件的绝对 URL ⇒ 必须进 missing；
  相对路径 ⇒ 必须被要求改绝对（否则守卫是恒绿的装饰）。

## 四、验证

CI 等价入口：`rc=0`（3424 passed / 38 skipped / 6 deselected，432.99s）。
`tests/test_readme_assets.py`（2 例，含反例）与 README 尺度守卫全绿。

## 五、边界

- 只保证"图能加载、指向真实文件"，不评价图的内容是否过时（演示图仍是旧字体生成的那批，
  是否重生成由用户决定，见 docs/369 §六）；
- raw URL 固定指向 `main` 分支：分支改名需同步 README（守卫会因 404 无法发现——这是静态守卫的边界）。
