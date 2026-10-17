"""笔记**版式**（2026-10-17 v2）：中文连贯讲解为主 + 分层自测 + 覆盖度/校验清单。

作者的原话（这一版版式的全部理由）：
> 不要「一行中文一行英文」的逐句对照……正文全中文讲解；只有重点术语首次出现时写成
> 「研究问题（research question）」……不要「一条一条 bullet 堆下去」。要系统性、由浅入深：
> 每个知识点是一段连贯的讲解（直觉 → 定义 → 为什么 → 怎么用 → 例题 → 变式），
> 像一篇有起承转合的教程……标出「这一节要用到上一节的 X」。

⭐ 所以这一版**不重排英文原句**（旧版 `_render_note` 的 `en/detail/zh` 三层被整个换掉）——
   英文只留在三个地方：术语原形、定义/考卷原文的引用、底部那份**逐句转录**（证据层）。

⚠️ 结构里的两条硬约束（旧版测试钉着同一件事，别在重写时丢了）：
   1. **骨架在细节之前**：📑 课堂纲要（课务/章节）紧跟速览、在讲解之前；
   2. **底座在最后**：逐句转录一条不少、原样（**不精修**）折叠在附录 B。

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
    #: 同目录的 HTML 文件名（自包含交互版）。
    html_name: str = ""
    #: 别处已经算好的修正说明（`note_v2.corrections_from_mismatch`）。
    corrections_extra: list = dataclasses.field(default_factory=list)
    #: 头部警告（如「本课未做课件核对」）—— 每行一条，进 header callout。
    warns: list = dataclasses.field(default_factory=list)


# ⚠️ `_POLISH_NOTE` **不在这里** —— 它的唯一定义点在 `obsidian_writer`（`polish:`
#    这个字段就是在那里定的，`test_wrapup` 也钉着 `ow._POLISH_NOTE`）。
#    本模块渲染时去那边取（同 `_render_outline_section` 的做法），不抄第二份。


#: 「修正说明」在正文里的锚点：`修正：` 开头的一行（渲染层自己写的）。
CORRECTION_PREFIX = "修正说明："


def _one(v) -> str:
    """单行化 + 渲染层兜底：`q^3-3q^2+12q` → `$q^{3}-3q^{2}+12q$`。

    ⚠️ 这一层是**兜底**，不是替模型写公式：prompt 仍然要求它自己写 `$…$`
       （2026-10-17 验收实测：整篇 HTML 里 `$` 个数是 **0** —— 正文写着
       `q^3-3q^2+12q` 这种纯文本，KaTeX 没有作用对象）。
       幂等：已经写好的 `$…$` 原样不动（`note_math.auto_tex` 的判据）。
    """
    import note_math
    return note_math.auto_tex(" ".join(str(v).split())) if v else ""


def _tex(v) -> str:
    """文本 → 补上 LaTeX 的文本（`q^3-3q^2+12q` → `$q^{3}-3q^{2}+12q$`）。

    ⚠️ 这是**渲染层兜底**，不是替模型写公式：prompt 仍然要求它自己写 `$…$`
       （2026-10-17 验收实测：整篇 HTML 里 `$` 个数是 **0**，KaTeX 没有任何作用对象）。
       转换是幂等的 —— 已经写好的 `$…$` 原样不动（`note_math.auto_tex` 有判据）。
    """
    import note_math
    return note_math.auto_tex(v)



def _fold(title: str, body: list, *, open_: bool = False) -> list:
    """折叠 callout —— 笔记里所有"先自己想/点开看"都走这一条（格式唯一定义点）。

    ⚠️ `-` 后缀 = 默认折叠（Obsidian 语法）；展开后是正常内容。
    """
    if not body:
        return []
    tag = "success" if title.startswith("答案") else "question"
    mark = "" if open_ else "-"
    out = [f"> [!{tag}]{mark} {title}", ">"]
    for line in body:
        for sub in str(line).splitlines() or [""]:
            out.append(f"> {sub}".rstrip())
    return out


def _steps_md(steps: list, *, reveal: bool) -> list:
    """例题的步骤。`reveal=True` 时**每步**单独折叠（HTML 里对应"点一下出下一步"）。"""
    out: list = []
    for i, st in enumerate(steps, 1):
        t, w = _one(st.get("text")), _one(st.get("why"))
        head = f"**第 {i} 步** {t}" if t else f"**第 {i} 步**"
        if reveal:
            out += _fold(f"第 {i} 步", [f"{t}", (f"为什么：{w}" if w else "")])
            continue
        out.append(f"{i}. {t}")
        if w:
            out.append(f"   - 为什么这一步成立：{w}")
    return out


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
    """附录 A 里"这课的转录精修了没有"那一行。**跟着 `polish:` 字段走**。"""
    if state == "off":
        return "- **逐句转录不再精修**（2026-10-17 起默认不跑）：原样保留，仅作证据层（附录 B）。"
    return (f"- 本课**转录做过精修**（`polish: {state}`，`--polish auto`）：附录 B 里是"
            "精修后的那版，`sessions/` 里的原始记录未动。")


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


def _section_md(sec: dict, idx: int) -> list:
    """一个知识点 -> Markdown。顺序：衔接 → 讲解 → 前置补课 → 例题 → 变式 → 补全 → 误区。"""
    L: list = []
    head = _one(sec.get("heading"))
    ts = _one(sec.get("ts"))
    L += [f"### {idx}. {head}" + (f"　`🎙 {ts}`" if ts else ""), ""]
    link = _one(sec.get("prereq_link"))
    if link:
        L += [f"> 🔗 {link}", ""]
    for para in (sec.get("prose") or []):
        L += [_one(para), ""]
    pre = _one(sec.get("prereq"))
    if pre:
        L += _fold("📚 前置补课（教授没讲、但要懂）", [pre]) + [""]
    ex = sec.get("example") or {}
    if ex.get("prompt") or ex.get("steps"):
        L += [f"#### 例题 · {_one(ex.get('title')) or '教授的例题'}", ""]
        if ex.get("prompt"):
            L += [f"**题目**：{_one(ex['prompt'])}", ""]
        L += _steps_md(ex.get("steps") or [], reveal=False)
        L.append("")
        if ex.get("answer"):
            L += _fold("答案", [_one(ex["answer"])]) + [""]
    for j, v in enumerate(sec.get("variants") or [], 1):
        L += [f"#### 变式 {j}（先自己写，再看）", "", f"**题目**：{_one(v.get('q'))}", ""]
        body = _steps_md(v.get("steps") or [], reveal=False)
        if v.get("a"):
            body += [_one(v["a"])]
        L += _fold("答案", body) + [""]
    fd = sec.get("faded") or {}
    if fd.get("q"):
        L += [f"#### 补全题（把关键步骤填上）", "", f"**题目**：{_one(fd['q'])}", ""]
        if fd.get("blanks"):
            L += ["- 空：⬜ " + "　⬜ ".join(_one(b) for b in fd["blanks"]), ""]
        if fd.get("a"):
            L += _fold("答案", [_one(fd["a"])]) + [""]
    if sec.get("pitfalls"):
        L += ["#### 常见误区", ""] + [f"- {_one(p)}" for p in sec["pitfalls"]] + [""]
    terms = sec.get("terms") or []
    if terms:
        L += ["> 🔑 " + " · ".join(
            f"{_one(t['zh'])}（{_one(t['en'])}）" if t.get("zh") else _one(t["en"])
            for t in terms), ""]
    srcs = sec.get("sources") or []
    if srcs:
        L += [f"> 📎 来源：{' · '.join(_one(s) for s in srcs)}", ""]
    return L


def render(d: NoteData) -> str:
    """`NoteData` -> 笔记 Markdown（streams 全在这一处）。"""
    entries = d.entries
    review = d.review or {}
    ts0 = entries[0]["ts"] if entries else "—"
    ts1 = entries[-1]["ts"] if entries else "—"
    stars = [e for e in entries if e.get("star")]
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

    checks = list(review.get("checks") or []) + list(d.corrections_extra or [])
    corr = list(review.get("corrections") or [])
    n_ok = sum(1 for c in checks if c.get("status") == "ok")
    n_bad = sum(1 for c in checks if c.get("status") == "mismatch")
    n_un = sum(1 for c in checks if c.get("status") == "unchecked")
    cov = review.get("covered") or []
    pts = review.get("lecture_points") or []
    if pts:
        n_cov = sum(1 for c in cov if _one(c.get("section")) and
                    _one(c.get("section")) not in ("缺失", "missing", "-", "无"))
        cov_line = f"覆盖度：教授知识点 {len(pts)} 个 → 笔记覆盖 {n_cov} 个"
        if n_cov < len(pts):
            cov_line += f"（缺 {len(pts) - n_cov}，见 §9）"
    else:
        cov_line = "覆盖度：未生成知识点清单（见 §0 的说明）"

    # ── 头部 ───────────────────────────────────────────────────────────
    L += ["> [!info] 这是什么 / 怎么用", ">"]
    L += ["> 这节课的**中文讲解笔记**：一个知识点一节，从零基础讲到课上那个程度。",
          "> 用法：先读 §0 速览；每节读完做那一节的例题/变式；最后用 §自测 闭卷过一遍。",
          ">"]
    L += [f"> 🕐 {ts0} – {ts1} · 🗣 {len(entries)} 句 · 💡 {len(d.term_rows)} 个术语 · "
          f"❓ {len(d.lost_items)} 处标记"]
    if d.materials_line:
        L += [f"> {d.materials_line}"]
    L += [f"> {cov_line}"]
    if n_ok or n_bad or n_un:
        # ⚠️ 学生看的是一句话，不是生成器的工作日志（作者 2026-10-17 第三轮：
        #    「页头给学生看的是内部诊断」）。内部那几条挪到页尾「生成核对记录」。
        L += [f"> ✅ 数字核对：{n_ok + n_bad} 处已核"
              + (f"（其中 {n_bad} 处按 📖/📚 改过，见附录 A）" if n_bad else "")
              + f" · {n_un} 处待你确认（清单见 §9）"]
    try:
        from obsidian_writer import _POLISH_NOTE
    except Exception:                                          # noqa: BLE001
        _POLISH_NOTE = {}
    _warn = _POLISH_NOTE.get(d.polish_state)
    if _warn:
        L += [f"> {_warn}"]
    L += [">", "> <sub>🎙 课上一手 · 📖 课件材料 · 📚 标准理论；冲突时 📖 > 📚 > 🎙。"
          "逐句转录**不再精修**，原样折叠在附录 B 作证据层。</sub>", ""]

    # ── §0 五分钟速览 ─────────────────────────────────────────────────
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

    # ── 📑 课堂纲要（骨架先于细节）─────────────────────────────────────
    try:
        import obsidian_writer as _ow
        if d.chapter_items:
            _sec = _ow._render_outline_section(d.chapter_items)
            if _sec:
                L += _sec
    except Exception:                                          # noqa: BLE001
        pass

    # ── 讲解（由浅入深）────────────────────────────────────────────────
    # ⚠️ 这一节**总是**出现（哪怕没内容）：它是"骨架之后才是细节"那条结构纪律的
    #    锚点（`tests/test_vault.py` 的位置判据指着它），也是"这次没生成"那句话
    #    该待的地方 —— 少了它，读者会以为讲解藏在别处。
    L += ["## 讲解（由浅入深）", ""]
    if sections:
        for i, s in enumerate(sections, 1):
            L += _section_md(s, i)
    else:
        L += [_missing_line(d), ""]

    # ── 自测 ──────────────────────────────────────────────────────────
    L += ["## 自测（先答再揭晓）", ""]
    if d.qa_items:
        L += ["> [!question] 🙋 我课上问过的问题 · 先自测这些", *d.qa_items, ""]
    if qa:
        L += ["> [!tip] 用法：遮住答案，先在纸上写一遍；展开再对照。"
              "`问题::答案` 那几行可以直接被 Spaced Repetition 插件当卡片。", ""]
        for i, x in enumerate(qa, 1):
            kind = {"calc": "计算", "trap": "易错", "concept": "概念"}.get(
                _one(x.get("kind")), "")
            L += [f"**{i}. {_one(x.get('q'))}**" + (f"　`{kind}`" if kind else ""), ""]
            L += _fold("答案与解析", [_one(x.get("a"))]) + [""]
            L += [f"- {_one(x.get('q')).replace('::', ': :')}::{_one(x.get('a'))}"]
        L += [""]
    else:
        L += ["> [!tip] 用 `问题::答案` 写自测题（装了 Spaced Repetition 插件就能当卡片复习）",
              "", "- ", ""]

    # ── §9 覆盖度与校验清单 ───────────────────────────────────────────
    L += ["## 覆盖度与校验清单", ""]
    if pts and cov:
        L += ["| 教授讲的（🎙） | 笔记覆盖 |", "|---|---|"]
        by_point = {_one(c.get("point")).lower(): _one(c.get("section")) for c in cov}
        for p in pts:
            pt = _one(p.get("point"))
            sec = by_point.get(pt.lower(), "")
            ok = bool(sec) and sec not in ("缺失", "missing", "-")
            tail = f" {sec}" if ok else ""
            mark = "✅" if ok else "⚠ **缺失**"
            L += [f"| {'`' + _one(p.get('ts')) + '` ' if p.get('ts') else ''}{pt} | {mark}{tail} |"]
        L += [""]
    miss = review.get("missing") or []
    if miss:
        L += ["**没覆盖到的（要补）：**", ""]
        L += [f"- `{_one(m.get('ts'))}` {_one(m.get('point'))}"
              + (f" —— {_one(m.get('why'))}" if m.get("why") else "") for m in miss]
        L += [""]
    if checks:
        L += ["**数字 / 公式三道核**（ⓐ 代数自洽 → ⓑ 对照课件 → ⓒ 核不了标未核对）：", "",
              "| 断言 | 怎么核的 | 结果 |", "|---|---|---|"]
        for c in checks:
            st = {"ok": "✅ 一致", "mismatch": "⚠ 已修正", "unchecked": "⬜ 未核对"}.get(
                c.get("status", ""), "⬜ 未核对")
            rec = _one(c.get("recomputed"))
            L += [f"| {_one(c.get('claim'))} | {_one(c.get('how'))} | {st}"
                  + (f"（应为 {rec}）" if c.get("status") == "mismatch" and rec else "") + " |"]
        L += [""]
    elif sections:
        L += ["*（本次没生成数字清单 —— 复习层这一层缺；正文里的数字请自己代入复核）*", ""]

    # ── 术语 ──────────────────────────────────────────────────────────
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

    # ── 复习排程 ──────────────────────────────────────────────────────
    if d.review_dates:
        L += ["## 复习排程（笔记只写日期，不推送）", "",
              "| 第几次 | 日期 | 做什么 |", "|---|---|---|"]
        plans = ["重做例题 + §自测 闭卷一遍（约 20 分钟）",
                 "只看 §0 速览 + §自测 答错的那几条（约 10 分钟）",
                 "合上笔记，把每个知识点用自己的话讲一遍（约 15 分钟）"]
        for i, dt in enumerate(d.review_dates):
            L += [f"| 第 {i + 1} 次 | {dt} | {plans[i] if i < len(plans) else plans[-1]} |"]
        L += ["", "> ⚠️ **感觉顺利 ≠ 学得牢**：重读一遍觉得「都懂」是最常见的错觉（desirable "
              "difficulty）。真正的检验是**合上笔记能不能写出来**。", ""]
        extras = []
        if d.anki_name:
            extras.append(f"这份笔记的自测与术语已导出为 Anki 卡：`{d.anki_name}`"
                          "（导入 Anki → File → Import）")
        if d.html_name:
            extras.append(f"交互版（例题分步展开 / 自测点击揭晓 / 可拖动参数的图）：`{d.html_name}`")
        if extras:
            L += [f"- {'；'.join(extras)}", ""]

    # ── ❓ / ⭐ / 🎯（既有层，原样保留）────────────────────────────────
    L += ["## 🤔 我标的地方（重点 / 没听懂）", ""]
    if d.lost_items:
        for blk in d.lost_items:
            L += blk.splitlines()
    else:
        L += ["*（课上没标；觉得哪句重要、或哪里没跟上，就标一下）*"]
    L += [""]
    if stars:
        L += ["## ⭐ 我标记的重点", ""]
        for e in stars:
            L += [f"- `{e['ts']}` {e['zh'] or e['en'] or e['asr']}"]
            if e["en"] and e["zh"]:
                L += [f"  - EN: {e['en']}"]
        L += [""]
    if d.keypoint_items:
        L += ["## 🎯 这节课最值得记的几句", "",
              "*（按 Jev 给「值不值得抄进复习纸」的概率排序。⚠️ **排序信号，不是考试预测** ——"
              " 实测同一句换个问法能掉 0.16，所以看顺序、别卡分数。）*", ""]
        for p, text, ix in d.keypoint_items:
            tag = f"L{ix[0] + 1}" if len(ix) == 1 else f"L{ix[0] + 1}–{ix[-1] + 1}"
            L += [f"- `{p:.2f}` `{tag}` {text}"]
        L += [""]

    # ── 附录 A：信息纪律 + 修正记录 + 生成核对记录（内部） ──────────────
    L += ["---", "", "## 附录 A · 信息纪律与修正记录", "",
          "- **来源三分**：🎙 课上一手（可回时间戳）/ 📖 官方材料（课件、试卷、更正）/ "
          "📚 标准理论（教科书级知识，用于讲通「为什么」或裁定口误）。",
          "- **冲突裁定序**：📖 官方材料 > 📚 标准理论 > 🎙 转录口误。",
          "- **数字三道核**：ⓐ 代数自洽 → ⓑ 对照课件 → ⓒ 核不了标「未核对」（清单见 §9）。",
          # ⚠️ 这行是**事实陈述，不是口号**：2026-10-17 起逐句精修默认关（转录原样当
          #    证据层），但 `--polish auto` 仍能把它开回来 —— 那时还写"不再精修"
          #    就是**假话**（本仓库对"假话"的容忍度是零：读者会拿它当"这条没被改过"的凭据）。
          _polish_line(d.polish_state), ""]
    all_corr = corr + list(d.corrections_extra or [])
    if all_corr:
        L += ["**修正记录**（课上原话 → 修正值 + 依据）：", ""]
        for c in all_corr:
            fixed = _one(c.get("fixed"))
            if c.get("kind") == "check":
                # ⚠️ 三道核发现的**不一致**与"教授口误"不是一回事：前者只是提醒
                #    （有时重算出来还是同一个值），写成"原话 → 修正值"会让人以为改了正文。
                L += [f"- **⚠ 三道核**：{_one(c.get('spoken'))}"
                      + (f" —— 重算：**{fixed}**" if fixed else "")
                      + (f"（{_one(c.get('basis'))}）" if c.get("basis") else "")]
                continue
            L += [f"- **{_one(c.get('where')) or '—'}**："
                  f"「{_one(c.get('spoken'))}」→ **{fixed}**"
                  + (f"（依据：{_one(c.get('basis'))}）" if c.get("basis") else "")]
        L += [""]
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

    # ── 附录 B：逐句转录（证据层）─────────────────────────────────────
    # ⚠️ 抬头**保留**旧的 `## 📜 完整逐句转录` 前半段（附录 B 只是补上编号）：
    #    它是对外可 grep 的锚（`tests/test_backfill.py` 与作者自己的肌肉记忆都指着它）。
    L += [f"## 📜 完整逐句转录（附录 B · 点击展开 · {len(entries)} 句）", "",
          "> [!note]- 逐句双语 + 原始 ASR（**未做任何删改**）", "> "]
    for e in entries:
        L.append(f"> > [!abstract] {e['ts']}" + (" ⭐" if e.get("star") else ""))
        if e["en"]:
            L.append(f"> > **EN**: {e['en']}")
        if e["zh"]:
            L.append(f"> > **ZH**: {e['zh']}{_bf(e)}")
        if e["asr"]:
            L.append(f"> > **ASR**: {e['asr']}")
        L.append("> ")
    return "\n".join(L) + "\n"


def chinese_ratio(text: str) -> float:
    """中文占比（验收表：正文中文 ≥60%）。**量具**，不是渲染的一部分。

    ⚠️ 口径：`汉字数 / (汉字数 + 拉丁字母数)` —— 只看"讲的是中文还是英文"，
       标点、markdown 记号、数字、公式都不进分母（旧口径 `非空白字符` 会把
       `- **` 这类排版记号算成"内容"，同一份笔记能差出 20 个百分点）。
    ⚠️ 只数**正文区**：附录 B 那份逐句转录是英文证据层，**必须**排除 ——
       把它算进"中文占比"等于用一个假指标骗自己。
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