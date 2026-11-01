# 阶段 365 · `wme-api` 的入口指错了函数：`--help` 不打用法而是起服务并阻塞

> 日期 2026-11-01 · 性质：**控制台入口契约**修复（阶段 364 安装态冒烟顺带抓到）

## 一、现场（两次实测，不是推测）

阶段 364 在全新 venv 里装出三个命令做冒烟，前两个正常，第三个卡住：

| 命令 | 修复前实测 | 应有行为 |
|---|---|---|
| `wme --help` | rc=0，42 行用法 | ✅ |
| `wme-cli --help` | rc=0，113 行用法 | ✅ |
| `wme-api --help` | **不打用法**，打的是 `REST 服务已启动 … 按 Ctrl+C 停止服务`，然后**阻塞**（我两轮冒烟脚本挂在这里，第一反应还以为是依赖或网络问题） | 打用法并退出 |

留档：`.scratch/evidence365_wme-api-help_before.txt`（修复前的原始输出）。

## 二、根因：入口指向了**函数式 API**

`pyproject.toml` 写的是 `wme-api = "server:start_server"`，而

```python
def start_server(host=None, port=None, max_concurrency=None) -> None: ...
```

不读 `sys.argv`。三层后果，一层比一层隐蔽：
1. `--help` 没有任何解析者 ⇒ 直接起服务并阻塞；
2. **写错的参数被静默忽略**（`--prot 7861` 照样跑）——这比报错危险：用户以为端口换了；
3. 忽略的代价是**真的占用一个端口**，还会让人以为 `API_AUTH_ENABLED` 那套 fail-closed 配置"已经生效"。

`wme` / `wme-cli` 之所以没事，是因为它们指向的 `main:main` / `cli:main` 本来就有 argparse 层。
⇒ 规则：**每个 `console_scripts` 都必须指向一层"会解析 argv 的薄壳"**，函数式 API 不做入口。

## 三、修法

- `server.py` 新增 `cli_main(argv=None) -> int`：`--host / --port / --max-concurrency`，
  不传即取 `config` 默认值 ⇒ 解析后调用 `start_server`。
  `start_server` 签名与语义**一字未改**，`main.py --api`、`cli.py --serve`、测试仍照旧调用它。
- `pyproject.toml`：`wme-api = "server:cli_main"`，注释写明为什么不能指回 `start_server`。
- 同步消费者（这一处漏改就会"打包与守卫各说各话"）：
  `tests/test_packaging_meta.py` 的入口映射、`tests/test_dist_artifacts364.py` 的合成夹具文本、
  README §3.3 那行（原文「等价于 `python server.py`」是错的——`python server.py` 是**自检**，
  随机端口探活后退出，不是起服务）、docs/297 runbook 第 5 步改成 `wme-api --help` 并写明期望。
- `server.py` 自检加**用例6**：`cli_main(["--help"])` 退出码 0 且打出 `usage: wme-api`；
  `cli_main(["--prot","7861"])` 退出码 2；两者都**没有**起服务。
  （编号接在既有 1–5 之后，中途一度写成重复的"用例5"，实测自检时才发现并改。）

## 四、守卫：不只修这一个，把整张表钉住

`tests/test_wme_api_cli365.py`（6 例）：
① `--help` 出用法、退出码 0，且 `start_server` **一次都没被调用**（用记录型替身，真服务绝不可能起）；
② 拼错的参数 ⇒ argparse 退出码 2 + `unrecognized arguments`，**没有**默默起服务；
③ 三个参数真的透传给 `start_server`（防止"解析完丢掉"，LESSONS #333 同族）；
③b 不传 ⇒ 全 `None` ⇒ 由 config 兜默认值（旧行为零变化）；
④ **全表检查**：`pyproject` 每个入口指向的函数体里都必须出现 `parse_args`（AST：裸名调用与属性调用都算）；
④b 反例 + 反-误报：把 `start_server` 那种函数当入口必须被抓；文档字符串里写 `ns.parse_args(...)` 不许算数
（文本判据的老坑，#316/#337/#339 之后第四次）。

## 五、验证

- 修复前后对照（同一条命令）：`python -c "...cli_main(['--help'])"` ⇒ 修复后 rc=0 + `usage:`；
- 安装态：重装新 wheel 后 `wme-api --help` ⇒ **rc=0 + 用法**；`wme --help` / `wme-cli --help` 仍 rc=0；
- `python server.py` 自检：用例1–6 全过（顺带把阶段 366 的 REST 缺陷一起暴露出来）；
- `test_wme_api_cli365 / test_packaging_meta / test_cli_help348` 共 18 例绿。

## 六、顺手抓到的另一半（另见 docs/366）

同一轮 `python server.py` 自检直接崩在半路：`AttributeError: 'QualityReport' object has no attribute 'detail'`
⇒ **`evaluate=true` 的 `/api/remove` 从阶段 337 起就是坏的**。
那是比入口接线更重的问题，单独记阶段 366。
