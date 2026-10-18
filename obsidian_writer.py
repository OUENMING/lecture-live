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

⭐ 3.9.7 第 5b 项: **同一路径写两回**。基础层(转写 + 译文, 含精修 / 碎句合并 / 补译)
先**原子落盘**一份; 复习层回来后再**原子重写**为完整版。复习层失败 / 被取消 → 留下的
那份加一行"复习层这次没生成"的说明(怎么用 `scripts/rebuild_note.py` 补)。理由与
原子性的要求见 `_atomic_write` 的 docstring。

复习层以**英文为主、中文为辅**: 知识点用英文陈述并展开细节, 中文只做点睛, 关键词给中英对照。
没设课程代码也能写(课程名默认 LECTURE)。

  --save-notes ask   结束时问(默认;答否则保留会话文件)
  --save-notes yes   不问,直接进 Obsidian
  --save-notes no    完全不写(会话文件也不建)
"""
from __future__ import annotations
import bisect, json, os, re, time
from pathlib import Path

import note_terms
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
    t = os.path.expanduser(t) if t else ""
    # ⚠️ 记下来的一定是绝对路径（面板选文件夹 / `remember_vault` 收的都是）。
    #    不是绝对路径 = 文件被手改坏了：照用会按**当前目录**（安装目录）解析，
    #    笔记写进一个谁也找不到的地方 —— 与上面那条「没有兜底目录」同一个道理，宁可不写。
    return t if os.path.isabs(t) else None


def remember_vault(vault: str, *, root=None) -> None:
    """把用户选过的库记下来 —— **下次双击启动（没有 shell 环境）才找得到它**。

    ⚠️ 失败**不出声**：记不住只影响下一次，本次照常运行，为它中断录课不值得。
    """
    p = paths.vault_config(root=root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # 存绝对路径：`--vault 相对路径` 下次从别的目录起就指错地方（`resolve_vault` 也只认绝对的）。
        v = os.path.expanduser(vault.strip())
        p.write_text((os.path.abspath(v) if v else "") + "\n", encoding="utf-8")
    except OSError:
        pass


# 会话文件里一条定稿块的抬头: "> [!abstract] 14:02:18" / "... ⭐ Exam Focus"
_TS = re.compile(r"^> \[!abstract\] (\d\d:\d\d:\d\d)( ⭐ Exam Focus)?\s*$")
_FIELDS = (("en", "EN"), ("zh", "ZH"), ("asr", "ASR"))

REVIEW_CHUNK = 70           # 复习层每次请求喂多少句; 长课分块, 避免超长 JSON 被截断
REVIEW_MAX_TOKENS = 3000    # 单块输出上限
REVIEW_WORKERS = 5          # 各块同时发（2026-10-15：4–5 块串行是下课→笔记最大的一段，约 25–35 s）

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
#: 跨段半句合并的**旁路**文件（3.9.7 第 4 项）——`sessions/<同名>.merges.jsonl`。
#: 与 `.lost.jsonl` **同一条纪律**（懒开 / 只追加 / flush / 幂等关闭 / 失败静默）：
#: 屏上那一行被合并替换过，`.md` 却一个字不动（三方契约），凭据就落在这里。
MERGE_TAIL = ".merges.jsonl"
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


def _bf_mark(e: dict) -> str:
    """🩹 课后补译出来的中文在笔记里的**唯一标记处**（空串 = 直播版，逐字不变）。

    ⚠️ 标记是**给读者的真话**：这一句的中文不是课上的直播译文，是课后补的
    （`backfill.py`）—— 分不出来就意味着把补译当成了直播译文在读。
    刻意做得很轻（行尾 ` ·补`），它不该跟 `⚠` 那类**异常**标记抢注意力。
    """
    return " ·补" if e.get("bf") else ""


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
#
# ⚠️⚠️ **2026-10-12：`off` 这一档改成空串。**（Owen 拍板「精修不再必须，保留代码但不执行」→
#    默认 `--polish off`。）「没精修」于是从**异常**变成了**常态** —— 每节课的笔记顶上挂一行 ⚠
#    会把真正该看的异常（failed / partial / interrupted）淹掉。
#    ⭐ 但**「这节到底精修了没有」这件事没有被丢掉**：它照旧写在抬头的 `polish:` 字段里
#    （`off` / `ok` / `failed` / `partial` / `interrupted`）。**事实进抬头，正文只留异常。**
_POLISH_NOTE = {
    "off": "",       # ← 见上面那段：常态不报警；事实在抬头 `polish:` 里
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


def _render_outline_section(items: dict, *, review: dict | None = None) -> list:
    """课堂纲要 -> 笔记里那一节。**纯函数**（不起窗口、不联网、可单测）。

    ⚠️ **返回空列表 = 这一节什么都不该写**（既没课务也没正式章）——
       调用方据此**整节跳过**，不是写一个空标题。
    ⭐ **排法①**（作者 2026-09-30）：**中文当正文、英文原句降小字** ——
       与直播字幕、面板里的纲要**同一条排法**。⚠️ 计划 §10.2 原写的是
       「英文合成句 + 括注中文术语」，作者后来两次要求「中文占比再提高」→ 按①实现。
       ⚠️ `en` 一个字没丢：它在小字那行。
    ⚠️ 没有合成句的章**退回它的原子要点**（不写"（空）"）。
    ⭐ 第五轮（作者 2026-10-17）：`review` 给了就先过一遍 `note_outline.check` ——
       纲要里的等式/除法/代入算不对不许原样上线（改对就标「已更正」，改不了标 ⚠）。
    """
    ds = list(items.get("deadlines") or ())
    chs = [c for c in (items.get("chapters") or ()) if c.get("status") == "final"]
    if not ds and not chs:
        return []
    # ── 纲要数字核：**渲染前**改条目（纯函数；失败也不许拖垮纲要本身）─────────
    _fixes: list = []
    _warns: list = []
    if review:
        try:
            import note_outline
            items, _fixes, _warns = note_outline.check(items, review=review)
        except Exception:                                     # noqa: BLE001
            _fixes, _warns = [], []
    # ⚠️ `check()` 返回的是**深拷贝**：上面那份 `chs` 指的还是旧条目 —— 必须重取，
    #    否则记账行说"1 处已更正"、正文却还是错的那个数（第五轮实测踩过）。
    ds = list(items.get("deadlines") or ())
    chs = [c for c in (items.get("chapters") or ()) if c.get("status") == "final"]
    # ⚠️ 2026-10-17 step 1：课务那一段**多一道闸门**（判据在 `chapter.deadline_worthy`，
    #    纯函数）。直播时"宁可多报"（`deadline_hits` 的取舍），但笔记是**留一个学期**
    #    的那份 —— 审计实测那份笔记 5 条课务 0 条是真的，读者会学会整块跳过。
    #    ⚠️ 被挡下的**不是静默消失**：下面写一行"已过滤 N 条"，与术语表页脚同一条纪律。
    try:
        import chapter as _chapter
        kept = [d for d in ds
                if _chapter.deadline_worthy(d.get("quote", ""),
                                            changed=bool(d.get("changed")))]
        _n_drop = len(ds) - len(kept)
    except Exception:                                         # noqa: BLE001
        kept, _n_drop = ds, 0                                 # 附加层不许拖垮主体
    ds = kept
    out: list = ["## 📑 课堂纲要", ""]
    # ⚠️ 记账（不静默）：纲要这一屏先于正文，错了最伤 —— 改了几处、几处待确认都写在脸上。
    if _fixes or _warns:
        bits = []
        if _fixes:
            bits.append(f"{len(_fixes)} 处已更正")
        if _warns:
            bits.append(f"{len(_warns)} 处待确认")
        out += [f"*纲要数字核：{'、'.join(bits)}（明细在各条行尾）。*", ""]
    if ds:
        out += ["### Deadlines · 课务", ""]
        for d in ds:
            # ⚠️ 第四轮：课务这条原来也是「· 时间 · 来源 · 状态」的词串行 —— 同一个版式
            #    毛病（中文为主 = 一行读得完）。英文引语是**课上的原话**，照旧保留。
            bits = [x for x in (str(d.get("t") or "").strip(),
                                f"来源 {d.get('source', '')}".strip(),
                                "**待确认**",
                                "**已改期**" if d.get("changed") else "") if x]
            out.append(f"- **{d.get('quote', '')}** —— " + "，".join(bits) if bits
                       else f"- **{d.get('quote', '')}**")
        out.append("")
    if _n_drop:
        # ⚠️ 记账（不静默）：读者要能分辨"这课真没有课务"与"课务被过滤了"。
        out += [f"*（另有 {_n_drop} 条课务线索疑似误报，未收录）*", ""]
    #: 已括注过的术语 —— 「**首次**出现写‘中文（English）’」靠它跨条目去重。
    _seen_terms: set = set()

    def _inline_terms(zh: str, terms) -> str:
        """把术语**就地**写成「中文（English）」（每个术语只在首次出现时括注）。

        ⚠️ 作者 2026-10-17 第四轮：「课堂纲要」还是「一行中文 + 一行英文整句 + 词串」，
           明确不要那种版式 —— 中文为主，术语（英文）就地括注。
        """
        out = zh
        for t in (terms or ()):
            if not (isinstance(t, (list, tuple)) and len(t) >= 2):
                continue
            en, zt = str(t[0] or "").strip(), str(t[1] or "").strip()
            if not en or not zt or (en, zt) in _seen_terms:
                continue
            if zt in out:
                out = out.replace(zt, f"{zt}（{en}）", 1)
                _seen_terms.add((en, zt))
        return out

    def _pair_md(x, en_key: str, flag: str = "") -> list:
        """⭐ **排法① 在笔记里的唯一定义点**：**一行中文**，术语就地「中文（English）」。

        ⚠️ 第四轮改版（作者原话：「不要一行中文一行英文 + 词串」）：
           旧版是「中文大字 + 小字行（英文整句 · 英 中 · 英 中）」两行。现在：
           - 中文当正文（`zh`；没有中文时才用英文原句 —— 一个字不丢）；
           - 术语在**首次**出现的条目里就地括注英文（`_inline_terms`）；
           - 英文整句不再单列（完整转录在附录 B，术语表在「本课术语」一节 ——
             英文没有消失，只是不再和中文并排重复一遍）；
           - `flag`（依赖板书 / 课堂讨论 / 已改期）改挂在该条**行尾**。
        ⚠️ `en_key`：合成句是 `en`、原子是 `text` —— 两个数据类键名不同而已。
        """
        en = (x.get(en_key) or "").strip()
        zh = (x.get("zh") or "").strip()
        line = _inline_terms(zh, x.get("terms")) if zh else en
        if not line:
            return []
        return [f"- **{line}**" + (f" —— {flag}" if flag else "")]

    for c in chs:
        out += [f"### {c.get('title_zh') or c.get('title') or '（无标题）'}"
                f"（{c.get('t0', '')}–{c.get('t1', '')}）", ""]
        sents = c.get("sentences") or []
        if sents:
            for sn in sents:
                flag = {"board": "⚠ 依赖板书或图，转录不完整",
                        "discussion": "课堂讨论"}.get(sn.get("flag"), "")
                out += _pair_md(sn, "en", flag)
        else:
            for a in (items.get("atoms") or ()):
                if (c.get("lo") or 0) <= (a.get("src") or [0])[0] <= (c.get("hi") or 0):
                    out += _pair_md(a, "text")
        out.append("")
    return out


class ObsidianWriter:
    def __init__(self, vault: str | None, course: str | None, mode: str = "ask",
                 api_key: str | None = None, model: str = "deepseek-flash",
                 glossary_path: str | None = None, polish: bool = True,
                 polish_model: str | None = None, keypoints=None,
                 keypoints_fn=None, lost_explain_fn=None, backfill_fn=None):
        self.mode = mode
        # ⚠️⚠️ **`enabled` 只管一件事：要不要记录**（`sessions/` 那层底稿）。
        #    **「写不写 Obsidian 笔记」是另一件事** —— 由 `self._vault is not None` 判
        #    （见 `close()` 里那处早退）。
        #
        #    2026-09-29 拆开。原来写的是 `enabled = bool(vault) and mode != "no"`：
        #    一个标志扛两件事，于是**没有库时连 `sessions/` 都不建**
        #    （实测：`ObsidianWriter(None, …)` → `session_path is None` → `append()`
        #    首行就 return）→ **整节课一个字都不落盘**。
        #    ⚠️ 这恰好绕过了本模块的立身之本：「**先落盘, 再询问**」（见文件头），
        #       而且命令行那句提示还写着"这次只写 sessions/" —— 那是假话。
        #    ⚠️ 触发它不需要意外：全新安装、或双击 `.app`（没有 `$OBSIDIAN_VAULT`
        #       也还没记住过库）就会命中。而那些正是**最需要会话底稿**的人
        #       —— 将来的「打包成 MD/PDF」导出的也就是它。
        self.enabled = mode != "no"
        self._vault = os.path.expanduser(vault) if vault else None
        self._course = course or DEFAULT_COURSE
        self._date = time.strftime("%Y-%m-%d")
        self._key = api_key                 # 有 key 才生成"知识点详解 + 自测"复习层
        self._model = model
        self._glossary_path = glossary_path or str(
            Path(__file__).with_name("glossary.txt"))
        #: 💡 本课术语表的**过滤结果**（`note_terms.Selection`）—— 由 `_glossary()`
        #: 在渲染时填，页脚那行 `已从术语表过滤 N 条…` 读它。`None` = 还没算过。
        self._gloss_sel = None
        #: 运行时查词表的**记忆化槽**（`_term_index()`）：`None` = 还没建，
        #: `False` = 建过但失败了（别每次都重试并刷警告）。
        self._term_notes = None
        #: 📖 本课**课件索引**（`materials.Materials`）—— 由 `close()` 建好后塞进来。
        #: `None` = 没有课件：术语语境核对退到"只看本课术语表"，且笔记头部写明
        #: 「本课未做课件核对」（2026-10-17 step 2 的退化档）。
        self._materials = None
        #: 💡 本课新加入术语库的词（`note_glossary.append` 的 `added`）。
        self._gloss_added: list = []
        #: 🗓 间隔复习日期（`note_v2.review_dates`）。
        self._review_dates: list = []
        #: 🔁 Anki CSV / 交互 HTML 的**文件名**（渲染时写进笔记的说明那几行）。
        self._anki_name = ""
        self._html_name = ""
        #: ⚠ 头部警告（构图方塞；渲染层照单全收）。
        self._note_warns: list = []
        #: 🥉 三道核判成 `mismatch` 的条目 -> 修正说明（`note_v2.corrections_from_mismatch`）。
        self._corr_extra: list = []
        self._polish = polish               # 落笔前二次精修(见 polish.py)
        self._polish_model = polish_model or model
        #: 🎯 Jev 的重点句：`[(概率, 句子, [原始行下标]), …]`。
        #: ⚠️ **空表 = 不加那一节**（没配 token / raw 档 / 调用失败）。
        self._keypoints = list(keypoints or ())
        #: ⚠️ 句子**到收尾才齐**，所以生产那条走**回调**（`close()` 里现算），
        #:    不是构造时传值。传值那条留给判据。
        self._keypoints_fn = keypoints_fn
        #: ❓ 课后讲解：`fn(blocks) -> [{"explain","q","a"}, …]`（与 blocks 等长，否则不用）。
        #: ⚠️ 同 keypoints：块要到收尾才齐，所以走回调。`None` = 不讲，笔记逐字不变。
        self._lost_explain_fn = lost_explain_fn
        #: 🩹 漏译句课后补译（3.9.7 第 2 项）：`fn(entries) -> (新 entries, {"need","done"})`。
        #: ⚠️ `None` = 不补（本模块的默认）：**档位与 key 的判据在 `main`**
        #:    （纯转录档一个请求都不发、只英·校档不要中文）—— 这里只负责在渲染笔记
        #:    **之前**放它跑一遍、并把拿回来的 entries 交给渲染。见 `backfill.py` 文件头。
        self._backfill_fn = backfill_fn
        self._n = 0
        self.session_path: Path | None = None
        self.vault_path: Path | None = None
        self._lost_h = None                 # ❓ 旁路文件句柄(懒开, 见 mark_lost)
        self._merge_h = None                # 跨段合并旁路句柄(懒开, 见 mark_merge)
        if self.enabled:
            SESSIONS.mkdir(exist_ok=True)
            self.session_path = SESSIONS / (
                f"{self._date}_{time.strftime('%H%M%S')}_{self._course}.md")
            self.session_path.write_text(
                f"# {self._course} · {self._date} · 实时会话日志\n\n", encoding="utf-8")

    @property
    def writes_notes(self) -> bool:
        """这节课收尾会不会写 Obsidian 笔记（记录开着 **且** 有库）。

        ⚠️ `main` 拿它决定「问不问存不存」和「结论卡要不要等用户点关闭」——
           没库时问「存入 Obsidian 吗？」是在问一件做不到的事。
        """
        return self.enabled and self._vault is not None

    # ---- 运行中: 每句立刻落盘(flush) ----
    def append(self, en: str, zh: str, flagged: bool = False, raw: str = "") -> str:
        """`en` 是 LLM 修正后的英文, `raw` 是**原始 ASR 转录**。

        两个都记: 修正版好读, 原始版是"模型实际听到什么"的凭据 ——
        LLM 偶尔会过度修正(把正确的词改错), 没有原始转录就无从复核。

        返回这条 callout 抬头的时间戳 `HH:MM:SS`（跨段半句合并要把两条原条目的时间戳
        写进 `.merges.jsonl`）；没落盘（未启用 / 没会话文件）→ `""`。
        ⚠️ 返回值是**加法**：老调用方不看它，行为一个字没变。
        """
        if not self.enabled or not self.session_path:
            # ⚠️ 不落盘也要**计数**（2026-10-01 审查 F22）：句号是"第几句定稿"，
            #    实时总结的窗口/章节区间（lo/hi、原子 src）靠它 —— `--save-notes no`
            #    时原来恒 0，所有区间都成了 0。落盘那条路在下面**写成功后**才加
            #    （F17：写失败不许把句号推过文件一格）。
            self._n += 1
            return ""
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
        # ⚠️ `_n` 在**写成功之后**才加（2026-10-01 审查 F17）：原来先加后写，
        #    写失败时 `_n` 比文件多一格 → 之后所有 `src`/全局句号整体错位 1。
        self._n += 1
        return ts

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
        """本课命中的术语 [(术语, 解析, 首次出现时间)]。纯本地查表, 零网络。

        ⚠️ 2026-10-17 step 1：这一层**不再原样返回命中结果** —— 过滤（人名 / 口语词 /
        常识词 / 错语境义）在 `note_terms.select`（唯一定义点），这里只做接线：
        命中来源是 `TermNotes`（预置 + 自动缓存），语境核对是「本课术语表 ∪ 本课课件」。
        被挡下的条目进 `self._gloss_sel.dropped`，渲染时写一行页脚 ——
        **静默丢东西是本仓库反复咬人的那一类失败**。
        """
        sel = self._term_selection(entries)
        self._gloss_sel = sel
        return [(h.term, h.gloss, h.ts) for h in sel.hits]

    def _term_selection(self, entries: list[dict]) -> note_terms.Selection:
        """`_glossary` 的实质。**纯本地**（术语表读盘 + 记忆化, 不发请求）。"""
        notes = self._term_index()
        if notes is None:
            return note_terms.Selection(hits=[], dropped=[])

        def match(sentence: str):
            for term, detail in notes.match(sentence):
                src, _meta = notes.entry_of(term)
                yield term, detail, (note_terms.SRC_CURATED if src == "curated"
                                     else note_terms.SRC_AUTO)

        def type_of(term: str) -> str:
            return str(notes.entry_of(term)[1].get("type") or "")

        return note_terms.select(entries, match=match, type_of=type_of,
                                 verify=self._term_verified)

    def _term_index(self):
        """运行时查词表（`build_notes.TermNotes`）。构造一次、记忆化。

        ⚠️ 一次渲染里要被问几十次（每条术语都要 type / 来源），每问一次就读两个
           JSON 是浪费；而且 `TermNotes.__init__` 会**打印**警告（读不出时），
           重复构造会把同一句警告刷好几遍。
        """
        if getattr(self, "_term_notes", None) is None:
            try:
                from build_notes import TermNotes
                self._term_notes = TermNotes()
            except Exception:                                 # noqa: BLE001
                self._term_notes = False
        return self._term_notes or None

    def _term_verified(self, term: str) -> bool:
        """自动缓存里的词能不能证明"本课真的用过它"。

        ⭐ 两个来源（任一命中即可）：**本课术语表**（`glossary/<课号>.txt`，
        `prep` 从课件里长出来的）与**本课课件材料**（step 2 的 `materials` 索引）。
        ⚠️ 没有课件索引时（旧会话 / 材料缺失）只剩术语表 —— 这时候宁可**少显示**：
        自动缓存的错语境义（`USC` → 南加州大学）正是靠这里挡下来的。
        """
        k = (term or "").strip().lower()
        if not k:
            return False
        if k in self._course_terms_lower():
            return True
        idx = self._materials
        return bool(idx is not None and idx.verify(term))

    def _course_terms_lower(self) -> set:
        """本课术语表的小写词集合（记忆化 —— 渲染层每句都问一遍）。"""
        cached = getattr(self, "_course_terms_cache", None)
        if cached is not None:
            return cached
        try:
            from translator import course_term_list
            got = {t.strip().lower() for t in
                   course_term_list(self._glossary_path, self._course) if t.strip()}
        except Exception:                                     # noqa: BLE001
            got = set()
        self._course_terms_cache = got
        return got


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

    # ---- 跨段半句合并：旁路文件 `sessions/<同名>.merges.jsonl`（3.9.7 第 4 项）----
    def _merge_path(self) -> Path | None:
        """`sessions/<同名>.merges.jsonl`。⚠️ **旁路文件，绝不改会话抬头** ——
        理由与 `_lost_path` 逐字相同（抬头正则 `_TS` 行尾锚定，`.md` 是三方契约）。"""
        if not self.session_path:
            return None
        return Path(str(self.session_path.with_suffix("")) + MERGE_TAIL)

    def _merge_handle(self):
        """懒开常驻句柄。**关过之后不再开**（`_merge_closed`）—— 与 `_lost_handle` 同一条纪律。"""
        if getattr(self, "_merge_closed", False):
            return None
        if self._merge_h is None:
            p = self._merge_path()
            if p is None:
                return None
            self._merge_h = p.open("a", encoding="utf-8")
        return self._merge_h

    def mark_merge(self, tail_ts: str, next_ts: str, en: str, zh: str, *,
                   gap_s: float | None = None, trigger: str = "") -> bool:
        """把一次**跨段合并**追加到旁路文件。返回是否写成。

        字段：两条原条目的时间戳（`a` / `b`，即 `.md` 里那两个 callout 的 `> [!abstract]`
        抬头）、合并后的 EN / ZH、`gap_s`、触发口径。**只追加、flush、不做 fsync**
        （与 `mark_lost` 同规矩）；失败静默（多一行少一行不该影响上课）。
        ⚠️ 屏上替换与这里**互不依赖**：写失败不代表屏上没替换。
        """
        p = self._merge_path()
        if p is None:
            return False
        try:
            h = self._merge_handle()
            if h is None:
                return False
            h.write(json.dumps({"epoch": round(time.time(), 3),
                                "t": time.strftime("%H:%M:%S"),
                                "kind": "merge",
                                "a": tail_ts or "", "b": next_ts or "",
                                "en": en or "", "zh": zh or "",
                                "gap_s": None if gap_s is None else round(float(gap_s), 3),
                                "trigger": trigger or ""},
                               ensure_ascii=False) + "\n")
            h.flush()
            return True
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ 合并记录失败({str(e)[:60]}); 课堂不受影响")
            return False

    def close_merges(self) -> None:
        """幂等。文件**不删** —— 同 `close_lost`。关过之后 `mark_merge` 不再开新句柄。"""
        self._merge_closed = True
        h = getattr(self, "_merge_h", None)
        if h is not None:
            try:
                h.close()
            except Exception:                             # noqa: BLE001
                pass
            self._merge_h = None

    # ---- 原子层：旁路文件 `sessions/<同名>.atoms.jsonl`（plan §1）----
    def _atom_writer(self):
        """懒建 `atom.AtomWriter`。⚠️ **复用那个类，不在这里重写路径拼法** ——
        路径形状（`<同名>.atoms.jsonl`）在那儿是唯一定义点。

        ⚠️ **关过之后不再开**（`_atom_closed`）：与 `_lost_handle` **逐字同一条纪律**
           （那边是 `_lost_closed`，2026-09-28 审查指出）—— 这里当时漏了同样的一半。
           少了它，`close_atom()` 之后任何一次 `append_atoms` 都会**新建一个再没人关的
           写入器**：不但句柄泄漏，还会往**已经渲染完笔记的** `.atoms.jsonl` 里追写。
         ⚠️ **可达路径不是理论上的**：`LiveSummarizer.finish()` 拿不到 `_step_lock`
           时会**放弃** worker 线程（`live_summary.py` 那段注释写着这条**实测发生过**），
           而那个被放弃的线程之后照样走到 `_absorb_window` → `append_atoms`。
           （2026-09-30 复现：`close()` 之后 `append_atoms` 返回 1、`_atom_w` 被重建。）
        """
        if getattr(self, "_atom_closed", False):
            return None
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
        """幂等。文件**不删**（它是这份笔记的证据，同 `close_lost`）。

        ⚠️ 置 `_atom_closed` 是为了让后来的 `append_atoms` **别再开新写入器** ——
           与 `close_lost` 那条**同一条纪律**（见 `_atom_writer` / `_lost_handle`）。
        """
        self._atom_closed = True
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

    def _keypoint_items(self, entries: list[dict]) -> list:
        """🎯 Jev 的重点句 → `[(概率, 句子, [原始行下标]), …]`。

        ⚠️⚠️ **这是一处会联网的取数**（`keypoints.pick` → CommandCode；🩹 补译那一层
           也联网），所以它**住在 `close()` 的护栏区里**，和 qa / lost 并列 ——
           而不是在 `_render_note` 里面。理由（2026-09-29 挪的）：
           · `_render_note` 是**纯函数**（判据靠它、不碰 AppKit / 网络），
             让它发请求是一条**没人知道的隐式契约**；
           · `close()` 里那段「八层护栏」的注释要能一眼数出是**八层**。

        ⚠️ 传给 `keypoints_fn` 的是**句子**，不是 entries —— 读法唯一的定义点
           在本文件 `_parse`，判据/生产都不该自己再抄一份"哪些算句子"。

        ⚠️ **本函数自己兜错**（不是只靠 `close()` 那层）：它是要联网的那一处，
           而另外三层取数也各自兜了 —— `_review`（docstring 逐字「失败是**正常
           路径**…绝不抛」）· `_lost_items`（内部接住并打「旁路文件读不出」）·
           `polish_entries`（无 key / 失败原样返回）。⚠️ `_qa_items` 例外：它是**纯
           字符串处理**，没有外部依赖，所以护栏只在 `close()` 那一层。
           两层都包是刻意的 —— 少了这层，"收尾这一步绝不抛"就成了一句
           只对调用方成立的话（判据当场抓过）。
        """
        kp = list(self._keypoints)
        if kp or self._keypoints_fn is None:
            return kp
        try:
            return list(self._keypoints_fn(
                [e["en"] for e in entries if e.get("en")]) or ())
        except Exception:                                     # noqa: BLE001
            return []                                         # ⚠️ 收尾这一步绝不抛

    # ---- 复习层 v2（2026-10-17）----
    def _chat(self, system: str, user: str, max_tokens: int) -> dict:
        """`note_v2` 那三条调用的**唯一出口**（`chat(system, user, max_tokens) -> dict`）。

        ⚠️ 收在这里的理由：判据要能整体换掉它（零网络），而三个步骤各接一次
           `llm_client` 就是三处重复的 try/异常口径。
        """
        import llm_client
        obj = llm_client.chat_json(self._key, self._model, system, user,
                                   max_tokens=max_tokens, temperature=0.3, timeout=300)
        return obj if isinstance(obj, dict) else {}

    def _chat_meta(self, system: str, user: str, max_tokens: int):
        """同 `_chat`，但带回 `finish_reason` —— 只有「五分钟串讲」走这条路：
        截断（`length`）必须被当成**失败**（半截叙述印进笔记比没有更坏，2026-10-18）。"""
        import llm_client
        obj, finish = llm_client.chat_json(self._key, self._model, system, user,
                                           max_tokens=max_tokens, temperature=0.3,
                                           timeout=300, return_meta=True)
        return (obj if isinstance(obj, dict) else {}), finish

    def _with_overview(self, review: dict) -> tuple:
        """⭐ 五分钟串讲（版式 A，2026-10-18）：合并后**一次小调用**，输入只有小节（无转录）。

        ⚠️ 失败（调用炸了 / 截断 / 模型没回可用内容）都不许抛、也不许印半截：
           记一条 ⚠ 到页尾、速览退回首句兜底 —— 笔记照常生成（fail-soft）。
        """
        import note_v2
        try:
            got = note_v2.overview(review, chat=self._chat, chat_meta=self._chat_meta)
        except Exception as e:                                 # noqa: BLE001
            return review, [], [f"五分钟串讲没生成（{type(e).__name__}）—— 详细版里少这一层"]
        if not got or not got.get("paragraphs"):
            return review, [], ["五分钟串讲没生成（模型没回可用内容/被截断）—— 详细版里少这一层"]
        out = dict(review)
        out["overview_long"] = list(got["paragraphs"])
        meta: dict = {}
        if got.get("one_line"):
            meta["one_line"] = got["one_line"]
        if got.get("takeaways"):
            meta["takeaways"] = list(got["takeaways"])
        if meta:
            out["overview"] = meta
        if got.get("self_check"):
            print(f"   🧵 串讲自检：{got['self_check'][:70]}", flush=True)
        return out, [], []

    def _review_v2(self, entries: list[dict], on_progress=None) -> dict:
        """v2 复习层：课件索引 → 分块 review → 覆盖度对齐 → 数字三道核 → 术语库。

        三步的**顺序不能换**（每一步都吃前一步的产物）：
          ① `review` 带课件片段逐块写讲解（并发、按块序合并）
          ② `align` 拿"教授知识点清单 × 笔记小节"做覆盖度（便宜：只喂标题）
          ③ `verify_numbers` 重算数字断言（**错了写进修正说明，不改正文**）
          ④ 术语库追加（作者硬要求；**失败不影响笔记**）
          ⑤ Anki CSV（纯本地）

        ⚠️ 每一步都 fail-soft：少了哪一步都只少那一层，**绝不抛给 `close()`**。
        """
        import note_v2
        self._materials = self._build_materials()
        try:
            from translator import course_title
            domain = course_title(self._glossary_path, self._course)
        except Exception:                                     # noqa: BLE001
            domain = ""
        # ⭐ 更正账本（作者 2026-10-17 第 4 条）：**生成前**把"教授自己更正过的地方 +
        #    官方公式"抽出来当硬约束；生成后 `reconcile` 再逐条核（MC=TC' 这类关系
        #    用数值核，不看模型的自觉）。实测：13:40:30 的口误 `−12Q` 与 13:40:41 的
        #    重述 `−24Q` 都在这份转录里 —— 连跑两次时一次写对、一次写回错的。
        import note_ledger
        import note_guards
        ledger = note_ledger.from_transcript(entries)
        ledger += note_ledger.from_materials(self._materials)
        t_fix = [x for x in ledger if x.get("kind")]
        fml = [x for x in ledger if not x.get("kind")]
        block = note_ledger.block(t_fix, fml)
        if block:
            print(f"   📒 更正账本：{len(t_fix)} 处教授自我更正 + {len(fml)} 条官方公式",
                  flush=True)
        # ⭐ 确定性覆盖度（作者第四轮）：**章节 + 实时要点 = 知识点清单的唯一口径**。
        #    没有章节（旧会话 / 开关关着）时骨架为空 → 退回「模型自己列知识点」的老路。
        import note_cover
        maybe = self._chapter_items()
        sk = note_cover.skeleton(maybe.get("chapters"), maybe.get("atoms"))
        if sk:
            print(f"   🧭 章节骨架：{len(sk)} 章 · "
                  f"{sum(len(c['points']) for c in sk)} 条实时要点（粒度由它定，不由模型定）",
                  flush=True)
        review = note_v2.review(entries, chat=self._chat, domain=domain,
                               materials=self._materials, on_progress=on_progress,
                               extra=block, skeleton=sk)
        if not review:
            return {}
        st = review.get("chunk_stats") or {}
        if st.get("failed"):
            print(f"   ⚠ 生成复习层：{len(st['failed'])}/{st.get('n', '?')} 块失败"
                  f"（已按兜底重试过；这些课段的讲解**缺失**）", flush=True)
        if sk or (self._materials is not None and len(self._materials)):
            # ⭐ 第六轮第 2 条：这一层是**辅助**（引用了哪些课件 + 没讲到的提示），
            #    覆盖度的数字由 `note_cover.report` 按时间窗算 —— 模型的整份回执
            #    曾经被 `max_tokens` 截断、异常又被吞成 `{}`，三节课全报"0/117"。
            #    ⚠️ 现在**失败要说出来**（`align` 返回 `{"failed": ...}`）。
            got = note_v2.align(review, chat=self._chat,
                                materials=self._materials, skeleton=sk)
            if got.get("failed"):
                msg = (f"覆盖度/课件核对没做成（{got['failed']}）—— 覆盖度仍按时间窗"
                       f"自己算，但「笔记引用了哪些课件」这一栏这次是空的")
                print(f"   ⚠ {msg}", flush=True)
                self._note_warns.append(msg)
                got = {k: v for k, v in got.items() if k != "failed"}
            review.update(got)
        if sk:
            # 生成后机械核一遍（**确定性**）：要点按时间窗算有没有节，缺了在 §9 标 ⚠。
            review["cover"] = note_cover.report(sk, review.get("sections") or [])
            cv = review["cover"]
            line = (f"🧭 覆盖度：章节 {cv['n_ok']}/{cv['n_chapters']} 都有讲解 · "
                    f"实时要点 {cv['n_covered']}/{cv['n_points']} 条落到节里")
            if cv["n_covered"] < cv["n_points"]:
                line += f"（缺 {cv['n_points'] - cv['n_covered']} 条，见 §9）"
            print("   " + line, flush=True)
        try:
            checks = note_v2.verify_numbers(
                review, chat=self._chat, materials=self._materials,
                # ⚠️ Jun「🎙 例子要能在转录里对上数字」那条判据要用它（第四轮）
                transcript="\n".join(
                    str(e.get("en") or e.get("asr") or "") for e in entries))
            if checks:
                review["checks"] = checks
                self._corr_extra = note_v2.corrections_from_mismatch(checks)
                bad = [c for c in checks if c.get("status") == "mismatch"]
                un = [c for c in checks if c.get("status") == "unchecked"]
                print(f"🥉 数字三道核：{len(checks)} 项（{len(bad)} 项修正、"
                      f"{len(un)} 项未核对）", flush=True)
        except Exception as e:                                # noqa: BLE001
            print(f"⚠ 数字核对失败({str(e)[:60]}); 笔记照常生成")
        # ⭐ 正文纪律（作者第 2、3 条）：删猜测句 / 引号里的英文必须逐字。
        # ⭐ 版式 A（2026-10-18）：清理链里插一步「五分钟串讲」—— 放在讲解已经清过一遍
        #    之后，它自己再被后面的第二遍 `scrub` 与 `quote_check` 覆盖（新写的散文
        #    必须走同一条纪律，不能因为是"总结层"就免检）。
        for fn, label in ((lambda r: note_ledger.reconcile(r),
                           "更正账本核对"),
                          (lambda r: note_guards.scrub(r), "猜测句清理"),
                          (lambda r: note_guards.ensure_caliber(
                              r, materials=self._materials), "停产价格口径"),
                          (lambda r: note_ledger.ensure_min_value(r), "最小平均成本补值"),
                          (lambda r: self._with_overview(r), "五分钟串讲"),
                          (lambda r: note_guards.scrub(r), "猜测句清理（串讲）"),
                          (lambda r: note_guards.quote_check(
                              r, materials=self._materials,
                              transcript="\n".join(
                                  str(e.get("en") or e.get("asr") or "") for e in entries)),
                           "引用逐字核对")):
            try:
                got_, extra_items, warns_ = fn(review)
                review = got_
                if extra_items and label == "更正账本核对":
                    self._corr_extra = list(self._corr_extra) + list(extra_items)
                for w in warns_:
                    self._note_warns.append(w)
                    print(f"   ⚠ {label}：{w}", flush=True)
            except Exception as e:                            # noqa: BLE001
                print(f"⚠ {label}失败({str(e)[:60]}); 笔记照常生成")
        # 💡 术语库：作者硬要求（从笔记抽词 → `prep.append_terms` 只追加 + 墓碑）。
        try:
            import note_glossary
            notes = self._term_index()
            keep, rejected = note_glossary.candidates(
                review, entries, materials=self._materials,
                # 人名挡在这里（`Masha` 这种词常常**真的**出现在课上，
                # 光靠"没出现过"挡不住）—— 类型来自运行时查词缓存，不另造判据。
                type_of=(lambda t: str(notes.entry_of(t)[1].get("type") or "")
                         if notes is not None else ""))
            outcome = note_glossary.append(keep, course=self._course,
                                           glossary_txt=self._glossary_path)
            self._gloss_added = list(outcome.added)
            print(outcome.line(), flush=True)
            if rejected:
                print(f"   （{len(rejected)} 条没进库："
                      f"{'、'.join(t for t, _ in rejected[:6])}…）", flush=True)
        except Exception as e:                                # noqa: BLE001
            print(f"⚠ 术语库更新失败({str(e)[:60]}); 笔记照常生成")
        return review

    def _build_materials(self):
        """本课课件索引。**失败 = 空索引**（不是异常）—— 复习层据此走退化档。"""
        try:
            import materials
            mats = materials.Materials.for_course(self._vault, self._course)
            print(f"   {mats.describe()}", flush=True)
            return mats
        except Exception as e:                                # noqa: BLE001
            print(f"⚠ 课件索引失败({str(e)[:60]}); 本课笔记不做课件核对")
            return None

    def _write_sidecars(self, data) -> None:
        """同目录的**交互 HTML** 与 **Anki CSV**（2026-10-17 追加要求 B / step 3）。

        ⚠️ 两者都是"额外一层"：任何一个失败都只是少一个文件，**笔记照写**
           （同 `close()` 那八层的纪律）。失败时把 `data.html_name` 清空 ——
           它会被写进 Markdown 的「复习排程」，**不能指着一个不存在的文件**。
        """
        stem = self.vault_path.with_suffix("")
        try:
            import note_html
            # ⭐ 第六轮：KaTeX 走**仓库内副本**（`vendor/katex/0.16.9`），不再走 CDN ——
            #    装到笔记旁边（幂等）之后用相对引用，断网也能渲染。
            data.katex_ok = note_html.install_assets(stem.with_suffix(".html"))
            if not data.katex_ok:
                print("⚠ 公式字体没装上（详细版里公式会显示成 TeX 源码）", flush=True)
                self._note_warns.append("公式字体没装上（KaTeX 副本没复制过去）"
                                        " —— 详细版里公式显示为 TeX 源码")
            self._atomic_write(stem.with_suffix(".html"), note_html.note_page(data))
            print(f"🧩 交互版 HTML：{data.html_name}", flush=True)
        except Exception as e:                                # noqa: BLE001
            data.html_name = self._html_name = ""
            print(f"⚠ 交互版 HTML 没写出来({str(e)[:60]}); Markdown 版照常")
        try:
            import note_v2
            rows = note_v2.anki_rows(data.review, course=self._course, date=self._date)
            if rows:
                self._atomic_write(stem.with_suffix(".anki.csv"), note_v2.to_csv(rows))
                print(f"🃏 Anki 卡 {len(rows)} 张：{data.anki_name}", flush=True)
            else:
                data.anki_name = self._anki_name = ""
        except Exception as e:                                # noqa: BLE001
            data.anki_name = self._anki_name = ""
            print(f"⚠ Anki CSV 没写出来({str(e)[:60]}); 笔记照常")

    def _chapter_items(self) -> dict:
        """课堂纲要 -> `{"windows","chapters","deadlines","atoms"}`（笔记那一节用）。

        ⚠️ **它不联网**（只读两个旁路文件），但仍**住在 `close()` 的护栏区里** ——
           和 qa / lost / keypoint 并排，几层读起来才是同一条纪律。
        ⭐ **边界全在 `chapter.load` / `atom.load` 那一处兜**（`None` 路径 / 文件不存在 /
           坏行 / 写到一半被杀）—— 这里**不重复兜**，否则就是"一条纪律两处定义"。
        ⚠️ **文件不存在是正常路径**：旧会话 / 纯转录档 / `CLASSLIVE_LIVE_SUMMARY=0` 都走这条
           → 返回空 → `_render_outline_section` 整节跳过、笔记照常生成。
        """
        import atom as _atom
        import chapter
        got = chapter.load(chapter.chapter_path_for(self.session_path))
        # ⭐ 原子要点也要 —— 「**没有合成句的章，列出它的原子要点**」（计划 §10.2）。
        # ⚠️ 路径推导**交给 `AtomWriter` 自己**：`<同名>.atoms.jsonl` 在那儿是唯一定义点。
        try:
            got["atoms"] = (_atom.load(_atom.AtomWriter(str(self.session_path)).path)
                            if self.session_path else [])
        except Exception:                                     # noqa: BLE001
            got["atoms"] = []
        return got

    def _lost_items(self, entries: list[dict], explain: bool = True) -> list[str]:
        """旁路事件 -> 笔记里的块（**一块一个标记**，块内已含它的那几句）。

        范围反查全在 `resolve_lost_range`（纯函数）；这里只做 I/O 与拼字。

        `explain`：有 `lost_explain_fn` 时给每块再追加「💡 讲解」与一张 `::` 闪卡
        （**会联网**，所以 `close()` 在用户取消时传 False）。fn 失败 / 返回条数对不上
        块数 -> **一字不加**，输出与没有这一层时逐字相同。
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
        spans: list[tuple[int, int]] = []        # 每块对应 entries[i0:i1+1]
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
                lines.append(f"  - `{e['ts']}` "
                             f"{e['zh'] or e['en'] or e['asr']}{_bf_mark(e)}")
            out.append("\n".join(lines))
            spans.append((i0, i1))
        if not (explain and self._lost_explain_fn and out):
            return out
        print("🤔 正在给 ❓ 标记写讲解…", flush=True)
        try:
            cards = self._lost_explain_fn(
                [[{"en": e["en"], "zh": e["zh"]} for e in entries[i0:i1 + 1]]
                 for i0, i1 in spans])
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ ❓ 讲解失败({str(e)[:60]}); 笔记照常生成")
            return out
        if not isinstance(cards, list) or len(cards) != len(out):
            return out                  # 对不上就整体不用, 绝不错位
        for k, c in enumerate(cards):
            if not isinstance(c, dict):
                continue
            ex, q, a = c.get("explain"), c.get("q"), c.get("a")
            if ex:
                out[k] += f"\n  - 💡 讲解：{ex}"
            if q and a:
                # `::` 在问题里会伪造卡片边界, 同 `_qa_items`
                out[k] += f"\n  - {q.replace('::', ': :')}::{a}"
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
    # ---- 组装双层笔记 ----
    def _render_note(self, entries: list[dict], review: dict,
                     qa_items: list[str] | None = None,
                     polish_state: str = "ok",
                     lost_items: list[str] | None = None,
                     keypoint_items: list | None = None,
                     chapter_items: dict | None = None,
                     review_missing: bool = False) -> str:
        """**位置参数一个没变**（旧判据仍钉着这条签名），版式交给 `note_render.render`。

        ⚠️ 2026-10-17（v2）：版面**整个换了** —— 中文连贯讲解、覆盖度/校验清单、
        自测折叠；英文只留在术语原形、引用原文与附录 B 的逐句转录里。旧版那三层
        （`en` / `detail_en` / `zh`）的渲染代码随之删掉，**不是**在旧函数里加分支：
        两套版式同时活着，迟早有人改了一处、另一处静默变旧（本仓库反复踩的那一类）。

        ⚠️ 壳留在原地的理由与 `_chapter_items` 同类：`tests/` 里四条现有判据
        （❓ 块 / ⭐ 节 / 🎯 节 / polish 三档）**指向的就是这个函数**，
        它们是"版式重写时不许弄丢的层"，签名一变它们就全静默失效。
        """
        import note_render
        return note_render.render(self._note_data(
            entries, review or {}, qa_items or [], polish_state, lost_items or [],
            keypoint_items or [], chapter_items or {}, review_missing))

    def _note_data(self, entries: list[dict], review: dict, qa_items: list,
                   polish_state: str, lost_items: list, keypoint_items: list,
                   chapter_items: dict, review_missing: bool):
        """把 writer 的运行时状态 -> `note_render.NoteData`（**纯数据组装**）。"""
        import note_render
        sel = self._term_selection(entries)          # 💡 命中 + 过滤 + 记账
        self._gloss_sel = sel
        extra = self._review_term_hits(review, entries)
        mats = self._materials
        warns: list = list(self._note_warns)
        if mats is not None and len(mats) == 0:
            warns.append("本课**未做课件核对** —— 没找到这门课的课件材料"
                         "（`Study/<课号>/materials/` 或 `~/.classlive/courses/<课号>/materials/`），"
                         "笔记里的「为什么」只能靠 📚 标准理论。")
        return note_render.NoteData(
            date=self._date, course=self._course, entries=entries, review=review,
            qa_items=qa_items, lost_items=lost_items, keypoint_items=keypoint_items,
            chapter_items=chapter_items, polish_state=polish_state,
            review_missing=review_missing,
            materials_line=(mats.describe() if mats is not None else ""),
            glossary_added=list(self._gloss_added),
            term_footer=sel.footer(),
            term_rows=note_terms.render_table(sel, extra=extra),
            review_dates=list(self._review_dates),
            anki_name=self._anki_name, html_name=self._html_name,
            corrections_extra=list(self._corr_extra), warns=warns)
        # ⚠️ `kaTeX` 那一格由 `_write_sidecars` 在**真装过之后**回填（`note_html` 是纯渲染，
        #    装资源是 I/O）—— 默认 True = "还不知道"，只有**已知失败**才写那一行警告。

    def _review_term_hits(self, review: dict, entries: list) -> list:
        """复习层讲到的术语 -> 术语表行（带**首次出现时间戳**）。

        ⚠️ 时间戳是**搜出来的**（`compile_term` 在转录里找第一句命中的），
           不是让模型编一个 —— 出处那一列必须能回源（审计 §2b 结论 4）。
        """
        out = []
        try:
            from build_notes import compile_term
        except Exception:                                       # noqa: BLE001
            compile_term = None
        tagged: list = [(t, _one_line(s.get("ts")))
                        for s in (review.get("sections") or [])
                        for t in (s.get("terms") or [])]
        tagged += [(t, "") for t in (review.get("terms") or [])]
        seen: set = set()
        for t, sec_ts in tagged:
            en, zh = _one_line(t.get("en")), _one_line(t.get("zh"))
            if not en or en.lower() in seen:
                continue
            seen.add(en.lower())
            ts = ""
            if compile_term is not None:
                try:
                    rx = compile_term(en)
                    for e in entries:
                        if rx.search(e.get("en") or e.get("asr") or ""):
                            ts = e.get("ts", "")
                            break
                except Exception:                               # noqa: BLE001
                    ts = ""
            # ⚠️ 转录里搜不到（ASR 把词听碎了、或原话用了别的说法）时退到**它所在小节的
            #    时间戳** —— 出处那一列宁可粗一点（"这一节在 13:23"），也不能空着：
            #    空的出处 = 读者没法回源（审计 §2b 结论 4 要的正是"双锚点"）。
            if not ts and sec_ts:
                ts = sec_ts
            out.append(note_terms.Hit(term=en, gloss=zh or "（本课术语）", ts=ts,
                                      source=note_terms.SRC_LECTURE))
        return out

    def _atomic_write(self, path: Path, text: str) -> None:
        """原子落盘：同目录写临时文件 → `os.replace` 换过去。

        ⚠️ 为什么必须原子（3.9.7 第 5b 项）：**同一个路径会被写两次**
        （基础版 → 复习层回来后的完整版），而 Obsidian 很可能正开着这份笔记。
        直接 `write_text` 会在「截断」和「写完」之间留一个**空文件 / 半截文件**的
        窗口 —— 用户正好切过去看到的就是那个。`os.replace` 是同文件系统内的原子
        rename，读者要么看到旧的、要么看到新的。
        ⚠️ 临时文件放在**同一目录**（不是系统临时目录）：跨文件系统的 `os.replace`
           会退化成 copy+unlink，就不是原子的了。
        ⚠️ 失败（磁盘满 / 只读目录）**照旧往外抛** —— 调用点那层护栏会把它变成
           「收尾失败」，与原来 `write_text` 抛出去的形状一致（不许把失败吞成"存好了"）。
        """
        tmp = path.parent / (path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

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
        # ⚠️ 关旁路句柄放在**最前面**：`close()` 有四条早退（未启用 / **没库** /
        #    零句又无问答 / 用户答"不保存"），放在后面就会在那些路径上漏掉它。
        #    幂等，重复调无妨。
        #    2026-09-28 由作者那句"不用保存笔记"提醒才发现 —— 那正是会走到早退的路径。
        self.close_lost()
        self.close_atom()      # ⚠️ 同一条教训：**都在早退之前**（下面有四条早退）
        self.close_merges()    # 同上：跨段半句合并的旁路句柄也在早退之前关
        if not self.enabled or not self.session_path:
            return ""
        # ⚠️ **没有库 = 只跳过「笔记」那一半** —— `sessions/` 已经在了（每句定稿
        #    都 `append()` 过）。2026-09-29 加的。（`enabled` 拆开之前这里到不了。）
        #    ⚠️ 必须**早退在这之前**：下面的精修 / 复习层要跑好几轮 LLM（花 API 钱），
        #       而它们**只服务于笔记** —— 没有库就没有笔记可写，跑了是白烧钱。
        #    ⚠️ 将来的「打包成 MD/PDF」接的正是这一档：`_render_note()` 是**纯函数**
        #       （不碰 vault），导出直接复用它换落点即可。
        if self._vault is None:
            # ⚠️ 这句会留在收尾卡上**等用户点关闭**（`main` 看 `writes_notes`）——
            #    2026-10-12 朋友实测：原来先问「存入 Obsidian 吗？」、点了「存」再在一张
            #    3 秒就关的卡上说没存成，她以为存好了，好几节课都没笔记。所以要写清「下次怎么办」。
            return (f"📄 这节课记了 {self._n} 句，但还没设笔记库，所以没生成 Obsidian 笔记。\n"
                    f"下次开课前在面板上点「笔记库」选好 Obsidian 库，之后每节课都会存进去。\n"
                    f"这节课的逐句记录在安装目录的 sessions/{self.session_path.name}")
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

        # ⚠️⚠️ **`errors="replace"` 不能省**（2026-09-30 OCR 审查发现）。
        #    写到一半被杀（崩溃 / 强杀）时，末行的**多字节汉字可能只剩半个** ——
        #    严格 utf-8 解码会抛 `UnicodeDecodeError: unexpected end of data`，
        #    于是**整份笔记写不出来**；而 `rebuild_note.py`（补笔记那条路）
        #    走的正是这个 `close()` → **连补救路一起断**。
        #    ⚠️ 同仓 8 处早就都带容错（`find.py` / `atom.py` / `chapter.py` /
        #       `entry_panel.py` / `corpus.py` / `keypoints.py` 全是 `errors=`），
        #       **只有这条主路径漏了**。用 `replace` 而不是 `ignore`：
        #       坏掉的那一个字节会留下 `�` 让人看见，而不是静默少一个字。
        entries = self._parse(self.session_path.read_text(encoding="utf-8",
                                                          errors="replace"))
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
        # ✂️ 碎句合并：字幕切成两三行的同一句话并回一行、整句重译（实测见 `note_merge.py` 文件头）。
        #    ⚠️ 同 polish：只改笔记里的 entries，`sessions/` 不动；额外一层，失败不许带走底座。
        #    放在复习层**之前**：复习层 / 🎯 重点句读到的也是整句。
        if self._key and entries and not (cancel is not None and cancel.is_set()):
            try:
                import note_merge
                from translator import course_title
                if note_merge.enabled():
                    mstats: dict = {}
                    entries = note_merge.merge_entries(
                        entries, self._key, self._model,
                        domain=course_title(self._glossary_path, self._course),
                        stats=mstats, cancel=cancel)
                    if mstats.get("merged"):
                        print(f"✂️ 碎句合并：{mstats['merged']} 句由几行并回一行"
                              + (f"（{mstats['runs'] - mstats['merged']} 句没并成，照旧分行）"
                                 if mstats["runs"] > mstats["merged"] else ""), flush=True)
            except KeyboardInterrupt:
                print("⚠ 碎句合并被中断；照旧分行", flush=True)
            except Exception as e:                        # noqa: BLE001
                print(f"⚠ 碎句合并失败({str(e)[:60]}); 照旧分行", flush=True)
        # 🩹 漏译句补译（3.9.7 第 2 项）：把**只有 ASR** 的句子在渲染笔记之前补上中英。
        #    ⚠️ 同 polish / note_merge：只改笔记里的 entries，`sessions/` 一个字不动；
        #       它也是「额外一层」，没网 / 到点 / 失败都只是少几句中文，笔记照写。
        #    ⚠️ 位置在**碎句合并之后**：并句要的是整句，补译只认"还没中文"的那几条 ——
        #       先并后补，两条路不会互相覆盖（已精修过的课这里 `need` 恒为 0）。
        #    ⚠️ **在复习层之前**：复习层喂模型的正是 `en or asr`，补出来的英文它也吃得到
        #       （两步串行，总时长与顺序无关，见 `_review` 那段的分块说明）。
        if self._backfill_fn is not None and entries \
                and not (cancel is not None and cancel.is_set()):
            try:
                entries, bstats = self._backfill_fn(entries)
                if bstats.get("need"):
                    print(f"🩹 漏译句补译：{bstats.get('done', 0)}/{bstats['need']} 句",
                          flush=True)
            except Exception as e:                        # noqa: BLE001
                print(f"⚠ 漏译句补译失败({str(e)[:60]}); 笔记照旧")
        # ⭐⭐ 3.9.7 第 5b 项：**基础版先原子落盘**（转写 + 译文这一层，含精修 /
        #    碎句合并 / 补译；不需要再调模型）。
        #    ⚠️⚠️ 为什么：复习层（`REVIEW_WORKERS=5` 并发那一段）实测要几十秒，
        #       而它没回来之前 `Lectures/` 下**一个文件都没有** —— 用户这时关窗 /
        #       崩溃 / 强杀，这节课**一份笔记都没有**（`sessions/` 里有逐句日志，
        #       但知道 `scripts/rebuild_note.py` 能补的人不多）。
        #       现在盘上任何时刻都有一份**可读的**笔记；复习层回来后再**原子重写**
        #       同一路径为完整版（`_atomic_write`，Obsidian 开着也只看到旧版或新版）。
        #    ⚠️ 只动 `Lectures/`，`sessions/` 的**三方契约**格式一个字节不碰。
        #    ⚠️ 路径在这里算**一次**（见结尾那段：`note_path_for` 遇到同名会另存一份）。
        self.vault_path = note_path_for(self._vault, self._date, self._course)
        # ⚠️ 基础版也带上**本地就能算**的两层（❓ 标记不讲解、📑 纲要）：传空会被渲染成
        #    「课上没按 ❓」——那是假话。`review_missing` 同理：有 key 时说「这次没生成」，
        #    不说「未配 API key」（2026-10-16 OCR 审查）。
        base_lost: list[str] = []
        base_chapters: dict = {}
        try:
            base_lost = self._lost_items(entries, explain=False)
        except Exception:                                 # noqa: BLE001
            pass
        try:
            base_chapters = self._chapter_items()
        except Exception:                                 # noqa: BLE001
            pass
        # 🧩 基础版**也写详细版 HTML**（2026-10-17 第六轮：md 变简版、转录搬进 HTML）：
        #    ⚠️ 只写 md 的话，复习层没回来这段时间盘上那份笔记里**转录一个字都没有**
        #       —— "盘上任何时刻都有一份可读的笔记"就落空了；而且 `html_name` 为空时
        #       简版顶部会写「**详细版这次没生成**」，而它两分钟后就有 —— 那是假话。
        #    ⚠️ 这与完整版走**同一条**组装+落盘路径（`_note_data` → `_write_sidecars`
        #       → `_atomic_write`），不另起一套"基础版渲染"。
        import note_render
        base_stem = self.vault_path.with_suffix("")
        self._html_name = base_stem.name + ".html"
        base_data = self._note_data(entries, {}, [], polish_state, base_lost, [],
                                    base_chapters, bool(self._key))
        self._write_sidecars(base_data)
        self._atomic_write(self.vault_path, note_render.render(base_data))
        if self._key:
            print("🤖 正在生成复习层(讲解 + 例题 + 自测)…", flush=True)
        # ⚠️ 同 `polish` 那条：复习层要跑好几轮 LLM（按 `note_v2.REVIEW_CHUNK` 分块），
        #    中断它同样**不许**带走底座。
        if cancel is not None and cancel.is_set():
            review = {}
            print("⚠ 已跳过复习层（用户取消）；笔记照常生成", flush=True)
        else:
            try:
                review = self._review_v2(entries, on_progress=_prog)
            except KeyboardInterrupt:
                review = {}
                print("⚠ 复习层被中断；笔记照常生成（只有逐句转录）", flush=True)
            except Exception as e:                        # noqa: BLE001
                # ⚠️ `_review_v2` 自己写着"绝不抛"，但**收尾最后这一层**再兜一次是同一条
                #    既有纪律（额外一层不许带走底座）—— polish 那层就是这么写的。
                #    3.9.7 第 5b 项起，兜住它 = 盘上那份基础版会补一行「复习层这次没生成」。
                review = {}
                print(f"⚠ 复习层失败({str(e)[:60]}); 笔记照常生成（只有逐句转录）")
        # ⭐ 复习层**本可以生成却没生成** → 完整版里留一行说明（怎么补回来）。
        #    ⚠️ 判据是「有 key」：没 key 时那行本来就在说"未配 API key"，不必改口。
        #    ⚠️ 这一版会**原子重写**基础版：内容只会更多（qa / ❓ / 重点句那一层是在
        #       复习层**之后**才算的）—— 不是把基础版留在盘上不管。
        review_missing = bool(self._key) and not (review.get("sections") or [])

        # 问答行自带 try/except: 渲染问答抛出去就再也没有那份转录笔记了。隔离是硬要求:
        # 笔记的底座是逐句转录, 它是不能丢的那件事; 问答只是额外一层。
        # ⚠️ 2026-09-28 起这条纪律**逐层补齐了**（polish / 碎句合并 / 🩹 补译 / review /
        #    qa / lost / keypoints / 📑 纲要 —— **八层**）；
        #    原来只有后两层有护栏，而前两层才是跑得最久的。
        #    ⚠️ 八层**住在这里**，不许藏进 `_render_note` —— 那个函数是纯的，
        #       让它发请求是一条没人知道的隐式契约（keypoints 2026-09-29 挪出来的原因）。
        qa_items: list[str] = []
        if qa:
            try:
                qa_items = self._qa_items(qa() if callable(qa) else qa)
            except Exception as e:                        # noqa: BLE001
                print(f"⚠ 问答落盘失败({str(e)[:60]}); 笔记照常生成")
        # ❓ 同一条纪律：它是额外一层，**逐句转录才是不能丢的那件事**。
        lost_items: list[str] = []
        try:
            # 讲解会联网: 用户取消(跳过精修)时不发请求, 其余与 keypoints 同一条纪律
            lost_items = self._lost_items(
                entries, explain=not (cancel is not None and cancel.is_set()))
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ ❓ 反查失败({str(e)[:60]}); 笔记照常生成")
        # 🎯 同上。⚠️ 这一层会联网（一节课实测约 6 秒 / 20 次请求；🩹 补译那层也会），
        #    所以它更不能把笔记带下水。`_keypoint_items` 自己也会兜一层，这里再兜
        #    一次是因为上面两层都这么写 —— 八层并排读起来才是同一条纪律。
        keypoint_items: list = []
        try:
            keypoint_items = self._keypoint_items(entries)
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ 🎯 重点句失败({str(e)[:60]}); 笔记照常生成")
        # 📑 课堂纲要。⚠️ **不联网**（只读旁路文件），但仍并排放在这里 ——
        #    几层护栏读起来才是同一条纪律（详见 `_chapter_items` 的 docstring）。
        chapter_items: dict = {}
        try:
            chapter_items = self._chapter_items()
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ 📑 课堂纲要失败({str(e)[:60]}); 笔记照常生成")
        # 🗓 复习排程（间隔复习日期 —— 写进笔记，**不做推送**）。
        import note_v2
        import note_render
        self._review_dates = note_v2.review_dates(self._date)
        # 🧩🃏 同目录的两个旁车文件。**先写旁车、再组数据**：Markdown 的「复习排程」
        #    那一节要写出它们的名字，而名字只有在**写成功**之后才算数
        #    （失败就清空 —— 否则笔记会指着一个不存在的文件，那是假话）。
        stem = self.vault_path.with_suffix("")
        self._html_name = stem.name + ".html"
        self._anki_name = stem.name + ".anki.csv"
        data = self._note_data(entries, review, qa_items, polish_state, lost_items,
                               keypoint_items, chapter_items, review_missing)
        self._write_sidecars(data)
        note = note_render.render(data)

        # ⚠️⚠️ **路径不再重算**：基础版那一步已经把它定下来了，而
        #    `note_path_for` 遇到同名会**另存一份带时间戳的** —— 再算一次就变成
        #    「同一节课两份笔记」，而且用户看到的还是缺复习层那份的兄弟文件。
        self._atomic_write(self.vault_path, note)
        layer = ("讲解+例题+自测+校验清单" if review.get("sections")
                 else "术语表+标记(复习层这次没生成)" if review_missing
                 else "术语表+标记(未生成讲解)")
        extra = "".join(x for x in (
            "，另存了交互版 HTML" if self._html_name else "",
            "，Anki CSV 已导出" if self._anki_name else "") if x)
        # ⭐ 第六轮：md 变成**简版**之后，讲解/例题/变式/自测/图表/转录**只在 HTML 里** ——
        #    下课这一行必须给出它的路径（作者原话："下课提示里给 HTML 的路径"），
        #    否则按老习惯打开 md 的人会以为笔记少了几层。
        html_line = (f"\n   详细版 HTML: {self.vault_path.with_suffix('.html')}"
                     if self._html_name else "")
        # 💡 术语库那一句走**现成通道**（收尾卡 / 终端返回串）—— 不新建 UI
        #    （作者要求「有感知」：这节课往术语表里加了什么，得有人告诉他）。
        gloss_line = (f"\n   💡 新加入术语库 {len(self._gloss_added)} 条："
                      f"{' · '.join(self._gloss_added)}" if self._gloss_added else "")
        return (f"📝 已存入 Obsidian({self._n} 句, 复习层: {layer}{extra}) → {self.vault_path}"
                f"{html_line}{gloss_line}\n"
                f"   原始逐句日志: {self.session_path}")
