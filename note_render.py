"""笔记**版式**（2026-10-17 v2）：md = **简版**，HTML = **唯一的详细版**。

作者的两句话（这一版版式的全部理由，2026-10-17 第六轮拍板）：

> HTML = 唯一的详细版（讲解/例题/变式/自测/图表全在里面）；下课提示里给 HTML 的路径。
> md = 简版：只放 五分钟速览 + 课堂纲要 + 术语表 + 一些事儿 + 顶部一行「打开交互版」
> 链接。目标体积 ≤ 原 md 的 15%。

⭐ 所以两份**同一份数据**（`NoteData`，生产者是 `obsidian_writer.close`）渲染，
   不为任何一份多调模型；md 版里丢掉的那几层**在 HTML 里一件不少**：
   讲解 / 例题 / 变式 / 补全题 / 自测 / 图表 / 修正记录 / 我标的地方 / 标星句 /
   🎯 最值得记的几句 / 逐句转录（证据层）全在 `note_html.note_page` 里。
   ⚠️ 换渲染层最容易出的事故是"两头都丢"（md 不印了、HTML 也没加）——
      `tests/test_note_v2.py::FormatSplit` 逐层钉着这件事。

⚠️ 本模块是**纯渲染**：不联网、不碰 AppKit、不读文件。判据直接喂 `NoteData`。
"""
from __future__ import annotations
import dataclasses
import re


@dataclasses.dataclass
class NoteData:
    """渲染一份笔记要的全部数据。**纯数据**（生产者是 `obsidian_writer.close`）。"""
    date: str = ""
    course: str = ""
    entries: list = dataclasses.field(default_factory=list)
    review: dict = dataclasses.field(default_factory=dict)
    qa_items: list = dataclasses.field(default_factory=list)      # 我课上问过的问题
    lost_items: list = dataclasses.field(default_factory=list)    # ❓ 标记块
    keypoint_items: list = dataclasses.field(default_factory=list)
    chapter_items: dict = dataclasses.field(default_factory=dict)
    polish_state: str = "off"
    review_missing: bool = False
    #: 📖 课件那一行（`materials.Materials.describe()` 或退化说明）。
    materials_line: str = ""
    #: 本课新加入术语库的词（step 3 的术语抽取）。
    glossary_added: list = dataclasses.field(default_factory=list)
    #: 术语表被过滤的记账行（`note_terms.Selection.footer()`）。
    term_footer: str = ""
    #: 术语表（已过滤/已合并）行 —— `note_terms.render_table` 的输出。
    term_rows: list = dataclasses.field(default_factory=list)
    #: 复习排程日期 ISO 列表（`note_v2.review_dates`）。
    review_dates: list = dataclasses.field(default_factory=list)
    #: Anki CSV 文件名（写了才有；只写进说明那一行）。
    anki_name: str = ""
    #: 同目录的 HTML 文件名（**详细版**；简版顶部那行链接指向它）。
    html_name: str = ""
    #: 别处已经算好的修正说明（`note_v2.corrections_from_mismatch`）。
    corrections_extra: list = dataclasses.field(default_factory=list)
    #: 头部警告（如「本课未做课件核对」）—— 每行一条，进页尾内部记录。
    warns: list = dataclasses.field(default_factory=list)
    #: 交互版有没有拿到 KaTeX 副本（`note_html.install_assets`）——
    #: 只有**已知失败**时才写一行说明（不知道就不说）。
    katex_ok: bool = True


# ⚠️ `_POLISH_NOTE` **不在这里** —— 它的唯一定义点在 `obsidian_writer`（`polish:`
#    这个字段就是在那里定的，`test_wrapup` 也钉着 `ow._POLISH_NOTE`）。
#    本模块渲染时去那边取（同 `_render_outline_section` 的做法），不抄第二份。


def _one(v) -> str:
    """单行化 + 渲染层兜底：`q^3-3q^2+12q` → `$q^{3}-3q^{2}+12q$`。

    ⚠️ 这一层是**兜底**，不是替模型写公式：prompt 仍然要求它自己写 `$…$`
       （2026-10-17 验收实测：整篇 HTML 里 `$` 个数是 **0** —— 正文写着
       `q^3-3q^2+12q` 这种纯文本，KaTeX 没有作用对象）。
       幂等：已经写好的 `$…$` 原样不动（`note_math.auto_tex` 的判据）。
    """
    import note_math
    return note_math.auto_tex(" ".join(str(v).split())) if v else ""


def _first_sentence(text: str, *, max_chars: int = 60) -> str:
    """一段讲解 → 速览里那句"这节讲什么"。**在句末收**，绝不切半句。

    ⚠️ 实测：`text[:80]` 会把「令 $f'(x)=0$ 解」这样半句话留在速览里 ——
       速览是"五分钟读完"的那一层，半句比没有更费解。
    """
    s = _one(text)
    if not s:
        return ""
    for mark in ("。", "；", ". ", "！", "?"):
        k = s.find(mark)
        if 0 < k <= max_chars:
            return s[:k + 1].strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    k = max(cut.rfind("，"), cut.rfind(","), cut.rfind(" "))
    return (cut[:k] if k > 10 else cut).strip() + "…"


def _bf(e: dict) -> str:
    """🩹 补译标记（` ·补`）—— **唯一定义点在 `obsidian_writer._bf_mark`**。

    ⚠️ 这一行是**给读者的真话**：这一句的中文不是课上的直播译文（是 `backfill.py`
       课后补的）。渲染层重写时最容易把它弄丢 —— `tests/test_backfill.py` 钉着它。
    """
    try:
        from obsidian_writer import _bf_mark
        return _bf_mark(e)
    except Exception:                                          # noqa: BLE001
        return ""


def _polish_line(state: str) -> str:
    """页尾内部记录里"这课的转录精修了没有"那一行。**跟着 `polish:` 字段走**。"""
    if state == "off":
        return "- **逐句转录不再精修**（2026-10-17 起默认不跑）：原样保留，仅作证据层。"
    return (f"- 本课**转录做过精修**（`polish: {state}`，`--polish auto`）："
            "逐句记录里是精修后的那版，`sessions/` 里的原始记录未动。")


def _missing_line(d: "NoteData") -> str:
    """「这次没生成讲解」那一行 —— **两种原因分开说**（同旧版 `_render_note` 的取舍）。

    ⚠️ 判据是调用方给的 `review_missing`（= **有 key 却没生成**），不是"有没有 sections"：
       · 有 key 没生成 → 「复习层这次没生成」+ **怎么补**（`scripts/rebuild_note.py`）；
       · 没 key → 「未配 API key 或调用失败」—— 这时说"这次没生成"是**假话**，
         说"去 rebuild"是让人白跑一趟（rebuild 同样没 key）。
    """
    if d.review_missing:
        return ("*（复习层这次没生成 —— 收尾时被跳过或失败。逐句转录一条没少；要补："
                "`ClassLive.app/Contents/MacOS/python scripts/rebuild_note.py "
                "<会话文件> <课号>`）*")
    return "*（未自动生成 —— 未配 API key 或调用失败；可课后自行补）*"


def _detail_link(d: "NoteData") -> list:
    """顶部那行「打开交互版」（**相对路径**指向同名 `.html`）。

    ⚠️ 详细版没生成时（HTML 那一步失败）这一行要**说清楚**，不能指着一个不存在的
       文件 —— 读者会以为笔记坏了（同 `_write_sidecars` 里清空 `html_name` 的取舍）。
    """
    if d.html_name:
        return [f"> 📄 **详细版**（讲解 / 例题 / 变式 / 自测 / 图表 / 逐句转录）："
                f"[{d.html_name}]({d.html_name})　（同目录，双击打开；公式离线也能渲染）", ""]
    return ["> ⚠ 这份是**简版**，而**详细版这次没生成**（讲解/例题/变式/自测/图表都不在"
            "这份里）。会话记录仍是完整的：`sessions/`。", ""]


def render(d: NoteData) -> str:
    """`NoteData` -> **简版** Markdown（作者第六轮第 6 条）。"""
    entries = d.entries
    review = d.review or {}
    ts0 = entries[0]["ts"] if entries else "—"
    ts1 = entries[-1]["ts"] if entries else "—"
    sections = review.get("sections") or []
    qa = review.get("qa") or []
    title = _one(review.get("title_zh")) or _one(review.get("title"))
    L: list = ["---",
               f"date: {d.date}",
               f"course: {d.course}",
               "type: lecture-notes",
               "tags: [lecture, review, v2]",
               f"polish: {d.polish_state}",
               "---", "",
               f"# {d.course} · {d.date}" + (f" · {title}" if title else ""), ""]

    # ── ① 打开详细版（一行）────────────────────────────────────────────
    L += _detail_link(d)

    # ── ② 这段课（几行事实；详细版里还有这些 + 全部内容）──────────────────
    checks = list(review.get("checks") or [])
    n_ok = sum(1 for c in checks if c.get("status") == "ok")
    n_bad = sum(1 for c in checks if c.get("status") == "mismatch")
    n_un = sum(1 for c in checks if c.get("status") == "unchecked")
    pts = review.get("lecture_points") or []
    cov = review.get("covered") or []
    cv = review.get("cover") or {}
    if cv.get("rows"):
        # ⭐ 第六轮：覆盖度**按时间窗算**（要点/小节都有时间戳，不再看模型的对齐回执）。
        cov_line = (f"覆盖度：章节 {cv['n_ok']}/{cv['n_chapters']} 都有讲解 · "
                    f"实时要点 {cv['n_covered']}/{cv['n_points']} 条落进笔记")
        if cv["n_points"] > cv["n_covered"]:
            cov_line += f"（缺 {cv['n_points'] - cv['n_covered']}，明细见详细版）"
    elif pts:
        n_cov = sum(1 for c in cov if _one(c.get("section")) and
                    _one(c.get("section")) not in ("缺失", "missing", "-", "无"))
        cov_line = f"覆盖度：教授知识点 {len(pts)} 个 → 笔记覆盖 {n_cov} 个"
    else:
        cov_line = "覆盖度：未生成知识点清单"
    L += ["> [!info] 这段课", ">"]
    L += [f"> 🕐 {ts0} – {ts1} · 🗣 {len(entries)} 句 · 💡 {len(d.term_rows)} 个术语 · "
          f"❓ {len(d.lost_items)} 处标记"]
    if d.materials_line:
        L += [f"> {d.materials_line}"]
    L += [f"> {cov_line}"]
    if n_ok or n_bad or n_un:
        L += [f"> ✅ 数字核对：{n_ok + n_bad} 处已核"
              + (f"（其中 {n_bad} 处按 📖/📚 改过）" if n_bad else "")
              + f" · {n_un} 处待你确认（明细见详细版）"]
    if d.katex_ok is False:
        L += ["> ⚠ 这次没装上公式字体（详细版里公式会显示成 TeX 源码）"]
    L += [""]

    # ── ③ 五分钟速览 ──────────────────────────────────────────────────
    L += ["## 0 · 五分钟速览", ""]
    if sections:
        L += ["这节讲的东西按下面的顺序串起来：", ""]
        for i, s in enumerate(sections, 1):
            gist = _first_sentence((s.get("prose") or [""])[0])
            L += [f"{i}. **{_one(s.get('heading'))}**" + (f" —— {gist}" if gist else "")]
        L += [""]
    else:
        L += [_missing_line(d), ""]
    plan = review.get("review_plan") or []
    if plan:
        # ⚠️ 上限 5 条：`review_plan` 是**每块**各给 2–3 条再合并的（5 块 = 十几条），
        #    铺出来就是一屏噪音 —— 这一节是"学完会什么"，不是课后作业清单。
        L += ["**学完你应该会：**", ""] + [f"- {_one(x)}" for x in plan[:5]] + [""]

    # ── ④ 课堂纲要（骨架先于细节）──────────────────────────────────────
    try:
        import obsidian_writer as _ow
        if d.chapter_items:
            _sec = _ow._render_outline_section(d.chapter_items, review=d.review)
            if _sec:
                L += _sec
    except Exception:                                          # noqa: BLE001
        pass

    # ── ⑤ 本课术语 ────────────────────────────────────────────────────
    L += ["## 本课术语", ""]
    if d.term_rows:
        L += d.term_rows
    else:
        L += ["*（本课没有命中术语表）*"]
    if d.term_footer:
        L += [d.term_footer]
    if d.glossary_added:
        names = " · ".join(f"`{t}`" for t in d.glossary_added)
        L += ["", f"**本课新加入术语库 {len(d.glossary_added)} 条**：{names}",
              "", "*（下一节课开课时，实时翻译会把它们全量注入 prompt —— "
              "见 `glossary/<课号>.txt`。）*"]
    L += [""]

    # ── ⑥ 一些事儿（复习排程 / Anki / 我标的地方 / 标星句 / 🎯）──────────
    L += ["## 一些事儿", ""]
    if d.review_dates:
        L += ["**复习排程**（笔记只写日期，不推送）："
              + " · ".join(f"第 {i + 1} 次 `{dt}`" for i, dt in enumerate(d.review_dates)), ""]
        L += ["> ⚠️ **感觉顺利 ≠ 学得牢**：重读一遍觉得「都懂」是最常见的错觉（desirable "
              "difficulty）。真正的检验是**合上笔记能不能写出来**。", ""]
    if d.anki_name:
        L += [f"- 自测与术语已导出为 Anki 卡：`{d.anki_name}`（导入 Anki → File → Import）"]
    if d.lost_items:
        L += ["", "**我课上标的地方**（重点 / 没听懂）：", ""]
        for blk in d.lost_items:
            L += blk.splitlines()
    stars = [e for e in entries if e.get("star")]
    if stars:
        L += ["", "**⭐ 我标记的重点**：", ""]
        for e in stars:
            L += [f"- `{e['ts']}` {e['zh'] or e['en'] or e['asr']}"]
    if d.keypoint_items:
        L += ["", "**🎯 这节课最值得记的几句**（按 Jev 给「值不值得抄进复习纸」的概率排序；"
              "⚠️ 排序信号，不是考试预测）：", ""]
        for p, text, ix in d.keypoint_items:
            tag = f"L{ix[0] + 1}" if len(ix) == 1 else f"L{ix[0] + 1}–{ix[-1] + 1}"
            L += [f"- `{p:.2f}` `{tag}` {text}"]
    if qa:
        L += ["", f"**自测 {len(qa)} 题**（先自己想，答案与解析在详细版里）：", ""]
        for i, x in enumerate(qa, 1):
            L += [f"{i}. {_one(x.get('q'))}"]
    L += [""]

    # ── ⑦ 生成核对记录（内部 · 折叠）───────────────────────────────────
    # ⚠️ 它留在简版里：这是"笔记被机器动过哪里"的凭据（作者第三轮的要求），
    #    详细版里那份更全（逐条修正记录），但简版不能一个字不提。
    L += ["---", "", "## 附录 · 信息纪律与生成核对记录", "",
          "- **来源三分**：🎙 课上一手 / 📖 官方材料（课件、试卷、更正）/ "
          "📚 标准理论（用于讲通「为什么」或裁定口误）。",
          "- **冲突裁定序**：📖 官方材料 > 📚 标准理论 > 🎙 转录口误。",
          "- **数字三道核**：ⓐ 代数自洽 → ⓑ 对照课件 → ⓒ 核不了标「未核对」。",
          # ⚠️ 这行是**事实陈述，不是口号**：2026-10-17 起逐句精修默认关（转录原样当
          #    证据层），但 `--polish auto` 仍能把它开回来 —— 那时还写"不再精修"
          #    就是**假话**（本仓库对"假话"的容忍度是零：读者会拿它当"这条没被改过"的凭据）。
          _polish_line(d.polish_state), ""]
    all_corr = list(review.get("corrections") or []) + list(d.corrections_extra or [])
    # ⚠️ 两类要**分开数**（`test_三道核的_mismatch_措辞不同_它是发现不是口误`）：
    #    · 「修正」= 课上原话 → 修正值（模型给的 `corrections`，是口误/ASR 听错）；
    #    · 「三道核发现」= 机械/模型重算出的不一致（`kind == "check"`）——
    #      把它并进"修正 N 处"是**改了笔记的账**：那处原话没错，是笔记核出来的发现。
    n_fix = sum(1 for c in all_corr if c.get("kind") != "check")
    n_find = len(all_corr) - n_fix
    if all_corr:
        bits = []
        if n_fix:
            bits.append(f"修正 {n_fix} 处（课上原话 → 修正值 + 依据）")
        if n_find:
            bits.append(f"三道核另有发现 {n_find} 处（重算不一致，不是口误）")
        L += ["- **" + " · ".join(bits) + "**（逐条记录在详细版里）。", ""]
    if d.materials_line:
        L += [f"- **本课课件核对**：{d.materials_line}"]
        if review.get("materials_used"):
            L += [f"  引用到的材料：{' · '.join(_one(x) for x in review['materials_used'])}"]
        L += [""]
    if d.warns:
        # ⚠️ 生成器的**工作记录**（改了哪条公式、删了几句猜测、口径是不是补的）放页尾折叠：
        #    学生不需要它，但"笔记被机器动过哪里"必须留痕（作者第三轮的要求）。
        L += ["> [!note]- 生成核对记录（内部 · 点开看）", "> "]
        import note_math
        for w in d.warns:
            # 短文本也要过同一个渲染（作者第三轮第 2 条）：md 里包成 `$…$`，
            # HTML 侧走 inline()（KaTeX + Unicode 兜底）。
            L += [f"> - {note_math.auto_tex(_one(str(w)))}"]
        L += [""]
    return "\n".join(L) + "\n"


def chinese_ratio(text: str) -> float:
    """中文占比（验收表：正文中文 ≥60%）。**量具**，不是渲染的一部分。

    ⚠️ 口径：`汉字数 / (汉字数 + 拉丁字母数)` —— 只看"讲的是中文还是英文"，
       标点、markdown 记号、数字、公式都不进分母（旧口径 `非空白字符` 会把
       `- **` 这类排版记号算成"内容"，同一份笔记能差出 20 个百分点）。
    ⚠️ 只数**正文区**：附录那份逐句转录是英文证据层，**必须**排除 ——
       把它算进"中文占比"等于用一个假指标骗自己（第六轮起转录搬去了 HTML，
       这里的分割线仍然留着：详细版/旧产物里那份转录还会从这里路过）。
    """
    body = re.sub(r"```.*?```", "", text or "", flags=re.S)
    # ⚠️ 抓的是**抬头**而不是某一段固定文字：2026-10-17 版式把那段改名成
    #    `## 📜 完整逐句转录（附录 B · …）`，用旧锚点 `## 附录 B` 会**静默**把
    #    345 句英文转录算进"正文"，同一份笔记的中文占比从 71% 掉到 34%
    #    （实测踩到：量具比被测对象先坏）。`tests/test_note_v2.py` 钉着这条。
    body = re.split(r"\n## [^\n]*完整逐句转录", body)[0]
    cjk = sum(1 for c in body if "\u4e00" <= c <= "\u9fff")
    latin = sum(1 for c in body if c.isascii() and c.isalpha())
    total = cjk + latin
    return cjk / total if total else 0.0
