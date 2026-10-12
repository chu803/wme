# 开发与贡献指南（内部）

> 本项目为**专有软件**（见 LICENSE，All Rights Reserved）。本文件记录**内部开发规约**：
> 环境搭建、验证入口、分层铁律与门禁家族。外部贡献须先取得著作权人书面许可。

## 环境搭建

- Python 3.13（本机实测 torch 2.9.1+cu128 可跑）；CPU 推理用默认依赖，GPU 见 requirements-gpu.txt。
- 安装：pip install -r requirements.txt（GPU 追加 -r requirements-gpu.txt，源 cu128）。
- 权重：python download_model.py 获取 big-lama.pt（不入库，见 .gitignore）。

## 唯一验证入口

一切检查走 scripts/run_all_checks.py（本地与 CI 同源）：

- 默认：pytest CI 等价子集（跳过 torch_required / network）+ undef_scan + docker_static（容器静态一致性）。
- --full：全量 pytest。--gates：追加真实/合成红线与性能门禁（需 torch、耗时）。--cov：覆盖率地板判定。
- 退出码 0/1/2（0 通过 / 1 有失败 / 2 全跳过）。

## 分层铁律

ui → services → core → utils → config，严禁反向依赖；core/services/utils 零 gradio；config 不依赖 utils.logger。

## 门禁家族与质量口径

- 合成线 scripts/quality_gate.py；真实线 scripts/real_gate.py --strict-scope（红线 + 漂移 + 259 余量 advisory 监控）。
- 检测对外打分用**掩码级**指标；红线是结论的 encoding，未获显式授权不得松动。
- 每个 --update/--refresh 须接 baseline_guard；脚本 print 非 GBK 字符须 force_utf8_stdio()。

## 改动纪律

- 探针 → 分类（defect vs by design）→ 最小修复 → 回归 → 注入式反例（sha256 字节级）→ 守卫测试 → 文档 → 记忆。
- 破坏性操作保守：移动优于删除、归档 rename；涉及真实文件删除前须显式确认。
- 禁「此处省略代码」；阶段完成主动告知。

## 文档与记忆

- 逐阶段细节入 docs/（阶段号命名）；里程碑归纳入 CHANGELOG.md；长期记忆入 .workbuddy/memory/。
- 记分卡 docs/quality/product_scorecard.json 每阶段刷新，保留旧值作进步曲线。
