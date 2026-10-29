"""上课时的**现场采集**：把「你实际看到的草稿流」原样记成 JSONL，课后用
`scripts/class_report.py` 出数。

## 为什么要有它（这是它存在的唯一理由）

`scripts/stream_eval.py` 只能**回放录音**：走的是脚本自己搭的流水线、用虚拟时钟、
不排队，所以它量到的**不是生产真值**。而「真实课堂的草稿回改率」恰恰是只有生产能
回答的头号未知（离线 3 分钟录音量到 39.6%，但那是 17 句的样本）。

它记的是 `main.drain()` 那个缝上发生的事 —— **用户看见了什么**，就在那一行。
于是：

1. **同场 A/B**：`raw`（模型原始假设）与 `shown`（冻结后上屏的）**两个都记**
   → 一次真课同时拿到「基线回改率」与「hold-back 后的回改率」，不用跑两节课。
2. **与离线基线可比**：每一行的字段与 `stream_eval.py --jsonl` 的同款
   → 同一份分析代码（`stream_eval.evaluate` + `stream_metrics`）读两种来源。

## 接口（刻意很小）

    log = livemetrics.open_for(session_path)        # 关着的时候返回 None
    log.draft(raw, shown, buf_s=b, compute_s=c)     # 一次草稿上屏
    log.draft_zh(zh)                                # 一段草稿译文增量
    log.final(en, zh, raw, lat=…)                   # 一句定稿（`lat` = 译文延迟 4 元组）
    log.event("tap_probe", ok=True)                 # 任何别的观测
    log.block(peak, gain, vad, n)                   # 每个音频块一次（不进 JSONL，见下）
    log.close(echo_retries=3)                       # 冲刷 + 写一行 summary（+ `.blocks.npz`）

## 逐块记录（`block()`，2026-10-09 加）

每 0.1 s 一块：**原始峰值 / 实际施加的增益 / 生产当场 VAD 的语音窗占比 / 样本数**。
它**不走队列、不进 JSONL**（一节课 ~2.6 万块，JSONL 会翻倍）—— 只往四个 `array`
里追加（亚微秒），`close()` 时一次写成 `<会话名>.blocks.npz`（~300 KB/节）。
为什么要：录音是归一化**之后**的信号，卡顶块丢了原始幅度，课后反推不出原始电平
（2026-10-09 实测反推器自检不过）。有了逐块增益，`原始 = 录音 / gain` 是精确的，
再配上**生产当场的** VAD 判定，就能回答"被增益上限卡住的块是讲话还是停顿"。

⚠️⚠️ **硬前提：绝不能影响上课**（与 `testmode.py` 同一条设计前提，那条比拿到数据重要）。
   所以：每个公开方法内部 `try/except`，队列**有界**（满了丢弃并计数），
   真正的写盘在后台守护线程里。记录失败 = 少一份数据，**不是**课跑不下去。
⚠️ `draft()` / `final()` 在主循环（AppKit 主线程）里被调用 —— 它们只做
   `put_nowait`，微秒级；**不许**在这里开文件、不许 `flush()`。

## 开关

`CLASSLIVE_LIVE_LOG=1` → 写 `<会话名>.live.jsonl`（就在会话 md 旁边）。
不设这个环境变量时 `open_for()` 直接返回 `None`，**生产路径零改动**。
"""
from __future__ import annotations

import array
import json
import os
import pathlib
import queue
import threading
import time

#: 队列上限。一节 50 分钟的课大约 2000–4000 次草稿 + 数百次定稿；
#: 8192 给足余量，真的堵住了就丢弃（并计数），绝不反压主循环。
QUEUE_MAX = 8192

_SENTINEL = object()


def _enabled_from_env(default: bool = False) -> bool:
    """`CLASSLIVE_LIVE_LOG` 设了就听它的（`0` 也算"设了" → 关）；没设 → `default`。"""
    v = os.environ.get("CLASSLIVE_LIVE_LOG", "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def open_for(session_path, *, enabled: bool | None = None, default: bool = False):
    """会话文件路径 → LiveLog（关着的时候返回 `None`）。

    `default` = 环境变量**没设**时开不开。`main` 在**测试模式**下传 `True`
    （App 里点「测试模式」就自动采；显式 `CLASSLIVE_LIVE_LOG=0` 仍能关掉）。

    ⚠️ `session_path` 为 `None`（没有 vault/没建会话文件）时返回 `None`：
       没有落点就没有采集，**不要**退回一个别的目录 —— 那会造出第二个数据落点。
    """
    if enabled is None:
        enabled = _enabled_from_env(default)
    if not enabled or session_path is None:
        return None
    try:
        # `sessions/2026-10-10_120000_ECON10770.md` → `sessions/2026-10-10_120000_ECON10770.live.jsonl`
        stem = pathlib.Path(str(session_path)).with_suffix("")
        return LiveLog(pathlib.Path(str(stem) + ".live.jsonl"), name=stem.name)
    except Exception:                                     # noqa: BLE001
        return None


class LiveLog:
    """现场采集器。构造它就会开文件 + 起写盘线程。

    输出的每一行：`{"i": <行号>, "t": <秒>, "kind": ..., ...}`。
    `kind` 只有五种：`draft` / `draft_zh` / `final` / `event` / `replace`，外加收尾那行 `summary`
    （`close()` 写，多带一个 `dropped` 说明丢了多少行）。
    """

    def __init__(self, path, name: str = "live"):
        self.path = pathlib.Path(path)
        self._t0 = time.monotonic()
        self._q: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
        self.dropped = 0                                   # 队列满丢掉的条数
        self.n_write_err = 0                               # 真去写却失败了的条数（下一条同）
        self.closed = False                                # 不再收新记录
        self._stopping = False                             # `close()` 已进来（幂等）
        self._f = None
        self._i = 0
        self._utt = 0                                      # 当前句子号（`final` 时 +1）
        self._n_draft = 0
        self._err = ""
        # 逐块记录（见文件头「逐块记录」）：只在内存里追加，`close()` 时落成 npz
        self._blk = {"peak": array.array("f"), "gain": array.array("f"),
                     "vad": array.array("f"), "n": array.array("I")}
        try:
            self._f = open(self.path, "a", encoding="utf-8")
        except Exception as e:                             # noqa: BLE001
            self._err = f"开不了 {self.path}: {e!r}"
            self.closed = True
            self._stopping = True
            return
        self._th = threading.Thread(target=self._run, daemon=True, name="cl-livelog")
        self._th.start()
        self.event("meta", name=name, pid=os.getpid(), cwd=os.getcwd())

    # ---- 内部：唯一一处拼事件并按行号入队 ----
    def _put(self, kind: str, **fields) -> None:
        if self.closed:
            return
        self._i += 1
        rec = {"i": self._i, "t": round(time.monotonic() - self._t0, 3),
               "kind": kind, **fields}
        try:
            self._q.put_nowait(rec)
        except queue.Full:
            self.dropped += 1
            self._i -= 1                                   # 没写出去，行号还回去

    def segment_end(self, seg: int) -> None:
        """记一行"第 `seg` 段结束了"。**只是个 sink —— 段号由 `main` 铸造。**

        ⚠️ 段号（"这一段是第几段"）是**流水线的身份**，不该由"开没开日志"决定；
           所以计数器长在 `main.run()` 里（`seg_gen`），这里只负责记下来。
        ⚠️ 它必须挂在 `Segmenter` 的 `on_utterance_end`（**音频结束那一刻**），
           **不能**挂到定稿上屏 —— 那条路要先跑 Whisper + LLM，比段落结束晚 2–4 秒，
           新段的前几份草稿会带着上一段的号（实测把 `草稿→定稿` 从 10.7% 顶到 65%）。
        """
        self._put("event", event="segment_end", seg=seg)

    def _note_err(self, msg: str) -> None:
        """记一条失败原因（**只留第一条** —— 否则一整节课的失败会把 `_err` 撑成 MB 级，
        而它整个要进 summary 那一行；计数另走 `n_write_err`）。三处失败共用这一处。
        """
        if not self._err:
            self._err = msg[:400]

    # ---- 公开接口（全部吞异常；主循环调用，绝不抛）----
    def block(self, peak: float, gain: float, vad: float, n: int) -> None:
        """一个音频块：原始峰值 / 实际增益 / 当场 VAD 语音窗占比 / 样本数。主循环每 0.1 s 调一次。"""
        if self.closed:
            return
        try:
            # ⚠️ **先全部转换、再一起追加**：边转边追加的话，第二个参数类型不对时
            #    `peak` 已经进去了、`gain` 没进 → 四列从此**错位**，而且没人知道。
            row = (float(peak), float(gain), float(vad), int(n))
            if row[3] < 0:
                return
            b = self._blk
            b["peak"].append(row[0])
            b["gain"].append(row[1])
            b["vad"].append(row[2])
            b["n"].append(row[3])
        except Exception:                                  # noqa: BLE001
            pass

    def draft(self, raw: str, shown: str | None = None, *, seg: int = 0,
              buf_s: float = 0.0, compute_s: float = 0.0,
              lag_s: float | None = None, engine: str | None = None) -> None:
        """一次草稿上屏。`raw` = 送进 ASR 的原始假设，`shown` = 实际显示的文本。

        ⚠️ `shown` 不给就等于 `raw`（没开冻结时的常态）。
        字段名与 `stream_eval.py --jsonl` 对齐：`text` = 上屏的那份。

        ⭐ `seg` = **这一段草稿属于哪次缓冲会话**（由 `main.run()` 的 `seg_gen` 给）。
           ⚠️⚠️ **这正是分组键，不是 `utt`** —— 一段 VAD 会被 `split_sentences` 切成
           **多句定稿**，所以"草稿 : 定稿"**不是 1:1**。实测（2026-10-09 真跑 TTS 语料）：
           按定稿分组时 32 句里 14 句只分到 0–1 份草稿，草稿→定稿跳成 93%
           （离线回放同一份音频是 10.7%）—— 那是**分组错了**，不是模型有问题。

        ⭐ `lag_s` = 这份草稿的音频缓冲**被交出来**（`Segmenter.on_partial`）到它**上屏**
           之间的墙钟秒数 —— 含排队、等 ASR 锁、转写、等主循环 drain。它比 `compute_s`
           （只是转写那一下）更接近"用户等了多久"。实时音源下
           **首字延迟 ≈ 该段第一份草稿的 `buf_s + lag_s`**（`class_report` 那么算）。

        ⭐ `engine` = 出这份草稿的引擎（`nemotron` 流式 / `parakeet` 10 s 滑窗）。
           量具据它判「窗口滑没滑」：只有 parakeet 会滑（`stream_eval._slid`）。
        """
        try:
            self._n_draft += 1
            extra = {} if lag_s is None else {"lag_s": round(lag_s, 3)}
            if engine:            # 哪个引擎出的（R3）。不给不写 → 旧文件 / 读端都视为 parakeet
                extra["engine"] = engine
            self._put("draft", utt=self._utt, seg=seg, n=self._n_draft,
                      text=shown if shown is not None else raw,
                      raw=raw, buf_s=round(buf_s, 3), compute_s=round(compute_s, 3), **extra)
        except Exception:                                  # noqa: BLE001
            pass

    def draft_zh(self, zh: str, *, clipped: bool = False) -> None:
        """草稿译文的一段增量（`ui.draft_zh` 吃什么这里就记什么）。
        `clipped`：这份译文只译了英文尾巴（`draft_translate.clip_tail`）；没裁就不带这个键。"""
        try:
            if clipped:
                self._put("draft_zh", utt=self._utt, text=zh, clipped=True)
            else:
                self._put("draft_zh", utt=self._utt, text=zh)
        except Exception:                                  # noqa: BLE001
            pass

    def final(self, en: str, zh: str = "", raw: str = "", *, seg: int = 0,
              lat: tuple | None = None, term_zh_n: int | None = None) -> None:
        """一句定稿上屏 —— 同时把句号推进（`utt` 只是"第几句"，**分组看 `seg`**）。

        ⭐ `lat` = 这一句的**译文延迟** 4 元组 `(zh_first_ms, zh_done_ms, seg_to_zh_ms,
        tr_model)`（`tr_latency.TrTimer.done()` 的产物，见那个文件头）。
        ⚠️ **不给就不写那四个键** —— 同 `draft(engine=…)` 的规矩：老文件 / 老读端
           零感知（`("final", …)` 那条 tag 只有**双语档**那个产出点会给，纯转录与
           只英·校两条路没送过翻译，报引擎名会让人以为它们翻出来了）。

        ⭐ #4 B2：`term_zh_n` = 这一句**注入 prompt 的译名条数**（`translator.select_term_zh`
        数的，与注入共用同一把尺子）—— 同样**不给就不写**。真课复查用它回答
        "译名到底有没有命中、命中多少"。
        """
        try:
            extra = ({}
                     if lat is None else
                     {"zh_first_ms": lat[0], "zh_done_ms": lat[1],
                      "seg_to_zh_ms": lat[2], "tr_model": lat[3]})
            # ⭐ #4 B2：这一句注入的译名条数 —— **不给就不写**（同上面 lat 的规矩）。
            if term_zh_n is not None:
                extra["term_zh_n"] = int(term_zh_n)
            self._put("final", utt=self._utt, seg=seg, text=en or "", zh=zh or "",
                      raw=raw or "", n_draft=self._n_draft, **extra)
            self._utt += 1
        except Exception:                                  # noqa: BLE001
            pass

    def event(self, kind: str, **fields) -> None:
        """任何别的观测（音源、探针结果、引擎切换……）。字段名自己定，别覆盖 `i/t/kind`。"""
        try:
            self._put("event", event=kind, **fields)
        except Exception:                                  # noqa: BLE001
            pass

    def replace(self, seg_i: int = 0, seg_j: int = 0) -> None:
        """一次**跨段半句合并**把屏上尾句那行换掉了。

        ⚠️ **只记数字和段号**（`seg_i`/`seg_j` = 被并的两段；⚠️ 不能叫 `i`——
           信封里的 `i` 是行号，`**fields` 在后会把它盖掉）—— 合并文本本身在
        `sessions/<名>.merges.jsonl` 与会话里各有一份，再往这里写就是第三份真源，
        回放统计（回改率 / 延迟）读到它会被污染（同 `("final", …)` 不许复用的理由）。
        """
        try:
            self._put("replace", seg_i=int(seg_i), seg_j=int(seg_j))
        except Exception:                                  # noqa: BLE001
            pass

    def close(self, **summary) -> None:
        """冲刷 + 写收尾行。幂等；最多等 2 秒。"""
        if self._stopping:
            return
        self._stopping = True                              # ⚠️ 先于 closed：`_put` 见 closed 就不写了
        blocks_path = self._save_blocks()
        if blocks_path:
            summary = {**summary, "blocks": len(self._blk["peak"]), "blocks_file": blocks_path.name}
        try:
            # ⚠️ **无条件写这一行**（2026-10-09 被自己的判据抓到）：原来只在
            #    `summary` 非空时才写 → `close()` 不带参数时**一行收尾都没有**，
            #    读的人分不出「正常收尾」和「进程被杀了」。
            # ⚠️ 自己的三个计数**在这里自己填** —— 不必逼调用方把它们递回来
            #    （那会造出三个只服务这一行的 property）。
            self._put("summary", final=True, drafts=self._n_draft, utterances=self._utt,
                      err=self._err, write_err=self.n_write_err,
                      **{**summary, "dropped": self.dropped})
        except Exception:                                  # noqa: BLE001
            pass
        if self.n_write_err:
            # ⚠️ summary 那一行**走的正是失败的那条写盘路径**（同一个 `_write`）——
            #    磁盘满的时候它自己也写不进去，证据就没了。所以再说一遍到 stdout
            #    （`.app` 下 stdout 进 `~/Library/Logs/ClassLive/`，那是另一个落点）。
            print(f"⚠ 现场采集写盘失败 {self.n_write_err} 条 —— 这一节的 .live.jsonl 不完整",
                  flush=True)
        self.closed = True
        try:
            self._q.put_nowait(_SENTINEL)
        except queue.Full:
            try:
                self._q.get_nowait()
                self._q.put_nowait(_SENTINEL)
            except Exception:                              # noqa: BLE001
                pass
        th = getattr(self, "_th", None)
        if th is not None:
            th.join(2.0)

    def _save_blocks(self):
        """把逐块记录写成 `<会话名>.blocks.npz`。没记过块 / 写失败 → `None`（**不抛**）。

        ⚠️ 同步写：只在收尾跑一次，~300 KB，几十毫秒。
        ⚠️ 不覆盖已有文件 —— `sessions/` 只追加（同名已在就放弃，宁可少一份）。
        """
        if not self._blk["peak"]:
            return None
        try:
            import numpy as np
            name = self.path.name
            base = name[:-len(".live.jsonl")] if name.endswith(".live.jsonl") else self.path.stem
            p = self.path.with_name(base + ".blocks.npz")
            if p.exists():
                self._note_err(f"{p.name} 已存在，没写")
                return None
            with open(p, "xb") as f:
                np.savez_compressed(f, **{k: np.frombuffer(v, dtype=v.typecode)
                                          for k, v in self._blk.items()})
            return p
        except Exception as e:                             # noqa: BLE001
            self._note_err(f"逐块记录没写成: {e!r}")
            return None

    # ---- 写盘线程 ----
    def _run(self) -> None:
        while True:
            # 3.9.7 省电：**阻塞等**（原来 `get(timeout=0.5)` 是 2 次/秒空醒）。
            # 收尾 `close()` 无条件投 `_SENTINEL`，所以这里阻塞到哨兵才退出。
            rec = self._q.get()
            if rec is _SENTINEL:
                break
            self._write(rec)
        self._drain_rest()

    def _drain_rest(self) -> None:
        """收尾：把发过哨兵之后又挤进来的（理论上没有）写完 + 关文件。"""
        while True:
            try:
                rec = self._q.get_nowait()
            except queue.Empty:
                break
            if rec is not _SENTINEL:
                self._write(rec)
        try:
            if self._f is not None:
                self._f.close()
        except Exception:                                  # noqa: BLE001
            pass

    def _write(self, rec: dict) -> None:
        try:
            self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._f.flush()                                # 崩了也不丢（一行一刷，几十微秒）
        except Exception as e:                             # noqa: BLE001
            # ⚠️⚠️ **失败要记账，不能静默吞**（2026-10-12 全仓审查核实）。
            #    磁盘满 / 句柄失效 / 序列化失败的时候，整节课的数据会从这里
            #    **一条不落地丢光**，而 `close()` 写出的 summary 里 `err` 仍是空串、
            #    计数照旧 ⇒ 读数据的人**分不出**「采到了」和「一条都没落盘」。
            #    ⚠️ 仍然不抛、不影响上课（文件头的硬前提）—— 只是把话说出来：
            #    计数进 summary，原因进 `err`（截断，别让一条长消息撑爆 summary 行）。
            self.n_write_err += 1
            self._note_err(f"写盘失败: {e!r}")
