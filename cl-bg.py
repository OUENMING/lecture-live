#!/usr/bin/env python3
"""把 ClassLive 从终端彻底剥离——双重 fork + setsid。

为什么需要它: `nohup ... &` 只挡 SIGHUP，**改不了进程组**。发起它的那条 shell
一结束，整个进程组会被连带清理掉。实测: 用 nohup 起的 ClassLive 会在几分钟内
**静默退出**（日志停在 `▶ 开始: source=mic`，无 traceback、无报错）——看起来
像"自己结束了"。

双重 fork 之后: 进程属于**新会话**、没有控制终端，任何一方退出都带不走它。
不是把它做成系统服务（不写 LaunchAgent、不动任何系统设置）。

用法:  python3 cl-bg.py [日志路径]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = sys.argv[1] if len(sys.argv) > 1 else "/tmp/classlive_bg.log"


def daemonize() -> None:
    if os.fork() > 0:                       # 第一刀: 让调用方立刻返回
        os._exit(0)
    os.setsid()                             # 新会话 -> 脱离控制终端
    if os.fork() > 0:                       # 第二刀: 确保不是会话首进程(git 不到终端)
        os._exit(0)
    os.chdir(HERE)


daemonize()

fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(os.open(os.devnull, os.O_RDONLY), 0)     # stdin -> /dev/null
os.dup2(fd, 1)
os.dup2(fd, 2)
if fd > 2:
    os.close(fd)

# 直接把 cl 这层复用掉: 参数、课程号(.course)、vault 默认值都留在那一处，
# 不做第二份拷贝 —— 否则"两处各记一次"迟早漂移。
#
# ⚠️ 外面再包一层 bash, 为的是**把退出码写下来**。ClassLive 出现过"几分钟后
# 静默消失、日志停在 ▶ 开始、零 traceback"的现象 —— 而"正常 return 0 退出"和
# "被 SIGKILL 打死"在日志上长得一模一样, 只能靠退出码分辨:
#   0  = 主循环真的退出了(有人置了 stopping / running)  -> 查 quit 路径
#   137/143 = 被 SIGKILL/SIGTERM  -> 查外部谁在杀它
# 不带这行, 下次复发还是只能靠猜。
os.execv("/bin/bash", ["/bin/bash", "-c",
                       f'"{os.path.join(HERE, "cl")}"; '
                       f'echo "EXIT=$? at $(date \'+%H:%M:%S\')" >> "{LOG}"'])
