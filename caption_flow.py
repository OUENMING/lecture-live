"""D 布局「屏上该显示哪几行」的唯一定义点：纯函数，不 import AppKit。

## 为什么存在

原来是「转录区 + 面板底部单独的草稿栏」两块：草稿（这一段还在识别、还在译）钉在
最底下，定稿行在转录区里。D 布局把草稿**并进转录区最底下那一行**（"当前卡"）——
草稿先占着这一行，这一段的定稿到了**在同一行原地升级**，屏幕上不多不少还是那一行。

于是"屏上到底哪几行、最后一行是谁、要不要显示"就不再是渲染代码的副作用，而是一条
能从 `Overlay` 的散装状态算出来的判据：`history` 是已冻结的历史行，`live` 是最后那张
可升级的卡。本文件是这条判据的**唯一定义点**，唯一调用点是 `Overlay._render` 的 cap
分支（D 布局落地时接线）。纯函数、不量文字、不碰 AppKit —— 所以能脱离界面单测。

## 四种组合（`history` 之后的那一两行）

    定稿行   草稿行   屏上
    ✓        ✓        items = history + [定稿行]，live = 草稿行
                     （上一段还在流式、老师已讲下一段 —— 10-09 真课 81% 的段都这样）
    ✓        ✗        live = 定稿行（这一段的定稿正在上屏）
    ✗        ✓        live = 草稿行（等定稿；定稿到了原地升级）
    ✗        ✗        live_visible = False（还没说话）

两个都存在时**草稿行才是 live**（它更新）：定稿行退成 items 里的一行、随历史滚走，
live 那一格留在原地等下一段草稿来升级。

`live_kind` 只报最后那张卡的**身份**（"draft" / "final" / "none"），渲染层用它决定
走不走草稿的入场 / 让位动效。

## 草稿桥（`BRIDGE_RATIO`）

`_render_draft` 的老规矩是「定稿一开始流式就藏起草稿」（防同一句在实时行和草稿栏
各出现一次），除非 `draft_over`（屏上草稿已是更新一段）。藏的那一瞬间，这一句的中文
从「草稿译文整句」掉成「定稿译文一个字都没有」——然后一个字一个字长回来。同一句的
中文在眼前先清空再长，比"改字"更狠（CHI 2023 N=123：已显示文字被改与分心正相关）。

桥：这段的定稿中文刚开始流、还没长到草稿译文的 `BRIDGE_RATIO` 时，中文槽**继续显示
草稿译文**（`live_kind` 也报 "draft"）；长过这个比例再换成定稿中文（"final"）。
只桥**中文**：英文槽在流式期本来就有 `seed_en`（ASR 原句）占位，不会空。

为什么 0.6：定稿译文与草稿译文长度相当，60% 大致是"新译文已经比旧译文更像这一句的
正文"的位置。⚠️ 这个数是**按观感定的、没有量具**（改它只动这一段的观感，不动判据结构）。
⚠️ 桥只比**长度**、不比文本：同一段的 `cur_zh` 是从空串开始长起来的，长度就是进度；
去比文本反而会在"定稿译文更长但说的是同一句"时白等。

## 让位节奏（10-17 Soniox 接入后加；`now` 不传 = 不启用，逐字退回上面的老规矩）

Soniox 封口后约 0.5 s 下一句就出字，而译文要 ~1 s 才写完 —— 老规矩「下一段草稿一到，
定稿行就让位上移」会让每句都**译到一半就被顶上去**、回改改到一半就上移（Owen 10-17 实测）。
三条（原型 `buildPlan` / `dCards` 的同一套门槛）：

1. **攒够词才开新行**：下一句草稿 ≥ `MIN_WORDS` 个词才占新行；上一句封口已过 `GAP_S`
   （老师停了一下）时 1 个词就开。
2. **上一句译完才让位**：定稿还在流式时草稿不上屏；流了 `HOLD_MAX_S` 还没完也让位（翻译卡住
   不能一直挡着）。
3. **短尾巴并进上一行**：封口不到 `MIN_WORDS` 词、离上一句定稿 ≤ `GAP_S` 的句子（"Okay?"）
   不单开一行，接在上一行后面（`merge_live`；判据 `is_tail`，落盘不受影响 —— 只改屏上）。
4. **连着讲就并成一段**（3.10.1，Owen 10-19：「教授一口气讲两三句，屏上被切成两三个
   句号，很割裂」）：这句和上一句之间的**音频停顿** ≤ `JOIN_GAP_S`、并完那一行仍装得下
   （行高是固定档位，装不下就照旧开新行，绝不截字）、一行最多 `JOIN_MAX` 句 → 同样走
   `merge_live`。判据 `is_join`。翻译仍一句一句出（延迟不变），落盘不变。

`recheck_at` = 结果下一次可能因为**时间**而变的时刻（宿主到点重画一次；`None` = 不用）。

## 输入就是 `Overlay` 里的现成状态

`cur_en` / `cur_zh` / `seed_en` / `streaming` 与 `_render` cap 分支逐字同义；
`draft` / `draft_zh` / `draft_over` 与 `_render_draft` 的 `active` 逐字同义
（草稿栏原来那个 "▸ " 前缀**不进**本文件：前缀是渲染层的事）。
`history` 是 `Overlay._history` 本身（外部引用，见 `transcript_view.LineLog.rows`）——
本函数只读它，返回一个**新的** list（规则 5）。
"""
from __future__ import annotations

import typing

#: 草稿桥的换手点：定稿中文长到草稿译文的这个比例之前，中文槽继续显示草稿译文。
BRIDGE_RATIO = 0.6
#: 新开一行要的词数（原型 `D_MIN_WORDS`；Owen 10-17 定 3）；也是「短尾巴」的上限（不含）。
MIN_WORDS = 3
#: 离上一句定稿超过这么久 = 老师停过 → 1 个词也开新行；短尾巴只在这个窗口内并（原型 `D_GAP`）。
GAP_S = 3.0
#: 两句之间的音频停顿（Soniox 上一句末词结束 → 这一句首词开始）不超过这么久 = 老师没停，
#: 并成一段。⚠️ 1.0 s 是**先定的**（句间自然停顿多在 1 s 内、刻意停顿更长），还没有真课
#: 的分布 —— 每句的停顿记进 live 日志（`sent_gap` 事件），上完一节课再按分布校准。
JOIN_GAP_S = 1.0
#: 一行最多并几句（再多就算装得下也太长，读起来像一堵墙）。
JOIN_MAX = 3
#: ⭐ 3.11 端点换行（试用分支）：Owen 10-19 第一版「封口后才并」会跳 —— 草稿先单占一行、
#: 封口后再被吸进上一行。现在改成**下一句第一次上屏前就定**（`draft_join`），定了不改：
#: 上一句之后 Soniox 报过句间端点（老师讲完一口气）或最后一行已经写满档位行数 → 另起一行，
#: 否则草稿从第一个词起就接在最后一行后面。离线量（OCW 14.01 32 分钟 569 句，默认端点档）：
#: 每分钟 17.6 行 → 8.5 行，平均一行 2.1 句，99% 的行仍装得下 2 行中文档位。
JOIN_ON = True
#: 定稿流式最多挡草稿这么久（翻译卡住时不能一直挡；实测译完 p50 1.1 s、p90 1.3 s）。
HOLD_MAX_S = 2.5


def n_words(text: str) -> int:
    return len((text or "").split())


def is_tail(en: str, since_last_final: float | None) -> bool:
    """这句是不是该并进上一行的短尾巴（"Okay?" 一类）。`since_last_final=None` = 没有上一行。"""
    return (since_last_final is not None and since_last_final <= GAP_S
            and 0 < n_words(en) < MIN_WORDS)


def is_join(gap_s: float | None, *, fits: bool, n_in_row: int) -> bool:
    """这句要不要并进上一行（连着讲的下一句）。`gap_s=None` = 不知道停顿（不并）。"""
    return (gap_s is not None and 0 <= n_in_row < JOIN_MAX and fits
            and -0.5 <= gap_s <= JOIN_GAP_S)


class CaptionRows(typing.NamedTuple):
    """屏上内容：`items` 是冻结的行，`live_*` 是最后那张可升级的卡。

    `live_kind` ∈ {"none", "draft", "final"} —— 只报身份，不含样式。
    """

    items: list[tuple[str, str]]
    live_en: str
    live_zh: str
    live_visible: bool
    live_kind: str
    recheck_at: float | None = None


def caption_rows(history: list[tuple[str, str]], *, cur_en: str, cur_zh: str,
                 seed_en: str, streaming: bool, draft: str, draft_zh: str,
                 draft_over: bool, now: float | None = None,
                 final_t0: float | None = None, last_final_t: float | None = None,
                 merge_live: bool = False, bridge_zh: str = "",
                 draft_join: bool = False) -> CaptionRows:
    """算这一帧屏上该有什么。见文件头：四种组合 + 草稿桥 + 让位节奏。不碰输入（`history` 只读）。

    `now` / `final_t0`（这句定稿开始流式的时刻）/ `last_final_t`（上一句定稿上屏的时刻）
    同一个时钟；`now=None` 时让位节奏整段不启用。
    """
    if now is None:
        return _rows(history, cur_en=cur_en, cur_zh=cur_zh, seed_en=seed_en,
                     streaming=streaming, draft=draft, draft_zh=draft_zh, draft_over=draft_over)
    recheck = []
    # ⓪ 这句定稿已经开始（`seed_en` 到了）但译文第一个字还没来（~1 s）：行照样占住。
    #    ⚠️ 不占的话 Soniox 封口那一刻这句从草稿里消失、定稿又还没开始流 -> 整行空一秒再回来
    #    （10-17 浮窗逐帧 OCR 抓到）。中文先接着显示这句封口前的草稿译文（`bridge_zh`）。
    if seed_en and not streaming and not cur_zh:
        streaming = True
    has_final = bool(streaming or cur_zh or cur_en)
    busy = has_final                  # 这句定稿还在流（并进上一行了也算 —— 下一句照样等它译完）
    # ③ 短尾巴 / 接着讲：屏上不单开一行，接在上一行后面（流式中就接上 —— 定稿到了不再跳一次）
    if has_final and merge_live and history:
        en = (seed_en or cur_en) if streaming else cur_en
        pe, pz = history[-1]
        # 草稿桥同一条：定稿中文长过草稿译文的 BRIDGE_RATIO 之前，接着显示草稿译文
        # （草稿阶段这一句已经以草稿译文接在这一行里了 —— 不能先清空再长回来）
        zh = cur_zh if (not bridge_zh or len(cur_zh) >= BRIDGE_RATIO * len(bridge_zh)) else bridge_zh
        history = list(history[:-1]) + [(f"{pe} {en}".strip(), f"{pz}{zh}")]
        cur_en = cur_zh = seed_en = ""
        streaming = has_final = False
    # ② 上一句没译完 -> 草稿先不上屏（到 HOLD_MAX_S 为止）
    if busy and (draft_over or not has_final) and (draft or draft_zh):
        t_end = (final_t0 if final_t0 is not None else now) + HOLD_MAX_S
        if now < t_end:
            recheck.append(t_end)
            draft = draft_zh = ""
    # ⓪' 接着讲（3.11）：草稿从第一个词起就接在最后一行后面 —— 不占新行，也就不用攒词
    if draft_join and history and not has_final and (draft or draft_zh):
        pe, pz = history[-1]
        items = list(history[:-1]) + [(f"{pe} {draft}".strip(), f"{pz}{draft_zh}")]
        return CaptionRows(items, "", "", False, "none",
                           min(recheck) if recheck else None)
    # ① 攒够词才开新行（只管「下一句」的草稿：它要占的是**新**行）
    new_row = draft_over or not has_final
    if new_row and (draft or draft_zh) and n_words(draft) < MIN_WORDS:
        gap_ok = (last_final_t is None or now - last_final_t > GAP_S) and n_words(draft) >= 1
        if not gap_ok:
            if last_final_t is not None:
                recheck.append(last_final_t + GAP_S + 1e-3)
            draft = draft_zh = ""
    if has_final and not draft and bridge_zh:
        # 下一句草稿被挡着：草稿桥改用**这句自己**封口前的草稿译文（`draft_zh` 此时可能已是
        # 下一句的了）
        draft_zh, draft_over = bridge_zh, False
    r = _rows(history, cur_en=cur_en, cur_zh=cur_zh, seed_en=seed_en, streaming=streaming,
              draft=draft, draft_zh=draft_zh, draft_over=draft_over)
    return r._replace(recheck_at=min(recheck) if recheck else None)


def _rows(history, *, cur_en, cur_zh, seed_en, streaming, draft, draft_zh,
          draft_over) -> CaptionRows:
    """四种组合 + 草稿桥（改前的 `caption_rows` 本体，一字未改）。"""
    # ① 定稿行：存在性 / 英文取谁，与 `_render` cap 分支逐字同义。
    #    ⚠️ `streaming` 为真时**不要求有字**：那一格是留给刚定稿这一句的
    #    （ASR 原句 / 译文都还没到就先占住），行在、内容可以先是空串。
    en = (seed_en or cur_en) if streaming else cur_en
    final_row = (en, cur_zh)
    has_final = bool(streaming or cur_zh or cur_en)

    # ② 草稿行：与 `_render_draft` 的 `active` 同义 —— 定稿流式期间藏起草稿，
    #    除非屏上草稿已是**更新一段**的（`draft_over`，main 的 DraftView 判）。
    draft_row = (draft, draft_zh)
    has_draft = bool(draft or draft_zh) and (not streaming or draft_over)

    # ③ 组合。两个都有时：定稿行进 items（它不再变），草稿行当 live（还会长）。
    if has_final and has_draft:
        return CaptionRows(list(history) + [final_row], draft, draft_zh, True, "draft")
    if has_final:
        # ④ 草稿桥：草稿行被藏了，但它的译文还在手边 —— 定稿中文长过 60% 之前接着显示
        #    草稿译文，不让这一句的中文先清空再长回来。
        if (draft_zh and not draft_over
                and len(cur_zh) < BRIDGE_RATIO * len(draft_zh)):
            return CaptionRows(list(history), en, draft_zh, True, "draft")
        return CaptionRows(list(history), en, cur_zh, True, "final")
    if has_draft:
        return CaptionRows(list(history), draft, draft_zh, True, "draft")
    return CaptionRows(list(history), "", "", False, "none")
