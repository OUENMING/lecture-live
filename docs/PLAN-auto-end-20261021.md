# 自动结束一节课（2026-10-21 提案）

> 状态：**提案，未实现**。先拿真课验证判据，再决定默认档位。

## 为什么要做

2026-10-21 ECS170 那节（美国用户）**不是**空闲案例，但它把另一个问题暴露出来：

- 64 分 17 秒的课，音频 3854.4 秒，Soniox **367 句 / p50 0.9 s / 无退回**；
- 17:13 用户把翻译模式切到「纯转录」（`ui.trans_mode=2`，两次点击 both→en→raw），之后 40 分钟只有 ASR、没有翻译；
- 笔记只覆盖到 **17:11**（3 章），后 40 分钟没进笔记；
- 所以「最后一条翻译之后」**不等于**「没人说话」，不能拿它当自动结束的判据。

自动结束仍要做，但目前**没有一节课能证明**「人走了、App 还在录」这个场景（这节不是）。
先做 `notify` 收集真课数据，再决定是否自动停。

ClassLive 现在**没有任何空闲自动停**：`power.keep_system_awake()` 还把系统睡眠挡住了（`main.py:4736`），
所以人走了电脑也不会睡。

## 现在已有的信号（都核实过代码）

| 信号 | 在哪 | 能回答什么 | 坑 |
|---|---|---|---|
| 课表 `timetable.json` | `~/.classlive/timetable.json`，`timetable.Slot` 有 `weekday/hh/mm/minutes/interval/skip` | 这节课**应该**几点结束（Owen 的 ECON10740 = 周一 15:00 + 110 分钟） | 只有导入过课表的人有；拖堂/调课会错 |
| `.course` | `~/lecture-live/.course` | 当前课号 | 只在本机 |
| VAD / `Segmenter` | `vad.py` | 最近一次「有人说话」是什么时候 | 长静音（小组作业/考试）会误判 |
| `SilenceWatch` | `capture.py` | 麦克风是不是**数字静音**（<−80 dBFS） | 麦克风被静音 ≠ 下课 |
| Soniox 流 | `stream_asr.py` | 有没有封口句 / 最后一条服务端消息 | 3.10.1 的 watchdog 判据写错，静默时不会断 |
| 系统睡眠/唤醒 | `overlay.py`（`NSWorkspaceWillSleep/DidWake`） | 人合盖/系统睡了 | `keep_system_awake` 挡空闲睡，只挡不住合盖 |
| 用户输入空闲 | **还没有**；macOS 有 `CGEventSourceSecondsSinceLastEventType`（Core Graphics，macOS 10.4+） | 键鼠多久没动（**弱信号**：安静听课也会很长） | 只看键鼠，不看人是否在看屏幕；不单独触发 |
| 锁屏/显示器睡 | `NSWorkspaceScreensDidSleep` 已有；锁屏要读 `CGSessionCopyCurrentDictionary` | 人走了的强信号 | 有人锁屏但课堂还在放（网课） |
| 转写内容 | 内存里的 `note_segment` / 最后几句 | 是不是在收尾（"that's all for today"） | 需要分类器；单靠主题漂移不可靠 |

## 外面怎么做（2026-10-21 回源核实）

- **Jamie**（会议记录）：同时满足三条才自动停 —— ① 麦克风不再被会议 App 使用；② 日历事件进入最后四分之一（过了结束时间也保持为真）；③ **至少 30 秒没有语音**。没有日历的临时录音用更严的规则。  
  来源：<https://docs.meetjamie.ai/getting-started/auto-stop-recording>（2026-10-21 取）。
- **Granola**：会在「检测到通话结束」（依据转写长度、会议 App 是否还在用麦克风、日历结束时间）、**15 分钟没有新音频**、**电脑睡眠**时自动停；但它承认相邻两场会议可能被并成一份转写，需要手动停。  
  来源：<https://docs.granola.ai/help-center/taking-notes/transcription>（2026-10-21 取）。
- **Panopto / Echo360**：按课表**定时开、定时停**的教室录播，不依赖人点停止。Panopto Remote Recorder 的官方说明逐字是「schedule a video session to start and stop automatically without needing someone to be at the ...」。  
  来源：<https://support.panopto.com/s/article/Schedule-Remote-Recordings>（2026-10-21 取）。
- **Apple**：`CGEventSourceSecondsSinceLastEventType(stateID:eventType:)` 返回距上一次输入事件的秒数（macOS 10.4+），就是系统级的「人离开多久」。  
  来源：<https://developer.apple.com/documentation/coregraphics/cgeventsource/secondssincelasteventtype(_:eventtype:)>（2026-10-21 取）。

结论：**课表 + 无语音 + 人离开**是成熟产品都在用的三条；30 秒对会议够，对课堂太短（小组作业、做题、考试会误停），课堂要 5–15 分钟。

## 建议的判据（保守版）

定义：

- `past_end`：课表算出的结束时间 + 宽限 10 分钟；
- `quiet`：距最后一次 VAD 语音 / 最后一条 ASR 文本（取较晚者）；
- `away`（**强信号**）：锁屏 **或** 显示器睡 **或** 系统睡；
- `hidle`（**弱信号**）：键鼠空闲 ≥ 10 分钟 —— 学生安静听课也会这样，**只用来提示，不单独触发**；
- `closing`：收尾短语命中，**或** 轻量分类器判 `ending` 且置信 ≥ 0.8。

| 课表 | quiet | 其他 | 动作 |
|---|---|---|---|
| 过了结束 +10m | ≥ 5m | 任意 | 60 秒倒计时 → 自动结束 |
| 未到结束 | ≥ 5m | `closing` | 60 秒倒计时（早下课） |
| 任意 | ≥ 10m | `away` | 60 秒倒计时（人确实离开） |
| 任意 | ≥ 20m | `hidle` ≥ 20m | **只提示**「看起来很久没说话了」，不自动停 |
| 没有课表 | ≥ 15m | 无 `closing`、无 `away` | **只提示**，不自动停 |
| 任何 | 任意 | 无 | 不动 |

硬规则：

1. **前 10 分钟永不自动停**；
2. **永远先倒计时**（60 秒），卡片上「继续上课 / 现在结束」；任何语音或键鼠输入都取消；
3. 自动结束走**正常的收尾路径**（笔记、报告、上传），不是 kill；
4. 默认档位 `notify`（只提示）跑两周，零误停后再开 `ask`；
5. 没导入课表的人，只有 `quiet + closing` 或 `quiet + away` 才倒计时；单靠 `hidle` 永远不自动停。

## 内容分类（只做确认，不做唯一依据）

- 先跑**免费**的收尾短语正则：`that's all for today` / `see you next week` / `have a good weekend` / `we'll stop here` / `any questions` / `thanks everyone` / `bye`。
- 静音 ≥ 2 分钟才调一次模型（最多 5 分钟一次）：把最后 ~600 词 + 课名发给 DeepSeek flash，返回
  `{still_lecture: bool, ending: bool, confidence: 0..1}`，`max_tokens≈80`、关思考。一次约 $0.0001，一节课最多 ~10 次。
- **主题漂移不算**：课堂可以讲任何主题；只有「收尾语言」和「长时间没有课堂内容」才算。
- 分类器只做 **confirm**（有 `past_end` 或 `away` 时才看它），不做唯一触发。

## 实现拆分

1. `auto_end.py`：纯状态机（`Signals` → `Decision`），不碰网络/UI，先写单测。
2. `presence.py`（或放 `auto_end.py`）：`hidle_seconds()`（Quartz）、`screen_locked()`（`CGSessionCopyCurrentDictionary`）、`display_asleep()`。
3. `main.py`：在 VAD/`note_segment` 回调里记 `last_speech` / `last_text`；主循环每 5 秒 `tick()`；命中后开倒计时卡片。
4. `overlay.py`：复用现有 notice/收尾卡，60 秒倒计时 + 两个按钮；语音/输入取消。
5. 课表：`timetable.load` + 按当前课号找 slot；`end = hh:mm + minutes`（`minutes=0` 时默认 60）。
6. 环境变量：`CLASSLIVE_AUTO_END=off|notify|ask|on`（默认 `notify`），阈值可调。
7. 判据（`tests/test_auto_end.py`）：
   - 前 10 分钟不触发；
   - `past_end + quiet 5m + away` → 倒计时；
   - 倒计时中一说话就取消；
   - 没课表、只有 quiet → 不动；
   - 没课表、`quiet 15m + away + closing` → 倒计时；
   - 麦克风数字静音但人还在（`away=false`）→ 不动。

## 风险与对策

- **长静音的小组作业/考试**：只有 `past_end`、`closing` 或强 `away` 才倒计时；`hidle` 单用只提示；默认档位只提示。
- **学生安静听课、手不在键鼠上**：`hidle` 会很长，所以它**不单独触发**，只看锁屏/显示器睡/系统睡。
- **人走了但课还在放（网课/录播）**：`away` 为真、`quiet` 可能不真（有声音），不会停；若要停，得再加「系统音频在放」判断，第一版不做。
- **麦克风被静音**：`SilenceWatch` 只报「数字静音」，不单独停；人还在（`away=false`）就不动。
- **分类器误判**：只做 confirm；模型输出不确定（confidence < 0.8）按 false。
- **隐私**：分类器发的就是已经发给翻译的转写文本，不新增数据出口；`CLASSLIVE_AUTO_END=off` 可关。

## 先做哪一步

先做 **`notify` + 课表 + 静音 + 人离开**（不自动停），拿 10 节课的日志看：
- 有多少次「其实还在上课」被提示；
- 那位美国用户这种「下课走开」能不能在 5 分钟内提示到。

零误报后再开 `ask`（倒计时自动停）。
