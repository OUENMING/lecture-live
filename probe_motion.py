"""动效的**基线**探针: 不做任何动画时, pump 一帧到底花多少时间。

    ClassLive.app/Contents/MacOS/python probe_motion.py

## 为什么要有它

草稿上滚的动效(见 `docs/PLAN-roll-motion.md`)要在 pump 里动 frame, 所以先得知道
**还剩多少预算**。判据沿用 `probe_scroll.py` 那一套: pump 耗时 > 8.3ms 即掉 120Hz 一帧。

⚠️ 为什么不用 `probe_scroll.py` 当基线: 它是**交互式**的(要人双指滚动), 而且
`CLAUDE.md` 记着**它本身不稳定** —— 改动前的代码连跑三次, 哪一阶段收到 0 个滚轮事件
会**翻转**。基点不能建在一个会翻转的量具上。

本探针是**确定性**的: 不依赖输入设备, 四条腿跑同样的循环, 同一台机器上可复跑对比。

## 五条腿

  ① 空转                    —— 没东西变(下界)
  ② 草稿churn               —— 草稿文本按真实节奏换(约 1/s)
  ③ 草稿上滚                —— 1 行 / 2 行按**真实节奏**切(每 300 轮一次 ≈1/s)
  ⑤ 每帧改frame + 同步重绘  —— ⚠️ **这条量不了, 见下** (不动文本, 只动位置)
  ④ 答案流式                —— `answer_delta` 高频推(已知最贵的一条路, `_mark_dirty` 非紧急)

## ⚠️ ⑤ 为什么**量不了** —— 不要拿它的数字下结论

`Overlay.pump()` 自己写着(文件里那段「顺序很重要」): 它先派发事件、再 tick/flush,
**最后让步给 run loop, 让 AppKit 把标签画到屏幕**。也就是说 **真正的绘制发生在那次让步里**,
而"这次让步里到底有没有把重绘做掉"是一个**竞态**。

实测(都是同一个负载, 同一个面板):
  · 三次连跑: 超 8.3ms 的帧数 **238 / 257 / 277**
  · 另一次:   **0 / 1500**
  · 再另一次: **2 / 1500**

→ **跨会话两态**。加循环、加 `displayIfNeeded()` 都修不好 —— 因为它不是循环写得不够狠,
是**成本落不落在计时区内本来就不确定**。

→ 所以:**判据 ④(动画期间的帧时间) 只能等动画真的存在之后, 在「在屏 + 真 run loop」
条件上量**(即 `probe_scroll.py` 那套设备、或另写一个真时钟探针), 而且**必须连跑三次**。
本探针能给出的基线只有 ①–④ 那四条**内容路径**的。

⚠️ 五条的**内容负载不同**, 所以腿与腿之间**不可直接比大小** ——
它们的用处是**各自跟自己比**(改完动画后再跑一次, 看这一条腿涨了多少)。

⚠️ **tick 与 pump 分开记**: `add_draft` 是 `_mark_dirty(urgent=True)`, 它**当场**就 render
(在 tick 里); 而改 frame 的重绘会被推迟进 `pump()`。成本落在哪一段是**量出来**的, 不是猜的。

⚠️ **本探针自己也被量过一次**: 第一版 ⑤ 不加 `displayIfNeeded()`, 连跑三次得
`2 / 262 / 158`(超 8.3ms 的帧数)—— **两个数量级的差**。所以任何"基线"都要**连跑三次**
再信, 这与 `CLAUDE.md` 对 `probe_scroll.py` 的要求是同一条纪律。

## 隔离

构造真 `Overlay` 会往仓库根写 `.window`(窗口尺寸记忆)。按「测试必须隔离写端」的规矩,
这里先备份、跑完还原。**不碰** `sessions/` / `glossary/` / `.course`。
"""
from __future__ import annotations

import contextlib
import pathlib
import statistics
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FRAME_120, FRAME_60 = 8.3, 16.7          # ms; 与 probe_scroll.py 同口径

# 真实长度的草稿样例(取自 79 份真实 sessions 的实测: 中位 111 字符)
DRAFT_1LINE = "so the marginal cost is the change in total cost"
DRAFT_2LINE = ("and we can't close the library when we're renovating so we're doing our best "
               "to move stuff around and close sections")
DRAFT_ZH = "而且在翻修期间我们不能关闭图书馆，所以我们正在尽力把东西挪来挪去，并关闭部分区域"
ANSWER = "边际成本是产量增加一个单位时总成本的变化量。在短期内它由可变成本决定，" * 12


@contextlib.contextmanager
def isolated_window_file():
    f = HERE / ".window"
    saved = f.read_bytes() if f.exists() else None
    try:
        yield
    finally:
        if saved is not None:
            f.write_bytes(saved)
        elif f.exists():
            f.unlink()


def measure(o, n: int, tick=None, warmup: int = 30):
    """跑 n 轮, 返回 `(tick 耗时, pump 耗时)` 两个 ms 列表。

    ⚠️ **两个分开记**: `add_draft` 之类是 `_mark_dirty(urgent=True)`, 它**当场**就 render
    (在 tick 里), 所以只看 pump 会把成本归错地方。哪些成本落在哪一段, 是量出来的, 不是猜的。

    `tick(i)` 在每轮 pump **之前**调 —— 腿与腿的差别全在它身上。"""
    for i in range(warmup):                     # 预热: 首帧的字体加载/首排不进统计
        if tick:
            tick(-1)
        o.pump()
    tick_ms, pump_ms = [], []
    for i in range(n):
        if tick:
            t0 = time.perf_counter()
            tick(i)
            tick_ms.append((time.perf_counter() - t0) * 1e3)
        t1 = time.perf_counter()
        o.pump()
        pump_ms.append((time.perf_counter() - t1) * 1e3)
    return tick_ms, pump_ms


def _line(label: str, xs: list[float]) -> None:
    xs = sorted(xs)
    p = lambda q: xs[min(len(xs) - 1, int(len(xs) * q))]      # noqa: E731
    over120 = sum(1 for v in xs if v > FRAME_120)
    over60 = sum(1 for v in xs if v > FRAME_60)
    print(f"    {label} 中位 {statistics.median(xs):7.3f}ms  p90 {p(0.90):7.3f}  "
          f"p99 {p(0.99):7.3f}  最大 {xs[-1]:8.3f}   "
          f"超8.3ms {over120:>4}/{len(xs)}   超16.7ms {over60:>4}/{len(xs)}")


def report(name: str, tick_ms: list[float], pump_ms: list[float]) -> None:
    print(f"  {name}")
    _line("tick   ", tick_ms or [0.0])
    _line("pump   ", pump_ms)


def main() -> int:
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    import overlay

    N = 1500
    with isolated_window_file():
        o = overlay.Overlay()
        try:
            width = int(o._width)
            tier = overlay._lines_for_width(o._width)
            print("=" * 72)
            print(f"动效基线: 不做任何动画时 pump 一帧的墙钟耗时")
            print(f"  面板宽 {width}px -> 中文档位 {tier} 行;  每腿 {N} 轮(另加 30 轮预热)")
            print(f"  判据: > {FRAME_120}ms = 掉 120Hz 一帧;  > {FRAME_60}ms = 掉 60Hz 一帧")
            print("=" * 72)

            results = {}

            # ① 空转: 什么都没变
            o.add_draft(DRAFT_1LINE); o.draft_zh(DRAFT_ZH)
            results["① 空转(没东西变)"] = measure(o, N)

            # ② 草稿 churn: 每 ~600 轮换一次文本(1500 轮里换 2-3 次, 贴近真实 1/s)
            state = {"n": 0}

            def tick_churn(_i):
                state["n"] += 1
                if state["n"] % 600 == 0:
                    o.add_draft(DRAFT_2LINE if state["n"] % 1200 == 0 else DRAFT_1LINE)
                    o.draft_zh(DRAFT_ZH)
            results["② 草稿churn(约1/s 换文本)"] = measure(o, N, tick_churn)

            # ③ 草稿上滚: **按真实节奏**每 300 轮切一次(≈1/s; 不是每帧都切)
            st3 = {"n": 0}

            def tick_roll(_i):
                st3["n"] += 1
                if st3["n"] % 300 == 0:
                    o.add_draft(DRAFT_2LINE if (st3["n"] // 300) % 2 else DRAFT_1LINE)
                    o.draft_zh(DRAFT_ZH)
            results["③ 草稿上滚(每300轮切一次)"] = measure(o, N, tick_roll)

            # ⑤ **每帧改 frame + 强制同步重绘, 不动文本** —— 动画本体要加的负载
            #
            # ⚠️ **必须强制 `displayIfNeeded()`**, 否则这个量具是**飘的**: 第一版不强制,
            #    连跑三次「超 8.3ms 的帧数」是 **2 / 262 / 158** —— 差两个数量级。
            #    原因: 本循环从不转 run loop, 重绘被 AppKit 合并、**机会性**地发生,
            #    落在哪一轮不确定。强制同步之后, 被推迟的成本变成确定的, 才能当基线。
            #    (这正是 `PLAN-roll-motion.md §1.3②` 说的「先建**确定性**探针面」。)
            st5 = {"y": 0.0, "net": None}

            def tick_frame(_i):
                st5["y"] = 0.0 if st5["y"] else 12.0
                from AppKit import NSMakeRect
                for lbl, box_h, box_y in ((o._draft_lbl, overlay.DRAFT_H, o._draft_box_y_en),
                                          (o._draft_zh, overlay.DRAFT_ZH_H, o._draft_box_y_zh)):
                    lbl.setFrame_(NSMakeRect(overlay.PAD, box_y + st5["y"],
                                             o._width - 2 * overlay.PAD, box_h))
                o._panel.displayIfNeeded()      # 把推迟的重绘**当场**做掉(见上面的警告)
            results["⑤ 每帧改frame+同步重绘(面板**未显示**)"] = measure(o, N, tick_frame)

            # ⑤b 同一个负载, 但**把面板真的显示出来** —— ⚠️ 必须对比这一条:
            #     离屏的窗口 AppKit 可能**跳过真正的合成**, 拿离屏数字当基线会给出
            #     虚假的余量, 而动画在真屏上就卡了。
            #     `probe_scroll.py` 量的是**在屏**的, 所以那条能报 0.02ms。
            o._panel.setFrameOrigin_((-4000.0, 0.0))     # 先挪到屏外再显示, 不闪他
            o._panel.orderFrontRegardless()
            print(f"    (面板可见性 isVisible={bool(o._panel.isVisible())} "
                  f"onScreen={bool(o._panel.isOnActiveSpace())})")
            results["⑤b 同上但面板**已显示**"] = measure(o, N, tick_frame)
            o._panel.orderOut_(None)

            # ④ 答案流式: 高频推送(已知最贵的一条路)
            st4 = {}

            def tick_ans(i):
                st4["open"] = st4.get("open", False)
                if not st4["open"]:
                    if i % 400 == 0:
                        o._answer_reset(); o.answer_delta(ANSWER[:200]); st4["open"] = True
                elif i % 400 == 399:
                    o.answer_done("q", ANSWER); st4["open"] = False
                else:
                    o.answer_delta("边际成本是产量增加一个单位时总成本的变化量。")
            results["④ 答案流式(高频推)"] = measure(o, N, tick_ans)

            print()
            for k, (tick_ms, pump_ms) in results.items():
                report(k, tick_ms, pump_ms)

            print()
            print("=" * 72)
            print("基线已量(①–④ 内容路径)。⚠️ 腿与腿内容负载不同, **只在改完动画之后各自跟自己比**。")
            print("⚠️ ⑤ 是**两态**的, 不要把它的数字当基线 —— 原因写在文件头的 docstring 里")
            print("   (绘制发生在 pump 让步给 run loop 的那一下, 落不落在计时区内是竞态)。")
            print("=" * 72)
        finally:
            o.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
