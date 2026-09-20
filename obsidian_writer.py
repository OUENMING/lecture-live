"""课堂记录落盘。

设计原则: **先落盘, 再询问** —— 运行中每句定稿就追加写入"会话文件",
所以崩溃 / 误按 Ctrl+C / 答"不保存" 都**不会丢内容**。

  sessions/<日期>_<时间>_<课程>.md   ← 运行中实时写的**原始逐句日志**(crash-safe)
        ↓ 结束时组装 + 询问
  <vault>/Lectures/<日期>_<课程>.md   ← **双层笔记**:
        复习层在上(英文知识点详解 / 英文自测 / 术语表 / 重点),
        完整逐句转录折叠在下。
        自测区**最前面**是"我课上问过的问题"(问答线程, 见 `close(qa=...)`)——
        自己卡过的问题才是复习的第一顺位。

⚠️ 双层不是"摘要 + 原文": 复习层只是**入口**, 下面那份转录是**逐字保留、未做任何删改**
的 —— 复习要的是"不遗漏", 摘要会把细节吃掉, 所以转录永远完整地在文件里。

复习层以**英文为主、中文为辅**: 知识点用英文陈述并展开细节, 中文只做点睛, 关键词给中英对照。
没设课程代码也能写(课程名默认 LECTURE)。

  --save-notes ask   结束时问(默认;答否则保留会话文件)
  --save-notes yes   不问,直接进 Obsidian
  --save-notes no    完全不写(会话文件也不建)
"""
from __future__ import annotations
import json, os, re, time
from pathlib import Path

DEFAULT_VAULT = "~/Obsidian/Vault"
DEFAULT_COURSE = "LECTURE"          # 没设课程代码时的占位名(线下课常不设)
SESSIONS = Path(__file__).with_name("sessions")

# 会话文件里一条定稿块的抬头: "> [!abstract] 14:02:18" / "... ⭐ Exam Focus"
_TS = re.compile(r"^> \[!abstract\] (\d\d:\d\d:\d\d)( ⭐ Exam Focus)?\s*$")
_FIELDS = (("en", "EN"), ("zh", "ZH"), ("asr", "ASR"))

REVIEW_CHUNK = 70           # 复习层每次请求喂多少句; 长课分块, 避免超长 JSON 被截断
REVIEW_MAX_TOKENS = 3000    # 单块输出上限

# 「我问过什么」(Phase 4) 在复习自测区里每条占**一行** `问题::答案` —— Spaced
# Repetition 插件按 `::` 切卡片, 所以不能有多行。讲解的自然长度实测 375–786
# completion token(见 cloud_translator.ANSWER_MAX_TOKENS 的实测注), 换算 1500–3200
# 字符, 直接铺进一行没法读。500 字符的依据: sessions/ 里 2555 句真实课堂英文,
# 中位句长 54 字符、p90 116 字符 —— 500 字符够装 4–9 句, 留得下 ANSWER_SYSTEM
# 结构里"先回答问题 + 它在课上哪一段"这两步, Obsidian 里折 ~4 行。这是**上限不是
# 目标**: 短回答原样保留。
QA_ANSWER_MAX_CHARS = 500

# 「我课上问过的问题」块的抬头行。刻意用 callout 而不是列表项: 列表项会被
# Spaced Repetition 当成潜在卡片, callout 不会。也不写"下面是自动生成的题" ——
# 没配 key 时下面根本没有自动生成的题, 那句话就成了假话。
QA_MARKER = "> [!question] 🙋 我课上问过的问题 · 先自测这些"

# `EN：` 英文辅助尾巴的上限。按 ANSWER_SYSTEM, 那一行只该是"几个词"(教授的原文
# 措辞/术语); 模型漂移写成长句时整行会涨到近 600 字符, 卡片背面就读不动了。
# 这只是安全网, 不是目标长度。
QA_AUX_MAX_CHARS = 120

_EN_KEEP = re.compile(r"^EN[：:]")          # ANSWER_SYSTEM 锁定的英文辅助行
_ASKED = "Question: "       # 格式契约见 cloud_translator.answer_user_content()

REVIEW_SYS = """你是课堂笔记助手, 为一名靠中文听英文课的中国经济学/社会学本科生整理复习层。
用户给你一节课**一段**的逐句中英对照转录。你只依据转录内容输出, 绝不引入外部知识、绝不猜测。

英文为主、中文为辅: 知识点用英文陈述并展开细节, 中文只做点睛, 关键词给中英对照。
输出严格 JSON, 结构如下:
{"title": "本课主题(英文, 不超过 12 词)",
 "overview_en": "一句话英文概括本段讲了什么(不超过 35 词)",
 "overview_zh": "同一句话的中文(不超过 60 字)",
 "sections": [
   {"heading": "英文小标题(一个知识板块, 如 'Internal energy as a state function')",
    "points": [
      {"en": "该知识点的英文陈述(1-2 句, 完整、能独立读懂)",
       "detail_en": "英文细节展开(2-5 句): 为什么重要 / 怎么用 / 关键细节 / 常见误解 / 例子",
       "zh": "中文辅助说明(1-2 句, 点睛即可, 不必逐句翻译英文)",
       "terms": [{"en": "本知识点最该记住的英文术语/短语", "zh": "对应中文"}]}
    ]}
 ],
 "qa": [{"q": "英文复习问题", "a": "英文答案(直接来自转录)", "zh": "答案的中文要点(不超过 30 字)"}]}

硬性要求:
- 英文为主: en / detail_en / heading / q / a 都用英文, 且完整准确; 中文只出现在 zh 字段与 terms 的中文侧。
- **穷尽本段的知识点与关键细节**: sections 给 2-6 个板块, 每个板块 points 2-6 条; 宁可多写, 不要漏要点, 也不要只挑一两个概括。
- 每条 point 的 terms 给 0-4 个英文术语 + 中文对照, 挑真正该记住的。
- qa 提 3-6 条, 覆盖核心概念 / 结论 / 易错点。
- 每个字段值都是**单行**: 不出现换行符、markdown 标记或引号。
- 只写转录里确实讲过的内容; 拿不准就不写。不要输出 JSON 以外的任何内容。"""

OVERVIEW_SYS = """你在为一节英文课堂的笔记写抬头。输入是这节课**知识点小标题**的列表(已按时间顺序)。
只依据这些标题, 输出严格 JSON:
{"title": "本课主题(英文, 不超过 12 词)",
 "overview_en": "一句话英文概括本课讲了什么(不超过 40 词)",
 "overview_zh": "同一句话的中文(不超过 60 字)"}
不要引入标题之外的信息。不要输出 JSON 以外的任何内容。"""


def _one_line(v) -> str:
    """压成单行; 非字符串 -> 空串。渲染层据此丢弃任何多余换行。"""
    return " ".join(v.split()) if isinstance(v, str) else ""


def _asked_question(turn_content: str) -> str:
    """从问答线程的 user turn 里取回**用户原话**。

    turn 正文由 `cloud_translator.answer_user_content()` 生成, 末尾那一段永远是
    `Question: <原话>`（前面可选的转录底座以空行隔开）。问题必须是用户自己的
    文字, 不重新生成、不改写 —— 复习钩子要的正是"我当时卡在哪"。
    拿不到就返回空串(这条问答不写进笔记), 绝不让猜出来的问题进笔记。
    """
    # ⚠️ 用**第一个**分隔符(partition)而不是最后一个(rpartition): 用户原话里若又
    # 出现一次 `\n\nQuestion: `, rpartition 只取后半段, 前半段**静默消失**(独立验证
    # 实测)。分隔符是我们自己追加在最后的, 而转录底座按句拼、不含空行, 所以第一个
    # 出现的就是真分隔符。
    head, sep, tail = turn_content.partition("\n\n" + _ASKED)
    if not sep:                       # 首次提问且当时还没有转录 -> 正文就是这一行
        if not turn_content.startswith(_ASKED):
            return ""
        return _one_line(turn_content[len(_ASKED):])
    return _one_line(tail)


def _qa_answer(raw: str) -> tuple[str, str]:
    """讲解原文 -> (单行**中文**正文, **英文**辅助)。

    ⚠️ 方向在 2026-09-18 反过来了(原设计是英文为主 + `中：`点睛): 作者实测后说
    "中文为主英文辅助吧 要不看不懂"。理由决定性 —— 讲解存在的全部意义就是让人
    **看懂**, 而他的英语不是母语(理解有词汇覆盖率阈值, 不到 90% 就是读不动)。
    英文并没有被丢掉, 而是换了位置: 讲清用中文, **教授的原文措辞/术语仍留一行英文**
    —— 考试是英文的, 那个词必须在考卷上认得出。

    `EN：` 行是 ANSWER_SYSTEM 要求的**独立行**。压进 `::` 一行时若原样内联, 中文
    段落里会嵌进一个英文短语 —— 所以把它整行抽出来, 按复习层既有的
    `　（EN：…）` 尾巴挂到行尾, 正文保持连续中文。
    `背景：` 行**保留在正文里**(它的标签本身是"这段不是课上讲的"的凭据, 摘掉即失真)。
    """
    body: list[str] = []
    keep: list[str] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _EN_KEEP.match(line)
        if m:
            g = _one_line(line[m.end():])
            if g:
                keep.append(g)
            continue
        body.append(line)
    return _one_line(" ".join(body)), " · ".join(keep)


def _truncate(text: str, limit: int) -> str:
    """超长回答截到 limit 字符, **只落在句末**; 整段没句末才退到词边界。

    切半句比短句子更难读(尤其对非母语阅读): 宁可早一句收, 不要留一个残句。

    ⚠️ 返回值**保证不超过 limit**。原来的写法 `cut = text[:limit]` 再加结尾的 " …"
    会到 limit+2(独立验证实测 499+2=502), 注释里的数字与实现不符。
    """
    if len(text) <= limit:
        return text
    cut = text[:max(1, limit - 2)]    # 先给结尾的 " …" 留两个字符
    if cut[-1] not in ".!?":          # 没停在句末 -> 退到最近的句末或词边界
        k = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        if k <= 0:
            k = cut.rfind(" ")
        cut = cut[:k + 1] if k > 0 else cut    # +1: 句号跟着句子
    return cut.rstrip() + " …"


def _clean_review(obj) -> dict:
    """LLM 返回的复习层 JSON -> 规范化的 dict, 缺字段/类型不对一律丢弃。"""
    if not isinstance(obj, dict):
        return {}
    r: dict = {}
    for src, dst in (("title", "title"), ("overview_en", "overview_en"),
                     ("overview_zh", "overview_zh")):
        v = _one_line(obj.get(src))
        if v:
            r[dst] = v
    sections = []
    for s in (obj.get("sections") or []):
        if not isinstance(s, dict):
            continue
        head = _one_line(s.get("heading"))
        pts = []
        for p in (s.get("points") or []):
            if not isinstance(p, dict):
                continue
            en = _one_line(p.get("en"))
            if not en:
                continue
            terms = []
            for tm in (p.get("terms") or []):
                if not isinstance(tm, dict):
                    continue
                te, tz = _one_line(tm.get("en")), _one_line(tm.get("zh"))
                if te:
                    terms.append((te, tz))
            pts.append({"en": en, "detail": _one_line(p.get("detail_en")),
                        "zh": _one_line(p.get("zh")), "terms": terms})
        if head and pts:
            sections.append({"heading": head, "points": pts})
    if sections:
        r["sections"] = sections
    qa = []
    for x in (obj.get("qa") or []):
        if not isinstance(x, dict):
            continue
        q, a = _one_line(x.get("q")), _one_line(x.get("a"))
        if q and a:
            qa.append({"q": q, "a": a, "zh": _one_line(x.get("zh"))})
    if qa:
        r["qa"] = qa
    return r


# 精修状态 -> 写进笔记的一行提醒。终端的 ⚠ 翻页就没了, 笔记得自己留证据:
# 一节课精修没生效时, 笔记外观与精修成功时**完全一样**(实测直播版 65% 的句子与原始 ASR 相同),
# 事后分不出来就意味着把未精修的转录当成了精修版在读。
_POLISH_NOTE = {
    "off": "⚠ 本课**未精修** —— 未配 API key 或精修已关, EN/ZH 为直播版。",
    "failed": "⚠ 本课**精修未生效** —— 调用失败、返回结构异常, 或一条都没采纳; EN/ZH 为直播版。",
    "partial": "⚠ 本课**部分批次精修失败** —— 失败批次保留直播版, 见下逐句转录。",
}


def _polish_state(stats: dict | None) -> str:
    """精修到底生效了没有。失败是 fail-soft 的(见 polish.polish_entries): 全失败时
    返回值与全成功时一模一样, 只改了几句话也算"生效" —— 所以判据是 applied。
    polish_entries 保证「一条都没采纳」的批次同样计入 failed, 于是 applied == 0
    必然 failed == batches, 归为 failed 是准确的。"""
    if not stats or stats.get("applied", 0) == 0:
        return "failed"
    return "partial" if stats.get("failed") else "ok"


class ObsidianWriter:
    def __init__(self, vault: str | None, course: str | None, mode: str = "ask",
                 api_key: str | None = None, model: str = "deepseek-flash",
                 glossary_path: str | None = None, polish: bool = True,
                 polish_model: str | None = None):
        self.mode = mode
        # 没设课程代码也照常落盘: 线下课常常没课号, 不该因此丢掉整节课的笔记。
        self.enabled = bool(vault) and mode != "no"
        self._vault = os.path.expanduser(vault) if vault else None
        self._course = course or DEFAULT_COURSE
        self._date = time.strftime("%Y-%m-%d")
        self._key = api_key                 # 有 key 才生成"知识点详解 + 自测"复习层
        self._model = model
        self._glossary_path = glossary_path or str(
            Path(__file__).with_name("glossary.txt"))
        self._polish = polish               # 落笔前二次精修(见 polish.py)
        self._polish_model = polish_model or model
        self._n = 0
        self.session_path: Path | None = None
        self.vault_path: Path | None = None
        if self.enabled:
            SESSIONS.mkdir(exist_ok=True)
            self.session_path = SESSIONS / (
                f"{self._date}_{time.strftime('%H%M%S')}_{self._course}.md")
            self.session_path.write_text(
                f"# {course} · {self._date} · 实时会话日志\n\n", encoding="utf-8")

    # ---- 运行中: 每句立刻落盘(flush) ----
    def append(self, en: str, zh: str, flagged: bool = False, raw: str = "") -> None:
        """`en` 是 LLM 修正后的英文, `raw` 是**原始 ASR 转录**。

        两个都记: 修正版好读, 原始版是"模型实际听到什么"的凭据 ——
        LLM 偶尔会过度修正(把正确的词改错), 没有原始转录就无从复核。
        """
        if not self.enabled or not self.session_path:
            return
        self._n += 1
        ts = time.strftime("%H:%M:%S")          # 定稿那一刻
        mark = " ⭐ Exam Focus" if flagged else ""
        lines = [f"> [!abstract] {ts}{mark}"]
        if en:
            lines.append(f"> **EN**: {en}")
        if zh:
            lines.append(f"> **ZH**: {zh}")
        if raw:
            lines.append(f"> **ASR**: {raw}")
        with self.session_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")

    @property
    def count(self) -> int:
        return self._n

    # ---- 解析原始日志 -> 条目 ----
    @staticmethod
    def _parse(text: str) -> list[dict]:
        entries: list[dict] = []
        cur = None
        for line in text.splitlines():
            m = _TS.match(line)
            if m:
                if cur:
                    entries.append(cur)
                cur = {"ts": m.group(1), "star": bool(m.group(2)),
                       "en": "", "zh": "", "asr": ""}
                continue
            if cur is None:
                continue
            for key, tag in _FIELDS:
                pre = f"> **{tag}**: "
                if line.startswith(pre):
                    cur[key] = line[len(pre):].strip()
                    break
        if cur:
            entries.append(cur)
        return entries

    # ---- 复习层素材 ----
    def _glossary(self, entries: list[dict]) -> list[tuple[str, str, str]]:
        """本课命中的术语 [(术语, 解析, 首次出现时间)]。纯本地查表, 零网络。"""
        try:
            from build_notes import TermNotes
        except Exception:                                 # noqa: BLE001
            return []
        notes = TermNotes()
        seen, out = set(), []
        for e in entries:
            for t, d in notes.match(e["en"] or e["asr"]):
                if t not in seen:
                    seen.add(t)
                    out.append((t, d, e["ts"]))
        return out

    def _call_review(self, transcript: str) -> dict:
        """对一段转录调一次 LLM, 返回规范化后的复习层 dict。失败 -> {}。"""
        try:
            import httpx
            payload = {"model": self._model, "stream": False,
                       "max_tokens": REVIEW_MAX_TOKENS,
                       "temperature": 0.3, "thinking": {"type": "disabled"},
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": REVIEW_SYS},
                                    {"role": "user", "content": transcript}]}
            r = httpx.post("https://api.deepseek.com/v1/chat/completions",
                           headers={"Authorization": f"Bearer {self._key}"},
                           json=payload, timeout=180)
            r.raise_for_status()
            obj = json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception:                                 # noqa: BLE001
            return {}
        return _clean_review(obj)

    def _overview(self, headings: list[str]) -> dict:
        """多块时用各块小标题再写一次**全课**总览。失败 -> {}（保留局部概览）。"""
        if not headings:
            return {}
        try:
            import httpx
            payload = {"model": self._model, "stream": False, "max_tokens": 500,
                       "temperature": 0.2, "thinking": {"type": "disabled"},
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": OVERVIEW_SYS},
                                    {"role": "user", "content": "\n".join(headings)}]}
            r = httpx.post("https://api.deepseek.com/v1/chat/completions",
                           headers={"Authorization": f"Bearer {self._key}"},
                           json=payload, timeout=60)
            r.raise_for_status()
            obj = json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception:                                 # noqa: BLE001
            return {}
        if not isinstance(obj, dict):
            return {}
        out = {}
        for k in ("title", "overview_en", "overview_zh"):
            v = _one_line(obj.get(k))
            if v:
                out[k] = v
        return out

    def _review(self, entries: list[dict]) -> dict:
        """LLM 生成复习层(英文知识点详解 + 概览 + 自测)。无 key / 失败 -> {}。

        长课**分块**生成再合并: 一堂 40 分钟的课有数百句, 一次性输出会撑爆
        max_tokens 被截断, 截断的 JSON 解析失败 -> 整份复习层丢失。分块既避开
        截断, 也让每段都被讲细。失败是**正常路径**(断网、没配 key 都要能落盘), 绝不抛。
        """
        if not self._key or not entries:
            return {}
        lines = [f"[{e['ts']}] {e['en'] or e['asr']}"
                 for e in entries if (e["en"] or e["asr"])]
        if not lines:
            return {}
        chunks = [lines[i:i + REVIEW_CHUNK]
                  for i in range(0, len(lines), REVIEW_CHUNK)]
        multi = len(chunks) > 1
        meta: dict = {}
        sections: list[dict] = []
        plain: list[str] = []                 # 未加时间前缀的小标题(供总览使用)
        qa: list[dict] = []
        for ci, chunk in enumerate(chunks):
            r = self._call_review("\n".join(chunk))
            if not r:
                continue
            if ci == 0:
                for k in ("title", "overview_en", "overview_zh"):
                    if r.get(k):
                        meta[k] = r[k]
            prefix = f"[{chunk[0][1:6]}] " if multi else ""   # [HH:MM]
            for sec in (r.get("sections") or []):
                plain.append(sec["heading"])
                if prefix:
                    sec = {"heading": prefix + sec["heading"],
                           "points": sec["points"]}
                sections.append(sec)
            qa.extend(r.get("qa") or [])
        # 多块时, 上面 meta 里的概览只描述第一段 —— 用小标题重写一份全课的。
        if multi and sections:
            full = self._overview(plain)
            if full:
                meta = full
        seen, dedup = set(), []
        for x in qa:
            k = x["q"].strip().lower()
            if k in seen:
                continue
            seen.add(k)
            dedup.append(x)
        out = dict(meta)
        if sections:
            out["sections"] = sections
        if dedup:
            out["qa"] = dedup[:8]
        return out

    # ---- 我课上问过什么(Phase 4) ----
    @staticmethod
    def _qa_items(history) -> list[str]:
        """问答线程 -> `- 问题::答案` 行(问题用原话, 答案压成单行)。

        **只写"我问过什么"**: 讲解正文不进笔记, 问答线程也不进 `sessions/`。
        为什么这些行就是复习钩子(而非又一个"记下来就算学了"的地方) —— 调研里那条
        3937 赞反思帖的原话是"先问 AI 再做成卡片, 最后只能学到关键词", 所以问过又
        不回来的问题必须自动变成复习项; `::` 这个格式 Spaced Repetition 插件直接认,
        零新增机制(PLAN-ai-explain-qa.md 0.6)。

        ⚠️ 收尾时 answer_worker **从不 join**: 它可能正在 `translator.answer()` 里,
        之后仍会往 history 追加。所以 `close()` 收到的是**快照**(list)或取快照的
        函数, 不是那个活着的 list 本身。
        """
        out: list[str] = []
        if not isinstance(history, list):
            return out
        for i, turn in enumerate(history):
            if not isinstance(turn, dict) or turn.get("role") != "user":
                continue
            q = _asked_question(turn.get("content") or "")
            if not q:
                continue
            nxt = history[i + 1] if i + 1 < len(history) else None
            body, gloss = ("", "")
            if isinstance(nxt, dict) and nxt.get("role") == "assistant":
                body, gloss = _qa_answer(nxt.get("content") or "")
            # 没有答案(收尾时答案还在流里 —— worker 从不 join, 快照里就没有它)或
            # 整条就是一句报错(main.py 的失败兜底)时: 只留问题、**不加 `::`**。
            # 空答案的卡片比没有卡片更糟; 但问题本身是"我卡在哪"的唯一凭据,
            # 静默丢掉它等于把这条钩子废掉 —— 所以留着, 只是不假装能自测。
            # ⚠️ 问题里出现 `::` 会**伪造一个卡片边界**: Spaced Repetition 按第一个
            # `::` 切, 于是正面只剩半句、背面粘着问题剩下的部分(独立验证实测:
            # "What is a::b in stats?" 被切成 front="What is a")。插一个空格拆开 ——
            # 读起来还是 "a::b", 但不再被当分隔符。
            qd = q.replace("::", ": :")
            # 没有答案(收尾时答案还在流里 —— worker 从不 join, 快照里就没有它)或
            # 整条就是一句报错(main.py 的失败兜底)时: 只留问题、**不加 `::`**。
            # 空答案的卡片比没有卡片更糟; 但问题本身是"我卡在哪"的唯一凭据,
            # 静默丢掉它等于把这条钩子废掉 —— 所以留着, 只是不假装能自测。
            if not body or body.startswith("⚠"):
                out.append(f"- {qd}　（这次没等到回答）")
                continue
            tail = ""
            if gloss:
                g = (gloss if len(gloss) <= QA_AUX_MAX_CHARS
                     else gloss[:QA_AUX_MAX_CHARS].rstrip() + "…")
                tail = f"　（EN：{g}）"
            out.append(f"- {qd}::{_truncate(body, QA_ANSWER_MAX_CHARS)}{tail}")
        return out

    # ---- 组装双层笔记 ----
    def _render_note(self, entries: list[dict], review: dict,
                     qa_items: list[str] | None = None,
                     polish_state: str = "ok") -> str:
        ts0 = entries[0]["ts"] if entries else "—"
        ts1 = entries[-1]["ts"] if entries else "—"
        stars = [e for e in entries if e["star"]]
        gloss = self._glossary(entries)
        sections = review.get("sections") or []
        qa = review.get("qa") or []
        title = review.get("title")
        L: list[str] = []
        L += ["---",
              f"date: {self._date}",
              f"course: {self._course}",
              "tags: [lecture, live-transcript, review]",
              "type: lecture-notes",
              f"polish: {polish_state}",
              "---", "",
              f"# {self._course} · 课堂笔记 {self._date}", "",
              "> [!info] 本课信息", ]
        if title:
            L.append(f"> 📖 **{title}**")
        L += [f"> 🕐 {ts0} – {ts1} · 🗣 {len(entries)} 句 · "
              f"⭐ {len(stars)} 处重点 · 💡 {len(gloss)} 个术语"]
        _warn = _POLISH_NOTE.get(polish_state)
        if _warn:
            L.append(f"> {_warn}")
        L.append("")

        # 概览: 英文为主, 中文辅助
        L += ["## 🎯 Overview 概览", ""]
        oe, oz = review.get("overview_en"), review.get("overview_zh")
        if oe or oz:
            if oe:
                L.append(f"> **EN** — {oe}")
            if oz:
                L.append(f"> **中** — {oz}")
            L += [">", "> <sub>🤖 自动生成 · 依据本课转录</sub>"]
        else:
            L += ["> *（未自动生成 —— 未配 API key 或调用失败）*"]
        L += [""]

        # 知识点详解: 英文陈述 + 英文细节 + 中文点睛 + 关键词中英对照
        L += ["## 📚 Key Concepts 知识点详解", ""]
        if sections:
            for i, sec in enumerate(sections, 1):
                L += [f"### {i}. {sec['heading']}", ""]
                for p in sec["points"]:
                    L.append(f"- **{p['en']}**")
                    if p["detail"]:
                        L.append(f"  {p['detail']}")
                    if p["zh"]:
                        L.append(f"  · 中：{p['zh']}")
                    if p["terms"]:
                        pairs = " · ".join(
                            f"`{t}` {z}" if z else f"`{t}`" for t, z in p["terms"])
                        L.append(f"  · 🔑 {pairs}")
                    L.append("")
        else:
            L += ["*（未自动生成 —— 未配 API key 或调用失败；可课后自行补）*", ""]

        # 复习自测: 英文问答, 中文要点附后; 保持 `问题::答案` 便于 Spaced Repetition
        L += ["## ❓ Review 复习自测", ""]
        # 自己问过的问题排在**最前**: 卡住过的地方才是复习的第一顺位, 也免得被自动
        # 生成的那 8 条挤到看不见。注意这里是**插进已有区块**, 不是另开一节 ——
        # 同一份内容写两遍(可读区一遍 + `::` 一遍)只会变噪音。
        if qa_items:
            L += [QA_MARKER, *qa_items, ""]
        if qa:
            L += ["> [!tip] 🤖 自动生成 · 请核对后再用于复习",
                  "> <sub>写成 `问题::答案`，可被 Spaced Repetition 插件识别</sub>", ""]
            for x in qa:
                tail = f"　（中：{x['zh']}）" if x["zh"] else ""
                # 题干里的 `::` 必须拆开 —— Spaced Repetition 按第一个 `::` 切,
                # 否则「什么是 a::b」会被切成 front="什么是 a"。与用户提问路径
                # (_qa_items) 同一处理, 那边早就消毒了, 这边漏了。
                q_text = x["q"].replace("::", ": :")
                L.append(f"- {q_text}::{x['a']}{tail}")
        else:
            L += ["> [!tip] 用 `问题::答案` 写自测题（可被 Spaced Repetition 插件识别）", "",
                  "- "]
        L += [""]

        L += ["## 💡 Terminology 本课术语表", ""]
        if gloss:
            L += [f"- **{t}** — {d}　`{ts}`" for t, d, ts in gloss]
        else:
            L += ["*（本课没有命中术语表）*"]
        L += [""]

        L += ["## ⭐ 我标记的重点", ""]
        if stars:
            for e in stars:
                L += [f"- `{e['ts']}` {e['zh'] or e['en'] or e['asr']}"]
                if e["en"] and e["zh"]:
                    L += [f"  - EN: {e['en']}"]
        else:
            L += ["*（课上没按 ⭐；觉得哪句重要就按一下，会自动归到这里）*"]
        L += [""]

        # 完整转录: 折叠 callout 包全套逐句块。**逐字保留**, 是这份笔记的底座。
        L += [f"## 📜 完整逐句转录（点击展开 · {len(entries)} 句）", "",
              "> [!note]- 逐句双语 + 原始 ASR（未做任何删改）", "> "]
        for e in entries:
            L.append(f"> > [!abstract] {e['ts']}" + (" ⭐" if e["star"] else ""))
            if e["en"]:
                L.append(f"> > **EN**: {e['en']}")
            if e["zh"]:
                L.append(f"> > **ZH**: {e['zh']}")
            if e["asr"]:
                L.append(f"> > **ASR**: {e['asr']}")
            L.append("> ")
        return "\n".join(L) + "\n"

    # ---- 结束: 询问是否进 Obsidian ----
    def close(self, ask=None, qa=None) -> str:
        """`qa` = 问答线程 history 的快照(list), 或**取快照的函数**。

        ⚠️ 不能走构造函数: writer 在 run() 里**先**建, qa 状态比它晚。
        ⚠️ 要函数而不是 list 的场景: 收尾里答案可能还在流(`answer_worker` 是 daemon,
        从不 join), 而"询问是否保存"可以停很久 —— 早取的快照会把这轮问答整条丢掉
        (问题还在, 答案没了)。函数形式把"取"推迟到真正要渲染的那一刻。
        ⚠️ 问答**只进 vault 笔记**, 不进 `sessions/` —— 那份逐句日志是三方共享
        契约(见 CLAUDE.md), 语义也不同(问答不是"课上讲了什么")。
        """
        if not self.enabled or not self.session_path:
            return ""
        if self._n == 0:
            # ⚠️ 原来是 `self._n == 0` 直接 return —— 但"有提问、零句转录"(麦克风故障,
            # 或开课十几秒就问了一句然后退出)会把**问答静默丢掉**, 而问题正是这个
            # 功能唯一要保住的东西(它不进 sessions/, 终端 echo 也不落盘, 丢了就真没了)。
            # 所以零句时再探一次"有没有问答", 有就继续往下走去写笔记。
            # 取舍: 这一次探测取快照**早于**保存询问, 而 callable 的意义正是把取快照
            # 推迟到询问之后。零句场景下没有转录在跑, "答案晚到"那个顾虑不成立 ——
            # 折中是有界的。
            try:
                n_qa = len(qa() if callable(qa) else qa) if qa else 0
            except Exception:                     # noqa: BLE001
                n_qa = 0
            if not n_qa:
                return ""
        save = self.mode == "yes"
        if self.mode == "ask":
            save = True if ask is None else bool(ask(self._n))
        if not save:
            return (f"📝 未存入 Obsidian({self._n} 句)。"
                    f"记录仍保留在:\n   {self.session_path}")

        entries = self._parse(self.session_path.read_text(encoding="utf-8"))
        # 落笔前二次精修: 直播矫正太保守(实测 65% 未改), 这里用领域 + 全课术语 + 前后文重做一遍。
        polish_state = "off"
        if self._key and self._polish and entries:
            try:
                from translator import course_term_list, course_title
                from polish import polish_entries
                print("🔧 正在精修转录(二次矫正 + 重译)…", flush=True)
                pstats: dict = {}
                entries = polish_entries(
                    entries, self._key, self._polish_model,
                    course_term_list(self._glossary_path, self._course),
                    course_title(self._glossary_path, self._course),
                    on_progress=print, stats=pstats)
                polish_state = _polish_state(pstats)
                if polish_state == "failed":
                    print(f"⚠ 精修未生效(applied=0 句, 失败批次 "
                          f"{pstats.get('failed')}/{pstats.get('batches')}); "
                          f"状态已写进笔记 frontmatter")
                elif polish_state == "partial":
                    print(f"⚠ 精修只生效了一部分(applied={pstats.get('applied')} 句, "
                          f"失败批次 {pstats.get('failed')}/{pstats.get('batches')}); "
                          f"状态已写进笔记 frontmatter")
            except Exception as e:                        # noqa: BLE001
                polish_state = "failed"
                print(f"⚠ 精修失败({str(e)[:60]}); 用直播版转录生成笔记")
        if self._key:
            print("🤖 正在生成复习层(知识点详解 + 自测)…", flush=True)
        review = self._review(entries)
        # 问答行自带 try/except: close() 这里**没有**异常护栏, 外层 finally 只接
        # KeyboardInterrupt —— 渲染问答抛出去就再也没有那份转录笔记了。隔离是硬要求:
        # 笔记的底座是逐句转录, 它是不能丢的那件事; 问答只是额外一层。
        qa_items: list[str] = []
        if qa:
            try:
                qa_items = self._qa_items(qa() if callable(qa) else qa)
            except Exception as e:                        # noqa: BLE001
                print(f"⚠ 问答落盘失败({str(e)[:60]}); 笔记照常生成")
        note = self._render_note(entries, review, qa_items, polish_state)

        d = Path(self._vault) / "Lectures"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{self._date}_{self._course}.md"
        if path.exists():
            # 同日同课已有一份(lecture + tutorial 常共用同一课号, 一天两节很现实):
            # 直接覆盖会把上一节的笔记抹掉 —— 会话日志还在 sessions/, 但复习层
            # 只此一份。加时间戳并存, 不覆盖。
            path = d / f"{self._date}_{time.strftime('%H%M%S')}_{self._course}.md"
        self.vault_path = path
        self.vault_path.write_text(note, encoding="utf-8")
        layer = ("知识点详解+自测+术语表+重点" if review.get("sections")
                 else "术语表+重点(未生成详解)")
        return (f"📝 已存入 Obsidian({self._n} 句, 复习层: {layer}) → {self.vault_path}\n"
                f"   原始逐句日志: {self.session_path}")
