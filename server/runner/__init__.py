# server/runner —— 网页版 VPS runner（契约 docs/CONTRACT-runner-20261120.md §7）
# 管什么：领笔记任务 → 拉逐句 → 复用桌面现成管道出笔记 → 交回 Worker。
# 对外接口：`python -m server.runner.loop`（或 `python server/runner/loop.py`）。
# 为什么这样：桌面 .py 一律不改，在本仓库根 import 它们；模型请求统一经
#   `llm_client.headers` 注入 `X-ClassLive-Bill-To`（见 note_job.py 文件头）。
