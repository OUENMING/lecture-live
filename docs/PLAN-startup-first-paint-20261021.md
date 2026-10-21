# 起步首屏（P0）与细粒度 trace（P4）— 2026-10-21

## 现象与已有测量

2026-10-21 14:09 双击 `.app`、点「开始上课」的首次启动：

```
点开始 → 字幕出现 23.0 s
  开麦试探 8.5
  草稿模型 0.2
  翻译与术语 13.7
  定稿 / 落盘 / 悬浮窗 0.3
```

同一份代码 14:14 热启动 **2.9 s**；隔离进程（`--source file`，静音 wav）**0.4 s**。
隔离测量里 `load_translator`、`term_zh_map`、`load_api_key` 都是 0.0–0.1 s；
`EngineRouter.warmup()` 在 `auto` 模式**不加载本地 MLX 模型**。

**结论（截至本文件）：**
- 13.7 s 不是本地翻译器加载；具体卡在“翻译与术语”段里的哪一子步，现有日志只到段级，**未定**。
- 首跑 23 s 不可复现；冷页缓存 / 当时的系统负载都可能贡献，不能凭一次样本断言。
- 用户体验的确定性问题不是“初始化到底几秒”，而是**初始化全部做完才建字幕窗**，期间屏幕空白。

## 方向性调研（外部依据）

| 结论 | 来源 | 抓取日期 / 发布 | 置信度 |
|---|---|---|---|
| macOS 不要求 launch screen；应用应尽快可交互；品牌 splash 只适合 onboarding | [Apple HIG — Launching](https://developer.apple.com/design/human-interface-guidelines/launching) | 页面 changelog 2024-06-10；抓取 2026-10-21 | 高（canonical） |
| 加载时能显示东西就先显示；用 placeholder / 文案并明确告知正在加载 | [Apple HIG — Loading](https://developer.apple.com/design/human-interface-guidelines/loading) | 页面 changelog 2025-06-09；抓取 2026-10-21 | 高（canonical） |
| 第一帧目标 400 ms；与第一帧无关的工作要推迟，阻塞工作移出主线程 | [WWDC19 — Optimizing App Launch](https://developer.apple.com/videos/play/wwdc2019/423/)（slides 第 164、1330–1340 行） | 2019；抓取 2026-10-21 | 高（Apple 一手） |

**方向判定：** 不做品牌 splash，也不把全部初始化硬塞到首帧前；先显示一个**功能性首屏**（磨砂小窗 + “正在准备…”），再把慢初始化留在后面跑，真悬浮窗建好后替换。这个方向同时满足 HIG 的“先显示”和 WWDC 的“首帧优先”。

## 为什么先做小窗，不直接把完整 Overlay 提前

`Overlay.__init__` 需要 `writer` / `tester` / `live` 以及一串回调（`submit_question`、`_on_lost_pressed`、`_duty_decided`、`start_new_topic` 等）。这些对象/闭包目前都在翻译初始化之后才定义；提前建完整 Overlay 要重排近千行，风险大于收益。首屏小窗只需要 `panel.build`，是一个独立的小 seam，先把“看不见”解决；后续 P1/P2 再把初始化移出关键路径。

## 已实现（P0）

- 新增 `startup_hud.py`：
  - `enabled(ui_mode)`：只在 `--ui overlay` 且未设 `CLASSLIVE_NO_STARTUP_HUD=1` 时启用。
  - `StartupHUD.show()`：建一个不可交互的磨砂小窗，`orderFrontRegardless()`；
    只有 `CLASSLIVE_FROM_APP` 时激活一次，终端/测试/探针不抢焦点。
  - `note(text)`：更新标题；`close()`：幂等关闭。
  - AppKit/窗口失败全部 fail-soft，返回 `None`，不拦上课。
- `main.run()`：在麦克风权限检查之后、`load_source()` 开麦试探之前显示首屏；
  在真悬浮窗建好后关闭。开麦失败/模型缺失提前 return 时随进程退出，不会留下窗口。
- `startup_timing()`：新增 `hud_s`（从 run 起算的绝对偏移），日志行加“首屏 x.x”；
  不插进原来的 audio→draft→translator→overlay 链。

## 已实现（P4）

`CLASSLIVE_STARTUP_TRACE=1` 时，`main.run()` 每步打一行：

```
⏱ trace hud: +0.169s (T+0.169s)
⏱ trace audio_probe: +0.060s (T+0.229s)
⏱ trace draft / corpus / term_zh / local_translator / api_key / cloud_translator
⏱ trace translator_done / final_asr / writer_tester_live / translator_warmup / overlay
```

2026-10-21 隔离热启动实测（静音 wav，`--ui overlay`）：

```
首屏 0.17 · 开麦试探 0.06 · 草稿 0.01 · 翻译与术语 0.20 · 悬浮窗 0.10
总 0.57 s
```

下次真实首跑就能看到 13.7 s 落在哪一子步，不再靠猜。

## 验证

- `tests/test_startup_hud.py`：开关 + `note`/`close` 幂等（不建真窗）。
- `tests/test_startup_timing.py`：`hud_s` 用绝对偏移，不破坏原有链。
- `tests/test_pump_idle.py`：静态接线判据 —— 首屏在 `load_source` 之前，
  在 `_tp['overlay']` 之后关闭；trace 开关存在。
- `scripts/probe_startup_hud.py`：真窗口实拍，输出三张图（首屏 / 更新状态 / 关闭后）。
- 实拍目录：`~/Desktop/classlive-review/20261021-startup-hud/`（由探针复制）。

## 还没做

- 真实冷启动（清页缓存 / 重启后第一次）复测；用 trace 定位 13.7。
- P1：开麦试探复用句柄或移出关键路径（8.5 s）。
- P2：翻译/术语初始化与首屏之后并行；首句翻译等它。
- P3：入口面板阶段预热（跨进程缓存 / helper），点开始时直接复用。
