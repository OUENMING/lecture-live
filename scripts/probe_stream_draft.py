#!/usr/bin/env python3
"""定稿流式那一两秒里悬浮窗的样子：改前 / 改后各拍一张。

    ClassLive.app/Contents/MacOS/python scripts/probe_stream_draft.py

场景（10-09 真课里 81% 的段都这样）：上一段的定稿正在流式吐中文，老师已经讲到下一段，
下一段的英文草稿和草稿译文都到了。
- 改前：草稿栏整块藏起来（`_streaming` 一律藏），实时行上面那行英文是空的（EN 在 ZH 之后才流）。
- 改后：`draft_over_stream(True)` → 草稿栏照常显示；`seed_en(原句)` → 英文行先放 ASR 原句。

⚠️ 只建 Overlay，不跑 main，一个字节都不写盘。⚠️ 先拍基线：显示器在睡时截图是纯黑。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_hint import _pump, _region_of, _shot  # noqa: E402

RAW = "so for a monopolist marginal revenue is below the price"
DRAFT_EN = "Now let's see what that means for the quantity the monopolist"
DRAFT_ZH = "现在我们看看这对垄断者的产量意味着什么"


def main() -> int:
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        from overlay import Overlay
    except Exception as e:                                    # noqa: BLE001
        print(f"⚠ 起不来：{type(e).__name__}: {e}")
        return 1
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    out = pathlib.Path(tempfile.mkdtemp(prefix="probe_stream_draft_"))
    base = _shot(out / "00-baseline.png")
    print(f"基线截图 {base} 字节", flush=True)

    bad = 0
    for name, new in (("01-改前.png", False), ("02-改后.png", True)):
        o = Overlay(on_lost=lambda: True)                     # 桩：不落盘
        o.show()
        o.finalize("Marginal cost is the cost of producing one more unit.",
                   "边际成本是多生产一单位的成本。")
        o.add_draft(DRAFT_EN)
        o.draft_zh(DRAFT_ZH)
        if new:
            o.seed_en(RAW)
        o.stream_zh("所以对垄断者来说，边际收益")
        if new:
            o.draft_over_stream(True)
        _pump(o)
        n = _shot(out / name, region=_region_of(o._panel))
        print(f"{name} {n} 字节 -> {out / name}", flush=True)
        if n == base:
            print("⚠ 与基线同字节 = 很可能是空图")
            bad = 1
        o.close()
    return bad


if __name__ == "__main__":
    sys.exit(main())
