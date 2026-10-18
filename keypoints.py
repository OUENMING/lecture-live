#!/usr/bin/env python3
"""重点句 —— 用 Jev（TypeSafe System One）的 `noul` 给句子打概率，取 **top-k**。

    ClassLive.app/Contents/MacOS/python keypoints.py sessions/<某节课>.md [--top 12]

**它不生成文本**，只回答「这一句值得记吗」并给一个 0–1 的概率。所以它比走翻译那条
DeepSeek 便宜得多、也快得多（实测一次 20 问 ≈ 1550 input / 355 output tokens）。

## ⭐ 为什么是 top-k，不是阈值

实测（2026-09-29，四个真实段落）：
- 概率分布是**压缩的**（0.05–0.77），**不是双峰** —— p≥0.9 永远是 0 条
- ⚠️ **同一段换一个 prompt 写法，同一句从 0.77 掉到 0.61** → 绝对阈值**跨 prompt 不可比**
- 要用阈值就得先校准，而校准需要 ground truth —— 而 ground truth 恰好不稳定
  （`[论文]` Zhang & Fung 2009：人类标注者对「哪句是重点」**边界样本上根本不一致**）
- ⭐ 但**排序是稳的**（四个段落里 top-5 几乎不变）→ 所以取 **top-k**

## 官方两条纪律（`[官方]` `docs.typesafe.ai`）

1. `primitives.md`：「Ask for a judgment a knowledgeable person makes **in a second**…
   'Analyze this message and determine the best course of action' is **not**」
   → 每条 `instructions` **只问一个判据**。第一版塞了五个判据，被官方原文判为反例。
2. `jaggedness` #5：「Accuracy falls as the state grows with content **unrelated to the
   decision**」→「**Filter first; send only what the question needs**」
   → `state` 只放这一段，而且每一问**点名**它只看哪一句。

## ⚠️⚠️ 四个实测出来的坑（都踩过）

| 坑 | 症状 |
|---|---|
| `questions` 是**对象**不是数组，每个值也是**对象** | `400 expected record, received array` |
| 字段叫 **`instructions`**（不是 `text`），`type` 必填 | `400 param: questions.q1.instructions` |
| ⚠️ **必须发 `User-Agent`** | urllib 默认发 `Python-urllib/3.x` → Cloudflare **403 `error code: 1010`**。**它看起来像"请求太大"** —— 我先用 curl 试小的过了、再用 urllib 试大的 403，差点去查 size 上限 |
| 一次最多 **20 个问题** | `400 at most 20 questions per call`。⚠️ **这条不在官方文档里** —— 官方只有 token 预算、**没有数量上限**，所以 **20 是 CommandCode 代理加的** |

## 第二个出口：术语闸门（`judge_terms`，2026-10-18）

课上候选词池（`live_terms`）的第五道门 **也是从这条唯一接入点走的**：问「一个一年级
学生在这门课里要不要解释这个词」—— 与离线实验 E2 的问法 A 逐字相同
（`scratchpad/jev_exp/report.md` §E2，102 条真候选 **AUC 0.976**）。
阈值在 `live_terms.JUDGE_MIN_P`；**缓存与硬超时在调用方**（`main.make_term_judge`）。
⚠️ 与 `INSTRUCTION` 同一条纪律：**改问法 / 改 state 形状必须重新标定那个阈值**。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

#: 下课收尾时同时在飞的批数（见 `score`）。
WORKERS = 4

#: 一次请求最多几个问题。⚠️ **CommandCode 代理的限制，不是 TypeSafe 的**（见模块头）。
CHUNK = 20

#: `User-Agent` —— ⚠️ **不给会被 Cloudflare 挡成 403**（见模块头）。
UA = "ClassLive/0.0 (+https://github.com/OUENMING/lecture-live)"

#: ⚠️ CommandCode **代理**那条路（`/provider/` + 官方路径）。
ENDPOINT = "https://api.commandcode.ai/provider/v1/systemone"
MODEL = "typesafe/jev"

#: ⭐⭐ **两家供应商**（2026-09-30 实测）：请求**格式完全一样** ——
#:    同一个 body、同一个响应结构（`answers.S1.noul`），因为 CommandCode 就是
#:    `/provider/` + 官方路径在转发。差别只在**三处**：
#:
#: | | 官方 `api.typesafe.ai` | CommandCode 代理 |
#: |---|---|---|
#: | **model** | `jev-latest` / `jev-1.13.0` | `typesafe/jev` |
#: | **认证** | TypeSafe 自己的 `apikey_…` | cc-switch 那条 token |
#: | **`User-Agent`** | **不需要** | **必须给**（否则其 Cloudflare WAF 挡成 403/1010）|
#:
#: ⚠️ 两边 **key 互不通用**：官方 key 打 CommandCode 是 401 `UNAUTHORIZED`。
#: ⚠️ 模型 ID 也**不通用**：官方拿 `typesafe/jev` 会 400 `Unknown model`。
OFFICIAL_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
#: ⭐ **钉版本号**（官方逐字：「If you have tuned confidence thresholds against a
#:    specific version, **pin that version's ID**」）—— 我们确实标定过阈值
#:    （`live_summary.DEADLINE_MIN_P`），所以**不跟随 `jev-latest`**。
OFFICIAL_MODEL = "jev-1.13.0"

#: 一句话、一个判据。⭐ 照官方 `primitives.md` 那个「in a second」的标准写的。
#: ⚠️ **改这句会让绝对概率整体移动**（实测同一句 0.77 → 0.61），
#:    但**排序稳定** —— 所以下游只许用排名，别拿绝对分去卡阈值。
INSTRUCTION = (
    "Look at line {tag} only. If a student had to pass an exam on this lecture, "
    "would they write {tag} down on their revision sheet? Ignore every other line.")

#: 术语闸门的问法 —— 与 E2 实验的问法 A **逐字相同**（`run_e2.py` 的 `INSTR_A`；
#: A 0.975 / B 0.964，A 更好）。⚠️ 改这句话**必须重新标定** `live_terms.JUDGE_MIN_P`
#: （与 `INSTRUCTION` 同一条纪律，见模块头「第二个出口」）。
TERM_INSTRUCTION = (
    "Look at line {tag} only. Would a first-year student in this course need this term "
    "explained, because it is a domain term of the subject being taught rather than "
    "everyday English? Ignore every other line.")


#: 句末标点。⚠️ **只认 ASCII 的三种** —— 转录里几乎不出现中文标点，
#: 而 `…` / `--` 那些是**未完**的信号，不能当句末。
_END = (".", "?", "!")


def thoughts(sents, *, max_chars: int = 260):
    """把**一行字幕**合并成**一句完整的话** → `[(句子, [原始下标, …]), …]`。

    ⚠️⚠️ **为什么必须合**：转录是「一句字幕一行」，而**字幕在句子中间就断**。
       实测（SOC10020 那节 275 句）挑出来的 top-15 里：
         · `He used the method of mapping.`  —— 完整 ✅
         · `was both thought over, created systematically…` —— **半句** ❌
         · `about how we relate to other people…`          —— **半句** ❌
       单行打分 = **在给碎片打分**，而碎片天然"不像值得记的内容"，系统性地压低分数。

    ⚠️ 判据是**上一行有没有句末标点**（不是"这一行够不够长"）——
       `was both thought over…` 那一行本身挺长，但它是上一句的续。
    ⚠️ **必须带回原始下标** —— 挑出来的东西要能回到转录里定位；
       丢掉下标 = 挑出一条你**找不到在哪**的句子（同 `atom.traceable` 那条纪律）。
    ⚠️ `max_chars` 是**兜底**：ASR 漏标点时不许无限累积下去。
    """
    out: list = []
    buf: list = []
    idx: list = []
    for i, s in enumerate(sents or ()):
        buf.append(s)
        idx.append(i)
        joined = " ".join(buf)
        if joined.rstrip().endswith(_END) or len(joined) >= max_chars:
            out.append((joined, list(idx)))
            buf, idx = [], []
    if buf:                       # 结尾没标点的残段也要交出来，**不许悄悄丢掉**
        out.append((" ".join(buf), list(idx)))
    return out


def build_state(sents) -> str:
    """一段句子 → `state`。⚠️ 只放这一段（官方 `jaggedness` #5：无关内容会拖低准确率）。"""
    return ("This is an excerpt from a university lecture (English). Each numbered "
            "line is one sentence the lecturer said, in order.\n\n"
            + "\n".join(f"S{i + 1}. {s}" for i, s in enumerate(sents)))


def build_questions(n: int, *, tag=lambda i: f"S{i + 1}") -> dict:
    """`n` 个 `noul` —— 一句一个判据。**纯函数。**"""
    return {f"S{i + 1}": {"type": "noul",
                          "instructions": INSTRUCTION.format(tag=tag(i))}
            for i in range(n)}


def term_state(terms, *, course_title: str = "") -> str:
    """候选词 → 术语闸门的 `state`。**纯函数。**

    ⭐ 与 E2 实验的骨架逐字相同（`run_e2.py::one`），只有两处**必要**的差别：
    - E2 每行尾随一句「真句」（`— lecture: "…"`）—— `live_terms` 抽取时没留句，
      这也是官方 `jaggedness #5` 的口径（state 里只放判据要的东西）；**不造**。
    - E2 是多课混批（每行带自己的 `[course: …]` 标签）；课上每轮只有**一节课**的词，
      所以 `course_title` 一份，逐行照贴 —— "tagged with the course it came from" 不变。
    """
    head = ("These are candidate terms extracted from the raw transcripts of university "
            "lectures. Each numbered line is one candidate term, tagged with the course "
            "it came from.")
    tag = f"  [course: {course_title}]" if course_title else ""
    return head + "\n\n" + "\n".join(
        f"S{i + 1}. {t}{tag}" for i, t in enumerate(terms or ()))


def parse_answers(payload, n: int) -> list:
    """响应 → 长度恰为 `n` 的概率表（缺的补 `None`）。**纯函数。**

    ⚠️ **按 `S<i>` 的编号回填，不按返回顺序** —— 少一条就整体错位一格，
       而错位在「取 top-k」下**完全看不出来**（还是 k 条，只是全偏了）。
    """
    out: list = [None] * n
    for k, v in ((payload or {}).get("answers") or {}).items():
        if not (isinstance(k, str) and k[:1] == "S" and k[1:].isdigit()):
            continue
        i = int(k[1:]) - 1
        p = (v or {}).get("noul")
        if 0 <= i < n and isinstance(p, (int, float)):
            out[i] = float(p)
    return out


def ask_batch(state: str, instructions: list, *, token: str | None = None,
              timeout: float = 90.0, batch_size: int = 20, tries: int = 2) -> list:
    """一批 `noul` 问题（`S1…Sn`，**≤ batch_size/请求**）→ 逐条概率（拿不到 = `None`）。**永不抛。**

    ⭐ F9b：笔记的「支持性核对」（`note_guards.support_check`）用这个口 ——
       Jev 在 E3（一条 claim 一个 state）AUC=1.000；这是它的**生产入口**。
    接口（deep module，见 `keypoints` 模块惯例）：
      · 返回值长度恒等于 `len(instructions)`；调用方逐条对位，不用 try；
      · `token` 不传 → `provider_token()`；**没配 token → 全 None、不发请求**（fail-soft）；
      · `>batch_size` 自动切批（每批独立 `state` —— 同一份 state 会被重复计费，见 `score()`）；
      · 429/529/5xx/超时 → 该批至多再试 `tries-1` 次（退避）；其它异常/解析不出 → 该批全 None。
      · ⚠️ **整批失败要出声**（同 `score()` 的纪律）：Jev key 过期 / 401 / 全局限流时
        全部批次记 `None`，终端一句话都没有 —— 而唯一生产调用方（`note_guards.support_check`）
        拿到全 `None` 会输出「支持性核对…全部找到」，于是"闸门坏了"和"闸门正常"长得一模一样。
    """
    items = [str(x or "") for x in (instructions or [])]
    if not items:
        return []
    if token is None:
        token = provider_token()
    out: list = [None] * len(items)
    if not token:
        return out
    step = max(1, min(20, int(batch_size)))
    n_batches = n_failed = 0
    for base in range(0, len(items), step):
        part = items[base:base + step]
        n_batches += 1
        qs = {f"S{i + 1}": {"type": "noul", "instructions": t}
              for i, t in enumerate(part)}
        payload = None
        for k in range(max(1, int(tries))):
            try:
                payload = ask_commandcode(state, qs, token=token, timeout=timeout)
                break
            except Exception as e:                                # noqa: BLE001
                msg = str(e)
                transient = any(x in msg for x in ("429", "529", "500", "502", "503",
                                                   "连不上", "timed out", "timeout"))
                if transient and k + 1 < max(1, int(tries)):
                    import time
                    time.sleep(1.5 * (2 ** k))
                    continue
                payload = None
                break
        if payload is None:
            n_failed += 1
        got = parse_answers(payload, len(part))
        for i, p in enumerate(got):
            out[base + i] = p
    # ⚠️⚠️ 失败必须出声（DB 审查 D-19）：整批无回执（key 过期 / 401 / 全局限流）时旧版
    #    静默返回全 None —— `support_check` 会把它当成"全部找到"。只打一行，不逐批刷屏。
    if n_failed:
        tail = ("（**全部**失败 —— 这一轮一条都没拿到）"
                if n_failed == n_batches else "（其余批次照常）")
        print(f"⚠ Jev 判分批 {n_failed}/{n_batches} 批没拿到{tail}")
    return out


def score(sents, *, ask) -> list:
    """一批句子 → 每句一个概率（`None` = 这次没拿到）。

    ⚠️ **`ask` 是注入进来的**（照 `codebase-design`「接受依赖，不要自己造依赖」）：
       判据传个假 `ask` 就能跑，不用联网。生产那条是 `ask_commandcode`。
    ⚠️ 超过 `CHUNK` 就分批 —— 每批各自带一份 `state`（⚠️ 该服务**没有 prompt cache**，
       所以 state 是被重复计费的）。
    """
    sents = list(sents or ())
    parts = [sents[lo:lo + CHUNK] for lo in range(0, len(sents), CHUNK)]

    def one(part):
        # ⚠️⚠️ **单批失败不许连坐**（2026-10-01 实测）：一节 50 分钟的课要打
        #    29 批，原来**任何一批**网络抖一下（超时/429/5xx）就把它抛给
        #    `pick` 那个大 `try` → **整节清零** → 🎯 那节整块静默消失，
        #    而且**偶发**（重跑一次可能就好），是最难查的一类。
        #    单批挂了只让这一批记 `None` —— 本函数的语义本来就是
        #    「`None` = 这次没拿到」（见 docstring），其余批次照常。
        try:
            return parse_answers(ask(build_state(part), build_questions(len(part))), len(part)), False
        except Exception:                                     # noqa: BLE001
            return [None] * len(part), True

    # ⭐ 批与批互不依赖 → 并发（3.9.4）。实测官方一批 20 题 0.24–0.43 s（2026-10-12），
    #    一节 548 句约 20–28 批：串行就是下课后干等 6–12 s。`map` 保序，结果逐批原位拼回。
    #    ⚠️ 只开 `WORKERS` 路：TypeSafe 的并发上限没查到，开太多撞 429 反而整批变 `None`。
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        res = list(ex.map(one, parts))
    out: list = [p for got, _ in res for p in got]
    n_batches = len(parts)
    n_failed = sum(1 for _, bad in res if bad)
    # ⚠️⚠️ **失败必须出声**（2026-10-01 审查 F5）：「单批不连坐」之后又留下一个
    #    静默面 —— key 过期 / 401 / 全局限流时**全部**批次记 `None` → `top_k`
    #    排除 `None` → 空表 → 🎯 整节消失，终端和笔记里一句话都没有，
    #    正是 3.8.6 修掉的那个 bug 的原始症状。同 `ask_one` 那条「失败要出声」。
    if n_failed:
        tail = ("（**全部**失败 —— 这节不会出 🎯 最值得记的几句）"
                if n_failed == n_batches else "（其余批次照常）")
        print(f"⚠ Jev 打分 {n_failed}/{n_batches} 批没拿到{tail}")
    return out


def top_k(scores, sents, k: int = 12) -> list:
    """概率最高的 `k` 句 → `[(概率, 下标, 句子), …]`，降序。**纯函数。**

    ⚠️ `None`（没拿到答案）**排除**，不当 0 —— 「没答」和「答了 0」是两件事。
    ⚠️ 并列按句子下标排（否则结果不可复现）。
    """
    rows = [(p, i, s) for i, (p, s) in enumerate(zip(scores, sents)) if p is not None]
    rows.sort(key=lambda r: (-r[0], r[1]))
    return rows[:k]


# ------------------------------------------------------------------ 生产那条路
_PROVIDER: dict | None = None


def _provider(*, root=None) -> dict:
    """现在的供应商 —— `{"name", "endpoint", "model", "token"}`。

    ⭐ 优先级见 `resolve`：官方 key → CommandCode → 通行码（作者的 Worker）
       → 都没有则 `token` 为空（= 功能关着，同本模块一贯的 fail-soft）。
    ⚠️ 解析一次就缓存：跑一节 50 分钟的课要调十几次，而 key 文件不会中途变。
    """
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = resolve(_read_key("jev_key", root=root), token(root=root), _translate_key())
    return _PROVIDER


def resolve(official: str, cc: str, translate_key: str | None) -> dict:
    """三把钥匙 → 用哪家。**纯函数。** 官方 key → CommandCode → 通行码（作者的 Worker 代付）。

    ⭐ 通行码那条（3.9.4）：朋友手里只有 `cl-` 码，Jev 由作者的 Worker（`/v1/jev`）拿作者的
       官方 key 代打 —— 模型在 Worker 那边**钉死** `OFFICIAL_MODEL`，这里写同一个值只为对账。
    ⚠️ 顺序是"自己配的优先"：作者本机有官方 key，就绝不绕作者自己的服务器走一圈。
    """
    if official:
        return {"name": "typesafe", "endpoint": OFFICIAL_ENDPOINT,
                "model": OFFICIAL_MODEL, "token": official}
    if cc:
        return {"name": "commandcode", "endpoint": ENDPOINT, "model": MODEL, "token": cc}
    import llm_client
    if llm_client.is_passcode(translate_key):
        return {"name": "classlive", "endpoint": llm_client.base_for(translate_key) + "/jev",
                "model": OFFICIAL_MODEL, "token": translate_key}
    return {"name": "commandcode", "endpoint": ENDPOINT, "model": MODEL, "token": ""}


def _translate_key() -> str:
    """翻译那栏填的东西（DeepSeek key 或通行码）。读不出返回空串，不抛。"""
    try:
        import cloud_translator
        return cloud_translator.load_api_key() or ""
    except Exception:                                     # noqa: BLE001
        return ""


def provider_token() -> str:
    """当前供应商的 token（**有官方 key 就走官方**，见 `_provider`）。

    ⚠️ **没配返回空串，不抛** —— 同本模块一贯的 fail-soft。
    ⭐ 存在的理由：`ask_commandcode` 的 `token` 是**必填参数**（它不自己去解析），
       所以任何要自己调 `score()` 的地方（探针、覆盖率）都得有一个公开的取值口。
    """
    try:
        return _provider()["token"]
    except Exception:                                     # noqa: BLE001
        return ""


def _read_key(attr: str, *, root=None) -> str:
    """从 `paths.<attr>()` 读一行。**读不出返回空串，不抛**（同 `token()`）。"""
    try:
        import paths
        return getattr(paths, attr)(root=root).read_text(
            encoding="utf-8").splitlines()[0].strip()
    except Exception:                                     # noqa: BLE001
        return ""


def ask_commandcode(state: str, questions: dict, *, token: str, timeout: float = 60.0,
                    endpoint: str | None = None, model: str | None = None):
    """真发一次请求。⚠️ **`User-Agent` 必须有** —— 见模块头那条 403。

    ⚠️ `endpoint` / `model` 不传就用 `provider()` 解析出来的那家（见模块头那张表）。
       ⚠️ **`User-Agent` 两家都发**：官方不需要但也不介意，CommandCode 缺了就被挡。
    """
    ep = endpoint or _provider()["endpoint"]
    md = model or _provider()["model"]
    body = json.dumps({"model": md, "state": state,
                       "questions": questions}).encode("utf-8")
    hdr = {"Authorization": "Bearer " + token, "Content-Type": "application/json", "User-Agent": UA}
    import llm_client
    hdr = {**llm_client.headers(token), **hdr}   # 通行码时多一个安装号（Worker 按它数设备）；别的 key 不带
    req = urllib.request.Request(ep, data=body, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # ⚠️ **把响应体带出来** —— 这个 API 的错误信息全在 body 里
        #    （`at most 20 questions per call` 那种），只报状态码等于没说。
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Jev {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        # ⚠️ **`URLError` 不是 `HTTPError` 的父类，反过来才对** ——
        #    原来只接 `HTTPError`，于是断网 / DNS 失败 / 连接被拒 / 读超时
        #    一律**裸 traceback 崩掉**（2026-09-29 修）。CLI 那条路
        #    （`main()` 里 `score(sents, ask=ask)` 直连本函数）没有任何兜底，
        #    所以用户看到的就是一个栈，而不是"连不上"。
        raise RuntimeError(f"Jev 连不上（{e.reason}）—— 检查网络或代理") from e


def sentences(path, *, with_index: bool = False) -> list:
    """一份会话文件 → 英文定稿句。

    ⚠️ 读法**不自己扫行**，唯一定义点在 `obsidian_writer._parse`。
    ⭐ `with_index=True` → `[(全局句号, 句子), …]`；
       默认 `False` 仍是 `[句子, …]` —— **既有调用方一个字都不用改**。

    ⚠️⚠️ **两种编号不是一回事**（2026-09-30 实测）：
       · **全局句号**（= `atom.src` 引的那一套）= `_parse` 的**条目下标 + 1**
         —— `ObsidianWriter.append` 每调一次 `_n += 1`，一个条目一个号
       · 过滤后的下标 = 另一套（丢掉了 <=15 字符的短句）
       理工那节实测 482 条目 → 447 句，两套**平均差 17.5、最大 36**
       → 要跟 `src` 对账**必须**用 `with_index=True`，**不许**拿 `enumerate` 的下标凑。

    ⚠️⚠️ **还有第二种漂法，比上面那种更阴**：`append` 允许 `en` 为空
       （只写 ZH/ASR 也照样落盘并占号，见它的 4 个 `if`），那时**条目在、EN 行不在**
       → 拿「EN 行的序号」当全局句号会**从那里起整段漂 1**。
       经济那节实测 **3 条**这样的（679 EN 行 vs 682 条目）→ 到结尾漂 3。
       ⚠️ 因为它漂出来是**一个像模像样的数**（不是 0），对账时看不出来。
    """
    import pathlib

    import obsidian_writer
    text = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
    out = []
    for i, e in enumerate(obsidian_writer.ObsidianWriter._parse(text)):
        t = (e.get("en") or "").strip()
        if len(t) > 15:          # 太短的（"Okay." / "Right."）没有判断价值
            out.append((i + 1, t))
    return out if with_index else [t for _, t in out]


def pick(sents, *, token_value: str = "", timeout: float = 30.0, k: int = 10) -> list:
    """一整节课的英文句子 → `[(概率, 句子, [原始行下标]), …]`，降序。**收尾那一步调它。**

    ⚠️⚠️ **失败一律返回空表，绝不抛。** 它跑在**收尾**那一步，抛了会连累后面的笔记落盘
       —— 本仓库记过同类形状（收尾里一个没包住的小功能 → 整节课丢笔记）。
    ⚠️ **没配 token 也返回空表**（= 功能关着，不是错误）。
    ⚠️ 单位走 `thoughts()`（并成一句完整的话）—— 实测并句比单行**省 14% token，
       而且能捞出单行漏掉的句子**（`this is one of the most important
       methodological breakthroughs` 那句就是）。
    """
    if not token_value:
        # ⚠️⚠️ **token 必须跟 `ask_commandcode` 解析的 endpoint/model 同一家**
        #    （2026-10-01 实测抓到的静默故障）：原来这里写的是 `token()` ——
        #    那是 **CommandCode 的** token，而官方 key 配好之后 endpoint/model
        #    已经走官方 → **401** → 被本函数的 `except` 吞成空表 →
        #    🎯「最值得记的几句」那节**静默消失**（一行报错都没有）。
        #    `ask_one` 那边一直是对的（`_provider()["token"]`），只有这里漏改。
        token_value = _provider()["token"]
    if not token_value or not sents:
        return []
    try:
        built = thoughts(sents)
        items = [t for t, _ in built]
        sc = score(items, ask=lambda st, qs: ask_commandcode(
            st, qs, token=token_value, timeout=timeout))
        return [(p, items[i], built[i][1]) for p, i, _ in top_k(sc, items, k)]
    except Exception as e:                                    # noqa: BLE001
        # ⚠️ 出声（2026-10-01 审查 F5）：这条兜底原来完全静默 ——
        #    「🎯 什么都没出现」和「选句提前抛了」在屏幕上长得一模一样。
        #    同 `ask_one` 的纪律：fail-soft 是对的，完全无声不行。
        print(f"⚠ 🎯 选句失败({type(e).__name__}: {str(e)[:60]})")
        return []


def ask_one(state: str, instruction: str, *, token_value: str = "",
            timeout: float = 30.0):
    """问**一个** `noul` 判据 → `0–1` 的概率；拿不到返回 `None`。

    ⚠️ 与 `pick()` 的分工：那个是「**一小批句子排 top-k**」，这个是「**一句话一个判据**」。
       两者共用本模块这一套 HTTP / schema（**本模块是 Jev 的唯一接入点**）。
    ⚠️ **失败一律返回 `None`，绝不抛** —— 调用方在 worker 线程上，
       一次网络抖动不该把整节课带走（同 `pick()` 那条纪律）。
    ⚠️ 官方 `primitives.md` 的那条要求在这里更严格：**一次只问一个判据**
       （`questions` 里就一项）。见本模块头第 1 条。
    """
    if not token_value:
        token_value = _provider()["token"]
    if not token_value or not state:
        return None
    try:
        # ⚠️⚠️ 键名**必须**是 `S1` 这种形状 —— `parse_answers` 里有一行
        #    `if k[:1] == "S" and k[1:].isdigit()`，它只认本模块 `S1..Sn` 那套约定。
        #    第一版这里写的是 `q1`（照官方 schema 的例子）→ **那条被静默跳过**
        #    → 永远返回 `None` → 上游的「课务闸门」**一条都不放行**，
        #    而看上去**像"闸门把假阳性全拦住了"**（2026-09-30 实测栽的）。
        qs = {"S1": {"type": "noul", "instructions": instruction}}
        got = ask_commandcode(state, qs, token=token_value, timeout=timeout)
        vals = parse_answers(got, 1)
        return vals[0]
    except Exception as e:                                    # noqa: BLE001
        # ⚠️ 失败**要出声**：fail-soft 是对的（一次网络抖动不该带走整节课），
        #    但**完全静默**会让「闸门坏了」看起来像「闸门在正常工作」。
        #    同 `atom._atom_flush` 那条「提不出来是少几条要点，不是课跑不下去」的写法。
        print(f"⚠ Jev 打分失败({type(e).__name__}: {str(e)[:60]})")
        return None


def judge_terms(terms, *, course_title: str = "", timeout: float = 4.0,
                token_value: str = "") -> list:
    """候选词 → 每词一个 0–1 概率（`None` = 这次没拿到）。**绝不抛。**

    ⭐ 课上候选词闸门（下游 `live_terms.JUDGE_MIN_P`）：问「一个一年级学生在这门课里
       要不要解释这个词」—— 与 E2 实验的问法 A 逐字相同（`scratchpad/jev_exp/
       report.md` §E2；102 条真候选 AUC 0.976）。

    - 一批（≤ `CHUNK` = 20 题，键 `S1..Sn`）**一个请求**；批与批**串行** ——
      课上每轮只有几条新词，正常就是 1 次；
    - ⚠️ **没配 token → 全 `None`，一个请求都不发**（= 功能关着，同 `pick`）；
    - ⚠️ 单批失败 → 只有那批全 `None`（**不许连坐**，同 `score`），而且要**出声**；
    - `timeout` 给 `ask_commandcode` 的 socket 超时。⚠️ 这只是**第一道**；调用方
      （`main.make_term_judge`）另有一道**硬截止**（另一条线程 + `future.result`）——
      「定稿线程最多等几秒」由那道保证。
    """
    terms = list(terms or ())
    out: list = [None] * len(terms)
    if not terms:
        return out
    try:
        tok = token_value or provider_token()
    except Exception:                                         # noqa: BLE001
        return out
    if not tok:
        return out
    for lo in range(0, len(terms), CHUNK):
        part = terms[lo:lo + CHUNK]
        try:
            # ⚠️ 键名必须是 `S1..Sn` 这种形状 —— `parse_answers` 只认这一套
            #   （见 `ask_one` 那条实测：写成 `q1` 会被**静默跳过**，
            #    表象是"闸门把假阳性全拦住了"—— 最难查的一类）。
            qs = {f"S{i + 1}": {"type": "noul",
                                "instructions": TERM_INSTRUCTION.format(tag=f"S{i + 1}")}
                  for i in range(len(part))}
            got = ask_commandcode(term_state(part, course_title=course_title),
                                  qs, token=tok, timeout=timeout)
            out[lo:lo + len(part)] = parse_answers(got, len(part))
        except Exception as e:                                # noqa: BLE001
            # fail-soft 是对的（课上少一刀），但**要出声** —— 「闸门坏了」不许看起来
            # 像「闸门在正常工作」（同 `score` / `ask_one` 那条纪律）。
            print(f"⚠ Jev 判术语失败({type(e).__name__}: {str(e)[:60]})")
    return out


def token(*, root=None) -> str:
    """从 `~/.classlive/jev-token` 读 token；**没有就返回空串**（= 功能关着）。

    ⚠️ **不是错误** —— 没配 token 就是不用这个功能（同 `polish` 的 fail-soft）。
    """
    # ⚠️ 这里**不需要 `import pathlib`** —— `paths.jev_token()` 返回的就是 `Path`，
    #    直接调它的 `read_text()` 即可。（原来那个 import 是死代码，
    #    会让读者以为这里在自行拼路径。2026-09-29 删。）
    try:
        import paths
        p = paths.jev_token(root=root)
    except Exception:                                         # noqa: BLE001
        return ""
    try:
        return p.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def token_from_cc_switch() -> str:
    """从 cc-switch 库里取 CommandCode 的 token。

    ⚠️ **这是实验用的近路**，不是产品的取凭证方式 —— 产品那条走
       `paths.credentials()`（`~/.classlive/credentials`）。

    ⚠️ 2026-09-29 修两处：① 连接原来没有 `with`/`close()`，查询抛异常时泄漏；
       ② `fetchone()` 在 `providers` 里没有 `commandcode` 那一行时返回 `None`，
       而 `None[0]` 抛的是 `TypeError` —— 不如直接说清"没找到"。
    """
    import pathlib
    import sqlite3
    db = pathlib.Path.home() / ".cc-switch/cc-switch.db"
    if not db.exists():
        raise RuntimeError(f"没有 cc-switch 库：{db}")
    # ⚠️ `with sqlite3.connect(...)` **不会关连接**（只包事务）—— 原注释声称
    #    这就是修泄漏，没修到（2026-10-01 审查 F16）。`closing` 才是真的关。
    import contextlib
    with contextlib.closing(sqlite3.connect("file:" + str(db) + "?mode=ro",
                                            uri=True)) as con:
        row = con.execute(
            "select settings_config from providers where name='commandcode'"
        ).fetchone()
    if not row:
        raise RuntimeError("cc-switch 里没有 commandcode 这个 provider")
    cfg = json.loads(row[0])
    return cfg["env"]["ANTHROPIC_AUTH_TOKEN"]


def main(argv=None) -> int:
    import sys
    import time
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    path = argv[0]

    def _opt(name):
        """取 `--name <值>`；没有返回 `None`。⚠️ **判越界**（2026-09-29 修）。

        原来写的是 `argv[argv.index(name) + 1]` —— 把 `--top` / `--unit` 写在
        **最后一个参数**时（`keypoints.py sess.md --unit`）直接 `IndexError`。
        ⚠️ `--unit` 那行尤其毒：`and` 的短路顺序让右侧索引**一定**被求值，
           所以**只要命令行末尾出现 `--unit` 就必崩**。
        """
        if name not in argv:
            return None
        i = argv.index(name) + 1
        if i >= len(argv):
            print(f"⚠️ `{name}` 后面没跟值")
            return None
        return argv[i]

    k = 12
    _t = _opt("--top")
    if _t is not None:
        try:
            k = int(_t)
        except ValueError:
            print(f"⚠️ `--top` 要一个整数，拿到 {_t!r}")
            return 2
    unit = "thought" if _opt("--unit") == "thought" else "line"
    raw = sentences(path)
    if not raw:
        print("这份文件里没读到英文句子")
        return 1
    if unit == "thought":
        built = thoughts(raw)
        sents = [t for t, _ in built]
        origin = [ix for _, ix in built]
    else:
        sents, origin = raw, [[i] for i in range(len(raw))]
    n_calls = (len(sents) + CHUNK - 1) // CHUNK
    print(f"{path}  单位={unit}  {len(raw)} 行字幕 → {len(sents)} 个打分单位"
          f" → {n_calls} 次调用")
    tok = token_from_cc_switch()
    tin = tout = 0

    def ask(state, qs):
        nonlocal tin, tout
        got = ask_commandcode(state, qs, token=tok)
        u = got.get("usage") or {}
        tin += int(u.get("input_tokens") or 0)
        tout += int(u.get("output_tokens") or 0)
        return got

    t0 = time.time()
    sc = score(sents, ask=ask)
    dt = time.time() - t0
    print(f"耗时 {dt:.1f}s（{dt / max(1, n_calls):.1f}s/次）  "
          f"tokens in {tin} / out {tout}  ≈ ${tin / 1e6 * 0.042:.5f}")
    print(f"\n=== 概率最高的 {k} 条 ===")
    for p, i, s in top_k(sc, sents, k):
        # ⭐ **带上原始行号区间** —— 挑出来的东西必须能回到转录里定位
        ix = origin[i]
        tag = f"L{ix[0] + 1}" if len(ix) == 1 else f"L{ix[0] + 1}-{ix[-1] + 1}"
        print(f"  {p:.3f}  [{tag:>10}] {s[:104]}")
    got = [p for p in sc if p is not None]
    if got:
        print(f"\n分布: min {min(got):.2f}  max {max(got):.2f}  "
              f"中位 {sorted(got)[len(got) // 2]:.2f}   拿到 {len(got)}/{len(sents)} 条")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
