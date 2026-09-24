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
# ⚠️ 默认日志**不放 /tmp**: 那是全局可写的, 固定名字有两个真实风险 ——
#   ① 别人可预建同名文件 → 启动即 EACCES 失败(拒绝服务);
#   ② 可预置指向任意文件的符号链接 → 追加写命中目标(符号链接跟随)。
#   放 macOS 惯例的 ~/Library/Logs/<App>/ 下, 只有本用户可写。
#   (2026-09-24 OCR 发现。)
_DEFAULT_LOG = os.path.expanduser("~/Library/Logs/ClassLive/bg.log")
LOG = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_LOG
os.makedirs(os.path.dirname(LOG), exist_ok=True)


def daemonize() -> None:
    if os.fork() > 0:                       # 第一刀: 让调用方立刻返回
        os._exit(0)
    os.setsid()                             # 新会话 -> 脱离控制终端
    if os.fork() > 0:                       # 第二刀: 确保不是会话首进程(git 不到终端)
        os._exit(0)
    os.chdir(HERE)


daemonize()

# ⚠️ 顺序与临时 fd 都要管: 若调用方关了标准 fd(如 `cl-bg.py <&-`), 下面
# `os.open(LOG)` 可能拿到 fd 0, 紧接着 `dup2(devnull, 0)` 就会把它关掉 ——
# 日志描述符当场丢失, 后面 dup2(fd,1) 实际把 stdout 指向 /dev/null, 日志与 EXIT 行
# 全写不进去, 而这正是本脚本存在的意义。另外临时 fd 不关会一路泄漏给 exec 后的 bash。
# (2026-09-24 OCR 全量审计发现。)
devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(devnull, 0)                              # 先把 stdin 接上, 再开日志
if devnull > 2:
    os.close(devnull)
# O_NOFOLLOW: 拒绝跟随符号链接 —— 即使路径可由他人预置, 也写不进去。
fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
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
# ⚠️ LOG 与 HERE 都经**环境变量**传, 都不拼进命令串: LOG 来自 sys.argv[1],
# HERE 来自脚本所在目录 —— 只要有一个被插值进 bash -c, 就是拿外部字符串当代码执行
# (目录名里带 `"` 或 `$(...)` 就会真跑起来)。LOG 先修过, HERE 漏了。
os.environ["CLASSLIVE_LOG"] = LOG
os.environ["CLASSLIVE_HERE"] = HERE
os.execv("/bin/bash", ["/bin/bash", "-c",
                       f'"$CLASSLIVE_HERE/cl"; '
                       f'echo "EXIT=$? at $(date \'+%H:%M:%S\')" >> "$CLASSLIVE_LOG"'])
