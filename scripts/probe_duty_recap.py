#!/usr/bin/env python3
"""「一些事儿」+「课前回顾」的样子 —— 在真悬浮窗里渲染并截图（给作者看）。

    ClassLive.app/Contents/MacOS/python scripts/probe_duty_recap.py [--out DIR]

拍六张（默认写到仓库外的临时目录，`--out` 指定就用它）：
  00-baseline  全屏基线（显示器在睡 / 没录屏权限 → 拍出来是黑的，先拦下）
  01-chip      课上：字幕在滚 + 角落小卡（不挡正在变的那两行）
  02-card      点开：可编辑卡片（类型 / 标题 / 日期 + 推算依据 / 原话 / 记下·忽略）
  03-failed    记下失败：卡上写明原因（写不进提醒事项那条路）
  04-wrapup    收尾汇总：漏处理的一次性确认
  05-recap-a   课前回顾 A 态（有上节笔记 + 今天有课件）
  06-recap-b   课前回顾 B 态（没传课件 ——「今天可能讲什么」整块不出现）
  07-first     首节课：`recap_show({})` **什么都不弹**（C1）

## 两条纪律（照 `probe_d_layout` / `probe_test_mode`）

⚠️ **只建 Overlay，不跑 main** —— 回调全是桩：`on_duty` 只打印、
   `on_lost` 返回 True，一个字节都不写 `sessions/` / `~/.classlive/` / vault。
⚠️ **先拍基线**：纯黑或单色 → 退出（那不是"界面没渲染出来"，是屏幕没醒）。
   `.window`（窗口尺寸记忆）备份后还原 —— 同 `probe_motion.isolated_window_file`。
"""
from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_hint import _pump, _region_of, _shot                    # noqa: E402
from probe_motion import isolated_window_file                      # noqa: E402

#: 全屏基线小于这么多字节 = 很可能纯黑（真桌面实拍 ~2MB，纯黑 ~10KB）。
MIN_BASELINE_BYTES = 60_000

DAY = datetime.date(2026, 10, 12)      # 周一（SOC10020 那节，原型里的真实日期）

DUTY_QUOTES = [
    ("There is no class on Wednesday this week.", "13:52:56", "model", True),
    ("Next week everyone must show up because groups will be assigned "
     "for the group project.", "13:56:12", "model", False),
    ("The problem set is due Friday.", "14:02:56", "regex", False),
]

RECAP_A = {
    "course": "ECON10790", "date": "10-15（周四）",
    "points": [
        {"name": "停业价格", "explain": "价格低到这条线以下，开工反而比关门亏得多，企业就该停产。"},
        {"name": "驻点与曲线形状", "explain": "导数等于零的点是驻点，极值只可能出现在驻点或边界上；"
                                        "曲线往上弯还是往下弯由二阶导数决定。"},
        {"name": "二阶导数检验", "explain": "在驻点上看二阶导数：往上弯是谷底，往下弯是山顶。"},
        {"name": "平均成本与边际成本", "explain": "平均成本是把总花销摊到每一件上；"
                                            "边际成本是多生产一件要多花的钱。"},
        {"name": "边际与平均的互动", "explain": "新一件比现在的平均便宜，就把平均往下拉；贵就往上推。"},
        {"name": "最小化平均成本的两条路", "explain": "找最低点有两条等价的路：曲线斜率归零，"
                                              "或边际成本正好等于平均成本。"},
    ],
    "stars": ["听到「边际」就想「多一件」：边际成本就是多生产一件要多花的钱。",
              "平均成本最低的地方，边际成本正好和它相等。",
              "曲线往上弯是谷、往下弯是峰：方向由二阶导数定。"],
    "open": ["13:42 那句没听懂：二阶导数的符号怎么定曲线的凹凸"],
    "duties": [{"type": "调课", "title": "本周三停课", "when": "10-14（周三）"},
               {"type": "其他", "title": "分组周必须到场", "when": "10-20（周二）"}],
    "today": ["① 今天新课件《week6-monopoly.pdf》：Monopoly · Deadweight loss",
              "② 上节课结尾提到：「these three are very difficult to process」"],
}


def _captions(o) -> None:
    """铺三句字幕（与 D 布局探针同一套公开接口）。"""
    o.finalize("I've gotten the research done over the last few weeks.",
               "过去几周我已经把研究做完了。")
    o.finalize("There is no class on Wednesday this week.",
               "这周三没有课。")
    o.add_draft("everyone gets attendance for this wee")
    o.draft_zh("这一周的出勤照记")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="", help="截图目录（默认 tempdir）")
    a = ap.parse_args()

    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        import overlay
        import duty
    except Exception as e:                                       # noqa: BLE001
        print(f"⚠ 起不来：{type(e).__name__}: {e}")
        return 1
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    out = pathlib.Path(a.out) if a.out else pathlib.Path(
        tempfile.mkdtemp(prefix="probe_duty_recap_"))
    out.mkdir(parents=True, exist_ok=True)

    base = _shot(out / "00-baseline.png")
    print(f"基线（全屏）{base} 字节", flush=True)
    if base < MIN_BASELINE_BYTES:
        print(f"⚠ 基线只有 {base} 字节（< {MIN_BASELINE_BYTES}）：显示器在睡 / "
              f"没开屏幕录制权限，拍下去也是纯黑。等屏幕亮起来再跑。")
        return 1

    shots: list = []
    trace: list[str] = []
    decisions: list = []

    def shot(o, name: str) -> None:
        # ⚠️ 每次拍之前把窗抬到最前：面板是 NonactivatingPanel（从不激活 app），
        #    别的窗口盖在它上面时，`screencapture -R` 拍到的就是**别人的窗口** ——
        #    2026-10-17 实测踩过（拍出来的是一张课程面板）。
        try:
            o._panel.orderFrontRegardless()
        except Exception:                    # noqa: BLE001
            pass
        _pump(o, 0.5)
        p = out / f"{name}.png"
        n = _shot(p, region=_region_of(o._panel))
        shots.append((name, n))
        trace.append(f"{name}: {n} 字节")
        print(f"  {name}.png {n} 字节", flush=True)

    with isolated_window_file():                 # ⚠️ `.window` 先备份、跑完还原
        o = overlay.Overlay(on_lost=lambda: True,
                            on_duty=lambda act, it: decisions.append((act, it.get("id"))))
        try:
            o.show()
            _pump(o, 0.6)
            o.set_session(course="SOC10020", day=DAY)
            _captions(o)
            board = duty.Board(day=DAY)
            for q, t, src, ch in DUTY_QUOTES:
                board.offer({"kind": "deadline", "t": t, "quote": q,
                             "src": [abs(hash(q)) % 900 + 10], "source": src,
                             "changed": ch})
            o.duty_update(board.items())
            shot(o, "01-chip")

            round1 = board.items()
            o._chip_click()                      # 点小卡 = 打开最新的那条
            shot(o, "02-card")

            # 真流程：先落决定（旁路文件），再让 worker 去写提醒事项 → 回执
            rid = round1[-1]["id"]
            board.decide(rid, "saved")
            o.duty_update(board.items())
            o.duty_reminder_result(rid, False,
                                   "系统没允许 ClassLive 控制提醒事项"
                                   "（系统设置 › 隐私与安全性 › 自动化）")
            shot(o, "03-failed")

            o._card_close()
            o.duty_wrapup_show(round1)
            shot(o, "04-wrapup")
            o.duty_wrapup_finish()

            o.recap_show(RECAP_A, course="ECON10790", day=DAY)
            shot(o, "05-recap-a")
            no_slides = {k: v for k, v in RECAP_A.items() if k != "today"}
            o.recap_update({"today": []})
            o._recap_data = no_slides
            o._recap_fill()
            shot(o, "06-recap-b")
            o._recap_dismiss()
            o.recap_show({})                     # 首节课：什么都不弹（C1）
            shot(o, "07-first")
            # 教授开口 → 自动淡出（再拍一张，证明让位了）
            o.recap_show(RECAP_A, course="ECON10790", day=DAY)
            _pump(o, 0.3)
            o.add_draft("good afternoon everyone today we start")
            _pump(o, 0.6)
            shot(o, "08-recap-auto-hide")
        finally:
            try:
                o.close()
            except Exception:                    # noqa: BLE001
                pass

    bad = [n for n, sz in shots if sz <= 0]
    print(f"\n拍了 {len(shots)} 张 → {out}")
    print(f"动作轨迹：{decisions}")
    miss = [k for k in ("01-chip", "02-card", "03-failed", "04-wrapup",
                        "05-recap-a", "06-recap-b", "07-first", "08-recap-auto-hide")
            if k not in {n for n, _ in shots}]
    if miss:
        print(f"⚠ 少拍了：{miss}")
    if bad:
        print(f"⚠ 空图：{bad}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
