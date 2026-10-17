#!/usr/bin/env python3
"""D 布局的回放探针：把一段 6 句的课堂按真实节奏喂给真 `Overlay`，关键时刻拍图。

    ClassLive.app/Contents/MacOS/python scripts/probe_d_layout.py
    ClassLive.app/Contents/MacOS/python scripts/probe_d_layout.py --frames-every 0.05 --from-s 3 --to-s 6

## 它演什么（一段 6 句的经济学课堂，垄断那一节）

按**真实节奏**驱动 `Overlay` 的公开接口（与 `main.drain()` 同款 —— 不碰内部状态，
所以 D 视图怎么改，探针都不用动）：
  · `add_draft` 逐词长出来（每 0.25s 一个词）—— 草稿先占住最底那一行（"当前卡"）
  · `draft_zh` 约 1s 更新一次（先部分、后全句）
  · `seed_en` + `stream_zh`（每 0.05s 2 个字）—— 这一段的定稿**就地**升级那一行
  · `finalize`（`clear_draft` 按"屏上有没有更新一段的草稿"给，与 `main` 的 DraftView 同款）
  · `draft_over_stream(True)` 在「上一段还在流式、下一段草稿已到」时置真
  · 第 2 / 5 句的草稿**插进上一段流式的中间**开始长 —— 真课里 81% 的段都这样（10-09 实测）

## 拍什么

每句 6 张：草稿刚出现 / 草稿译文到 / 定稿开始流式（桥生效的那几帧）/ 定稿流到一半 /
finalize 后 0.1s / finalize 后 0.4s。拍完用 PIL 拼一张**竖排联系表** `sheet.png`，
连同目录一起打印出来。`--frames-every S --from-s X --to-s Y`：在 X–Y 秒之间每 S 秒
连拍（看动效用，另存 `frames/`，不进联系表）。

⚠️ 截图本身很贵（一次 `screencapture` ≈0.1s）。连拍时**截图耗掉的时间不计入回放时间轴**
   （否则课堂被拖长、动效看着变慢）——回放的总时长只数事件之间的等待；实际帧率受截图
   限制，跑完会打出来（0.05s 的节拍在这里**拍不满**，别拿它当帧率判据）。

## 隔离（照 probe_motion.py）

构造真 `Overlay` 会往仓库根写 `.window`（窗口尺寸记忆）：先备份、跑完还原。
⚠️ 不跑 `main`、回调全是桩、**不碰** `sessions/` 与 `~/.classlive/`。
⚠️ 先拍一张全屏基线：显示器在睡 / 没开屏幕录制权限时，`screencapture` 拍出来是纯黑。
   基线字节数太小或整图单色 → 退出并提示（别把黑图当成"界面没渲染出来"）。

⚠️ 本探针**不做断言**：它是给出图的地方（照 `show-before-push`），红了说明起不来。
   现在拍的还是旧布局 —— `caption_flow` 的判据刚落地、还没接进 `_render`（Claude 在接）。
"""
from __future__ import annotations

import argparse
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import typing

# ⚠️ 本文件在 `scripts/` 下，仓库根是**上一级**（`.parent` 会指向 scripts/）。
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_hint import _pump, _region_of, _shot                        # noqa: E402
from probe_motion import isolated_window_file                          # noqa: E402

# ---- 节奏（任务给定；改这里会改整段回放的时间轴）----
WORD_S = 0.25          # 草稿英文：每 0.25s 一个词
STREAM_S = 0.05        # 定稿中文：每 0.05s 2 个字
CHUNK = 2
DRAFT_ZH_S = 1.0       # 草稿译文约 1s 一版
#: 草稿吐完 → 定稿开始流式。真课里这段（段尾 → 定稿中文首字）p50 已等 4s；这里取短一点，
#: 图更好读（本探针不是量延迟的，量延迟的是 `tr_latency` / `stream_eval`）。
FINAL_LAG_S = 1.0
FINALIZE_LAG_S = 0.15  # 最后一个字 → finalize
SEG_GAP_S = 0.4        # 上一段定稿 → 下一段草稿（不重叠的那些）
#: 下一段草稿在第几成流式处插进来（只给 OVERLAP_NEXT 那几句）。
OVERLAP_AT = 0.45

#: 第 2 / 5 句（0-based 1 / 4）的草稿插进上一段流式的中间 —— 演「上一段还在流式」。
OVERLAP_NEXT = {1, 4}

#: 全屏基线小于这么多字节 = 很可能是纯黑（真桌面实拍 ~2MB，纯黑 ~10KB）。
MIN_BASELINE_BYTES = 60_000


class Sent(typing.NamedTuple):
    """一句话的五个文本。草稿（≈即时识别）+ 定稿（ASR 原句 / 修正版 / 两版译文）。"""
    draft_en: str
    seed_en: str
    final_en: str
    draft_zh: str
    final_zh: str


LECTURE: list[Sent] = [
    Sent("today we are going to look at how a monopolist chooses",
         "today we are going to look at how a monopolist chooses output",
         "Today we are going to look at how a monopolist chooses output.",
         "今天我们要看垄断者如何选择产量",
         "今天我们要看的是，垄断者如何选择产量。"),
    Sent("a monopolist is the only seller in the market",
         "a monopolist is the only seller in the market",
         "A monopolist is the only seller in the market.",
         "垄断者是市场上唯一的卖家",
         "垄断者是市场上唯一的卖家。"),
    Sent("so it faces the entire market demand curve by itself",
         "so it faces the entire market demand curve by itself",
         "So it faces the entire market demand curve by itself.",
         "所以它独自面对整个市场的需求曲线",
         "所以它独自面对整条市场需求曲线。"),
    Sent("that means marginal revenue is always below the price",
         "that means marginal revenue is always below the price",
         "That means marginal revenue is always below the price.",
         "这意味着边际收益总是低于价格",
         "这意味着边际收益总是低于价格。"),
    Sent("so the monopolist produces less than a competitive market would",
         "so the monopolist produces less than a competitive market would",
         "So the monopolist produces less than a competitive market would.",
         "所以垄断者的产量低于竞争市场",
         "所以垄断者的产量低于一个竞争市场会有的产量。"),
    Sent("and that gap is what we call the deadweight loss",
         "and that gap is what we call the deadweight loss",
         "And that gap is what we call the deadweight loss.",
         "这个差距就是无谓损失",
         "而这个差距就是我们所说的无谓损失。"),
]


class Script:
    """一段脚本化课堂：`events` 是「到点了要调哪个方法」，`shots` 是「到点了拍哪张」。"""

    def __init__(self) -> None:
        self.events: list[tuple[float, str, str, tuple, dict]] = []
        self.shots: list[tuple[float, str, bool]] = []

    def at(self, t: float, label: str, meth: str, args: tuple = (), kw: dict | None = None):
        self.events.append((t, label, meth, args, kw or {}))

    def shot(self, t: float, name: str, *, frame: bool = False):
        """`frame=True` 的是连拍帧：进 `frames/`、不进联系表。"""
        self.shots.append((t, name, frame))

    def sort(self) -> None:
        """按时间排好，并**按时间顺序重编图名序号**（重叠段里 build 的顺序会交叉）。"""
        self.events.sort(key=lambda e: e[0])
        self.shots.sort(key=lambda e: e[0])
        self.shots = [(t, f"{k:02d}-{name}", fr)
                      for k, (t, name, fr) in enumerate(self.shots, 1)]


def build(lecture: list[Sent]) -> Script:
    """把 6 句排成时间轴。返回的 `events` / `shots` 都会按时间排好（本函数里就有序）。"""
    sc = Script()
    cursor = 0.0        # 上一段的时间轴终点（它的 finalize）
    prev = None
    for i, s in enumerate(lecture):
        words = s.draft_en.split()
        n_words = len(words)
        if prev is not None and i in OVERLAP_NEXT:
            # 上一段还在流式、下一段的草稿已经到 —— 插进它的流式中间。
            t_draft = prev["t_stream"] + OVERLAP_AT * prev["stream_dur"]
            # `main` 同款：新一段的第一份英文草稿到 → 先清掉上一段残留的草稿译文。
            sc.at(t_draft, f"s{i+1} draft_over_stream(True)", "draft_over_stream", (True,))
            sc.at(t_draft, f"s{i+1} draft_zh('')", "draft_zh", ("",))
        else:
            t_draft = cursor + (SEG_GAP_S if prev is not None else 0.0)
        for k in range(n_words):
            sc.at(t_draft + k * WORD_S, f"s{i+1} draft[{k + 1}/{n_words}]",
                  "add_draft", (" ".join(words[:k + 1]),))
        # 草稿译文：词的中途先给一版部分的（前 2/3），吐完给全句。
        t_zh1 = t_draft + (n_words // 2) * WORD_S
        sc.at(t_zh1, f"s{i+1} draft_zh(部分)", "draft_zh",
              (s.draft_zh[: max(1, len(s.draft_zh) * 2 // 3)],))
        t_draft_end = t_draft + (n_words - 1) * WORD_S
        sc.at(t_draft_end + DRAFT_ZH_S * 0.3, f"s{i+1} draft_zh(全句)", "draft_zh", (s.draft_zh,))
        # 定稿：ASR 原句占位 → 中文每 0.05s 2 个字 → finalize。
        t_stream = t_draft_end + FINAL_LAG_S
        sc.at(t_stream, f"s{i+1} seed_en", "seed_en", (s.seed_en,))
        n_chunks = max(1, math.ceil(len(s.final_zh) / CHUNK))
        for k in range(n_chunks):
            sc.at(t_stream + k * STREAM_S, f"s{i+1} stream[{k + 1}/{n_chunks}]",
                  "stream_zh", (s.final_zh[k * CHUNK:(k + 1) * CHUNK],))
        stream_dur = (n_chunks - 1) * STREAM_S
        t_fin = t_stream + stream_dur + FINALIZE_LAG_S
        # 屏上已是**更新一段**的草稿时旧段定稿不许擦它（与 `DraftView.on_final` 同款）。
        clear = (i + 1) not in OVERLAP_NEXT
        sc.at(t_fin, f"s{i+1} finalize(clear_draft={clear})", "finalize",
              (s.final_en, s.final_zh), {"clear_draft": clear})
        if not clear:
            sc.at(t_fin + 0.05, f"s{i+1} draft_over_stream(False)", "draft_over_stream", (False,))
        # 关键时刻。
        sc.shot(t_draft + 0.15, f"s{i+1}-draft")                       # 草稿刚出现
        sc.shot(t_zh1 + 0.15, f"s{i+1}-draft_zh")                      # 草稿译文到
        sc.shot(t_stream + 2 * STREAM_S + 0.05, f"s{i+1}-stream_start")  # 定稿开始流（桥）
        sc.shot(t_stream + (n_chunks // 2) * STREAM_S + 0.05, f"s{i+1}-stream_half")
        sc.shot(t_fin + 0.1, f"s{i+1}-finalize+0.1")
        sc.shot(t_fin + 0.4, f"s{i+1}-finalize+0.4")
        prev = {"t_stream": t_stream, "stream_dur": stream_dur, "t_fin": t_fin}
        cursor = t_fin
    sc.sort()
    return sc


# ---- PIL 只有 homebrew 的 python3 有；App 自带的 python 没有 ----
_PIL_CODE = r"""
import pathlib, sys
from PIL import Image, ImageDraw
outdir = pathlib.Path(sys.argv[1])
paths = [p for p in sys.argv[2:]]
W, PAD, BAR = 520, 8, 20
imgs = []
for p in paths:
    im = Image.open(p).convert("RGB")
    if im.width > W:
        im = im.resize((W, max(1, round(im.height * W / im.width))), Image.LANCZOS)
    imgs.append((pathlib.Path(p).stem, im))
H = PAD + sum(im.height + BAR + PAD for _, im in imgs)
sheet = Image.new("RGB", (W + 2 * PAD, H), (26, 26, 28))
d = ImageDraw.Draw(sheet)
y = PAD
for name, im in imgs:
    d.rectangle([PAD, y, PAD + W, y + BAR - 3], fill=(58, 58, 64))
    d.text((PAD + 5, y + 3), name, fill=(236, 236, 240))
    y += BAR
    sheet.paste(im, (PAD, y))
    y += im.height + PAD
sheet.save(outdir / "sheet.png")
print(f"{outdir / 'sheet.png'} {sheet.size[0]}x{sheet.size[1]} 格数={len(imgs)}")
"""

_EXTREMA_CODE = r"""
import sys
from PIL import Image
lo, hi = Image.open(sys.argv[1]).convert("L").getextrema()
print(f"{lo} {hi}")
"""


def _pil_python() -> str | None:
    """找一个装了 PIL 的解释器（本仓库的运行环境里没有）。不联网、不装包。"""
    for cand in (sys.executable, shutil.which("python3"), "/opt/homebrew/bin/python3",
                 "/usr/bin/python3"):
        if not cand or not pathlib.Path(cand).exists():
            continue
        try:
            p = subprocess.run([cand, "-c", "import PIL"], capture_output=True, timeout=30)
        except Exception:                                        # noqa: BLE001
            continue
        if p.returncode == 0:
            return cand
    return None


def _pil_run(py: str, code: str, *args: str) -> str:
    p = subprocess.run([py, "-c", code, *args], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        tail = (p.stderr.strip().splitlines() or ["?"])[-1]
        print(f"⚠ PIL 那一步失败：{tail}")
        return ""
    return p.stdout.strip()


def run(o, sc: Script, out: pathlib.Path, trace: list[str]) -> list[tuple[str, int]]:
    """按**绝对时间**回放：到点派事件、到点拍图，其余时间都在 `o.pump()`。

    ⚠️ 截图（含"把这一帧画出来"的那一下 pump）耗掉的时间**加回到 t0**上（`capture_s`）
       —— 否则连拍会把课堂整个拖长、动效看起来变慢。也就是说：回放时长只数事件之间的
       等待，不数拍图。
    """
    t0 = time.monotonic()
    capture_s = 0.0
    i = j = 0
    taken: list[tuple[str, int]] = []
    evs, shots = sc.events, sc.shots
    while i < len(evs) or j < len(shots):
        now = time.monotonic() - t0 - capture_s
        while i < len(evs) and evs[i][0] <= now:
            t, label, meth, args, kw = evs[i]
            i += 1
            getattr(o, meth)(*args, **kw)
            trace.append(f"{t:7.2f}s  {label}")
        if j < len(shots) and shots[j][0] <= now:
            t, name, frame = shots[j]
            j += 1
            c0 = time.monotonic()
            _pump(o, 0.03)                    # 刚改完还没画上屏 —— 先把它画出来
            path = out / (f"frames/{name}.png" if frame else f"{name}.png")
            path.parent.mkdir(exist_ok=True)
            n = _shot(path, region=_region_of(o._panel))
            capture_s += time.monotonic() - c0
            taken.append((name, n))
            trace.append(f"{t:7.2f}s  📷 {name}  {n} 字节")
            if n <= 0:
                print(f"⚠ 截图没落盘：{name}")
        o.pump()
        time.sleep(0.001)
    print(f"回放（含截图）{time.monotonic() - t0:.1f}s；其中截图本身占 {capture_s:.1f}s")
    return taken


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--frames-every", type=float, default=0.0, metavar="S",
                    help="连拍：每 S 秒一张（要配 --from-s / --to-s）")
    ap.add_argument("--from-s", type=float, default=None, metavar="X", help="连拍起（回放秒）")
    ap.add_argument("--to-s", type=float, default=None, metavar="Y", help="连拍止（回放秒）")
    ap.add_argument("--no-sheet", action="store_true", help="不拼联系表（只留原图）")
    a = ap.parse_args()

    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        import overlay
    except Exception as e:                                       # noqa: BLE001
        print(f"⚠ 起不来：{type(e).__name__}: {e}")
        return 1
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)

    out = pathlib.Path(tempfile.mkdtemp(prefix="probe_d_layout_"))
    sc = build(LECTURE)
    if a.frames_every and a.from_s is not None and a.to_s is not None:
        t = a.from_s
        while t <= a.to_s:
            sc.shot(t, f"frame-{t:07.3f}", frame=True)
            t += a.frames_every
        sc.sort()
    end = max(t for t, *_ in sc.events)
    n_frames = sum(1 for _, _, fr in sc.shots if fr)
    print(f"脚本：{len(LECTURE)} 句 / {len(sc.events)} 个事件 / "
          f"{len(sc.shots) - n_frames} 张关键图 + {n_frames} 张连拍；"
          f"回放时长约 {end:.1f}s（不含截图开销）")
    print(f"布局：LAYOUT_D={getattr(overlay, 'LAYOUT_D', '?')} "
          f"CLASSLIVE_LAYOUT={os.environ.get('CLASSLIVE_LAYOUT', '(未设)')}")

    py = _pil_python()
    print(f"PIL：{'没有（联系表会跳过）' if py is None else py}")

    base = _shot(out / "00-baseline.png")
    print(f"基线（全屏）{base} 字节", flush=True)
    if base < MIN_BASELINE_BYTES:
        print(f"⚠ 基线只有 {base} 字节（< {MIN_BASELINE_BYTES}）：显示器在睡 / 没开屏幕录制权限，"
              f"拍下去也是纯黑。等屏幕亮起来再跑。")
        return 1
    if py is not None:
        lo_hi = _pil_run(py, _EXTREMA_CODE, str(out / "00-baseline.png")).split()
        if len(lo_hi) == 2 and lo_hi[0] == lo_hi[1]:
            print(f"⚠ 基线是**单色**图（灰度极值 {lo_hi[0]}..{lo_hi[1]}）—— 同上的原因，退出。")
            return 1
        print(f"基线灰度极值 {lo_hi}（0..255 且有差别 = 真的拍到了屏幕）")

    trace: list[str] = []
    with isolated_window_file():            # ⚠️ `.window` 先备份、跑完还原
        o = overlay.Overlay(on_lost=lambda: True)     # 桩：不落盘
        try:
            o.show()
            _pump(o, 0.6)                   # 建完立刻截会拍到空白
            taken = run(o, sc, out, trace)
        finally:
            o.close()

    bad = [n for n, sz in taken if sz <= 0]
    frames = [n for n, _ in taken if n.split("-", 1)[1].startswith("frame")]
    print(f"\n拍了 {len(taken)} 张（其中连拍 {len(frames)} 张）。")
    if py is None:
        print("⚠ 没找到带 PIL 的解释器 → 没拼联系表（原图都在上面那个目录里）")
    elif not a.no_sheet:
        keys = [str(out / f"{n}.png") for n, _ in taken
                if not n.split("-", 1)[1].startswith("frame")]
        print("联系表：" + _pil_run(py, _PIL_CODE, str(out), *keys))
    (out / "trace.txt").write_text("\n".join(trace) + "\n", encoding="utf-8")
    print(f"输出目录 {out}")
    print(f"事件轨迹（每一行 = 到点做了什么）{out / 'trace.txt'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
