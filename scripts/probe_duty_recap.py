#!/usr/bin/env python3
"""「一些事儿」+「课前回顾」的样子 —— 在真悬浮窗里渲染并截图（给作者看）。

    ClassLive.app/Contents/MacOS/python scripts/probe_duty_recap.py [--out DIR]

拍六张（默认写到仓库外的临时目录，`--out` 指定就用它）：
  00-baseline  全屏基线（显示器在睡 / 没录屏权限 → 拍出来是黑的，先拦下）
  01-chip      课上：字幕在滚 + 角落小卡（不挡正在变的那两行）
  02-card      点开：可编辑卡片（类型 / 标题 / 日期 + 推算依据 / 原话 / 记下·忽略）
  03-failed    记下失败：卡上写明原因（写不进提醒事项那条路）
  04-wrapup    收尾汇总：漏处理的一次性确认
  05-recap-a   课前回顾 A 态（有上节笔记 + 今天有课件）—— ⚠️ 「临近的一些事儿」里
               那条 **10-14 已经过去 → 不显示**（按今天过滤，作者 2026-10-17）
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
#: 回顾那几张的「今天」—— 回顾说的是**上一节 10-15（周四）**的课，所以今天是 10-16（周五）。
#: ⚠️ 与 `DAY` **刻意不同**：`DAY` 是卡片/收尾那几张的上课日（SOC10020 · 周一），
#:    回顾是另一门课（ECON10790）的下一节 —— 两段故事各自自洽，日期才对得上。
RECAP_DAY = datetime.date(2026, 10, 16)

DUTY_QUOTES = [
    ("There is no class on Wednesday this week.", "这周三没有课。", "13:52:56", "model", True),
    ("Next week everyone must show up because groups will be assigned "
     "for the group project.", "下周必须到场，要分组做项目。", "13:56:12", "model", False),
    ("The problem set is due Friday.", "习题集周五截止。", "14:02:56", "regex", False),
]

RECAP_A = {
    "course": "ECON10790", "date": "10-15（周四）",
    "points": [
        {"name": "停业价格", "explain": "价格低到这条线以下，开工反而比关门亏得多，企业就该停产。"},
        {"name": "驻点与曲线形状", "explain": "可导函数的极值点导数为零（驻点）；"
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
    "duties": [
        # ⚠️ 这条**不会显示**（10-14 已经过去 —— 今天 `RECAP_DAY` = 10-16）：
        #    故意留在这儿，截图里看不到它才说明「按今天过滤」真跑起来了
        #    （判据见 `tests/test_recap.py::临近的一些事儿`）。
        {"type": "调课", "title": "本周三停课", "when": "10-14（周三）",
         "date": "2026-10-14"},
        # ⚠️ 与 04-wrapup 是**同一件事**：日期/写法都按管道算出来的那份对齐
        #    （`duty_when` 从「Next week」按上课日 10-12 推 = 10-19（周一））。
        #    原来这里手写成 10-20（周二）、标题缩写成了「分组周必须到场」—— 已对齐。
        {"type": "其他", "title": "下周必须到场，要分组做项目", "when": "10-19（周一）",
         "date": "2026-10-19"},
    ],
    # ⚠️ 2026-10-17：这一块**只留课件那条**（作者砍掉了「上节课结尾提到」）。
    "today": ["今天新课件《week6-monopoly.pdf》：Monopoly · Deadweight loss"],
}


def _fit_panel(o, need_h: float) -> None:
    """把面板加高到装得下卡片内容（**探针专用**）。

    ⚠️ 卡片放不下的块会被**整块藏掉**（设计如此：宁可少显示，也不在旧 frame 上露半行）。
       截图前不加高，拍出来的「这一块没有」就会是假象（2026-10-17 实测踩过）。
       真课上内容短、或用户自己拉窗口，不需要这一步。
    """
    from AppKit import NSMakeRect
    f = o._panel.frame()
    if need_h > f.size.height:
        o._panel.setFrame_display_(
            NSMakeRect(f.origin.x, f.origin.y - (need_h - f.size.height),
                       f.size.width, need_h), True)
    o.pump()


def _clipped_blocks(o) -> list:
    """被空间裁掉的块（`kind`）—— 探针把它打出来，免得拿一张缺块的图去评审。"""
    out = []
    for blk in o._card_pending:
        v = blk["view"]
        if v is None or blk["kind"] in ("hdr_action", "sub"):
            continue
        try:
            if v.isHidden():
                out.append(blk["kind"])
        except Exception:                                     # noqa: BLE001
            pass
    return out


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
        # ⚠️⚠️ 而且**同级抢不过**：作者可能正开着另一份 ClassLive（真课堂），
        #    它的面板跟我们同级、还会自己抬自己 → 只 `orderFrontRegardless()`，
        #    区域里会拍进它半张脸。探针里把**我们自己的**窗临时抬到屏保级别
        #    （拍完还原）—— 绝不去动别人的窗。
        lvl = None
        try:
            from AppKit import NSScreenSaverWindowLevel
            lvl = o._panel.level()
            o._panel.setLevel_(NSScreenSaverWindowLevel)
        except Exception:                    # noqa: BLE001
            lvl = None
        try:
            o._panel.orderFrontRegardless()
        except Exception:                    # noqa: BLE001
            pass
        _pump(o, 0.6)
        # ⚠️ **窗稳住 + 拍两次、留第二张**（2026-10-17 实测）：卡片刚打开 / 窗刚挪位时，
        #    `screencapture -R` 会抓到过渡中的那一帧 —— 拍出过"空卡片"和"半张窗在左边"。
        #    先拍到窗口 frame 不再变，再留第二张。
        tmp = out / f"_settle-{name}.png"
        settled = False
        for _ in range(3):
            f0 = o._panel.frame()
            _shot(tmp, region=_region_of(o._panel))
            time.sleep(0.15)
            o.pump()
            if o._panel.frame() == f0:
                settled = True
                break
        if not settled:
            print(f"  ⚠ {name}：窗一直在动（4 次都没稳住）", flush=True)
        time.sleep(0.1)
        _pump(o, 0.25)
        try:
            o._panel.orderFrontRegardless()
        except Exception:                    # noqa: BLE001
            pass
        p = out / f"{name}.png"
        n = _shot(p, region=_region_of(o._panel))
        try:
            tmp.unlink()
        except OSError:
            pass
        if lvl is not None:
            try:
                o._panel.setLevel_(lvl)
            except Exception:                # noqa: BLE001
                pass
        shots.append((name, n))
        trace.append(f"{name}: {n} 字节")
        clipped = _clipped_blocks(o)
        print(f"  {name}.png {n} 字节" + (f" ⚠ 有块被裁掉：{clipped}" if clipped else ""),
              flush=True)

    with isolated_window_file():                 # ⚠️ `.window` 先备份、跑完还原
        o = overlay.Overlay(on_lost=lambda: True,
                            on_duty=lambda act, it: decisions.append((act, it.get("id"))))
        try:
            o.show()
            _pump(o, 0.6)
            o.set_session(course="SOC10020", day=DAY)
            _captions(o)
            board = duty.Board(day=DAY)
            for q, zh, t, src, ch in DUTY_QUOTES:
                # ⚠️ `zh` = 管道里**本来就有**的那份中文（正则 = 即时译文；模型 = Atom.zh）
                #    —— 卡片标题的中文摘要来自它，见 `duty.summary_zh`。
                board.offer({"kind": "deadline", "t": t, "quote": q, "zh": zh,
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

            _fit_panel(o, 780)               # 课前回顾块多：先把面板加高，别让块被裁掉
            o.recap_show(RECAP_A, course="ECON10790", day=RECAP_DAY)
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
            o.recap_show(RECAP_A, course="ECON10790", day=RECAP_DAY)
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
