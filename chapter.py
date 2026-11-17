#!/usr/bin/env python3
"""章节层 —— 把一节**已提取出原子**的课，切成「一章一章的纲要」。

    ⚠️ 形状**照抄 `atom.py`**（纯函数 + 一个旁路写入器），不要另立一套。

## 住哪：`sessions/<同名>.chapters.jsonl`（旁路文件）

⭐ 照 `.atoms.jsonl` / `.lost.jsonl` 的成例：**绝不碰会话 `.md`**。
   会话抬头是**三方共享契约**（`obsidian_writer` 写 / `_parse` 读回 / `cl last` grep），
   而 `_TS` 正则是**行尾锚定**的 —— 多一个字段 `_parse` 就认不出那一条，
   会把它的 EN/ZH/ASR **静默盖到上一条头上**。

## 三种记录（**只追加，不改写旧行**）

```
{"type": "window",   "w", "t", "topic", "topic_zh", "lo", "hi", "n_points"}
{"type": "chapter",  "type", …Chapter 全部字段…}
{"type": "deadline", "type", "t", "quote", "src", "source", "changed"}
```

- `w` = 窗口序号，**从 1 起**
- 同一个 `id` 的**临时版**（`status="interim"`）与**正式版**（`"final"`）写同一个 `id`，
  后写覆盖先写 → `load()` 取「每个 id 最后一条」就天然拿到正式版
- `Chapter.version` = 同一个 `id` 第几次合成（临时版 1、2、3…，正式版是最后一次）

## 不变量（同 `atom.py`）

1. **只增不改** —— 旧行永不重写
2. `src` 必须是**全局句号**且落在本章的 `[lo, hi]` 内（机械闸门保证）
3. 「没有实质内容」是**合法输出**（`sentences` 为空）
4. `close()` 之后**拒绝写入**（新建 `_closed` 标志）——
   ⚠️ 这是本模块与 `AtomWriter` **刻意不同**的一处：`AtomWriter._handle()` 在句柄为
   `None` 时会**静默重开文件**；而「被 `finish()` 放弃的线程」正是本模块引入的新暴露面，
   它迟到回来时文件已经关了，重开 = 写脏数据 + 泄漏句柄。
   （`atom.py` 那半在计划里是**单独一个提交**改的，见 D17。）
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import threading

import atom

#: ⚠️ **沿用 `atom.MODEL` 这一个定义点**，不在这里再写一个字符串。
MODEL = atom.MODEL
MAX_TOKENS = 2000         # ⚠️ 2026-09-30 从 900 抬到 1400（加了 `zh` 之后**一句话两遍**：
                          # 英文 + 中文译文，4 句 × (en≈60 + zh≈150 + terms≈30)
                          # 再加标题和 JSON 壳 ≈ 1100）。
                          # ⚠️ 2026-11-17 再抬到 2000 —— **多了 `para` 这层视图**（R2 §5.1）：
                          #   `sentences` ≈ 1100（同上一行）
                          #   + para：lead+body 共 55–90 词的 en ≈ 120 token、同量的 zh ≈ 110、
                          #     bold/bold_en/src/terms/flag ≈ 60、JSON 壳 ≈ 60 → ≈ 350–450
                          #   → 合计 ≈ 1550。留到 2000（≈ +30%）。
                          #   ⚠️ 截断的表现是「最后一段莫名其妙没了」，不像报错，
                          #   而一次截断就毁掉整章的 `para`（它排在 `sentences` 后面）。
                          # ⚠️ 它只是**上限**：模型写得短就花得少。
TEMPERATURE = 0.0         # 同 atom：要可复现
MAX_SENTENCES = 4
SENT_MAX_CHARS = 400      # 单句长度上限（同 atom 的 200，放宽 —— 合成句比要点长）
#: `zh` 的上限。⚠️ 中文比英文密，同样内容字数少一截 —— 拿 `SENT_MAX_CHARS` 卡它会**卡不住**。
#: ⚠️ 超长 / 缺失 / 非字符串一律置空，**不丢句** —— `en` 才是唯一依据，`zh` 只供阅读。
SENT_ZH_MAX_CHARS = 200
#: `para` 的机械闸门（R2 §5.1 / §5.3）。
#: ⚠️ `body` 上限 **3** = 加上 `lead` 刚好 4 句，与「3 到 4 句」对齐；
#:    多出来的直接丢（不带病渲染），不重试。
MAX_PARA_BODY = 3
#: 每句最多加粗几处（R2 §5.1 第 9 条：2–3 处，过密失效；超过就**截**，不报错）。
MAX_BOLD = 3
#: ⭐ **整段**最多加粗几处（2026-11-17 真课重放：按句限 3 → 一段亮了 9–11 处，关键词等于没标）。
#: R2 §5.2 的口径本来就是「每**段** 2–3 处」；按 lead → body 的顺序先到先得。
MAX_PARA_BOLD = 3
#: 一段的词数上限（R2 §5.1 第 2 条：55–90 词）。
#: ⚠️ 超了**不截断**（截断会毁掉「第一句是结论」的结构），只记 `para_over` 让界面
#:    自己决定折叠；也**不重试**（重试 = 再花一次钱，而多几十个词不是错误）。
MAX_PARA_WORDS = 90
#: ⭐ 短章写短：本章每 **10 句原文**最多写 1 句（向上取整，1–4 句）。
#: 2026-11-17 拿桶里 9 门课重放：提示词里写了「不到 30 句只写 1–2 句」，7 个短章仍有 6 个写满 3 句
#: —— 3 句原文写 3 句总结 = 照抄，不是总结；凑出来的第三句常是旁白（「讲者希望学生…」）。
#: 正常章（100+ 句）不受影响（上限仍是 4）。超了**从尾巴丢依据句**，同 ④'。
SRC_PER_PARA_SENT = 10
CHAPTER_TAIL = ".chapters.jsonl"

#: `flag` 的唯一定义点。不在里面的一律当 `None`（**fail-open**，同 `atom.KINDS` 的取舍：
#: 宁可丢掉一个标记，不可因为一个非法值把整句合成句扔掉）。
FLAGS = (None, "board", "discussion")

WINDOW, CHAPTER, DEADLINE = "window", "chapter", "deadline"

SYS_CHAPTER = """你在听一节课的转录, 手里是**一章**的原料: 这一章的英文原句(带全局句号)、
这一章已经提取出的要点、以及前面几章的**标题**(只给标题, 供你接续)。

把这一章写成**纲要**, 输出严格 JSON:
{"title": "本章标题(不超过 8 个英文词)",
 "title_zh": "中文标题",
 "sentences": [{"en": "一句简短的英文",
                "zh": "这一句的中文译文",
                "terms": [["price elasticity of demand", "需求价格弹性"]],
                "src": [12, 13],
                "flag": null}]}

硬性要求:
- 写 **2 到 4 句**, 按「**定义 → 推导或理由 → 结论**」的顺序。
- **保留公式**。口述出来的公式要规范成 `Qd = 120 − 2P` 这种写法, 不要留着口语说法。
- **只依据给你的内容**, 不引入任何外部知识 —— 你没上过这节课。
- 没有实质内容(这一章全是闲聊、点名、设备调试)时, `sentences` 返回空数组。
- 每条 `src` 必须是给你的**全局句号**里真实存在的; 不许编。
  ⚠️ 它只能落在本章范围内 —— 越界的整句会被丢掉。
- 英文写**简单句、常用词** —— 它是给非英语母语的人一眼扫过的。
- ⭐ 每句都要给 `zh`: **忠实翻译那一句 `en`** 的中文, 不是概括、也不是另写一句。
  ⚠️ `en` 才是**唯一依据**(`src` 指的是英文原句); `zh` 只供阅读, 不算引用来源。
  ⚠️ 不许在 `zh` 里加 `en` 没有的信息。公式照抄, 别改写。
- `terms` 只放关键术语和关键短语, **每句最多 3 对**, 形如 `[["英文", "中文"]]`。
  没把握就留空数组。
- `flag` 只有两种取值:
  · 讲这一句时**依赖板书或图**(如 "as you can see here"、"look at this diagram") → `"board"`
  · 这一句的内容**来自学生发言**(课堂讨论) → `"discussion"`
  · 其余一律 `null`

⭐ 另外再给一版「**一章一段**」的段落视图 `para`(界面右栏显示的就是它):
{"para": {"lead": {"en": "第一句 = 结论",
                  "zh": "这一句的中文",
                  "bold": ["zh 里的关键词"],
                  "bold_en": ["keyword in the English sentence"],
                  "src": [12, 13]},
          "body": [{"en": "…", "zh": "…", "bold": ["…"],
                    "bold_en": ["…"], "src": [14]}],
          "terms": [["price elasticity of demand", "需求价格弹性"]],
          "flag": null}}

`para` 怎么写(和 `sentences` 是**两套视图**, 各写各的, 但讲的是同一章):
1. `lead.en` **第一句就是结论** —— 这一章讲了什么、结论是什么, 一句说完。
   不许铺垫: 不写 `This chapter will…` / `Let's look at…` / `Now we turn to…`。
   结论 = 讲者给出的**论点 / 判断 / 因果 / 定义**, 不是话题、提问或某人的能力。
   ⚠️ 反例(不许当第一句): `A student asked what people misunderstand about the IMF.`
   (提问不是结论 → 写讲者的回答) · `Engineers can explain problems and costs.`
   (能力描述 → 写这一章的判断, 如「技术人才存在但不在需要的地方, 所以要保留一批可派往边缘地区的专家」)。
   ⭐ 选**这一章最重要**的那个论断。本章先给一个定义、再推出关系 / 性质 / 结果时,
   第一句写**推出来的那个**, 定义放进后面的依据句。
   ⚠️ 反例: `Price elasticity is defined as %ΔQ divided by %ΔP.`
   (本章重点是「弹性大于 1 时涨价会让总收入下降」→ 第一句写它) · `AT represents total factor productivity.`
   (本章重点是边际产量递减、规模报酬不变 → 第一句写这个)。
   只有当这一章**本身就是在讲这个定义**时, 定义才能当第一句。
2. 整段 `lead + body` 一共 **3 句为主(最多 4 句)、55–80 个英文词, 硬上限 90**。
   先写完再数词: 超过 80 就**删掉最不具体的那一句**, 不是压缩每一句。
   学生是边听课边扫一眼, 段落越短越会被读完。
   ⭐ **短章写短**: 下面会告诉你本章共几句原文 —— **不到 30 句时 `para` 只写 1–2 句**,
   有几个具体点写几句, 不凑到 3 句。
   ⭐ **不许复述**: 后一句只是把前一句换个说法(没有新的具体项) → 删掉。
   ⚠️ 反例(第二句是复述): `Aggregate capital accumulation is written as an equation for K(t+1).`
   + `That equation for K(t+1) is what we are looking for.`
3. **每一句至少含一个具体项**: 数字、条件、定义、公式、术语或例子。
   写不出具体项的句子删掉。
4. **禁止过程旁白与元话语**: 不写 `The lecturer talks about…` /
   `Then the class moved on to…` / `As mentioned earlier…` / `This is important`。
   ⚠️ 反例(不许写): `The lecture then discussed elasticity and its applications.`
   —— 它一个具体项都没有。反例(不许写): `So the key point here is that it matters.`
5. **禁止泛化收尾**: 不写 `This is important for the exam` /
   `You should remember this one` 这类**没有依据**的判断。
6. **只写本章新增**: 前面几章已经给了标题和结论 —— **与它们重复的句子必须删**。
7. **数字与公式保留原形**(口述公式规范成 `Qd = 120 − 2P`)。
8. 英文用**简单句**, 一句一个从句。`zh` = **忠实翻译同一句 `en`**。
9. **整段一共加粗 2 到 3 处**(不是每句 2–3 处), 只给**最关键的**词 / 数字 / 术语, **不给整句**。
   多数句子不加粗。
   · 只加粗**本章特有的**术语 / 数字 / 公式; 不加粗 `劳动` `资本` `产出` `价格` 这类全课通用的词。
   · `bold` 的每一项必须**逐字出现在这一句的 `zh` 里**
   · `bold_en` 的每一项必须**逐字出现在这一句的 `en` 里**
   ⚠️ 逐字对不上的会被丢掉 —— 所以**照抄原文, 不要改写、不要补标点**。
10. 这一章全是点名 / 设备调试 / 闲聊时, `para` 返回 `null`(**空章合法**, 不要凑)。

`para` 里每句的 `src` 规矩与 `sentences` 完全相同(必须落在本章全局句号内,
越界**整段 `para` 丢掉**)。`sentences` 仍然要给 —— 笔记和回查靠它。"""


# ---------------------------------------------------------------- 课务正则
#: 课务线索。⚠️ **必须按词边界** —— 否则 `exam` 会命中 `example`、
#:    `due` 会命中 `during`（课堂里这两个词的出现频率高得离谱）。
#: ⚠️ 不设 `final\s+exam` 这种冗余分支：`exam` 已经覆盖了它。
#: ⚠️⚠️ **复数要收**（2026-09-30 独立审查抓到）：第一版全是单数词根 + `\b`，
#:    于是 `The exams are next week` / `midterms` / `quizzes` / `assignments`
#:    **一条都不命中** —— 而老师在课上说的十有八九是复数。
#:    按本模块自己写的优先级（漏真 deadline 比多报一条重得多），这个缺口必须补。
DEADLINE_RE = re.compile(
    # ⚠️ `due(?!\s+to\b)`（2026-10-01 审查 F23）：`due to` 是"因为"，不是课务。
    #    实测 200 份会话 / 7,667 行英文里正则共命中 34 行，其中 `due` 3 行、
    #    **3 行全是 `due to`**（占 9%）。词边界管不住这个（due 本身是词）。
    r"\b(due(?!\s+to\b)|deadlines?|submits?|submissions?|hand\s+in|problem\s+sets?|"
    r"homeworks?|assignments?|midterms?|quiz(?:zes)?|exams?)\b", re.I)

#: 改期线索。命中它 = 这条课务**被改过**（界面要标「已改期」）。
#:
#: ⚠️⚠️ **2026-09-30 实测抓到的错**：第一版写的是 `pushed\s+to` ——
#:    而原话是 `We pushed **the deadline** to Wednesday.`，两个词**根本不相邻**
#:    → `changed` 恒为 False，「已改期」这条功能**整个是死的**。
#:    ⚠️ 更阴的是：**当时的冒烟测放它过去了**，因为那条只断言「命中没命中」、
#:       **没断言 `changed` 的值** —— 命中是对的，值错得离谱。
#:    → 修法：只认**动词**，不认动词后面跟什么（反正在 `DEADLINE_RE` 命中的句子里，
#:       "push / postpone / extend / move" 不会有别的意思）。
#:    → 纪律：**判据要指向那个字段**，不是指向"有没有命中"（同 `verify-criteria-must-point-at-the-field`）。
#: ⚠️ 词形要收全：`moved` 之外还有 `move / moves / moving`（`I'm moving the deadline`
#:    实测漏过）；`push` 之外还有 `pushing`。（2026-09-30 独立审查抓到。）
CHANGED_RE = re.compile(
    r"\b(postpon\w*|push(?:ed|es|ing)?|extend\w*|extension|"
    r"mov(?:e|ed|es|ing)|reschedul\w*)\b", re.I)


def deadline_hits(en) -> dict | None:
    """一句英文 → `{"changed": bool}`，没命中返回 `None`。**纯函数、不调模型。**

    ⭐ **两条独立的判据，顺序不能反**：
       ① **有没有课务对象**（`DEADLINE_RE`：due / deadline / problem set / exam …）
       ② **它有没有被改期**（`CHANGED_RE`：postpone / push / extend / move …）
    ⚠️ **只有改期动词、没有课务对象的句子不算** —— `"It's been pushed back a week."`
       返回 `None`，因为**不知道被推迟的是什么**。反过来也一样：
       `"The problem set is due Friday."` 命中但 `changed=False`。
       这是刻意的：界面上「待确认」和「已改期」是两种东西。

    ⚠️ 它跑在**每一句定稿**上（`LiveSummarizer.step`），所以必须便宜到可以忽略 ——
       两个 `re.search`，没有别的。
    ⚠️ 误报是**设计上接受**的：课堂里「讨论考试」也会命中。每条都显示原句、
       都标「待确认」，用户自己判断。**别为了压误报把词表收窄** ——
       漏掉真的 deadline 比多显示一条假的重得多。
    """
    s = str(en or "")
    if not DEADLINE_RE.search(s):
        return None
    return {"changed": bool(CHANGED_RE.search(s))}


# ── 笔记里那一节的第二道闸门（2026-10-17 step 1 止血）────────────────────────
#
# 为什么需要：`deadline_hits` 的误报是**设计上接受**的（上面那段：直播时宁可多显示），
# 但审计发现这些误报**原样进了笔记**并且永远留在那里（`notes/report.md` §1.2 A8）：
# 那份笔记的「Deadlines · 课务」**5 条里 0 条是真的** ——
#   `The deadline is alive.`（ASR 把"person is alive"听碎）/ `I have the final exam of
#   last year.` / `At the final exam, I made the assumption…` …
# 后果不是"多几行"，而是读者**学会整块跳过**：真 deadline 来时会一起错过（原话）。
#
# ⚠️ 分工刻意如此：**直播那一层不动**（`deadline_hits` 的词表一个字不改 ——
#    把误报压下去就会漏真 deadline，那是直播路径的取舍），第二道闸门挂在
#    **笔记渲染**上（`obsidian_writer._render_outline_section`）。同一份数据
#    在课上"宁可多报"、在笔记"宁可少留"，因为笔记是**会留一个学期**的那份。

#: 时间锚点 —— 课务**真的成立**的标志（"什么时候"）。
#: ⚠️ `by\s+the` / 裸 `will` **不算锚点**：实测 `But by the time you get to the final
#:    exam, you have done the processes.` 会被 `by\s+\w+` 放行，而它是句废话。
TIME_ANCHOR_RE = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|"
    r"(?:this|next|last|coming)\s+(?:week|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|month|semester|term|class|lecture|tutorial)|"
    r"week\s*\d+|(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"january|february|march|april|may|june|july|august|september|october|"
    r"november|december|midnight|noon|end\s+of\s+(?:the\s+)?(?:week|month|term)|"
    r"by\s+(?:next|this|tomorrow|tonight|the\s+\d|end\s+of|\d)|"
    r"on\s+(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?|"
    r"\d{1,2}\s*(?:am|pm)|\d{1,2}[:.]\d{2}|"
    r"\d+\s+(?:days?|hours?|weeks?)\b|"
    r"reading\s+week|study\s+week|"
    r"周[一二三四五六日末]|星期[一二三四五六日]|下周|本周|这周|今天|明天|之前|截止)\b",
    re.I)

#: 提交/改期动作 —— 与时间锚点并列的第二种"这条课务是真的"的证据
#: （`Hand in your essay tomorrow` 有时间锚点；`Submit it on Brightspace` 靠这个）。
ACTION_RE = re.compile(
    r"\b(submit\w*|hand(?:ed|s|ing)?\s+in|upload\w*|post(?:ed|ing)?\s+(?:it|them|your)|"
    r"extended?|extension|postpon\w*|reschedul\w*|"
    r"cancell?ed|no\s+(?:class|lecture|tutorial|lab)|"
    r"brightspace|canvas|moodle|blackboard|"
    r"moved?\s+to|starts?\s+(?:on|at)|finish(?:es)?\s+(?:on|by))\b", re.I)

#: 过去/非课务的标记 —— 命中它**且没有时间锚点**时丢掉。
#: ⚠️ 只挡 `last year/week/time`、`already`、`used to` 这类**明确回指过去**的词：
#:    课堂上"讲去年的卷子"很常见，而它**不是**一条 deadline（实测 2/5 条误报都是它）。
PAST_RE = re.compile(
    r"\b(last\s+(?:year|week|semester|term|time)|yesterday|used\s+to|"
    r"already\s+(?:passed|happened|done)|has\s+passed)\b", re.I)


def deadline_worthy(quote: str, *, changed: bool = False) -> bool:
    """**笔记**里这条课务值不值得留。纯函数，只吃原句。

    ⭐ 判据（缺一不可）：
      ① 有时间锚点 **或** 提交/改期动作（`TIME_ANCHOR_RE` / `ACTION_RE`）；
      ② 不是"只回指过去"的句子（`PAST_RE` 命中时，只有**同时**有时间锚点才留）。

    ⚠️ `changed=True`（已改期）**直接放行**：那是 `CHANGED_RE` 亲手判的强信号，
       压掉它等于把「已改期」这个功能在笔记里删掉。
    ⚠️ 少留是有代价的（真 deadline 被挡掉）—— 所以判据**只挡明确讲不通的句子**，
       宁可放过"讨论考试"那种模糊句（它至少提到了未来）。
       📏 实测（vault 全部笔记里 14 条进过笔记的课务条目）：**挡 10 / 留 4** ——
       挡掉的 10 条里含审计点名的全部 5 条（`notes/report.md` §1.2 A8），
       留下的 4 条全是真课务（Brightspace 上的 assignment / 本周三停课 /
       本周出勤 / 下周分组）。判据见 `tests/test_note_format.py`。
    """
    s = str(quote or "")
    if changed:
        return True
    if not s.strip():
        return False
    has_anchor = bool(TIME_ANCHOR_RE.search(s))
    if PAST_RE.search(s) and not has_anchor:
        return False
    return has_anchor or bool(ACTION_RE.search(s))


# ---------------------------------------------------------------- 数据类
@dataclasses.dataclass(frozen=True)
class Chapter:
    """一章纲要。临时版与正式版**同一个 `id`**，靠 `status` 区分。"""
    id: int
    status: str                  # "interim" | "final"
    title: str                   # 英文标题
    title_zh: str
    t0: str                      # "HH:MM:SS"
    t1: str
    lo: int                      # 全局句号区间，**闭区间**，1-based
    hi: int
    sentences: list = dataclasses.field(default_factory=list)
    #: ⭐ 2026-11-17：「一章一段」的段落视图（`chapter.parse_para` 产出）。
    #: ⚠️ **追加字段，默认 `None`** —— 旧 `.chapters.jsonl`（没有这个键）读回后就是 `None`，
    #:    所有读回方都靠 `c.get("para")` 读，不构造 `Chapter`，所以不崩。
    #:    界面拿不到 `para` 时退回 `sentences`（fail-open；不因为少一段就不显示这一章）。
    para: dict | None = None
    version: int = 1

    def as_json(self) -> dict:
        return {"type": CHAPTER,
                "id": self.id, "status": self.status,
                "title": self.title, "title_zh": self.title_zh,
                "t0": self.t0, "t1": self.t1,
                "lo": self.lo, "hi": self.hi,
                "sentences": [dict(s) for s in self.sentences],
                "para": dict(self.para) if self.para else None,
                "version": self.version}


# ---------------------------------------------------------------- 纯函数
def build_prompt(sentences, atoms, prior_titles, prior_leads=None) -> str:
    """本章的原料 → 给模型的 block。**纯函数。**

    `sentences` = `[(全局句号, 英文), …]`。
    `prior_titles` = 前面几章的**英文标题**（老参数，照旧可用）。
    `prior_leads` = 与 `prior_titles` **按下标对齐**的各章结论句（`para.lead.zh`）。
    `None` / 短了 / 那一项是空串 → 那一章**只给标题**（不编、不留空行）。

    ⚠️ 光给标题拦不住重复（R2 §5.1 第 6 条）：一章的原料是 3–10 分钟窗口，**相邻窗
       天然重叠**，于是模型会**换个说法把上一章写过的结论再写一遍** —— 它看标题
       认不出「这句写过了」。给一行结论 + 一句明令才拦得住。
    ⚠️ 句号是**全局的**（1-based，与落盘后的 `Atom.src` **同域**）——
       绝不许在这里重新编号，否则模型回的 `src` 和我们存的整整差一节。
    """
    leads = list(prior_leads or ())
    parts: list = []
    if prior_titles:
        parts.append("前面几章讲的是（标题 + 各一行结论；"
                     "**与它们重复的句子必须删** —— 只写本章新增的内容）：")
        for i, t in enumerate(prior_titles):
            lead = str((leads[i] if i < len(leads) else "") or "").strip()
            parts.append(f"- {t}｜结论：{lead}" if lead else f"- {t}")
        parts.append("")
    # ⭐ 句数直接告诉模型 —— 「短章写短」那条规则要靠它判断（模型自己数长列表不可靠）
    parts.append(f"这一章的英文原句（共 {len(sentences or ())} 句；`全局句号. 句子`）：")
    parts.extend(f"{gid}. {en}" for gid, en in (sentences or ()))
    rows = []
    for a in (atoms or ()):
        text = str(getattr(a, "text", "") or "")
        if text:
            rows.append(f"- [{getattr(a, 'kind', '')}] {text}")
    if rows:
        parts.append("")
        parts.append("这一章已经提取出的要点（供你判断重点，**不要照抄**）：")
        parts.extend(rows)
    return "\n".join(parts)


def parse_reply(obj, lo: int, hi: int) -> list:
    """模型回的 dict → `[{"en","zh","src","terms","flag"}, …]`。**这里是全部的机械闸门。**

    - `src` 为空、或**越出 `[lo, hi]`** → **整句丢掉，不修剪**
    - `flag` 不在 `FLAGS` 里 → 一律当 `None`
    - `terms` 走 `atom.terms_of`（**同一条校验，不是抄一份**）
    - ⭐ `zh` 不合法（缺 / 非字符串 / 超长）→ **置空串，句子照留** ——
      `en` 才是唯一依据，`zh` 只是给人读的；为一个阅读字段丢内容不划算
    - 最多 `MAX_SENTENCES` 句

    ⚠️ 丢掉而不是修剪：`src` 越界意味着这句**引用不实**，
       修剪成"本章最近的那句"是**替模型编引用**（`atom.parse_reply` 逐字的同一条纪律）。

    ⚠️ 段落视图 `para` 的闸门在**紧跟着的** `parse_para` 里（两半同一层，共用下面的常量）。
       `live_summary` 调 `parse_chapter` 一起过 —— 只调其中一个就等于漏一半闸门。
    """
    if not isinstance(obj, dict):
        return []
    raw_sents = obj.get("sentences")
    if not isinstance(raw_sents, (list, tuple)):
        return []                                        # ⚠️ 形状不对 -> 空表，**不抛**
    out: list = []
    for s in raw_sents:
        if not isinstance(s, dict):
            continue
        en = str(s.get("en") or "").strip().replace("\n", " ")
        if not en or len(en) > SENT_MAX_CHARS:
            continue
        # ⚠️⚠️ **先判形状再迭代**。`(s.get("src") or [])` 对 `"src": 12` 这种
        #    非容器**会抛 `TypeError`**，而这个函数自称是「**全部的机械闸门**」——
        #    **闸门自己炸掉比放过一条脏数据糟得多**（异常会冒到 worker 线程，
        #    打死总结器，之后整节课不再出章节）。同 `atom.traceable` 那条纪律。
        raw_src = s.get("src")
        if not isinstance(raw_src, (list, tuple)):
            continue                                     # 形状不对 = 整句丢
        src = [x for x in raw_src if isinstance(x, int)]
        if not src or any(x < lo or x > hi for x in src):
            continue                                     # ⚠️ 整句丢，不修剪
        flag = s.get("flag")
        if flag not in FLAGS:
            flag = None                                  # fail-open
        zh = str(s.get("zh") or "").strip().replace("\n", " ")
        if len(zh) > SENT_ZH_MAX_CHARS:
            zh = ""                                      # ⚠️ 置空，**不丢句**
        out.append({"en": en, "zh": zh, "src": sorted(set(src)),
                    "terms": atom.terms_of(s), "flag": flag})
        if len(out) >= MAX_SENTENCES:
            break
    return out


def _bold_of(raw, text: str) -> list:
    """`bold` / `bold_en` 的机械闸门：**每一项必须逐字出现在 `text` 里**。

    - 非字符串 / 空串 / **正文里根本没有** → 丢这一项
    - 去重（同一句里重复加粗没意义），最多 `MAX_BOLD` 项

    ⚠️ 丢**单项**、不丢整句：加粗只是阅读辅助，为一个对不上的高亮把整段扔掉不划算
       （同「`zh` 超长置空、不丢句」的取舍）。
    ⚠️ 但**必须逐字对**：`bold` 指的是「这句里哪几个词」，对不上就是模型改写过了，
       猜一个位置等于**替模型编高亮**（同 `src` 越界不修剪那条纪律）。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out: list = []
    for x in raw:
        if not isinstance(x, str):
            continue
        x = x.strip()
        if not x or x not in text or x in out:
            continue
        out.append(x)
        if len(out) >= MAX_BOLD:
            break
    return out


def _para_sentence(raw, lo: int, hi: int) -> tuple:
    """`para` 里的**一句**（`lead` 或 `body[i]`）→ `(句 dict | None, src 合法吗)`。

    两个返回值是给调用方分开用的：
    · `src` 为空 / 形状不对 / **越出 `[lo, hi]`** → `(None, False)` = **引用不实**，
      整段不可用（R2 §5.3：整段丢）
    · `en` 空 / 过长 / 不是 dict → `(None, True)` = 只是**这一句**没用
      （`lead` 这样也是整段丢，因为「第一句是结论」是这段的定义；`body` 只丢这一句）
    ⚠️ 两者同时坏时算 `src` 坏（`False`）—— 保守方向：宁可少一段，不可留一段假引用。
    其余规矩与 `parse_reply` 逐条相同：`zh` 超长 → **置空、不丢句**。
    """
    if not isinstance(raw, dict):
        return None, True
    # ⚠️ **先判形状再迭代**（同 `parse_reply`：`(raw.get("src") or [])` 对
    #    `"src": 12` 会抛 `TypeError`，而闸门自己炸掉比放过一条脏数据糟得多）。
    raw_src = raw.get("src")
    src = [x for x in raw_src if isinstance(x, int)] if isinstance(raw_src, (list, tuple)) else []
    if not src or any(x < lo or x > hi for x in src):
        return None, False                               # ⚠️ 引用不实
    en = str(raw.get("en") or "").strip().replace("\n", " ")
    if not en or len(en) > SENT_MAX_CHARS:
        return None, True
    zh = str(raw.get("zh") or "").strip().replace("\n", " ")
    if len(zh) > SENT_ZH_MAX_CHARS:
        zh = ""                                          # ⚠️ 置空，**不丢句**
    return {"en": en, "zh": zh,
            #: ⚠️ `bold` 对的是 **`zh`**（界面中文为大字），`bold_en` 对的是 `en`。
            "bold": _bold_of(raw.get("bold"), zh),
            "bold_en": _bold_of(raw.get("bold_en"), en),
            "src": sorted(set(src))}, True


def _cap_para_bold(sents: list) -> None:
    """整段的 `bold` / `bold_en` 各自只留前 `MAX_PARA_BOLD` 项（按句序，结论句先占）。原地改。"""
    for key in ("bold", "bold_en"):
        left = MAX_PARA_BOLD
        for s in sents:
            got = list(s.get(key) or [])[:max(0, left)]
            s[key] = got
            left -= len(got)


def parse_para(obj, lo: int, hi: int) -> dict | None:
    """模型回的 dict → `para` 段落视图，**不可用就返回 `None`**（fail-open）。

    返回：`{"lead": {en,zh,bold,bold_en,src},
            "body": [同结构…], "words": int, "terms": [...],
            "flag": None, "para_over": bool}`

    ⭐ 判据（每条都是**结构闸门**，不靠模型自觉）：
      ① `para` 缺失 / 不是 dict / `lead` 不合法（含 `lead.en` 空）→ **整个 `para=None`**
         → 界面退回旧 `sentences` 渲染（**旧回包照旧出章**）。
      ② `lead` 与 `body[*]` 的 `src` 必须 **⊆ `[lo, hi]`** —— 有一句越界
         就是**整个 `para` 丢**（不是丢那一句）：一段话是**一整块论点**，
         抽掉其中一句它就变成另外一句话了（与句子层「越界整句丢」不同，
         因为那里每句本身是独立可读的）。
      ③ `bold` / `bold_en` 每项**逐字出现在对应的 `zh` / `en` 里**，否则丢该项；
         每句最多 `MAX_BOLD` 项（R2 §5.3）。
      ④ `words` **由代码按 `en` 重算**，不信模型自报（同 `atom.traceable`：
         数字必须是我算的）；`> MAX_PARA_WORDS` **不截断**，只置 `para_over=True`。
      ⑤ `body` 里单句不合法（无 en / src 越界）→ 丢这一句。
      ⑥ `terms` 走 `atom.terms_of`（同一条校验，不抄一份）；`flag` 不在 `FLAGS` → `None`。

    ⚠️ 本函数与 `parse_reply` 是**同一层**的两半（都是模型输出 → 可落盘数据），
       所以它们必须相邻、共用 `SENT_MAX_CHARS` / `SENT_ZH_MAX_CHARS` / `FLAGS`。
       `live_summary` 只调 `parse_chapter`（两半一起过）—— 少一个调用点就少一处漏闸门。
    """
    if not isinstance(obj, dict):
        return None
    raw = obj.get("para")
    if not isinstance(raw, dict):
        return None                                      # ①旧回包 / 空章
    lead, _src_ok = _para_sentence(raw.get("lead"), lo, hi)
    if lead is None:
        return None                                      # ①「第一句就是结论」缺了 / 引用不实 → 整段丢
    raw_body = raw.get("body")
    if not isinstance(raw_body, (list, tuple)):
        raw_body = ()                                    # ⚠️ 先判形状（见 `parse_reply`）
    body: list = []
    for item in raw_body:
        got, src_ok = _para_sentence(item, lo, hi)
        if not src_ok:
            return None                                  # ②有一句引用不实 → **整段丢**
        if got is None:
            continue                                     # ⑤单句无用（无 en）→ 丢这一句
        body.append(got)
        if len(body) >= MAX_PARA_BODY:
            break
    # ⑦ 短章写短：句数不超过 ceil(原文句数 / SRC_PER_PARA_SENT)（结论句永远在）
    budget = max(1, -(-(hi - lo + 1) // SRC_PER_PARA_SENT))
    del body[max(0, budget - 1):]
    words = sum(len(s["en"].split()) for s in [lead, *body])   # ④我算的，不是模型说的
    # ④' 超词数 → **从尾巴整句丢依据句**，至少留 1 句依据（2026-11-17 真课重放：提示词写了
    #    「超了删句」，6 段里仍有 2 段 94–102 词）。丢的是**最后的整句**，结论句永远在 ——
    #    不破坏「第一句是结论」（那是不截断的唯一理由），也不半句截断。还超就标 para_over。
    while words > MAX_PARA_WORDS and len(body) > 1:
        words -= len(body.pop()["en"].split())
    _cap_para_bold([lead, *body])                        # ③' 整段 ≤ MAX_PARA_BOLD（结论句优先）
    flag = raw.get("flag")
    if flag not in FLAGS:
        flag = None                                      # fail-open
    return {"lead": lead, "body": body, "words": words,
            "terms": atom.terms_of(raw), "flag": flag,
            #: ④超词数：先丢尾句（④'），仍超才标记；不半句截断、不重试（省钱）。
            "para_over": bool(words > MAX_PARA_WORDS)}


def parse_chapter(obj, lo: int, hi: int) -> dict:
    """模型回的 dict → `{"sentences": [...], "para": {...} | None}`。**唯一的调用入口。**

    ⚠️ 两半各自 fail-open：`para` 坏了不影响 `sentences`（笔记 / 回查 / 面板靠它），
       `sentences` 坏了也不影响 `para`（界面右栏靠它）。
    """
    return {"sentences": parse_reply(obj, lo, hi),
            "para": parse_para(obj, lo, hi)}


def title_of(obj) -> tuple:
    """`(title, title_zh)` —— 两个都截断。**与 `atom.topic_of` 同一套读法。**"""
    return (atom._label_of(obj, "title", atom.TOPIC_MAX),
            atom._label_of(obj, "title_zh", atom.TOPIC_ZH_MAX))


def merge_deadlines(items) -> list:
    """课务条目按 `src` **有交集**合并。**纯函数。**

    ⚠️ 合并是必须的：正则路径（句子一定稿就命中）与模型路径（原子里的 `课务`）
       **必然会撞上同一条** —— 前者看的是原句，后者看的是提炼后的要点，
       它们指的是同一件事。不合并的话用户会看到同一条 deadline 出现两次。
    ⚠️ `source` 两个都记（`"model+regex"`）而不是后写覆盖：那是**来源证据**，
       丢掉它就再也说不清这条是"哨兵抓的"还是"模型读出来的"。
    """
    out: list = []
    for d in (items or ()):
        if not isinstance(d, dict):
            continue
        src = set(d.get("src") or ())
        hit = next((o for o in out if src & set(o.get("src") or ())), None)
        if hit is None:
            out.append(dict(d))
            continue
        hit["src"] = sorted(set(hit.get("src") or ()) | src)
        both = {str(hit.get("source") or ""), str(d.get("source") or "")}
        hit["source"] = "+".join(sorted(x for x in both if x))
        hit["changed"] = bool(hit.get("changed") or d.get("changed"))
    return out


def chapter_path_for(session_path):
    """会话文件 → 章节旁路文件路径。**参数为 None 返回 None。**

    ⚠️ 这是那条路径的**唯一定义点** —— `ChapterWriter` 与 `obsidian_writer`
       都要它（后者下课时要 `load()` 它来写笔记）。各写一遍迟早分叉，
       而分叉的表现是「课上写的和课后读的不是同一个文件」，**静默的**。
    """
    if not session_path:
        return None
    return pathlib.Path(str(pathlib.Path(session_path).with_suffix("")) + CHAPTER_TAIL)


# ---------------------------------------------------------------- 旁路写入器
class ChapterWriter:
    """写 `sessions/<同名>.chapters.jsonl`。**每次写完就 flush。**

    ⚠️ 收的是**路径**（不是会话路径）—— 推导在 `chapter_path_for` 那一处，
       别让两个函数各推一遍。没有路径 = 不写（不报错）。

    ⚠️ 纪律照 `atom.AtomWriter`：
       · `flush()` **必须**（Python 的 8KB 缓冲，同 `testmode.py` 那个坑）
       · `fsync` **刻意不做** —— 会话 `.md` 自己也只到页缓存
       · 文件**不删** —— 它是这份笔记的证据
       · `close()` **幂等**
    ⚠️⚠️ **但有一处刻意不同**：`close()` 之后 `append()` 是 **no-op**（见模块头第 4 条）。
    """

    def __init__(self, path=None):
        self._path = pathlib.Path(path) if path else None
        self._h = None
        self._n = 0
        self._closed = False
        #: ⚠️ 只保护「`_closed` + `_h`」这一对状态。见 `append()` / `close()` 的说明。
        self._lock = threading.Lock()

    @property
    def path(self):
        return self._path

    def _handle(self):
        # ⚠️ **这里刻意不判 `_closed`** —— 那个判在 `append()` 里，一处就够。
        #    第一版两处都判，变异验证时发现删掉这一处**判据照样全绿**
        #    （因为 `_handle` 只被 `append` 调，根本不可达）→ 那就是死代码。
        if self._h is None and self._path is not None:
            self._h = self._path.open("a", encoding="utf-8")
        return self._h

    def append(self, records) -> int:
        """追加一批（dict 或 `Chapter`）。返回**写成了几条**。

        ⚠️ 写失败**不是致命错**（同 `AtomWriter` 的取舍）：少几行章节记录
           比把整条流水线带崩轻得多。但**关了之后的 no-op 不是失败**，
           所以它**不出声**（迟到的线程回来是预期内的，不该刷屏）。
        """
        recs = []
        for r in (records or ()):
            recs.append(r.as_json() if isinstance(r, Chapter) else r)
        if not recs:
            return 0
        # ⚠️⚠️ **判 + 写必须在同一把锁里**（2026-10-12 全仓审查，核实过）：
        #    原来是 check-then-act —— `append()` 判完 `_closed` 之后、真去 `_handle()` 之前，
        #    收尾线程完全可能已经跑完 `close()`（置 `_closed`、关掉旧句柄、`_h = None`），
        #    于是 `_handle()` **把文件重新打开**又写进一条 —— 既违反本模块自己写明的
        #    「关了就再也不开」，又漏一个**没人关**的句柄。
        #    ⚠️ 这条路是真的：类 docstring 明确欢迎「被 `finish()` 放弃的迟到线程」回来。
        #    ⚠️ `_handle()` 里那条「刻意不判 `_closed`」的注释仍然成立（那是对的，
        #       判一次就够）—— 缺的从来不是第二次判，是**原子性**。
        with self._lock:
            if self._closed:
                return 0                                 # ⭐ 静默丢弃（见 docstring）
            return self._append_locked(recs)

    def _append_locked(self, recs) -> int:
        """真的写。⚠️ 调用方**必须**已经持有 `self._lock`（与 `close()` 互斥）。"""
        try:
            # ⚠️ `_handle()` 必须在 `try` **里面** —— 它是唯一做 I/O 的地方。
            #    `atom.py` 那条注释（2026-09-29）逐字适用：放在外面的话，
            #    磁盘只读 / 目录被删时 `OSError` 直接向上抛，与本函数「不带崩流水线」自相矛盾。
            h = self._handle()
            if h is None:
                return 0
            for r in recs:
                h.write(json.dumps(r, ensure_ascii=False) + "\n")
            h.flush()
            self._n += len(recs)
            return len(recs)
        except Exception as e:                            # noqa: BLE001
            print(f"⚠ 章节写入失败({str(e)[:60]}); 课堂不受影响")
            return 0

    def close(self) -> None:
        """幂等。文件**不删**。⭐ 关了就再也不开（`_closed` 是单向的）。

        ⚠️ **持同一把锁** —— 与 `append()` 互斥。两件事一起才成立：
           ① 在途的那次写不会撞上被关掉的句柄；
           ② 「关了就再也不开」不再是个只在单线程下成立的承诺。
        """
        with self._lock:
            self._closed = True
            if self._h is not None:
                try:
                    self._h.close()
                except Exception:                         # noqa: BLE001
                    pass
                self._h = None

    @property
    def count(self) -> int:
        return self._n


# ---------------------------------------------------------------- 读回
def load(path) -> dict:
    """读回一个 `.chapters.jsonl` → `{"windows": [...], "chapters": [...], "deadlines": [...]}`。

    - **窗口**：全部（它们是"那一窗说了什么"的流水，不覆盖）
    - **章节**：每个 `id` **最后一条** —— 后写覆盖先写，所以正式版自然替换临时版
    - **课务**：按 `src` 有交集合并（`merge_deadlines`）

    ⚠️ 坏行跳过 —— 它是**系统边界**（可能是上个进程写的、可能写到一半被杀）。
       同 `atom.load` 的宽容度：一行坏掉不该让整份纲要消失。
    ⚠️ 文件不存在返回空的三组，**不抛** —— 旧会话 / 纯转录档 / 开关关着时都是这种情况。
    """
    windows: list = []
    last: dict = {}
    order: list = []
    deadlines: list = []
    # ⚠️⚠️ **`path` 为 `None` 是预期输入，不是理论边界**：`chapter_path_for()` 在
    #    会话路径为空时正是返回 `None`（开关关着 / 纯转录档 / 旧会话都走这条），
    #    而 `obsidian_writer` 下课时就是拿它的返回值直接来 `load()`。
    #    少了这一行，`pathlib.Path(None)` 抛的是 **`TypeError`**，
    #    而下面只吞 `OSError` → 从「不抛」的契约里逃出去。
    #    （2026-09-30 `ocr` 抓到的。我自己的判据只试了「文件不存在」，没试 `None`。）
    # ⚠️ **不要图省事把 `TypeError` 也塞进下面那个 `except`** —— 第一版就是那么写的，
    #    变异验证一测：**删掉这一行判据照样全绿**（因为宽吞把它兜住了）→ 那这行就是死代码。
    #    而且宽吞 `TypeError` 会把「真出了别的类型错」伪装成「文件不存在」。
    #    → 显式守门 + 窄 `except`，两者**各管一段**。
    if not path:
        return {"windows": [], "chapters": [], "deadlines": []}
    try:
        txt = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"windows": [], "chapters": [], "deadlines": []}
    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:                                 # noqa: BLE001
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("type")
        if kind == WINDOW:
            windows.append(obj)
        elif kind == CHAPTER:
            cid = obj.get("id")
            # ⚠️⚠️ `id` 必须是**可哈希**的。`{"id": [1]}` 会让 `cid not in last`
            #    抛 `TypeError: unhashable type: 'list'` —— 从「坏行跳过、不抛」
            #    的契约里逃出去，而这是**课后的笔记路径**（`obsidian_writer` 要调）。
            #    （2026-09-30 独立审查抓到。）
            if not isinstance(cid, (int, str)):
                continue                                 # 坏行跳过
            if cid not in last:
                order.append(cid)
            last[cid] = obj                              # 后写覆盖先写
        elif kind == DEADLINE:
            # ⚠️ 同理：`src` 不是列表时 `merge_deadlines` 里的 `set(...)` 会抛。
            if not isinstance(obj.get("src"), (list, tuple)):
                continue
            deadlines.append(obj)
    return {"windows": windows,
            "chapters": [last[c] for c in order],
            "deadlines": merge_deadlines(deadlines)}
