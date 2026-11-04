"""收尾进度模型 —— **纯函数**：阶段 → 百分比 / 阶段清单 / ETA。

为什么要有它（`docs/PLAN-note-background-20261103.md` §6）：卡片上原来只有一句
「正在生成笔记…」，既没有真实进度、也没有「还要多久」。而进度**必须是真的**：
Nielsen（2026-08）—— 假百分比比没有进度更糟；进度条**永不后退**；阶段清单不许
静默变长。这些约束全在这个模块里用代码表达，UI 只负责画。

## 契约

- `begin(stage)` / `tick(stage, done, total)` / `finish(stage)`：生产者按阶段调。
  **可以乱序、可以重复调**（工作线程 + 多块并发），模型自己保证单调。
- `snapshot()`：给 UI 的只读快照 `{percent, state, rows, eta_text, stage}`。
  `rows` = `[(glyph, 名称, 右侧数字), …]`，glyph ∈ `✓ ◐ ○`（不只靠颜色）。
- ETA：只有**跑完 ≥2 个阶段**才给，取历史中位数、向上取整到 30 秒、**显示成区间**。
  历史住 `~/.classlive/note-timing.json`（最近 20 次，按课号分桶，不够退全局）。

⚠️ 全模块**不碰 AppKit、不联网、不读时钟以外的环境**；`clock` 可注入。
"""
from __future__ import annotations

import json
import pathlib
import threading
import time

#: 阶段顺序与显示名（**唯一定义点**；卡片、日志、报告都照它）。
STAGES: tuple[tuple[str, str], ...] = (
    ("save", "保存逐句记录"),
    ("base", "整理底稿"),
    ("review", "复习层"),
    ("layers", "重点句 · 术语 · 核对"),
    ("check", "收尾报告 · 上传"),
)
_LABEL = dict(STAGES)
_ORDER = [k for k, _ in STAGES]

#: 初始权重（和 = 100）。`review` 是大头 —— 实测 15 分钟里它占 70%。
WEIGHTS: dict[str, float] = {"save": 1.0, "base": 5.0, "review": 70.0,
                             "layers": 18.0, "check": 6.0}
#: 跑多久算「比平时慢」（历史 p90 的倍数）；到点只**改文案**，进度条不动。
STALLED_MULT = 2.0
#: 历史保留条数 / ETA 粒度（秒）。
HISTORY_MAX = 20
ETA_ROUND_S = 30.0
#: 少于这么多个阶段跑完就不给 ETA（单点的误差太大，Nielsen：只在能诚实计算时给）。
ETA_MIN_STAGES = 2


def history_path(*, root=None) -> pathlib.Path:
    """`~/.classlive/note-timing.json`（走 `paths`，不在别处再拼一次）。"""
    import paths
    return paths.STATE_ROOT / "note-timing.json" if root is None \
        else pathlib.Path(root) / "note-timing.json"


def load_history(*, root=None, course: str = "") -> dict:
    """历史阶段耗时 `{阶段: 秒列表}`；**本课桶覆盖全局桶**。读不出来当空。

    ⚠️ 全局桶是键 `""`（`save_timing(course="")` 写的那份）。课号桶里没有的阶段
       退回全局 —— 新课第一节也有 ETA，不用等它自己攒够样本。
    """
    try:
        raw = json.loads(history_path(root=root).read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return {}
    if not isinstance(raw, dict):
        return {}
    g = raw.get("")
    out = dict(g) if isinstance(g, dict) else {}
    bucket = raw.get(str(course)) if course else None
    if isinstance(bucket, dict):
        out.update(bucket)
    elif not out:
        # 老格式（没分桶，直接是 `{阶段: [...]}`）—— 当全局桶读。
        out = {k: v for k, v in raw.items()
               if isinstance(v, list) and not isinstance(raw.get(k), dict)}
    return out


def save_timing(durations: dict, *, root=None, course: str = "", clock=time.time) -> None:
    """把本次每阶段耗时并进历史（按课号分桶；各留最近 `HISTORY_MAX` 次）。**永不抛。**"""
    try:
        p = history_path(root=root)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            raw = raw if isinstance(raw, dict) else {}
        except Exception:                                    # noqa: BLE001
            raw = {}
        key = str(course or "")
        cur = raw.get(key)
        cur = dict(cur) if isinstance(cur, dict) else {}
        for st, sec in (durations or {}).items():
            try:
                vals = [float(x) for x in (cur.get(st) or [])]
            except Exception:                                # noqa: BLE001
                vals = []
            vals.append(round(float(sec), 1))
            cur[st] = vals[-HISTORY_MAX:]
        raw[key] = cur
        tmp = p.with_suffix(".tmp")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        import os
        os.replace(tmp, p)
    except Exception:                                        # noqa: BLE001
        pass


#: 生产者报的 stage 名（`polish.STAGE_NAME` 的口径）→ 本模块的阶段。**唯一定义点。**
STAGE_MAP: dict[str, str] = {"polish": "base", "polish_partial": "base",
                             "review": "review"}


def stage_of(name: str) -> str:
    """生产端的 stage 名 → 本模块的阶段键。

    认识的（`polish*` / `review`）照 `STAGE_MAP` 映；**本来就是我们这五个键之一**
    的直接原样返回（调用方显式起「保存 / 整理底稿 / 收尾」那几段时用）；
    其余算 `layers`（最后一段杂活：重点句 / 术语 / 核对都在那里）。
    """
    s = str(name or "")
    if s in _LABEL:
        return s
    return STAGE_MAP.get(s, "layers")


def _median(vals: list) -> float | None:
    xs = sorted(float(v) for v in (vals or []) if isinstance(v, (int, float)))
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def eta_text(remaining_s: float | None) -> str:
    """剩余秒 → 一行区间文案（**不显示单点数字**）。`None` = 不给。"""
    if remaining_s is None or remaining_s <= 0:
        return ""
    lo = max(0.5, remaining_s * 0.8)
    hi = remaining_s * 1.6

    def _fmt(s: float) -> str:
        m = max(0.5, s / 60.0)
        return f"{m:.1f}".rstrip("0").rstrip(".") if m < 10 else f"{m:.0f}"

    return f"预计还要 {_fmt(lo)}–{_fmt(hi)} 分钟"


class Progress:
    """阶段进度的**单调**状态机。线程安全（内部一把锁，操作都是 O(1)）。"""

    def __init__(self, *, clock=time.monotonic, weights: dict | None = None,
                 history: dict | None = None, course: str = "") -> None:
        self._clock = clock
        self._w = dict(weights or WEIGHTS)
        self._hist = dict(history or {})
        self._course = str(course or "")
        self._reason = ""
        self._lock = threading.Lock()
        self._total = sum(self._w.get(k, 0.0) for k, _ in STAGES) or 100.0
        self._frac: dict[str, float] = {k: 0.0 for k, _ in STAGES}   # 每阶段完成比例
        self._started_at: dict[str, float] = {}
        self._dur: dict[str, float] = {}
        self._t0 = self._clock()
        self._shown = 0.0                     # ⭐ 已展示的最高百分比（只增不减）
        self._cur = ""
        self._done = False
        self._failed = False
        self._stalled = False

    # ---- 生产者 ----
    def begin(self, stage: str) -> None:
        """进入某阶段。**按 `STAGES` 顺序把更早的阶段全部收尾**（见到后一段 = 前一段完了）——

        生产端只报它自己知道的阶段（精修 / 复习层），中间那几段没人报；
        不自动收尾的话清单会永远停在第 1 行。
        """
        with self._lock:
            if stage not in _LABEL:
                return
            idx = _ORDER.index(stage)
            for k in _ORDER[:idx]:
                self._frac[k] = 1.0
                self._started_at.pop(k, None)
            self._cur = stage
            self._started_at.setdefault(stage, self._clock())

    def tick(self, stage: str, done: int, total: int) -> None:
        """某阶段的子进度（`done/total`）。**只增不减**。"""
        with self._lock:
            if stage not in _LABEL:
                return
            self._cur = stage
            self._started_at.setdefault(stage, self._clock())
            try:
                frac = (float(done) / float(total)) if total else (1.0 if done else 0.0)
            except (TypeError, ValueError, ZeroDivisionError):
                frac = 0.0
            self._frac[stage] = max(self._frac.get(stage, 0.0), min(1.0, max(0.0, frac)))

    def finish(self, stage: str, *, ok: bool = True) -> None:
        with self._lock:
            if stage not in _LABEL:
                return
            self._frac[stage] = 1.0 if ok else self._frac.get(stage, 0.0)
            t0 = self._started_at.pop(stage, None)
            if t0 is not None:
                self._dur[stage] = max(0.0, self._clock() - t0)

    def all_done(self) -> None:
        with self._lock:
            for k, _ in STAGES:
                self._frac[k] = 1.0
            self._cur = ""
            self._done = True

    def fail(self, reason: str = "") -> None:
        with self._lock:
            self._failed = True
            self._reason = str(reason or "")

    # ---- 消费者 ----
    def percent(self) -> float:
        with self._lock:
            got = sum(self._w.get(k, 0.0) * self._frac.get(k, 0.0) for k, _ in STAGES)
            pct = 100.0 * got / self._total
            self._shown = max(self._shown, pct)          # ⭐ 单调
            return round(self._shown, 1)

    def elapsed(self) -> float:
        """从构造到现在的秒数（卡片「已用」那一段用它）。"""
        return max(0.0, self._clock() - self._t0)

    def durations(self) -> dict:
        """本次每个**跑完**的阶段耗时（秒）—— 收尾时写进历史（`save_timing`）。"""
        with self._lock:
            return dict(self._dur)

    def snapshot(self) -> dict:
        pct = self.percent()
        with self._lock:
            rows = []
            for k, label in STAGES:
                f = self._frac.get(k, 0.0)
                if f >= 1.0:
                    glyph, right = "✓", ""
                elif f > 0.0 or k == self._cur:
                    glyph, right = "◐", f"{int(round(f * 100))}%" if f > 0 else ""
                else:
                    glyph, right = "○", ""
                rows.append((glyph, label, right))
            state = ("done" if self._done else "failed" if self._failed
                     else "running")
            stage = self._cur
            eta = self._eta_locked(pct)
        return {"percent": pct, "state": state, "stage": stage,
                "rows": rows, "eta_text": eta_text(eta)}

    def _eta_locked(self, pct: float) -> float | None:
        """剩余秒（历史中位数外推）。⚠️ 调用方已持锁。

        ⚠️ **必须真的量到过阶段耗时**才算「能诚实计算」：`begin()` 会把更早的阶段
           自动标完成（生产端不报它们），那些格子没有实测时长 —— 只数「完成几格」
           会在刚开始就给出 ETA（假精度，Nielsen 明确反对）。
        """
        if pct <= 0 or pct >= 100:
            return None
        done_n = sum(1 for k, _ in STAGES if self._frac.get(k, 0.0) >= 1.0)
        if not self._dur:
            return None                          # 这一节还**没有**量到任何阶段的真实耗时
        if done_n < ETA_MIN_STAGES:
            return None
        total_s = 0.0
        for k, _ in STAGES:
            if self._frac.get(k, 0.0) >= 1.0:
                total_s += float(self._dur.get(k) or _median(self._hist.get(k)) or 0.0)
            else:
                hist = _median(self._hist.get(k))
                if hist is None:
                    hist = float(self._dur.get(k) or 0.0) or 30.0
                total_s += hist * (1.0 - self._frac.get(k, 0.0))
        elapsed = max(0.0, self._clock() - self._t0)
        left = total_s - elapsed
        if left <= 0:
            return None
        return float(int(left / ETA_ROUND_S + 0.999) * ETA_ROUND_S)

    def stalled(self) -> bool:
        """当前阶段是不是跑超了历史 p90 的 `STALLED_MULT` 倍（只改文案，不动进度条）。"""
        with self._lock:
            k = self._cur
            if not k or k not in self._started_at:
                return False
            vals = [float(v) for v in (self._hist.get(k) or [])
                    if isinstance(v, (int, float))]
            if len(vals) < 3:
                return False
            p90 = sorted(vals)[min(len(vals) - 1, int(len(vals) * 0.9))]
            return (self._clock() - self._started_at[k]) > p90 * STALLED_MULT


def status_line(snap: dict) -> str:
    """卡片标题下那行：`正在生成笔记 · 已用 3:12`。**纯函数。**"""
    st = str(snap.get("state") or "running")
    if st == "done":
        return "笔记已保存"
    if st == "failed":
        return "生成失败"
    return "正在生成笔记"


def elapsed_text(t_snap: float | None) -> str:
    """秒 → `3:12` / `1:02:03`。`None` = 空。**纯函数。**"""
    if t_snap is None:
        return ""
    s = max(0, int(t_snap))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def card_lines(snap: dict, *, elapsed_s: float | None = None) -> tuple:
    """卡片三段文字：`(状态行, 阶段清单, 提示行)`。**纯函数**（可单测，不碰 AppKit）。

    ⚠️ 阶段清单**固定 5 行**（`STAGES` 的长度）—— Nielsen：清单不许静默变长。
    ⚠️ 只有 `running` 才显示清单：结束时那三行结论文字已经说清了。
    """
    st = str(snap.get("state") or "running")
    head = status_line(snap)
    el = elapsed_text(elapsed_s)
    if el:
        head += f" · 已用 {el}"
    if st != "running":
        return head, "", ""
    rows = "".join(f"{g} {name}{('  ' + right) if right else ''}\n"
                   for g, name, right in (snap.get("rows") or []))
    eta = str(snap.get("eta_text") or "")
    if not eta:
        eta = "已用时间见上；进度按完成量算"
    return head, rows.rstrip("\n"), eta
