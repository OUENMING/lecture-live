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
import bisect, json, os, re, time
from pathlib import Path

import paths

DEFAULT_COURSE = "LECTURE"          # 没设课程代码时的占位名(线下课常不设)
SESSIONS = Path(__file__).with_name("sessions")


def resolve_vault(cli: str | None = None, *, env=None, root=None) -> str | None:
    """Obsidian 库路径：`--vault` → `$OBSIDIAN_VAULT` → `~/.classlive/vault` → **None**。

    ⚠️⚠️ **没有兜底目录。** 返回 `None` = 不写 Obsidian（会话照常落 `sessions/`，
       那才是主记录 —— `ObsidianWriter.enabled` 本来就是这么用的）。

    这里**曾经**有一条 `~/Obsidian/Vault` 兜底，实测后果：
    双击 `.app` 启动时 `$OBSIDIAN_VAULT` **不在环境里**（它只定义在 `~/.zshrc`，
    Finder 起的是 launchd 环境），于是笔记被写进一个**用户从没选过的目录** ——
    `~/Obsidian/Vault/Lectures/` 里躺着 9 个笔记，而那个目录连 `.obsidian` 都没有。
    「凭空造一个目录再把笔记放进去」比「不写」坏得多：用户永远找不到它们。
    """
    if cli:
        return os.path.expanduser(cli)
    env = os.environ if env is None else env
    if env.get("OBSIDIAN_VAULT"):
        return os.path.expanduser(env["OBSIDIAN_VAULT"])
    p = paths.vault_config(root=root)
    try:
        t = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return os.path.expanduser(t) if t else None


def remember_vault(vault: str, *, root=None) -> None:
    """把用户选过的库记下来 —— **下次双击启动（没有 shell 环境）才找得到它**。

    ⚠️ 失败**不出声**：记不住只影响下一次，本次照常运行，为它中断录课不值得。
    """
    p = paths.vault_config(root=root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(vault.strip() + "\n", encoding="utf-8")
    except OSError:
        pass


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

# ---- ❓「没听懂」：按下只记时刻，回退范围**课后**才算 ------------------------
# 为什么是课后：意识到没懂时话已经过去几句了，再加字幕延迟 —— 所以记的是
# 「回退一段」，不是「这一句」。常数全部来自**实测**（20 节非测试课 / 5026 句）：
#   回退 15s 覆盖的句数  p05=2  p50=4  p95=7  max=16
#   相邻句落盘间隔 ≤3s 占 57%（同一段语音内）· ≥8s 占 36%（段与段之间）
# ⚠️ 这两个数**不引 Thiede 2003 当依据** —— 那是「读完文章延迟写关键词再判断
#    学没学会」，与「回退 20 秒标一段」是两回事。窗口就是参数，用真实按下时间校准。
LOST_TAIL = ".lost.jsonl"
LOST_LOOKBACK_S = 15.0      # 作者原话「回退十几秒」
LOST_MIN_SENT = 3           # 15s 窗口 p05=2；慢速段落会短到 2，兜到 3
LOST_MAX_SENT = 8           # p95=7；封顶才读得动
LOST_BURST_S = 3.0          # 段内间隔 ≤3s —— 用它把两端吸到整段语音的边界


def _hms_sec(t) -> int | None:
    """`HH:MM:SS` -> 当日秒数。解不出返回 `None`（**不抛**）。"""
    try:
        h, m, s = str(t).split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except (ValueError, AttributeError):
        return None


def _hms_str(sec: int) -> str:
    """当日秒数 -> `HH:MM:SS`（`_hms_sec` 的逆，只给显示用）。"""
    sec = int(sec) % 86400
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def note_path_for(vault, date: str, course: str) -> Path:
    """今天这门课的笔记路径。**同日同课已有一份就加时间戳并存，绝不覆盖。**

    ⚠️⚠️ **抽成模块级函数是为了让判据指得到它**（2026-09-28 审查指出）：
       这段逻辑原来内联在落盘那一步里，而 `tests/test_audit_regressions.py` 的 R4
       断言的是**测试里手抄的一份副本** —— 生产把冲突处理删掉/改坏，R4 照样绿。
       同 `entry_panel.card_title` / `courses.readiness` 那几条：**判据要指向那个位置**。

    ⚠️ 为什么是"并存"而不是覆盖：lecture + tutorial 常共用同一课号，一天两节很现实。
       覆盖会把上一节的笔记抹掉 —— 会话日志还在 `sessions/`，但**复习层只此一份**。
    """
    d = Path(vault) / "Lectures"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{date}_{course}.md"
    if p.exists():
        p = d / f"{date}_{time.strftime('%H%M%S')}_{course}.md"
    return p


def monotone_secs(tss) -> list[int]:
    """`HH:MM:SS` 列表 -> **单调不减**的当日秒数。

    ⚠️ **不许拿字符串直接比大小**：跨午夜时 `00:05` 会排在 `23:50` 前面，
    而系统休眠还会让墙钟跳。遇到「比上一条小」就 +1 天（86400）—— 一节课不会
    跨两天，这一步是把序列拉直，不是在猜时区。
    """
    out: list[int] = []
    day, prev = 0, -1
    for t in tss:
        s = _hms_sec(t)
        if s is None:
            continue
        while s + day < prev:
            day += 86400
        prev = s + day
        out.append(prev)
    return out


def resolve_lost_range(press_sec: int, secs: list[int], *,
                       i_max: int | None = None) -> tuple[int, int] | None:
    """按下时刻（当日秒数）-> entries 的**整句**下标区间 `[i0, i1]`（闭区间）。

    入参全是纯数据、返回纯数据 —— **这条就是本特性的深度所在**，AppKit 与文件
    都不在这条路上（`R15` 钉的就是它）。`secs` 是 `monotone_secs` 的输出（升序）。

    - 锚点 `i1` = 最后一条 ≤ `press_sec` 的句子；**一条都没有**（开课头十几秒就按了）
      则取 0 —— 那是"开头那几句"的**真话**，不假装知道更多
    - 起点从 `press_sec - LOST_LOOKBACK_S` 起，再往前吸到**整段语音的开头**
      （间隔 ≤ `LOST_BURST_S` 就一直往前）；终点同理往后 —— **不交回半段**
    - 兜底：不足 `LOST_MIN_SENT` 往前补；超过 `LOST_MAX_SENT` **只从旧的那头削**
      （宁可多给一句让人读，也不能把真没听懂的那句削掉）
    """
    n = len(secs) if i_max is None else min(len(secs), i_max + 1)
    if n <= 0:
        return None
    i1 = bisect.bisect_right(secs, press_sec, 0, n) - 1
    if i1 < 0:
        i1 = 0
    i0 = max(0, bisect.bisect_left(secs, press_sec - LOST_LOOKBACK_S, 0, n))
    # ⚠️ 夹到 `i1`：按下时刻远在最后一句**之后**（窗口整段落在末尾之外）时,
    #    `bisect_left` 会返回 `n`（全小于下界），随后 `secs[i0]` 直接越界。
    #    R15 的遍历用例抓到的就是这个。
    i0 = min(i0, i1)
    while i0 > 0 and secs[i0] - secs[i0 - 1] <= LOST_BURST_S:
        i0 -= 1
    while i1 + 1 < n and secs[i1 + 1] - secs[i1] <= LOST_BURST_S:
        i1 += 1
    if i1 - i0 + 1 < LOST_MIN_SENT:
        i0 = max(0, i1 - LOST_MIN_SENT + 1)
    if i1 - i0 + 1 > LOST_MAX_SENT:
        i0 = i1 - LOST_MAX_SENT + 1
    return i0, i1

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


def _flat(v: str) -> str:
    """把可能带换行的文本压成一行。

    ⚠️ 会话格式是三方共享契约(callout 行 + `_parse` 只取每个字段的**第一行**):
    值里混进换行会同时造成两件事 —— ① 插出没有 `>` 前缀的裸行, callout 结构裂开;
    ② 第二行起被 `_parse` 静默丢弃, 最终笔记缺内容而外观完全正常。
    实测 187 份真实会话里一次都没发生过, 但这是**静默**的, 所以落盘前压一下。
    (2026-09-24 OCR 发现。)"""
    return " ".join((v or "").split())


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
    # ⚠️ 用户主动跳过（Ctrl+C / 卡上的 [跳过精修]）。它**不是失败**（调用没出错），
    #    所以单独一档 —— 写成 failed 会让人以为 API 出问题了，然后去查一个不存在的问题。
    #    ⚠️ 措辞要同时盖住两种情形：① 一批都没跑（KeyboardInterrupt 在半路抛的，
    #       `entries` 原封不动）② 跑到某个批次边界才取消（前面几批**已经生效**）。
    #       所以不能写死「EN/ZH 为直播版」。
    "interrupted": "⚠ 本课**精修被跳过** —— 未跑到的那部分是直播版，已生效的批次保留。",
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
                 polish_model: str | None = None, keypoints=None,
                 keypoints_fn=None):
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
        #: 🎯 Jev 的重点句：`[(概率, 句子, [原始行下标]), …]`。
        #: ⚠️ **空表 = 不加那一节**（没配 token / raw 档 / 调用失败）。
        self._keypoints = list(keypoints or ())
        #: ⚠️ 句子**到收尾才齐**，所以生产那条走**回调**（`close()` 里现算），
        #:    不是构造时传值。传值那条留给判据。
        self._keypoints_fn = keypoints_fn
        self._n = 0
        self.session_path: Path | None = None
        self.vault_path: Path | None = None
        self._lost_h = None                 # ❓ 旁路文件句柄(懒开, 见 mark_lost)
        if self.enabled:
            SESSIONS.mkdir(exist_ok=True)
            self.session_path = SESSIONS / (
                f"{self._date}_{time.strftime('%H%M%S')}_{self._course}.md")
            self.session_path.write_text(
                f"# {self._course} · {self._date} · 实时会话日志\n\n", encoding="utf-8")

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
            lines.append(f"> **EN**: {_flat(en)}")
        if zh:
            lines.append(f"> **ZH**: {_flat(zh)}")
        if raw:
            lines.append(f"> **ASR**: {_flat(raw)}")
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

    def _review(self, entries: list[dict], on_progress=None) -> dict:
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
            # ⚠️ `review` 这个 stage 名定义在 `polish.STAGE_NAME` 里却**从来没有发出过**
            #    （2026-09-28 OCR 审计发现）—— 复习层按 `REVIEW_CHUNK` 分块、579 句
            #    不止一轮 LLM，而收尾卡上这一段**一点进度都看不到**，正是把收尾搬进
            #    worker + 做卡片的主要动机所在。这里补上。
            if on_progress is not None:
                try:
                    on_progress("review", ci, len(chunks))
                except Exception:                         # noqa: BLE001
                    pass
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
        # ⚠️ 用 update 而非整体替换: _overview 只返回它真正生成出来的字段, 模型漏一个
        # (比如只回了 title) 时整体替换会把第一段已算好的概览整段抹掉。
        if multi and sections:
            full = self._overview(plain)
            if full:
                meta.update(full)
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

    # ---- ❓「没听懂」：旁路文件 + 课后反查 ----
    def _lost_path(self) -> Path | None:
        """`sessions/<同名>.lost.jsonl`。

        ⚠️ **旁路文件，绝不改会话抬头。** 抬头正则 `_TS` 是**行尾锚定**的，而会话
        格式是 `obsidian_writer` 写 / `_parse` 读回 / `cl last` grep 的**三方共享
        契约** —— 抬头多一个后缀，`_parse` 就认不出那一条，会把它的 EN/ZH/ASR
        静默盖到**上一条**头上（上一条被替换、这一条消失）。
        与 `testmode` 的 `<stem>.report.json` 同一个套路。
        """
        if not self.session_path:
            return None
        return Path(str(self.session_path.with_suffix("")) + LOST_TAIL)

    def _lost_handle(self):
        """懒开常驻句柄。**不注册 atexit** —— 每次按下都 flush 了，没有缓冲尾巴。

        ⚠️ **关过之后不再开**（`_lost_closed`）：`close_lost()` 跑在**收尾 worker** 上，
           而 `mark_lost()` 是 AppKit 主线程回调 —— 收尾能跑十几分钟（精修 / 复习层），
           这期间 ❓ 按钮**仍然可点**（`overlay.wrapup_show` 只换收尾卡自己的按钮）。
           少了这个标志，那次按下会**重新开一个再没人关的句柄**，而且这条标记
           进不了已经建好的笔记，`mark_lost` 却仍返回 True（2026-09-28 审查指出）。
        """
        if getattr(self, "_lost_closed", False):
            return None
        if self._lost_h is None:
            p = self._lost_path()
            if p is None:
                return None
            self._lost_h = p.open("a", encoding="utf-8")
        return self._lost_h

    def mark_lost(self) -> bool:
        """按一下 ❓：把**这一刻**立刻追加到旁路文件。返回是否写成。

        ⚠️ 这是 AppKit 主线程回调，所以只做「拼一行 + 写 + flush」，不联网不 sleep。
        `flush()` 必须（Python 的 8KB 缓冲正是 `testmode.py` 文档里那个坑）；
        `fsync` 刻意不做 —— 会话 `.md` 本身也只到页缓存，只给旁路文件更硬的保证
        是**假契约**。
        """
        p = self._lost_path()
        if p is None:
            return False
        try:
            h = self._lost_handle()
            if h is None:
                return False
            h.write(json.dumps({"epoch": round(time.time(), 3),
                                "t": time.strftime("%H:%M:%S"),
                                "n": self._n, "kind": "lost"},
                               ensure_ascii=False) + "\n")
            h.flush()
            return True
        except Exception as e:                            # noqa: BLE001
            # 记不下来是"少一个标记", 不是"课跑不下去" —— 与 testmode 同一条纪律
            print(f"⚠ ❓ 记录失败({str(e)[:60]}); 课堂不受影响")
            return False

    def close_lost(self) -> None:
        """幂等。文件**不删** —— 它是这份笔记的证据（与 `_POLISH_NOTE` 同一条纪律）。

        ⚠️ 置 `_lost_closed` 是为了让后来的 `mark_lost` **别再开新句柄**（见 `_lost_handle`）。
        """
        self._lost_closed = True
        h = getattr(self, "_lost_h", None)
        if h is not None:
            try:
                h.close()
            except Exception:                             # noqa: BLE001
                pass
            self._lost_h = None

    # ---- 原子层：旁路文件 `sessions/<同名>.atoms.jsonl`（plan §1）----
    def _atom_writer(self):
        """懒建 `atom.AtomWriter`。⚠️ **复用那个类，不在这里重写路径拼法** ——
        路径形状（`<同名>.atoms.jsonl`）在那儿是唯一定义点。"""
        w = getattr(self, "_atom_w", None)
        if w is None and self.session_path:
            import atom as atom_mod
            w = atom_mod.AtomWriter(self.session_path)
            self._atom_w = w
        return w

    def append_atoms(self, atoms) -> int:
        """写一批 atom。⚠️ **不做任何 LLM 调用** —— 那些在 worker 里。

        ⚠️ 它跑在 **`main.atom_worker` 那个后台线程上**（旧注释写"这里是主线程回调"，
           与事实不符 —— `mark_lost` 才是主线程回调；留着会误导后来者按 AppKit
           主线程的约束去改这里。2026-09-28 审查指出）。
        ⚠️⚠️ **必须 fail-soft**（与 `mark_lost` 同一条纪律）：调用点
           `main._atom_flush()` 在 `atom_worker` 的循环里**没有被 try 包住**
           （那里只包了 `atomq.get`）—— 这里一抛，那个 daemon 线程就**直接死掉**，
           此后整节课的 atom 再也不落盘，日志里只留一次 traceback。
           写不下来是"少几条要点"，不是"课跑不下去"。
        """
        try:
            w = self._atom_writer()
            return w.append(atoms) if w is not None else 0
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ atom 落盘失败({str(e)[:60]}); 课堂不受影响")
            return 0

    def close_atom(self) -> None:
        """幂等。文件**不删**（它是这份笔记的证据，同 `close_lost`）。"""
        w = getattr(self, "_atom_w", None)
        if w is not None:
            try:
                w.close()
            except Exception:                             # noqa: BLE001
                pass
            self._atom_w = None

    def _lost_marks(self) -> list[tuple[int, int | None]]:
        """旁路文件 -> `[(按下时刻的当日秒数, 按下时已定稿几句), ...]`。

        ⚠️ **第二个值必须带上**：它是「按下时到底有没有东西可回退」的唯一凭据。
        2026-09-28 从**真实按下**里发现的缺陷：一段 mic 会话开了 20 秒、一句都还没
        定稿，这时按 ❓ 的事件 `n = 0`，而反查把**第一句**当答案返回了 —— 于是
        「回退到」指着一个**比按下时刻晚 19 秒**的句子。`n` 就是为拦这个而记的
        （见 `resolve_lost_range` 的 `i_max`）。`n` 缺失/非整数 -> `None`（不设上界）。

        ⚠️ 这是**系统边界**（文件可能是上一个进程写的、末行可能被崩坏截断），
        所以逐行 try/except、坏行跳过 —— 与 `_parse` 对会话文件的宽容度一致。
        """
        p = self._lost_path()
        if p is None or not p.exists():
            return []
        out: list[tuple[int, int | None]] = []
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line) or {}
                    s = _hms_sec(row.get("t"))
                except Exception:                         # noqa: BLE001
                    continue
                if s is None:
                    continue
                n = row.get("n")
                out.append((s, int(n) if isinstance(n, int) else None))
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ ❓ 旁路文件读不出({str(e)[:60]}); 笔记照常生成")
        return out

    def _lost_items(self, entries: list[dict]) -> list[str]:
        """旁路事件 -> 笔记里的块（**一块一个标记**，块内已含它的那几句）。

        范围反查全在 `resolve_lost_range`（纯函数）；这里只做 I/O 与拼字。
        """
        marks = self._lost_marks()
        if not marks or not entries:
            return []
        # `ts` 解不出的 entry 不进 secs，否则下标会与 entries 错位 —— 用 idx 映射回去
        idx = [i for i, e in enumerate(entries) if _hms_sec(e.get("ts")) is not None]
        if not idx:
            return []
        secs = monotone_secs([entries[i]["ts"] for i in idx])
        out: list[str] = []
        for t, n_at_press in marks:
            # ⚠️ `i_max` **必须传**：它把范围封在"按下那一刻已经存在的句子"之内。
            #    不传的话，按下时一句都没有（`n = 0`）会把**第一句**当答案返回，
            #    而那一句可能比按下时刻晚十几秒 —— 那就成了"往后指"。（真事。）
            i_max = None if n_at_press is None else n_at_press - 1
            rng = resolve_lost_range(t, secs, i_max=i_max)
            if rng is None:
                continue          # 按下时还没有可回退的句子 -> 不编一个出来
            j0, j1 = rng
            i0, i1 = idx[j0], idx[j1]
            lines = [f"- 按于 `{_hms_str(t)}` · 这几句（`{entries[i0]['ts']}`–"
                     f"`{entries[i1]['ts']}`，共 {i1 - i0 + 1} 句）"]
            for e in entries[i0:i1 + 1]:
                lines.append(f"  - `{e['ts']}` {e['zh'] or e['en'] or e['asr']}")
            out.append("\n".join(lines))
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
                     polish_state: str = "ok",
                     lost_items: list[str] | None = None) -> str:
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
        # ⚠️ 汇总行**不再数 ⭐**（2026-09-29）：⭐ 按钮已并进 ❓，新会话里 ⭐ 恒为 0，
        #    「⭐ 0 处重点」是在告诉用户一个**已经不存在**的动作。
        #    历史会话（`rebuild_note.py` 重建）里那两处 ⭐ 由下面那一节照旧渲染。
        L += [f"> 🕐 {ts0} – {ts1} · 🗣 {len(entries)} 句 · "
              f"💡 {len(gloss)} 个术语 · ❓ {len(lost_items or [])} 处标记"]
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
                  "> <sub>写成 `问题::答案`；装了 Spaced Repetition 插件就能当卡片复习</sub>", ""]
            for x in qa:
                tail = f"　（中：{x['zh']}）" if x["zh"] else ""
                # 题干里的 `::` 必须拆开 —— Spaced Repetition 按第一个 `::` 切,
                # 否则「什么是 a::b」会被切成 front="什么是 a"。与用户提问路径
                # (_qa_items) 同一处理, 那边早就消毒了, 这边漏了。
                q_text = x["q"].replace("::", ": :")
                L.append(f"- {q_text}::{x['a']}{tail}")
        else:
            L += ["> [!tip] 用 `问题::答案` 写自测题（装了 Spaced Repetition 插件就能当卡片复习）", "",
                  "- "]
        L += [""]

        L += ["## 💡 Terminology 本课术语表", ""]
        if gloss:
            L += [f"- **{t}** — {d}　`{ts}`" for t, d, ts in gloss]
        else:
            L += ["*（本课没有命中术语表）*"]
        L += [""]

        # ❓ 按钮 2026-09-29 起**兼**表「重点」和「没听懂」（⭐ 按钮已并进来），
        # 所以抬头也照实说。⚠️ 抬头刻意用 `🤔` 而不是 `❓` —— 这份笔记里已经有一个
        #    `## ❓ Review 复习自测`，两个同名抬头会让 grep 分不清。按钮仍是 ❓
        #    （按钮上只有一个字符的位置），靠 tooltip 把两者连起来。
        L += ["## 🤔 我标的地方（重点 / 没听懂）", ""]
        if lost_items:
            for blk in lost_items:
                L += blk.splitlines()
        else:
            L += ["*（课上没按 ❓；觉得哪句重要、或哪里没跟上，都按它）*"]
        L += [""]

        # ⭐ 那一节**只在真有 ⭐ 时渲染**（2026-09-29）：按钮已经没了，
        # 空的时候再写一句「课上没按 ⭐」是**假话** —— 它描述的是一个用户
        # 按不到的动作。历史会话里的 ⭐ 照旧渲染（`stars` 来自会话抬头的
        # `⭐ Exam Focus`，`rebuild_note.py` 重建时同样走这条路）。
        if stars:
            L += ["## ⭐ 我标记的重点", ""]
            for e in stars:
                L += [f"- `{e['ts']}` {e['zh'] or e['en'] or e['asr']}"]
                if e["en"] and e["zh"]:
                    L += [f"  - EN: {e['en']}"]
            L += [""]

        # ── 🎯 Jev 的重点句（2026-09-29）──────────────────────────────────
        # ⚠️ **句子到收尾这一刻才齐**，所以不能在构造 writer 时算 —— 这里现算。
        # ⚠️ **默认没有**（没配 token / raw 档 / 调用失败 → 空），那时**整节不出现**
        #    —— 不写一句"（没有）"，那会让读者以为读过而没结果。
        kp = list(self._keypoints)
        if not kp and self._keypoints_fn is not None:
            try:
                # ⚠️ **把句子交给它** —— 判据/生产都不该自己再抄一份"哪些算句子"
                #    （读法唯一的定义点在本文件 `_parse`）。
                kp = list(self._keypoints_fn(
                    [e["en"] for e in entries if e.get("en")]) or ())
            except Exception:                                 # noqa: BLE001
                kp = []                                       # ⚠️ 收尾这一步绝不抛
        if kp:
            L += ["## 🎯 这节课最值得记的几句", "",
                  "*（按 Jev 给「值不值得抄进复习纸」的概率排的序。⚠️ **它是排序信号，"
                  "不是考试预测** —— 实测同一句换个问法能从 0.77 掉到 0.61，"
                  "所以**看顺序，别卡分数**。）*", ""]
            for p, text, ix in kp:
                tag = f"L{ix[0] + 1}" if len(ix) == 1 else f"L{ix[0] + 1}–{ix[-1] + 1}"
                L += [f"- `{p:.2f}` `{tag}` {text}"]
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
    def close(self, ask=None, qa=None, on_progress=None, cancel=None) -> str:
        """`qa` = 问答线程 history 的快照(list), 或**取快照的函数**。

        ⚠️ 不能走构造函数: writer 在 run() 里**先**建, qa 状态比它晚。
        ⚠️ 要函数而不是 list 的场景: 收尾里答案可能还在流(`answer_worker` 是 daemon,
        从不 join), 而"询问是否保存"可以停很久 —— 早取的快照会把这轮问答整条丢掉
        (问题还在, 答案没了)。函数形式把"取"推迟到真正要渲染的那一刻。
        ⚠️ 问答**只进 vault 笔记**, 不进 `sessions/` —— 那份逐句日志是三方共享
        契约(见 CLAUDE.md), 语义也不同(问答不是"课上讲了什么")。

        `on_progress(stage, done, total)`: 精修/复习层的进度, **从工作线程里被调**
        (2026-09-28 起收尾整段可以跑在 worker 上), 所以实现里**不许碰 AppKit**。
        形状与 `prep.py:952` 一致; stage 的取值见 `polish.STAGE_NAME`。
        ⚠️ 它只是**额外**的通路 —— 终端那行人话照旧 `print` 出去, 两者都要有。

        `cancel`: 一个 `threading.Event`, 置位后**在批次边界**放弃精修/复习层,
        **但仍然把笔记写出来**(用直播版 / 已精修到一半的那一份)。
        ⚠️ 为什么需要它: 收尾跑在 worker 上之后, **`KeyboardInterrupt` 只投递到主线程**,
           worker **永远收不到** —— 上面那两处 `except KeyboardInterrupt` 护栏在真实
           流程里是死的。跨线程让长活停下来只能靠标志。卡上的 [跳过精修] 按钮
           与终端的 Ctrl+C 走的是同一条路。
        """
        # ⚠️ 关旁路句柄放在**最前面**：`close()` 有三条早退（未启用 / 零句又无问答 /
        #    用户答"不保存"），放在后面就会在那些路径上漏掉它。幂等，重复调无妨。
        #    2026-09-28 由作者那句"不用保存笔记"提醒才发现 —— 那正是会走到早退的路径。
        self.close_lost()
        self.close_atom()      # ⚠️ 同一条教训：**都在早退之前**（下面有三条早退）
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

        # ⚠️ 进度一律**先 print（终端照旧）再转发给 UI**，两条路都要有 ——
        #    `--ui terminal` 那条没有 UI，而 UI 那条也不该逼人回头看终端。
        #    ⚠️ 转发那步包 try：一个坏的回调不该把整节课的笔记带下水。
        def _prog(stage, done, total):
            from polish import progress_text
            print(f"  {progress_text(stage, done, total)}", flush=True)
            if on_progress is not None:
                try:
                    on_progress(stage, done, total)
                except Exception:                         # noqa: BLE001
                    pass

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
                    on_progress=_prog, stats=pstats, cancel=cancel)
                polish_state = _polish_state(pstats)
                if pstats.get("cancelled"):
                    # 用户在批次边界叫停了（Ctrl+C / 卡上的 [跳过精修]）。
                    # ⚠️ 与 `polish: failed` **分开记**：那不是调用出错，别让人去查
                    #    一个不存在的问题。已生效的批次照旧留在 entries 里。
                    polish_state = "interrupted"
                    print("⚠ 精修被跳过（用户取消）；已生效的批次保留", flush=True)
                if polish_state == "failed":
                    print(f"⚠ 精修未生效(applied=0 句, 失败批次 "
                          f"{pstats.get('failed')}/{pstats.get('batches')}); "
                          f"状态已写进笔记 frontmatter")
                elif polish_state == "partial":
                    print(f"⚠ 精修只生效了一部分(applied={pstats.get('applied')} 句, "
                          f"失败批次 {pstats.get('failed')}/{pstats.get('batches')}); "
                          f"状态已写进笔记 frontmatter")
            except KeyboardInterrupt:
                # ⚠️⚠️ **绝不能让它穿出去** —— 这一层是"额外的"，底座（逐句转录）在
                #    `entries` 里，已经解析好了。放它出去 = **整份笔记不写**。
                #    2026-09-28 查出的真缺陷：原来下一支是 `except Exception`，而
                #    `KeyboardInterrupt` **不是** `Exception` 的子类 → 在最长的那一段
                #    （实测 579 句要 11 分钟）按 Ctrl+C，会把整节课的 Obsidian 笔记
                #    **静默丢掉**，只剩 `sessions/` 里的原始文件。
                #    ⚠️ 中断发生在 `polish_entries` 内部时它**没有返回**，所以 `entries`
                #       仍是直播版 —— 下面那句文案是准确的，不是安慰话。
                #    同一条纪律代码在 `qa` / `lost` 两处已经写过：「额外一层不该拖垮底座」。
                polish_state = "interrupted"
                print("⚠ 精修被中断；用直播版转录生成笔记", flush=True)
            except Exception as e:                        # noqa: BLE001
                polish_state = "failed"
                print(f"⚠ 精修失败({str(e)[:60]}); 用直播版转录生成笔记")
        if self._key:
            print("🤖 正在生成复习层(知识点详解 + 自测)…", flush=True)
        # ⚠️ 同 `polish` 那条：`_review` 也要跑好几轮 LLM（按 REVIEW_CHUNK 分块，
        #    579 句不止一轮），中断它同样**不许**带走底座。
        if cancel is not None and cancel.is_set():
            review = {}
            print("⚠ 已跳过复习层（用户取消）；笔记照常生成", flush=True)
        else:
            try:
                review = self._review(entries, on_progress=_prog)
            except KeyboardInterrupt:
                review = {}
                print("⚠ 复习层被中断；笔记照常生成（只有逐句转录）", flush=True)
        # 问答行自带 try/except: 渲染问答抛出去就再也没有那份转录笔记了。隔离是硬要求:
        # 笔记的底座是逐句转录, 它是不能丢的那件事; 问答只是额外一层。
        # ⚠️ 2026-09-28 起这条纪律**四层都补齐了**（polish / review / qa / lost）；
        #    原来只有后两层有护栏，而前两层才是跑得最久的。
        qa_items: list[str] = []
        if qa:
            try:
                qa_items = self._qa_items(qa() if callable(qa) else qa)
            except Exception as e:                        # noqa: BLE001
                print(f"⚠ 问答落盘失败({str(e)[:60]}); 笔记照常生成")
        # ❓ 同一条纪律：它是额外一层，**逐句转录才是不能丢的那件事**。
        lost_items: list[str] = []
        try:
            lost_items = self._lost_items(entries)
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ ❓ 反查失败({str(e)[:60]}); 笔记照常生成")
        note = self._render_note(entries, review, qa_items, polish_state, lost_items)

        self.vault_path = note_path_for(self._vault, self._date, self._course)
        self.vault_path.write_text(note, encoding="utf-8")
        layer = ("知识点详解+自测+术语表+重点" if review.get("sections")
                 else "术语表+重点(未生成详解)")
        return (f"📝 已存入 Obsidian({self._n} 句, 复习层: {layer}) → {self.vault_path}\n"
                f"   原始逐句日志: {self.session_path}")
