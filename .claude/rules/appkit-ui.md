---
paths:
  - "overlay.py"
  - "transcript_view.py"
  - "panel.py"
  - "entry_panel.py"
  - "entry_launch.py"
  - "whatsnew.py"
  - "notice.py"
  - "objc_own.py"
---

# AppKit 界面（悬浮窗 / 面板 / 弹窗）

## 先读

- 外观（同心圆角 / 字号字距 / 对比度 / 材质）：`docs/RESEARCH-macos-aesthetic.md`
- 字幕行数与 roll-up（为什么 2 行、断点降级、上滚即冻结、贴底）：`docs/RESEARCH-live-caption-rollup.md`
- 草稿上滚动效（时长、四条判据、谁拥有 frame）：`docs/PLAN-roll-motion.md`
- 磨砂面板配方是 `panel.py` 唯一真源；ObjC 子类只在 `objc_own.py` 定义 —— 动手前读它们的文件头。

## 雷区

- **主线程**：AppKit 调用只能发生在主线程；`capture` / `vad` 回调必须立刻返回。
- **新交互视图要自己接管点击**：磨砂面板上的视图 `mouseDownCanMoveWindow` 默认是 `True`
  （AppKit 默认值是 `!isOpaque`），按下会拖走整个窗口。真正的触发条件是**视图没有实现 `mouseDown_`**
  （咬过两次：窗口四角缩放、`NSSplitView` 的 pane）。
  → 新视图**自己实现 `mouseDown_`**，或显式让 `mouseDownCanMoveWindow` 返回 `False`（任取其一），
  并加一条**行为**断言「回调真的触发」—— 别钉 flag 值（`overlay.ClickView` 是 `True` 且完全正常）。
  未覆盖：app 激活态（本 app 是 `.accessory` + NonactivatingPanel，正常用不到）。
- `ignoresMouseEvents` **只有 NSWindow 有**（NSView 没有）；一开穿透，面板内所有交互失效。
- 弹窗必须走 `notice._present()`（先激活、再抬到 `NSModalPanelWindowLevel`），否则模态框被自家面板盖住、整块假死。

## 判据（`scripts/check.py` 会跑 `test_panel` / `test_entry_panel`；下面是它们盖不住的）

- `tests/test_panel.py` 和**逐字冻在测试里的 `frozen_recipe`** 同进程对拍。**别改 `frozen_recipe`** ——
  它红了就是在逼你想清楚「有意改配方，还是改坏了」。它是静态摊平：live resize 盖不住。
- 拖拽接受行为（第 ⑨ 组）用桩 sender + 假 pasteboard；假 pasteboard **必须实现 `types()`**
  （`panel.dragging_entered` 的日志是提前求值的，在 `_call` 的 try 外面）。
- `tests/test_entry_panel.py` 只钉纯函数那半（卡片文字、计数同源、高度与坐标同源、零参数入口退出码 0/1/2/3）。
  写断言先问「改坏实现它会不会红」（这个文件被抓出过 4 条没区分能力的）。
- 只能手验：真实 Finder 拖拽 `scripts/probe_drag.py`、课程面板 `scripts/probe_entry_panel.py`（隔离模式）、
  live resize、动效基线 `scripts/probe_motion.py`（确定性）。
- 布局或滚动：`scripts/probe_scroll.py`。⚠️ **它自己会翻转**（同一份代码连跑三次，恰好一个阶段收到 0 个滚轮事件，
  失败阶段会换）—— ❌ 先连跑两三次、或 `git stash` 对拍改动前，稳定挂在同一阶段才是真回归。
- 测试模式开关的拍图：`scripts/probe_test_mode.py`（一个字节不写盘）。UI 改动推送前先拍图给作者看。
