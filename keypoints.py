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
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

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


def score(sents, *, ask) -> list:
    """一批句子 → 每句一个概率（`None` = 这次没拿到）。

    ⚠️ **`ask` 是注入进来的**（照 `codebase-design`「接受依赖，不要自己造依赖」）：
       判据传个假 `ask` 就能跑，不用联网。生产那条是 `ask_commandcode`。
    ⚠️ 超过 `CHUNK` 就分批 —— 每批各自带一份 `state`（⚠️ 该服务**没有 prompt cache**，
       所以 state 是被重复计费的）。
    """
    sents = list(sents or ())
    out: list = []
    for lo in range(0, len(sents), CHUNK):
        part = sents[lo:lo + CHUNK]
        # ⚠️⚠️ **单批失败不许连坐**（2026-10-01 实测）：一节 50 分钟的课要打
        #    29 批，原来**任何一批**网络抖一下（超时/429/5xx）就把它抛给
        #    `pick` 那个大 `try` → **整节清零** → 🎯 那节整块静默消失，
        #    而且**偶发**（重跑一次可能就好），是最难查的一类。
        #    单批挂了只让这一批记 `None` —— 本函数的语义本来就是
        #    「`None` = 这次没拿到」（见 docstring），其余批次照常。
        try:
            got = parse_answers(ask(build_state(part),
                                    build_questions(len(part))), len(part))
        except Exception:                                     # noqa: BLE001
            got = [None] * len(part)
        out.extend(got)
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

    ⭐ 优先级：**有官方 key 就走官方**（能钉版本号）→ 否则 CommandCode
       → 都没有则 `token` 为空（= 功能关着，同本模块一贯的 fail-soft）。
    ⚠️ 解析一次就缓存：跑一节 50 分钟的课要调十几次，而 key 文件不会中途变。
    """
    global _PROVIDER
    if _PROVIDER is None:
        off = _read_key("jev_key", root=root)
        cc = token(root=root)
        if off:
            _PROVIDER = {"name": "typesafe", "endpoint": OFFICIAL_ENDPOINT,
                         "model": OFFICIAL_MODEL, "token": off}
        else:
            _PROVIDER = {"name": "commandcode", "endpoint": ENDPOINT,
                         "model": MODEL, "token": cc}
    return _PROVIDER


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
    req = urllib.request.Request(
        ep, data=body,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json",
                 "User-Agent": UA})
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
    except Exception:                                         # noqa: BLE001
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
    with sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True) as con:
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
