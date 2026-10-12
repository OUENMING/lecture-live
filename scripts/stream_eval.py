#!/usr/bin/env python3
"""流式字幕量具：把音频按真实流水线的分段逻辑回放一遍，量 草稿稳定度 / 延迟 / 错词率。

用法（用 App 自带的 python，从仓库根目录）：
  ClassLive.app/Contents/MacOS/python scripts/stream_eval.py <音频> [--ref ref.txt]
      [--start S] [--dur D] [--final whisper|parakeet|cloud] [--draft auto|parakeet|nemotron]
      [--pace-rpm N] [--jsonl out.jsonl] [--json out.json]

回放方式（与生产的差别要看清）：
  · 复用生产的 `Segmenter`（VAD 切句 + 每 1s 一次 on_partial）和 `PeakNormalizer`；
  · 草稿 = **生产那一套草稿引擎**（`draft_engine.load_draft_engine`：R3 之后是 Nemotron 流式，
    缺模型 / 内存不够 / 熔断才退回 Parakeet 滑窗）；定稿 = 整句转写（`--final`）；
    ⚠️ 2026-10-11 之前这里写死 Parakeet 滑窗、注释还写着"同生产"—— R3 P1 落地后那句话就不成立了，
       R3 之后的回改率差着几十倍（0.69% vs 36.6%），拿旧引擎的数字说现在的体验会得出反的结论。
  · **同步回放、不排队**：生产里草稿线程与定稿线程抢同一把 ASR 锁，会有排队延迟，
    这里没有 → 报告的延迟是**下限**。"计算量"是真实墙钟，"音频时刻"是回放时钟（借 SimulStreaming
    的 computation-aware / unaware 两种记法）。
  · 翻译不在回放里（量的是 ASR 这一层）；草稿翻译的触发策略另有 --draft-policy 回放（见下）。

输出：每次草稿/定稿一行 JSONL（emission_time 风格），以及汇总指标。
指标定义见 `stream_metrics.py` 文件头。
"""
from __future__ import annotations
import argparse, json, os, sys, time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import stream_metrics as M

import draft_engine
import models

#: 定稿有哪几种（`load_final_engine` 认的就是这三个）—— 与它挨着定义，两个 CLI 共用
FINAL_MODES = ("whisper", "parakeet", "cloud")

PARTIAL_MAX_S = 10        # 与 main.PARTIAL_MAX_S 一致（不 import main：它拖一堆 UI 依赖）


#: Worker 对 Groq 免费档的限流：**20 请求/分钟**（`server/asr-worker/src/quota-core.js` 的
#: `GROQ_MIN_MAX = 19`，留一次余量）。离线回放是背靠背发的（60–80 次/分钟），不节流必被打成 429
#: → 熔断 60 s → 后面全走本地兜底，跑出来的就不是云端的成绩了。生产是实时节奏（6–12 次/分钟），
#: 压根碰不到这条线。
RPM_LIMIT = 20


def pace_for(final_engine: str, rpm: float) -> float:
    """回放该以多快发请求（秒/段）。只对云端那条链生效；本地引擎不必节流。

    ⭐ 与 `RPM_LIMIT` 一起是「节奏」的唯一定义点 —— 两个 CLI 别各写一份换算，不然迟早漂。
    """
    if final_engine != "cloud" or rpm <= 0:
        return 0.0
    return 60.0 / min(rpm, RPM_LIMIT)


def load_drafter(which: str, parakeet):
    """装配**草稿**层：`auto` = 生产行为（Nemotron 流式优先，缺模型/内存不够/熔断退 Parakeet）。

    与 `load_final_engine` 并排 —— 这是「引擎怎么装配」的唯一定义点；两个调用方
    （本文件的 CLI 与实验 runner）都走它，别各写一份（写两份就会漂：一边用 `SR`、
    一边硬编码 `16000` 就是这么来的）。
    ⚠️ 用 `env=` 传开关，不去改动进程全局环境变量（那会污染同进程里别的调用）。
    """
    import draft_engine
    from capture import SR
    return draft_engine.load_draft_engine(
        parakeet, window_samples=PARTIAL_MAX_S * SR,
        env={**os.environ, draft_engine.ENV: which})


def load_final_engine(which: str, model_dir: str, draft_asr):
    """按名字装配**定稿**那一层：`whisper` / `parakeet` / `cloud`（生产那条链）。

    ⭐ 唯一接线处 —— CLI 与实验脚本都走它，免得两边各写一份（云端那层写两遍就会漂）。
    `cloud` 返回的对象多一个 `last_source`（每段走的哪条路），`replay` 会记进 utterances。
    """
    if which == "cloud":
        import final_asr
        # 生产那条**云端定稿链**：`FinalASR` 内部就是 Worker → Groq/千问 → 本地兜底。
        # ⚠️ 云端开关**尊重用户的配置**（`~/.classlive/cloud-asr.json` 与 `CLASSLIVE_CLOUD_ASR=0`），
        #    不写死 `lambda: True` —— 否则用户在面板里关掉云端，回放照样把音频发出去。
        #    （生产的完整判据在 `main.cloud_asr_off_reason`；那两个门函数理想上该挪进 `final_asr`。）
        fin = final_asr.load_final(model_dir, draft_asr,
                                   lambda: bool(final_asr.read_config()["enabled"]),
                                   preload_local=False)

        return _text_only(fin)
    if which == "whisper":
        # 走生产那层装配：Whisper 缺失 / 加载失败会**退回 Parakeet 并说一句**（自己写会漏掉这个容错）
        import final_asr
        fin = final_asr.load_final(model_dir, draft_asr, lambda: False, preload_local=True)
        return _text_only(fin)
    return draft_asr


def _text_only(fin):
    """把 `final_asr` 那层收成「`transcribe(buf) -> str`」—— `replay` 只吃文本。

    ⚠️⚠️ **两个分支都必须过这一层**（2026-10-12 全仓审查，已复现）：
       原来只有 `cloud` 分支包了，`whisper` 分支**直接返 `FinalASR`** ——
       而 `FinalASR.transcribe()` 返的是 `FinalResult`（`final_asr.py:295`），不是 str。
       `replay.on_end` 拿它当定稿文本存进 `u["final"]`，收尾 `evaluate()` 里
       `" ".join(u["final"] …)` 就 `TypeError: expected str instance, FinalResult found`。
    ⚠️ 而 `--final` 的**默认值就是 `whisper`**（`real_class_slices.sh` 也不覆盖）
       → **默认那条路必崩**，且回放跑完好几分钟才在收尾处炸，量具白跑。
    ⚠️ `last_source` / `last_fell_back` / `last_steps` 是事后要回答"这段发出去没"用的，
       用生产现成的字段，别拿 `source` 字符串去猜。
    """

    class _TextOnly:
        last_source: str | None = None
        last_fell_back: bool = False
        last_steps: tuple = ()

        def transcribe(self, buf):
            r = fin.transcribe(buf)
            self.last_source = r.source
            self.last_fell_back = r.fell_back
            self.last_steps = r.steps
            return r.text

    return _TextOnly()


def replay(audio: np.ndarray, drafter, asr_final, *, draft_filter=None, progress=None,
           pace_s: float = 0.0):
    """回放，返回 utterances 列表。

    `drafter` 是**生产的草稿引擎**（`draft_engine.DraftEngine`，`main.partial_worker` 用的同一个）：
    `drafter.transcribe(buf, tag) -> (text, engine)`，`buf` 是**本段从段首累积**的样本、
    `tag` 是段号。⚠️ 别传裸 ASR —— 流式引擎靠 `tag` 分段、靠累积 `buf` 只喂增量；
    传一个每 1 s 自己截窗的 Parakeet 进来，量到的就是 R3 之前的行为（差几十倍）。

    draft_filter(prev_shown:str, new:str) -> str：草稿显示层策略（默认直通 = 生产现状）。

    `pace_s`：**每段定稿之间真实 sleep 这么多秒**（虚拟时钟不受影响，音频时间轴照旧）。
    ⚠️ 测云端时必须节流：Worker 对 Groq 免费档限 **20 请求/分钟**（`GROQ_MIN_MAX = 19`），
       而离线回放是背靠背发的（约 60–80 次/分钟）→ 429 → 熔断 60 s → 后面全走本地兜底。
       那种跑出来的 WER 是"云端+本地"混出来的，**不是云端的成绩**。生产是实时节奏
       （每 5–10 秒一段 ≈ 6–12 次/分钟），压根碰不到这个限制。"""
    from capture import PeakNormalizer, SR
    from vad import Segmenter
    import vad
    clock = {"t": 0.0}
    cur = {"drafts": [], "last_req": 0.0}

    class _VirtualTime:          # Segmenter 用 time.monotonic() 判 1s 一次的草稿节拍
        @staticmethod            # 不换成回放时钟的话，快于实时的回放几乎触发不了草稿
        def monotonic():
            return clock["t"]
    real_time = vad.time
    vad.time = _VirtualTime
    utts: list[dict] = []

    def on_partial(buf):
        t0 = time.perf_counter()
        txt, used = drafter.transcribe(buf, len(utts))   # 段号 = 已经收尾的段数
        dt = time.perf_counter() - t0
        if not txt:
            return
        shown_prev = cur["drafts"][-1]["shown"] if cur["drafts"] else ""
        shown = draft_filter(shown_prev, txt) if draft_filter else txt
        cur["drafts"].append({"audio_t": clock["t"], "compute_s": dt, "raw": txt, "shown": shown,
                              "buf_s": len(buf) / SR, "engine": used})

    def on_end(buf):
        t_req = time.monotonic()          # 发请求的时刻（节流要按"请求到请求"算，不是按段）
        t0 = time.perf_counter()
        txt = asr_final.transcribe(buf)
        dt = time.perf_counter() - t0
        dur = len(buf) / SR
        utts.append({"start_t": clock["t"] - dur, "end_t": clock["t"], "dur": dur,
                     "final": txt, "final_compute_s": dt, "drafts": cur["drafts"],
                     # 云端那趟要能回答"这一段到底发出去了没、走的哪条路"
                     "final_source": getattr(asr_final, "last_source", None),
                     "final_fell_back": getattr(asr_final, "last_fell_back", None)})
        cur["drafts"] = []
        # 节流：只对**真的发了请求**的段计节奏。
        # ⚠️ 三个坑：① 拿"段间隔"当节拍会白等 —— 段间隔 = 编码 + 往返 + pace，实测比
        #    `--pace-rpm 18` 少发 ~15% 的请求、全量跑白等 7–8 分钟；② 熔断 / 关掉云端 /
        #    编码失败这几条路**一次网络都不发**，照睡就是纯空等；③ 反过来，
        #    **真发了请求但云端没成**（429 / 超时 → 回落本地）那类必须**照节流**
        #    —— 按"最后是谁给的文本"（`last_source`）判会漏掉它们，而那正是最需要
        #    减速的时刻（2026-10-12 全仓审查核实）。
        #    → 判据用生产自己记的 `steps`：里面有没有 `cloud:` 那一步，就是"发没发"。
        if pace_s:
            if _sent_request(asr_final):
                wait = pace_s - (t_req - cur["last_req"])
                if wait > 0:
                    time.sleep(wait)
                cur["last_req"] = t_req
        if hasattr(draft_filter, "reset"):
            draft_filter.reset()                 # 定稿一到，冻结前缀作废

    try:
        seg = Segmenter(on_partial, on_end)
        norm = PeakNormalizer()
        CH = SR // 10
        for i in range(0, len(audio), CH):
            clock["t"] = (i + CH) / SR
            seg.accept(norm.process(audio[i:i + CH]))
            if progress and (i // CH) % 600 == 0:
                progress(clock["t"])
        seg.flush()
    finally:
        vad.time = real_time
    return utts


def _slid(d) -> bool:
    """这份草稿的窗口滑了没有。只有**窗口型**引擎（parakeet：只看最近 10 s）会滑；
    流式引擎（R3 nemotron）看整段，不滑 —— 照剔会把它的长段草稿整批丢掉，回改率被**低估**。
    没有 `engine` 字段（R3 之前的文件 / 离线回放）= parakeet，数字逐位不变。"""
    return (d.get("engine") or "parakeet") == "parakeet" and d["buf_s"] > PARTIAL_MAX_S


def _sent_request(asr_final) -> bool:
    """这一段**真的把请求发出去了**吗（节流只认这个）。

    判据是生产自己记的 `steps` 里有没有 `cloud:` 那一步 —— 那是"发过请求"的唯一证据：
      · 429 / 超时 / 云端空串回落本地 → `steps` 里有 `cloud:timeout` / `cloud:http-429` …
        **必须照节流**（限流那一刻尤其要减速），可 `last_source` 这时是 `local-*`；
      · 熔断开 / 关掉云端 / 编码失败 → `steps` 只有 `breaker` / `off` / `encode`，
        **一次网络都没发**，照睡就是纯空等（实测白等 7–8 分钟）。
    """
    for st in getattr(asr_final, "last_steps", ()) or ():
        if str(st).startswith("cloud:"):
            return True
    return False


def _is_cloud_source(src) -> bool:
    """这段定稿的**文本**是不是云端给的（`groq` / `qwen` / `cloud`；本地是 `local-*`）。

    ⚠️ 与「发没发过请求」是**两个问题** —— 后者看 `steps`（见 `replay.on_end` 的节流）。
       这里问的是"最后这段话是谁给的"，所以按来源判，而不是按 `fell_back`。
    """
    s = str(src or "")
    return bool(s) and s != "none" and not s.startswith("local")


def evaluate(utts, ref_text: str | None, k: int = M.DEFAULT_K,
             want_disagreement: bool = True) -> dict:
    """⚠️ `want_disagreement=False` 跳过"整课草稿 vs 定稿"那一次编辑距离 ——
    它是**整课词表的 O(n²) DP**（2026-10-09 实测：2.4k 词 0.9s、4.9k 词 4.4s、10k 词 17.8s，
    词数翻倍时间 ×4），而它只是 `evaluate()` 里的一个附带观测量。
    `class_report` 出**文本表**时把它关掉（那张表不打这一项）；要 `--json` 时才开。
    """
    n_draft = sum(len(u["drafts"]) for u in utts)
    # 回改：每句内部的相邻草稿（用"显示出来的"文本，而非 ASR 原始输出）
    # ⚠️ 缓冲超过 PARTIAL_MAX_S 后草稿窗口在滑动（开头的词滚出窗口，不是"改写"），
    #    这些草稿不进回改统计，单独数个数 —— 否则长句会把回改率虚高一大截。
    sliding = sum(1 for u in utts for d in u["drafts"] if _slid(d))
    shown = rew = beyond = 0
    for u in utts:
        s = M.rewrite_stats([d["shown"] for d in u["drafts"] if not _slid(d)], k=k)
        shown += s["shown"]; rew += s["rewritten"]; beyond += s["rewritten_beyond_k"]
    # 草稿 → 定稿的跳动
    fr_edits = fr_len = 0
    for u in utts:
        if u["drafts"] and u["final"]:
            r = M.final_rewrite(u["drafts"][-1]["shown"], u["final"])
            if r["edits"] is not None:
                fr_edits += r["edits"]; fr_len += r["draft_len"]
    # ⚠️ 两处 `is not None` 是给**现场采集**（`livemetrics.py` 写的 `<会话>.live.jsonl`）
    #    留的口子：那份没有音频时刻、也没有定稿耗时 → 这几项**留空**（`n=0`），
    #    而不是拿 0 冒充。回放那条路字段齐全，数字与以前逐位一致。
    first_vis = [u["drafts"][0]["audio_t"] - u["start_t"] + u["drafts"][0]["compute_s"]
                 for u in utts
                 if u["drafts"] and u["drafts"][0]["audio_t"] is not None]
    final_comp = [u["final_compute_s"] for u in utts if u["final_compute_s"] is not None]
    rtf_final = [u["final_compute_s"] / u["dur"] for u in utts
                 if u["dur"] > 0 and u["final_compute_s"] is not None]
    srcs: dict[str, int] = {}
    for u in utts:
        if u.get("final_source"):
            srcs[u["final_source"]] = srcs.get(u["final_source"], 0) + 1
    out = {
        "utterances": len(utts), "drafts": n_draft,
        # 定稿各条路的段数（`{"groq": 58, "local-parakeet": 2}`）；本地单测回放时为空
        "final_sources": srcs,
        # ⭐ **云端给的**段数与秒数：按来源判（见 `_is_cloud_source`）。
        #    ⚠️ 别写成「有 source 且没 fell_back」——`--final whisper`（**默认值**）走
        #    `allow_cloud=False`，每段都是 `local-whisper` 且 `fell_back=False`，
        #    那样一节课会被算成"云端 100%"（2026-10-12 全仓审查核实）。
        "cloud_segments": sum(1 for u in utts if _is_cloud_source(u.get("final_source"))),
        "cloud_audio_s": round(sum(u["dur"] for u in utts
                                   if _is_cloud_source(u.get("final_source"))), 1),
        "audio_s": round(sum(u["dur"] for u in utts), 1),
        "draft_rewrite": {"k": k, "sliding_drafts_excluded": sliding, "shown_words": shown, "rewritten": rew,
                          "rate": rew / shown if shown else None,
                          "rewritten_beyond_k": beyond,
                          "rate_beyond_k": beyond / shown if shown else None},
        "draft_to_final": {"edits": fr_edits, "draft_words": fr_len,
                           "rate": M.rate(fr_edits, fr_len)},
        "latency_first_visible_s": M.summarize_latency(first_vis),
        "latency_final_compute_s": M.summarize_latency(final_comp),
        "rtf_final": M.summarize_latency(rtf_final),
    }
    hyp = " ".join(u["final"] for u in utts if u["final"])
    if ref_text:
        w = M.wer(ref_text, hyp)
        out["wer_vs_reference"] = w
    # 无参考时给"草稿与定稿的分歧"，明确不是对真值的 WER
    # ⚠️ 拼接要放在这个 `if` **里面**：`class_report` 的常规路径就是 `want_disagreement=False`
    #    （4 遍里 3 遍关着），这个开关存在的全部理由就是省掉整课词表的 O(n²) DP —— 顺带也省掉拼串。
    if want_disagreement and hyp:
        last_drafts = " ".join(u["drafts"][-1]["shown"] for u in utts if u["drafts"])
        if last_drafts:
            out["draft_vs_final_disagreement"] = M.wer(hyp, last_drafts)["wer"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--ref")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=0.0, help="0 = 到结尾")
    ap.add_argument("--final", choices=list(FINAL_MODES), default="whisper",
                    help="cloud = 生产那条云端定稿链（Worker → Groq/千问，失败退本地）")
    ap.add_argument("--pace-rpm", type=float, default=0.0, metavar="N",
                    help="云端定稿的节流：每分钟最多 N 次请求（Worker 限 20/分；0 = 不节流）")
    ap.add_argument("--draft", choices=list(draft_engine.MODES), default="auto",
                    help="草稿引擎，与生产同一个开关 CLASSLIVE_DRAFT_ENGINE（默认 auto = 生产行为）")
    ap.add_argument("--model-dir", default=models.path_of("parakeet", "~/models/parakeet-tdt-0.6b-v3-int8"))
    ap.add_argument("--final-model-dir", default=models.path_of("whisper", "~/models/sherpa-onnx-whisper-turbo"))
    ap.add_argument("--k", type=int, default=M.DEFAULT_K,
                    help="hold-back 词数（只影响 beyond_k 指标）")
    ap.add_argument("--stabilize", type=int, default=None, metavar="K",
                    help="草稿显示层用 draft_stable（前缀冻结，灰尾 K 词）；不给 = 生产现状（直通）")
    ap.add_argument("--jsonl")
    ap.add_argument("--json")
    a = ap.parse_args()

    from capture import load_file
    from asr import load_asr
    # `-ss`/`-t` 走**输入定位**（`capture.load_file` 的 docstring 就是为这个写的）：
    # 从 40 分钟的源里取 5 分钟不必解完再切（实测 9.5 s → 1.0 s，峰值内存也少一大截）。
    # 样本与"先全解再切"逐位相同（本轮实测过：rms 比 1.0、峰值与位置一致）。
    audio = load_file(a.audio, start=a.start, dur=a.dur)
    asr_d = load_asr(a.model_dir)
    asr_f = load_final_engine(a.final, a.final_model_dir, asr_d)
    drafter = load_drafter(a.draft, asr_d)
    print(f"草稿引擎：{drafter.name}" + (f"（{drafter.reason}）" if drafter.reason else ""),
          file=sys.stderr, flush=True)
    t0 = time.time()
    flt = None
    if a.stabilize is not None:
        from draft_stable import DraftStabilizer
        flt = DraftStabilizer(a.stabilize)
    pace = pace_for(a.final, a.pace_rpm)
    utts = replay(audio, drafter, asr_f, draft_filter=flt, pace_s=pace,
                  progress=lambda t: print(f"  … 回放到 {t:.0f}s", file=sys.stderr, flush=True))
    ref = open(a.ref, encoding="utf-8").read() if a.ref else None
    res = evaluate(utts, ref, k=a.k)
    res["wall_s"] = round(time.time() - t0, 1)
    res["audio_file"] = os.path.basename(a.audio)
    res["final_engine"] = a.final
    res["draft_engine"] = drafter.name
    res["stabilize_k"] = a.stabilize
    if a.jsonl:
        with open(a.jsonl, "w", encoding="utf-8") as f:
            for i, u in enumerate(utts):
                for d in u["drafts"]:
                    f.write(json.dumps({"utt": i, "kind": "draft", "audio_t": round(d["audio_t"], 2),
                                        "compute_s": round(d["compute_s"], 3), "text": d["shown"],
                                        # ⭐ `raw` = 冻结**之前**的模型原始假设（2026-10-09 加）。
                                        #    有了它，`class_report.py` 能把**同一份回放**离线
                                        #    跑成 hold-back 0/1/2/3 的曲线（不必重跑 ASR），
                                        #    也能和生产的 `<会话>.live.jsonl` 用同一套分析。
                                        "raw": d["raw"], "buf_s": round(d["buf_s"], 3),
                                        # ⭐ `seg` = 段号。回放本来就是"一段 = 一组"，
                                        #    所以这里 = `utt`；写上是为了让**生产与离线两份
                                        #    文件的分组键逐字同款**（`class_report` 就不用靠
                                        #    "字段在不在"去嗅探来源）。见 §9 那条说明。
                                        "seg": i,
                                       # ⚠️ `engine` 必须写：`class_report` 靠它判"这是滑窗草稿还是流式草稿"
                                       #    （`_slid`），漏了会把流式的长段草稿当成滑窗整批剔掉、
                                       #    回改率被**低估** —— 正是 `_slid` 自己警告过的那个失效模式。
                                       "engine": d.get("engine")},
                                       ensure_ascii=False) + "\n")
                f.write(json.dumps({"utt": i, "kind": "final", "audio_t": round(u["end_t"], 2),
                                    "compute_s": round(u["final_compute_s"], 3),
                                    "text": u["final"], "seg": i,
                                    # ⚠️ 生产那份 `<会话>.live.jsonl` 的 final 行**没有**来源字段
                                    #    （`livemetrics.LiveLog.final` 只写 utt/seg/text/zh/raw/n_draft），
                                    #    逐段的来源只在测试模式打包里。离线这份多写一个 `source`，
                                    #    是为了回答"这段到底发出去了没"——读的人别以为两份逐字同款。
                                    "source": u.get("final_source")},
                                   ensure_ascii=False) + "\n")
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
