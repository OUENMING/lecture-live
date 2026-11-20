#!/usr/bin/env python3
"""网页版 runner 的实时总结支 —— 每课一个桌面 `LiveSummarizer`，只换接线（W16 §4.2）。

管什么：`tick(client, state, now)`（契约 §7，loop 每 20 s 调）：看正在上的课 → 拉游标后的新句子喂
桌面状态机（每课一个实例 + 自己的 worker 线程，模型调用不占 tick）→ 章节有变化才 `put_summary`
（契约 §5）→ 课消失（下课 / 3 h 没动静）时收尾后写回 `final=true`。永不抛。
对外接口：只有 `tick`。为什么：桌面 0 行改动、依赖全注入（`main.py:4335` 是接线样板）；
`REFRESH_GAP_S=inf` 只关逐段刷新，每章 ≈2 次调用（W16 §K 实测）；计费头与 W23 同一 seam ——
包一层 `llm_client.headers`（全仓唯一头出口），contextvar 带 `X-ClassLive-Bill-To: <hash16>`。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)          # 契约 §7：桌面 .py 一律在仓库根 import
try:
    from .client import SessionGone   # W23 的 client.py（包式：loop.py 的 import 路径）
except ImportError:                  # 脚本式 import（server/runner 在 sys.path）时
    try:
        from client import SessionGone
    except ImportError:
        class SessionGone(Exception):  # 隔离环境下兜底；真判别在 _gone() 的名字检查
            pass

import live_summary as _live
# W16 §K：只关「逐段刷新」这一支（`_maybe_interim` 运行时读模块全局），
# 首段（FIRST_INTERIM_AFTER_S=0）与封章照旧 → 每章 ≈2 次模型调用。
_live.REFRESH_GAP_S = float("inf")

MAX_SESSIONS = 20                      # 契约：同时进行的课最多 20 节
FINISH_TIMEOUT_S = 110.0               # 收尾 ≤120 s（契约），跑在它自己的线程里
from server.runner import billing  # noqa: E402  计费头唯一出口（与 note_job 共用一层包装）
_BILL = billing.CTX                    # 记账头（契约 §6）：每次模型调用前后 set/reset


try:
    billing.install()                  # 记账头装不上也不许拦住总结
except Exception:                      # noqa: BLE001
    pass


def _load_key():
    """runner 码：走桌面读码那套路径（`$DEEPSEEK_API_KEY` → 隔离 HOME 的 credentials）。"""
    try:
        from cloud_translator import load_api_key
        return load_api_key()
    except Exception:                  # noqa: BLE001
        return None


def _chat_for(hash16: str):
    """每课一个 chat callable —— 同 `main._summ_chat`：按预算分调用类、20 s 短超时
    （攥 `_step_lock` 的路不许卡 180 s）。真出口 `build_notes._chat_json`。"""
    import atom
    import build_notes
    import chapter as ch

    def chat(sys_p, block, max_tokens, temperature):
        key = _load_key()
        if not key:
            return None
        cls = "chapter" if max_tokens >= ch.MAX_TOKENS else "atom"
        tok = _BILL.set(hash16)
        try:
            return build_notes._chat_json(key, atom.MODEL, sys_p, block,
                                          max_tokens, temperature,
                                          timeout=20.0, call_class=cls)
        finally:
            _BILL.reset(tok)

    return chat


def _hms(sec: float) -> str:
    """课内秒 → "HH:MM:SS"（桌面 feed 第 2 元素同形；`_span_s` 只取差，锚点无关）。"""
    n = int(max(0.0, sec))
    return f"{n // 3600:02d}:{n % 3600 // 60:02d}:{n % 60:02d}"


class _Session:
    """一节课：游标 + 桌面状态机 + 它自己的 worker 线程（时钟 = 内容秒锚 + 墙钟漂移）。"""

    def __init__(self, hash16: str, session_id: str):
        self.hash, self.sid = hash16, session_id
        self.after = 0                 # 游标：已喂到的最大 seq
        self.vbase = 0.0               # 时钟锚：最新一句的课内秒
        self.vwall = time.monotonic()  # 锚定那一刻的墙钟
        self.dirty = False             # 有新章节待写回
        self.finishing = False         # 收尾已启动（不许再喂）
        self.done = False              # 可从 state 移除
        self.lock = threading.Lock()
        self.chapters = {}             # 章 id → 最新 emit 记录（worker 线程写）
        self.running = threading.Event()
        self.running.set()
        self.summ = _live.LiveSummarizer(chat=_chat_for(hash16),
                                         append_atoms=None,   # runner 不落盘，全在内存
                                         chapter_path=None,
                                         emit=self._emit, clock=self._clock)
        threading.Thread(target=self.summ.run, args=(self.running,), daemon=True,
                         name=f"summary-{session_id}").start()

    def _clock(self) -> float:
        return self.vbase + (time.monotonic() - self.vwall)

    def _emit(self, p: dict) -> None:
        """只收章节（summary.json 只装它）；deadline / atoms / gap 忽略。"""
        if isinstance(p, dict) and p.get("kind") == "chapter":
            c = p.get("chapter") or {}
            with self.lock:
                self.chapters[c.get("id")] = c
                self.dirty = True


def _para_zh(para) -> str:
    if not isinstance(para, dict):
        return ""
    out = []
    lead = para.get("lead")
    if isinstance(lead, dict) and str(lead.get("zh") or "").strip():
        out.append(str(lead["zh"]).strip())
    for b in para.get("body") or ():
        if isinstance(b, dict) and str(b.get("zh") or "").strip():
            out.append(str(b["zh"]).strip())
    return "\n".join(out)


def _doc(s: _Session, final: bool) -> dict:
    """契约 §5 的形状 —— 字段一个不多一个不少。"""
    with s.lock:
        chs = sorted(s.chapters.values(), key=lambda c: c.get("id") or 0)
    chapters = [{
        "idx": i + 1,
        "title": str(c.get("title_zh") or c.get("title") or ""),
        "start_seq": int(c.get("lo") or 0),
        "end_seq": int(c.get("hi") or 0),
        "closed": c.get("status") == "final",
        "summary": _para_zh(c.get("para")),
        "points": [z for z in (str(x.get("zh") or x.get("en") or "")
                               for x in (c.get("sentences") or ())) if z.strip()],
    } for i, c in enumerate(chs)]
    return {"session_id": s.sid,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "final": bool(final), "chapters": chapters}


def _gone(e) -> bool:
    return isinstance(e, SessionGone) or type(e).__name__ == "SessionGone"


def _drop(s: _Session) -> None:
    """SessionGone：直接丢掉 —— 不收尾、不写回（课已被删，final 无处可去）。"""
    s.finishing = True
    s.running.clear()
    try:
        s.summ.close()
    except Exception:                  # noqa: BLE001
        pass
    s.done = True


def _feed(client, s: _Session) -> None:
    lines = client.lines(s.hash, s.sid, s.after)
    if not lines:
        return
    newest = s.vbase
    for l in lines:
        seq, t = int(l.get("seq") or 0), float(l.get("t") or 0.0)
        s.summ.feed((seq, _hms(t), str(l.get("en") or ""), str(l.get("zh") or "")))
        s.after = max(s.after, seq)
        newest = max(newest, t)
    # ⚠️ 先喂完再推时钟：窗口边界只由「批与批之间 ≥75 s 的内容差」决定，批内不裂。
    s.vbase, s.vwall = newest, time.monotonic()


def _start_finish(client, s: _Session) -> None:
    """下课 → 收尾（残余窗 + 最后一章正式合成）→ 写回 final → done。独立线程，不阻塞 tick。"""
    s.finishing = True

    def work():
        try:
            s.summ.finish(timeout_s=FINISH_TIMEOUT_S)
        except Exception as e:         # noqa: BLE001
            print(f"[summary] 收尾失败 {s.sid}: {e}", flush=True)
        try:
            client.put_summary(s.hash, s.sid, _doc(s, True))
        except Exception as e:         # noqa: BLE001
            if not _gone(e):
                print(f"[summary] final 写回失败 {s.sid}: {e}", flush=True)
        finally:
            s.running.clear()
            s.done = True

    threading.Thread(target=work, daemon=True, name=f"summary-final-{s.sid}").start()


def tick(client, state: dict, now: float) -> None:
    """契约 §7：跨 tick 的东西全在 `state` 里；永不抛（内部吞掉、打日志）。"""
    try:
        _tick(client, state)
    except Exception as e:             # noqa: BLE001
        print(f"[summary] tick 失败（不影响笔记支）: {e}", flush=True)


def _tick(client, state: dict) -> None:
    sessions = state.setdefault("sessions", {})
    try:
        live = client.live()
    except Exception as e:             # noqa: BLE001
        print(f"[summary] live() 失败: {e}", flush=True)
        return
    seen = {(str(x.get("hash")), str(x.get("session_id"))) for x in live}
    for x in live:                      # 新课（超 20 节记日志跳过）
        k = (str(x.get("hash")), str(x.get("session_id")))
        if k in sessions:
            continue
        if len([s for s in sessions.values() if not s.done]) >= MAX_SESSIONS:
            print(f"[summary] 同时 {MAX_SESSIONS} 节课，跳过新课 {k[1]}", flush=True)
            continue
        try:
            sessions[k] = _Session(k[0], k[1])
        except Exception as e:         # noqa: BLE001
            print(f"[summary] 开课失败 {k[1]}: {e}", flush=True)
    for k, s in list(sessions.items()):
        try:                           # 坏一节不影响别的课
            if s.done:
                del sessions[k]
            elif s.finishing:
                continue
            elif k not in seen:
                _start_finish(client, s)   # 下课 / 3 h 没动静（live 只回 3 h 内的）
            else:
                _feed(client, s)
                if s.dirty:
                    # 先清再拍快照（OCR E2）：put 往返期间 worker 又 emit 的会重新置 dirty，下一 tick 补写；
                    # 原来 put 完才清，会把这段时间的新章节抹掉，要等下一次 emit（分钟级）才写回。
                    with s.lock:
                        s.dirty = False
                    try:
                        client.put_summary(s.hash, s.sid, _doc(s, False))
                    except Exception:
                        with s.lock:
                            s.dirty = True     # 失败留给下一 tick 重试（原语义）
                        raise
        except Exception as e:         # noqa: BLE001
            if _gone(e):
                print(f"[summary] 课没了，丢弃 {k[1]}", flush=True)
                _drop(s)
            else:
                print(f"[summary] 单课失败 {k[1]}: {e}", flush=True)
