# server/runner —— VPS 笔记 runner（W23）

管什么：Linux 常驻进程，从 Worker 领笔记任务，复用**桌面现成管道**（仓库根的
`main.rebuild_note_for` / 底层 `obsidian_writer` 链）生成与桌面同一套笔记，交回
`note.md` / `note.html`。契约：`docs/CONTRACT-runner-20261120.md` §7。

对外接口：`python -m server.runner.loop`（或 `python server/runner/loop.py`）。
不改任何桌面 .py；模型请求经 `llm_client.headers` 注入 `X-ClassLive-Bill-To`（见
`note_job.py` 文件头的 seam 论证）。

## 模块

| 文件 | 管什么 |
|---|---|
| `client.py` | `RunnerClient` 六条 runner 路由；`LeaseLost` / `SessionGone`；5xx/网络错重试 2 次（1 s、3 s），4xx 不重试 |
| `session_md.py` | 网页逐句 → `sessions/<日期>_<课名>.md`（借桌面 `_flat` 拼块，`_parse` 读回断言） |
| `note_job.py` | `run_note` 全流程；`install_bill_to` 计费头；main 不可用 → 底层模块 fallback |
| `loop.py` | 主循环：5 s 领 / 30 s 心跳 / 20 s `summary_job.tick` / SIGTERM 优雅退出 |
| `tests/` | unittest（见下「跑测试」） |

## VPS 安装

```bash
# 1) 代码：checkout 本仓库（桌面 .py 就在仓库根，runner 直接 import）
sudo mkdir -p /opt/classlive && sudo chown $USER /opt/classlive
git clone <本仓库> /opt/classlive && cd /opt/classlive

# 2) 依赖：httpx + numpy（`llm_client.headers` → `final_asr` 顶层要 numpy；纯本地、无模型文件）
sudo apt install python3 python3-pip
sudo pip3 install httpx numpy

# 3) 隔离 HOME（runner 的桌面数据全在这；**不要**用真家目录）
sudo mkdir -p /var/lib/classlive-runner/.classlive
# runner 通行码（Owen 用 `cl_admin.py add runner` 发，形如 cl-runner-…）：
sudo sh -c 'printf %s "cl-runner-xxxxxxxx" > /var/lib/classlive-runner/.classlive/credentials'
sudo chmod 700 /var/lib/classlive-runner && sudo chmod 600 /var/lib/classlive-runner/.classlive/credentials

# 4) token（Worker secret，rn_<32 hex>）
sudo sh -c 'printf "RUNNER_TOKEN=rn_<32hex>\n" > /etc/classlive/runner.env && chmod 600 /etc/classlive/runner.env'

# 5) systemd（unit 文件就在本目录）
sudo cp server/runner/classlive-runner.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now classlive-runner
```

环境变量：`RUNNER_BASE`（默认 `https://asr.bldcam.page`）、`RUNNER_TOKEN`（必填）、
`RUNNER_HOME`（隔离桌面 HOME，必填）、`RUNNER_NAME`（默认主机名）。
⚠️ `HOME` 必须在进程里指到 `RUNNER_HOME`（loop 启动时自己设）—— 桌面 `paths.STATE_ROOT`
是 import 时算的，所以**别**在 shell 里先 import 过桌面模块再起 runner。

看日志：`journalctl -u classlive-runner -f`（一行一事件）。产物落点（本地留档）：
`$RUNNER_HOME/Desktop/烤菠萝的笔记库/<课号>/<日期>_<课号>.md|.html` 与
`$RUNNER_HOME/sessions/`。

## 跑测试（本仓库根）

```bash
python3 -m unittest discover -s server/runner/tests -t .    # 全部
# Linux 可行性（子进程塞 AppKit=None 那套）：
python3 server/runner/tests/test_note_linux_import.py
```

测试全程 HOME 指向临时目录（读端写端一起隔离）；不连真 Worker、不调真模型。
