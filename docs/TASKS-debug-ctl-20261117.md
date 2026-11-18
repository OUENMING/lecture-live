# 任务单：调试通道（clctl）

2026-11-17 · Owen 定：学 cicada 的 `lsctl` 加一条调试通道，目标是比它好。
参照：cicada `tools/README.md`（lsctl 用法）、`Sources/Cicada/DebugControl.swift`（命令带 id、回 JSON、退出码）。
派活方式、通用规矩、交回格式：照 `TASKS-web-cloud-20261114.md`「怎么用」「通用规矩」两节。

## 要什么

让 AI（和作者）在一节**正在跑**的 ClassLive 上：问状态、等某个条件成立、发几条控制命令，全部拿结构化回复和退出码，不靠 grep 日志。

比 cicada 好的三条，每条都有判据：

1. **关着就等于没有**：开关没开时不起线程、不建 socket，主循环里只多一次布尔判断。
2. **能等事件，不只等状态**：`--wait` 既能等状态字段（`trans.mode == raw`），也能等事件计数（`events.final >= 5`）。cicada 只能等状态。
3. **默认就在隔离运行里**：`run_isolated.py` 自动打开它，socket 落在隔离目录里；正式上课默认关。

## 规格（两张单共用）

- **开关**：`CLASSLIVE_DEBUG_CTL=1`。登记 `HANDBOOK.md` §6.1（R30 钉着）。
- **传输**：Unix domain socket，目录权限 0700、socket 0600。路径 `paths.ctl_dir() / f"ctl-{pid}.sock"`，`ctl_dir()` 挂在 `STATE_ROOT/run/` 下。macOS 的 socket 路径上限约 104 字节：超了就改放 `/tmp/classlive-ctl-<uid>/<pid>.sock`，并在 `ctl_dir()/ctl.path` 写一行真实路径。进程退出时删 socket 和 `ctl.path`。
- **协议**：一行一个 JSON。请求 `{"id","cmd","args"}`；回复 `{"id","ok","error":{"code","msg"}?,"data","slow_ms"?}`。
- **线程**：accept 和读写都在通道自己的线程上。命令以 `(请求, Future)` 进队；**只在主线程**由 `poll()` 取出执行，每次最多 8 条；连接线程等 Future，超过 5 s 回 `timeout`。主线程从不碰 socket。
- **命令（第一版只做这些）**：`ping` · `help`（列命令和状态分区，带每个字段的类型）· `state[:分区,…]` · `events[:since=<seq>]` · `mode:<both|en|raw>` · `stall:<秒>`（故意卡主线程，验卡顿探针；上限 5 s）· `stop`（等于用户点了下课，走同一个入口）。
- **事件**：环形缓冲最近 2000 条 `{seq, t, kind, n?}`。`kind` 取 streamq 的 tag；默认不带正文。设了 `CLASSLIVE_DEBUG_CTL_TEXT=1` 才带 `en` / `zh` 前 200 字（Owen 11-17：用户已授权）。也登记 §6.1。
- **安全**：没有 eval，没有「任意路径」参数，没有改文件的命令。key、通行码、install-id 不进任何回复。
- **客户端** `tools/clctl.py`：`clctl [--root <STATE_ROOT>] [--sock <路径>] <命令>`；`clctl --wait '<路径> <==|!=|>|<|>=|<=> <值>' [--timeout 10]`（路径以 `events.` 开头时按事件计数）；`clctl --record <输出.jsonl> --every 0.5 --for 60`（定时取 state 落盘）。退出码与 lsctl 相同：0 成功 · 1 命令失败 · 2 没回复 / 超时 · 3 没有这个命令 · 64 用法错。找 socket 的顺序：`--sock` → `$CLASSLIVE_CTL_SOCK` → `<root>/run/ctl.path` → `<root>/run/` 下最新的 `ctl-*.sock`。

## D-1 通道本体（不接线）

**只许改 / 新建**：`debug_ctl.py`（新）、`tools/clctl.py`（新）、`paths.py`（只加 `ctl_dir()`）、`tests/test_debug_ctl.py`（新）、`docs/HANDBOOK.md` §6.1（两行）

**先加载的 skill**：`codebase-design`、`verification-before-completion`

1. `debug_ctl.py`：`Ctl` 类 —— `start()`、`poll()`、`close()`、`register_state(name, fn)`、`register_command(name, fn, help)`、`emit(kind, n=None, text=None)`；模块级 `enabled()` 读开关。开关没开时 `start()` 直接返回、不建线程。
2. `tools/clctl.py`：按上面的客户端规格。只依赖标准库。
3. 测试（全部 HOME 隔离、进程内）：
   - 开关关：`start()` 后线程数不变、`ctl_dir()` 里没有 socket。
   - 开关开：起 `Ctl`，在测试线程里循环 `poll()` 充当主线程；用 `clctl` 的函数入口跑 `ping`、`state`、`help`、未知命令（退出码 3）、`events:since=`。
   - 命令确实在调 `poll()` 的线程上执行（记 `threading.get_ident()` 对比）。
   - 不调 `poll()` 时请求 5 s 后回 `timeout`，退出码 2。
   - `--wait 'events.final >= 3'`：另一线程 `emit("final")` 三次后成立；不够则超时退出 2。
   - 长路径：`STATE_ROOT` 设成 120 字节以上的目录，socket 落到 `/tmp/classlive-ctl-*`，`ctl.path` 指过去，`clctl --root` 找得到；`close()` 后两者都没了。
   - 权限：目录 0700、socket 0600。
   - 哨兵：注册一个返回含 `"cl-SENTINEL-KEY"` 字符串的状态分区以外的一切数据都不含它 —— 具体是：把哨兵塞进环境变量和 `paths.test_key()` 文件，跑完全部命令，所有回复的 JSON 文本里搜不到哨兵。

完成判据：上面每条测试都有，`scripts/check.py --changed` 全绿；变异验证两条（把 `poll()` 改成在连接线程里直接执行 → 线程断言变红；把 0600 改成 0644 → 权限测试变红）。

## D-2 接进 main 和隔离运行

**前置**：D-1 验收
**只许改**：`main.py`、`docs/experiments/run_isolated.py`、`docs/HANDBOOK.md`（§6.0 一句）、`tests/test_debug_ctl.py`、`tests/test_loop_probe.py`（只在需要时）

**先加载的 skill**：`verification-before-completion`、`diagnosing-bugs`（排查时）

1. `run()` 里起 `Ctl`，注册状态分区：`build`（版本、pid、`--source`/`--ui`/课程）· `listen`（running、stopping、音源、是否播完、是否在说话）· `stream`（现有 `stream.metrics()`，没有流时 null）· `trans`（`trans` 字典原样）· `queues`（streamq / sealedq / finalq 当前长度）· `loop`（测试模式下卡顿探针的计数，没有为 null）。读到的对象不存在时给 null，不抛。
2. 命令：`mode:` 走 `main.py:3084` 那个 `on_translate` 同一个入口，**并且**让悬浮窗的模式显示跟着变（找到 UI 侧的 setter，`overlay.py` 不在只许改里 —— 找不到就只改 `trans`，写进「计划外问题」）；`stop` 走用户点下课的同一个入口；`stall:` 在主线程 `time.sleep`。
3. 事件：在 `drain()` 逐条分发的地方 `emit(tag)`。
4. `poll()` 放在主循环里 `drain()` 之后、卡顿探针 `_l3` 打点之前（这样它的耗时算进 `drain` 那一步）；HANDBOOK §6.0 的 `drain` 释义加一句「含调试通道」。收尾等待（`wait_for` 的 `tick`）里也要调 `poll()`。
5. 退出时（含异常）`close()`。
6. `run_isolated.py`：设 `CLASSLIVE_DEBUG_CTL=1`；把 key 换成 test key —— **在改 `paths.STATE_ROOT` 之前**用 `cloud_translator.load_test_key()` 读，读不到就退出并说明；install-id 用作者机器上已有的那个（`~/.classlive/install-id`，同样在改 `STATE_ROOT` 之前读出再写进隔离目录），否则会撞 test1 的设备上限。启动后打印一行 `⏱ __CTL__ <socket 路径>`。

完成判据：

- 不设开关时：`check.py --changed` 全绿；`tests/test_loop_probe.py` 不改即过（或改了的话交回里说明为什么）。
- 进程内测试：用 `run_isolated` 同款的写端隔离跑一段 20 s 的作者自己的课堂音频（`~/.classlive/eval/ocw/lec11.wav` 截前 20 s，`--speed 4`，所以不走云端识别、不花钱），期间用 `clctl` 依次：`ping` → `state:listen` → `--wait 'events.final >= 1'` → `mode:raw` → `--wait 'trans.mode == raw'` → `stop`，全部退出码 0；进程正常结束、socket 和 `ctl.path` 都删了；`sessions/`、`~/.classlive/` 对拍无变化。
- `stall:2` 之后，测试模式报告的 `loop_stalls.n_ge_1s` ≥ 1。
- 〔作者做〕真开一节悬浮窗模式的回放（`run_isolated.py … --ui overlay`），`clctl mode:en` 后看悬浮窗的模式胶囊变没变。2 分钟。

## 以后再加（不在本单）

`inject:sealed`（往 sealedq 塞一句假封口句，给 L-2 用）· 主线程卡 > 2 s 自动 `sample` 一次（cicada `Watchdog.swift`）· 截图 / 悬浮窗渲染进文件。
