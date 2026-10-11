# R1 实施细则：翻译空窗（3.8.11）+ ❓ 轻点拨（3.8.12）

给**执行者**（Sonnet，medium effort）看的逐文件改法。背景与数字在 `docs/RESEARCH-cloud-asr.md` §10.3、§13，总计划在 `docs/PLAN-cloud-final-asr.md` §2。
行号是 2026-10-11 的快照，**以函数名为准**，动手前用 grep 重新定位。

## 0. 执行规矩（每条都来自本仓库 CLAUDE.md / 事故史）

1. **开工前**加载 skill：`verification-before-completion`；出现失败时加载 `diagnosing-bugs`；全部改完后跑 `simplify`。
2. **测试先红后绿**：每个新用例先在**未改的代码**上跑一次确认会红，再实现。贴出红、绿两次的输出。
3. 任何会真跑会话的命令都带 `CLASSLIVE_UPLOAD=0`（`run_isolated.py --test-mode` 曾真上传过）。
4. `sessions/` **只读**，不删任何文件，不用通配符删东西。
5. **不 commit、不 push、不改 VERSION / CHANGELOG** —— 做完交回，由 Owen 审。
6. 新增 `streamq` tag 必须在 `main.drain()` 加同名分支（唯一分发点，漏了数据会被丢）。
7. 运行环境用 `ClassLive.app/Contents/MacOS/python`，装包用 `uv pip install --python …`（这个 python 没有 pip）。
8. 不确定的地方停下来问，不要猜。尤其是任何会**改变译文措辞**的地方（第 4 项默认关）。

## 1. 改动总览

| # | 改什么 | 文件 | 量 | 默认 |
|---|---|---|---|---|
| A | 草稿译文触发：新增 `novel` 策略 | `draft_policy.py`、`main.py` | ~40 | **开** |
| B | 草稿送翻前剥掉结尾悬挂词 | `main.py` | ~15 | **开** |
| C | 草稿译文独立线程 + 过期丢弃 | `main.py`、`main.EngineRouter` | ~60 | **开** |
| D | 草稿翻译带领域/术语/上一句 | `cloud_translator.py`、`main.EngineRouter` | ~40 | **开**（2026-10-11 Owen 看过对比后定；`CLASSLIVE_DRAFT_CONTEXT=0` 关） |
| E | carry 只看最后一句 | `main.py` | ~30 | **开** |
| F | 回放量具 | `scripts/draft_gap_replay.py`（新） | ~120 | — |
| G | ❓ 课中一句话点拨 + 课后讲解卡（3.8.12，单独一轮） | `overlay.py`、`main.py`、`cloud_translator.py`、`obsidian_writer.py`、`polish.py` | ~200 | 开 |

**执行顺序（Owen 2026-10-11 定）**：
1. **第一步**：A–F（**不动 `vad.py`**，这样回放对比在同一套切段下，数据可比）。全部验收通过。
2. **第二步**：I（硬切回退低谷，带 §I 的三条守卫），单独做、单独验收（单测 + `scripts/test_pipeline.py` + `probe_testmode_report.py`）。
3. **第三步**：G（3.8.12），等 Owen 看过 D 的译文对比与 5 分钟回放、确认满意后才开工。

---

## A. `novel` 触发策略

**问题**：`should_translate` 的 `legacy` 规则要求「比上次送翻时**多 ≥6 个词**」（`n_words - last_len >= 6`）。Parakeet 每秒重转最近 10 s，词数忽多忽少且有上限，送过一次高点后本段就再也凑不够 → 10 s 没有新中文（10-09 真课 seg 91：19 词送翻后 13/17/19/22/20/23，门槛 25 永远到不了）。

**`draft_policy.py`** 新增（放在 `should_translate` 之前）：
```python
NOVEL_MIN_NEW = 4      # 相对上次送出的文本，至少 4 个新词
NOVEL_STARVE_S = 5.0   # 或者距上次 ≥5 s 且至少 1 个新词

def novel_words(sent_text: str, text: str) -> int:
    """`text` 里有多少词不在上次送出的 `sent_text` 里（多重集差；词身份同 `_tokens`）。"""
    have = Counter(_tokens(sent_text))
    n = 0
    for w in _tokens(text):
        if have[w] > 0:
            have[w] -= 1
        else:
            n += 1
    return n
```
`should_translate` 加关键字参数 `sent_text: str = ""`，并在**最前面**处理 `novel`（它不走 legacy 的「+6 词」前置检查）：
```python
if policy == "novel":
    gap = now - last_tr
    if gap < MIN_GAP_S:
        return False
    new = novel_words(sent_text, text)
    return new >= NOVEL_MIN_NEW or (gap >= NOVEL_STARVE_S and new >= 1)
```
文件头 docstring 补一段 `novel` 的说明与依据（10-09 回放：讲话中 ≥6 s 无新中文 398 s → 约 20 s；同思路见 LiveCaptions-Translator `src/Translator.cs` 的「变化次数 / 空闲时间」触发）。

**`main.partial_worker`**：
- `draft_policy_name` 的默认值从 `"legacy"` 改成 `"novel"`；允许集合加 `"novel"`（`CLASSLIVE_DRAFT_TRIGGER=legacy` 仍可退回）。
- 新增局部变量 `last_sent = ""`。
- ⚠️ **节流重置改用 `seg_gen["n"]`，不再用 `final_gen["n"]`**（2026-10-11 审查核实）：草稿属于**正在录的那一段**（`seg_gen` 在音频段结束那一刻递增，见 `_open_segment` 上方注释），而 `final_gen` 要等上一段定稿上屏才变（晚 2–4 秒；进了 carry 时根本不变）。用 `final_gen` 会在新段讲到一半时把它的节流清零。改法：把 `partial_worker` 里 `if final_gen["n"] != last_gen:` 那段（重置 `last_tr, last_len`）改成比较 `seg_gen["n"]`，并一并 `last_sent = ""`。`DraftStabilizer` 那处（`stab_gen`）**不动**（默认关，且它语义上就是跟定稿走）。
- 调 `should_translate(..., sent_text=last_sent)`；`_ok` 为真时 `last_sent = t`（存**未剥悬挂词**的原文，见 B）。

**测试 `tests/test_draft_policy.py`** 新增 `class Novel(unittest.TestCase)`：
1. seg 91 复刻：`sent_text` 为 19 词的那份，新草稿只有 13 词但含 ≥4 个新词 → True（legacy 同输入为 False，断言两者都写上）。
2. 间隔 <2.5 s → False（无论新词多少）。
3. 只有 1–3 个新词、间隔 3 s → False；间隔 5.0 s → True。
4. 完全相同的文本、间隔 10 s → False（`new == 0`）。
5. `novel_words` 多重集：`"the the price"` 相对 `"the price"` 是 1。

## B. 送翻前剥掉结尾悬挂词

`main.py` 在 `is_incomplete` 旁边加纯函数：
```python
def strip_dangling(text: str, max_strip: int = 2) -> str:
    """草稿送翻前削掉结尾 1–2 个悬挂词（"... the limit of" → "... the limit"）。
    有句末标点、或削完不足 3 个词时原样返回。只用于**草稿**，定稿不动。"""
```
实现**直接循环调用 `is_incomplete`**（每削一个词都重新检查句末标点，口径与它 100% 一致；避免 "See what it can be. And" 削掉 "And" 后又去削 "be."）：
```python
def strip_dangling(text: str, max_strip: int = 2) -> str:
    words = (text or "").strip().split()
    for _ in range(max_strip):
        if len(words) <= 3 or not is_incomplete(" ".join(words)):
            break
        words.pop()
    return " ".join(words)
```
`partial_worker` 入队时送 `strip_dangling(t)`。
**测试**：放进 `tests/test_audit_regressions.py` 或新文件 `tests/test_draft_text.py`（与现有风格一致即可）：`"a b c of the"` → `"a b c"`；`"a b c."` 不变；`"of the"` 不变（不足 3 词）；`"See what it can be. And"` → `"See what it can be."`（只削一个）。

## C. 草稿译文独立线程

**问题**：草稿译文现在在 `final_worker` 的空闲分支里跑（`finalq` 空才轮到），定稿优先 → 回放应触发 570 次、真课只出 417 条，约 27% 被挤掉。

**改法**：
1. `draftq` 的元素改成三元组 `(seq, seg, text)`：`seq` 是 `partial_worker` 自增的序号，`seg` 是 `seg_gen["n"]`（与 `drafts` 元组里那个段号同源）。`maxsize=1`、「只留最新」的写法不变。
2. 从 `final_worker` 的 `if buf is EMPTY:` 分支里**删掉**草稿译文那一段（`draftq.get_nowait()` 到 `continue`），只留「sleep 0.2 + carry 超时检查」。顺带把 `partial_worker` 里「它要抢同一把 ASR 锁」那段已经过时的注释改成实话：定稿走 Whisper 时两把锁不同；只有 Parakeet 兜底时才共用。
3. 新线程（与 `final_worker` 并列定义，`t5 = threading.Thread(target=draft_tr_worker, daemon=True, name="draft-tr")`，与 t1/t2 一起 start）：
```python
def draft_tr_worker():
    while running.is_set():
        try:
            item = draftq.get(timeout=0.3)
        except queue.Empty:
            continue
        seq, dseg, dtext = item
        if trans["mode"] != "both" or not dtext:
            continue
        # 本地引擎（MLX 全局锁）时别和定稿抢：定稿在排队/在跑就让开
        if not translator.uses_cloud and (not finalq.empty() or busy["on"]):
            draft_stats["yield_local"] += 1
            continue
        try:
            translator.translate_draft(
                dtext, lambda d, s=seq, g=dseg: drafts_zh.put((s, g, d)),
                context=finals[-1:])
        except Exception:                                   # noqa: BLE001
            draft_stats["errors"] += 1
```
4. `EngineRouter` 加只读属性 `uses_cloud`（返回 `self._use_cloud`），`translate_draft(en, on_zh=None, context=None)` 把 `context` 只传给云端（本地 `Translator.translate_draft` 签名不变）。
5. **过期丢弃**（在 `drain()` 里，主线程）：
   - 闭包里加 `draft_view = {"fin_seg": -1, "seq": -1}` 和 `draft_stats = {"stale": 0, "yield_local": 0, "errors": 0}`。
   - `drain()` 的 `"final"` 分支里：`draft_view["fin_seg"] = max(draft_view["fin_seg"], int(item[4]) if len(item) > 4 else draft_view["fin_seg"])`。
   - `drafts_zh` 那个循环改为拆 `(seq, dseg, zh)`：`dseg <= fin_seg`（这段已经定稿上屏）或 `seq < draft_view["seq"]`（更新的已经显示过）→ `draft_stats["stale"] += 1`，跳过；否则更新 `draft_view["seq"] = seq` 再照旧 `live.draft_zh` + `ui.draft_zh`。
   - ⚠️ `drain()` 没有 try/except，拆包要像 `_pair()` 那样防御：形状不对就丢，绝不抛。
   - ⚠️ **不要用 `final_gen` 判过期**：它在上一段定稿时就自增，而那时下一段的草稿已经在翻 —— 会把有效译文当过期丢掉。
5b. **别让上一段的定稿擦掉下一段的草稿**（2026-10-11 审查核实）：`Overlay.finalize()` 无条件清空草稿行（`_draft`、`_draft_zh_val` 和 roll-up 滞回状态 `_draft_base_len/_draft_zh_base_len`）。第 K 段定稿到达时，第 K+1 段的英文草稿多数已在屏上，拆线程后它的中文草稿也常已在屏上 → 被擦。
   - `Overlay.finalize(self, en, zh, *, clear_draft: bool = True)`：`_cur_zh/_cur_en/_streaming` 照清；只有 `clear_draft` 为真才清 `_draft`、`_draft_zh_val` 和两个 `*_base_len`。`TerminalUI.finalize` 加同名关键字参数（忽略即可）。默认值为 True → 现有调用与测试零改动。
   - `draft_view` 再加 `"cur_seg": -1`：`drafts` 循环里 `_seg > draft_view["cur_seg"]` 时（新一段的第一份英文草稿）更新 `cur_seg`，并 `ui.draft_zh("")` 清掉上一段残留的中文草稿。`TerminalUI.draft_zh` 加 `if zh:` 守卫（否则终端会打一行空的 `↳`）。
   - `"final"` 分支：`ui.finalize(..., clear_draft=draft_view["cur_seg"] <= draft_view["fin_seg"])` —— 注意先更新 `fin_seg` 再算这个值；屏上已经是更新一段的草稿时，旧段定稿不碰草稿槽。
5c. **carry 超时补发要带上它自己的段号**（审查核实的关联 bug）：`_force_emit_carry()` 现在调 `_emit(t + " …")` 不带段号，`_emit` 就取 `seg_gen["n"]` —— 那已经是**正在讲的下一段**。按 5 的规则，这会把 `fin_seg` 推到当前段，从而把当前段后续所有草稿译文当成过期丢掉。改法：`carry = {"text": "", "since": 0.0, "seg": 0}`；挂起半句时存 `carry["seg"] = _fseg`；`_force_emit_carry()` 调 `_emit(t.rstrip() + " …", carry["seg"])`。E 里新的挂起代码也要存 `seg`。
6. 收尾时 `echo` 一行 `draft_stats`（全零不打印）；如果 `livemetrics` 有通用事件写入口就顺手记一条，没有就不加新 kind。

**测试**：这段在 `run()` 闭包里，单测碰不到。用 F 的回放量具 + 真跑验收（见 §验收）。另在 `tests/test_audit_regressions.py` 加一条**静态**守卫：`final_worker` 源码里不再出现 `translate_draft`（用 `inspect.getsource(main.run)` 找 `def final_worker` 到下一个 `def` 之间的文本）—— 防止以后有人把它挪回去。

## D. 草稿翻译带上下文（默认关）

**问题**：`CloudTranslator.translate_draft` 只发系统提示 + 草稿原文，没有课程领域和术语。10-09 真课里把经济学的 "Competition" 草稿翻成「比赛」，4 秒后定稿才改成「竞争」。

**改法**（`cloud_translator.py`）：
```python
def translate_draft(self, en: str, on_zh=None, context: list[str] | None = None) -> str:
    use_ctx = os.environ.get("CLASSLIVE_DRAFT_CONTEXT", "0") == "1"
    user = self._draft_user(en, context or []) if use_ctx else en
    ...（其余不变）

def _draft_user(self, en, context):
    """稳定内容在前（领域 → 课程术语），随句变的在后（上一句 → 本句）—— 前缀缓存只认从头逐字相同的那段。"""
```
- 领域用 `domain_block(self._domain)`，课程术语用 `self._course_terms`（最多 40 个，逗号分隔）；上一句是 `context[-1]`，标明「仅作上下文，不要翻译」；本句前一行写「这是正在说的话，可能停在半句；只翻已有的部分」（英文措辞与 `DRAFT_SYSTEM_CLOUD` 同风格）。
- 三样都没有时返回 `en` 原文 —— 与旧行为逐字相同。
- **不碰** `fix_and_translate_stream` 的任何字节（`tests/test_translator.py` 逐字冻着老布局）。

**对比脚本**（`scripts/draft_ctx_ab.py`，新）：从 `sessions/2026-10-09_120723_ECON10770.live.jsonl` 取 40 条真实送过翻的草稿（`draft` 行里与 `draft_zh` 时间相邻的那份），同一模型各翻一遍（关 / 开上下文），输出一份并排的 Markdown 到 scratchpad（**不写进 `sessions/`**）。费用 <$0.01。**交给 Owen 看，他说行再把默认改成开。**

**测试**（⚠️ 该文件用自定义 `@case("…")` 装饰器 + `CASES` 列表，**不是** unittest —— 沿用它，否则新测试不会被执行）：`tests/test_translator.py` 加一条 —— 环境变量关时，`translate_draft` 发出的 messages 与改动前逐字相同（用替身 `_stream_chat` 截获）；开时 user 消息里按顺序出现「领域 → 术语 → 上一句 → 本句」。

## E. carry 只看最后一句

**问题**（核实过，RESEARCH §13.6）：`final_worker` 拿**整段**文本判断要不要 carry：`is_incomplete(text) and len(text.split()) < CARRY_MAX_WORDS`。
① 段 <25 词且以悬挂词结尾时，把**已经说完的前一句**也一起扣住，最多晚 5 s；② 段 ≥25 词时根本不 carry。
⚠️ **预期收益要说清楚**：10-09 真课里末句无标点的 92 段中，末词是悬挂词的只有 10 段；其余 82 段以实词结尾（12 s 硬切切在句中）。而这类段到下一段定稿的间隔中位 8.3 s，超过 `CARRY_MAX_S = 5 s`，所以**把 carry 扩到所有硬切段不划算**（只会让半句晚 5 s 出来、照样单独翻）。本项只修 ①②，不扩大 carry 的范围；硬切劈句交给 §H 的切点实验与 R3 流式。

**改法**：模块级纯函数（放在 `split_sentences` 后面）：
```python
def split_carry(text: str, max_words: int = 25) -> tuple[str, str]:
    """(立刻上屏的部分, 要挂起等下一段的半句)。只看**最后一句**：
    它没有句末标点、以悬挂词结尾（`is_incomplete`）、且 < max_words 词，才挂起。"""
    sents = split_sentences(text)
    if not sents:
        return "", ""
    last = sents[-1]
    if is_incomplete(last) and len(last.split()) < max_words:
        return " ".join(sents[:-1]), last
    return text, ""
```
`final_worker` 里原来那段「未说完且未超上限 → 继续等」替换为：
```python
ready, tail = split_carry(text, CARRY_MAX_WORDS)
if tail:
    busy["on"] = True                 # ⚠️ 先置 busy，再动 carry（all_settled 的四项不能同时为空）
    carry["text"], carry["since"], carry["seg"] = tail, time.monotonic(), _fseg
    try:
        if ready:
            _emit(ready, _fseg)
    finally:
        busy["on"] = False
    continue
```
其后原有的「busy → 清 carry → `_emit(text)`」路径保持不变。
**测试**（纯函数，放 `tests/test_audit_regressions.py`，毫秒级）：
1. `"First done. Then we go to the"` → `("First done.", "Then we go to the")`（旧逻辑会整段扣住）。
2. 30 词完整句 + `"and the"` 结尾的短句 → 前面立刻出、只挂短句（旧逻辑整段 ≥25 词直接不挂）。
3. 以实词结尾的半句 → 不挂（`("…", "")`），锁住「不扩大范围」这条决定。
4. 空串 → `("", "")`。
⚠️ 碰了 `carry` 与 `busy` → 必跑 `scripts/probe_testmode_report.py`。

## F. 回放量具 `scripts/draft_gap_replay.py`（新）

读 `<会话>.live.jsonl`（`draft` / `final` / `draft_zh` 行），对每种策略（`legacy` / `novel`，B 开/关）重放草稿触发，翻译耗时按 1.0 s 计，输出：
- 送翻次数；以悬挂词结尾的送翻占比；
- **讲话中 ≥6 s 无新中文**的次数与总秒数（「讲话中」= 该区间内 ≥4 条英文草稿）、其中 ≥10 s 的次数；
- 以真实记录（`draft_zh` + 带 `zh` 的 `final`）为对照行。
判据直接调 `draft_policy.should_translate`（不在脚本里另写一份 —— 本仓库吃过「离线模拟和生产不是同一条路」的亏，见 `draft_policy.append_only` 的 docstring）。
**预期**（我在 scratchpad 里跑过同样的回放）：`legacy` ≈52 次 / 398 s → `novel` ≈2–3 次 / 15–23 s；送翻次数 +40% 左右。

## 验收（3.8.11）

1. `tests/test_draft_policy.py`、`tests/test_audit_regressions.py`、`tests/test_translator.py` 全绿；新用例都有「改前红」的输出。
2. `python scripts/draft_gap_replay.py sessions/2026-10-09_120723_ECON10770.live.jsonl` 出表，`novel` 行的空窗总秒数 < 60。
3. `CLASSLIVE_UPLOAD=0 ClassLive.app/Contents/MacOS/python scripts/probe_testmode_report.py` 通过。
4. `CLASSLIVE_UPLOAD=0 CLASSLIVE_LIVE_LOG=1` 用 `cl file` 回放一段 5 分钟真课录音（从 `sessions/` 的 opus 复制到 scratchpad 再用，**不动原文件**）→ 对新产出的 `.live.jsonl` 跑 F，空窗应与回放预期同量级；收尾打印的 `draft_stats` 里 `stale` 不应超过送翻数的 10%。
5. 交 Owen 看：D 的并排对比 + 第 4 步那 5 分钟的屏幕效果（「推送前先给看效果」）。

---

## G. ❓ 轻点拨（3.8.12，A–F 确认后再做）

**依据**：10-09 那节 `report.json` 的 `blocks.ui` 只有 `outline: 6`、`quit: 1`，❓ 0 次。按下 ❓ 当场没有任何反馈，课后笔记也只是把那几句原字幕再贴一遍；旁边的「讲一下」当场讲透、课后还有卡片。定位改成：**不想切走字幕时按 ❓（一句话点拨），想停下来细看时按「讲一下」**。

1. **按下即反馈**（`overlay.py`）：`_lost()` 里在 `_on_lost()` 之后调 `self.show_hint("❓ 已标记 · 正在提炼…", ttl=20)`。
   新方法 `show_hint(text, ttl)`：存 `self._hint = (text, time.monotonic() + ttl)` 并 `_mark_dirty(urgent=True)`；`_gloss_text()` 在 `_pinned_term` 判断**之前**：hint 未过期就返回它。过期后要能自动消失 —— 在 `Overlay.pump()` 里、`self._flush_if_due()` 之前检查 hint 是否过期，过期就清掉并 `_mark_dirty()`。
2. **课中一句话**（`main.py`）：`on_lost` 回调改成 `lambda: (writer.mark_lost(), hintq.put(_hint_snapshot()))`。`_hint_snapshot()` 在主线程取 `finals[-4:]`（复制）+ 当前草稿英文；**只做 list 复制，不联网**。
   新线程 `hint_worker`：从 `hintq` 取 → `translator.hint(lines)` → **`if not stopping.is_set():`** `streamq.put(("hint", text))`（`streamq` 是收尾判据 `all_settled()` 的输入之一，照 `answer_worker` 的写法）；失败或本地引擎时放 `("hint", "")`。
   `drain()` 加 `"hint"` 分支：非空 → `ui.show_hint("💡 " + text, ttl=15)`；空 → `ui.show_hint("❓ 已标记（课后笔记里会有讲解）", ttl=6)`。`TerminalUI` 补一个打印版的 `show_hint`。
   `CloudTranslator.hint(lines)`：非流式，`max_tokens=80`，系统提示「用不超过 35 个汉字说出刚才这段的核心意思，给正在听课的学生看；只输出这一句」，带 `domain_block`。`EngineRouter.hint` 云端优先，本地返回 `""`（本地 1.7B 讲解只会编，与 `answer` 的既有决定一致）。
3. **课后讲解卡**（`obsidian_writer.py` + `polish.py`）：`polish.py` 新增 `explain_lost(blocks, api_key, model, course_terms, domain, timeout=60)`：一次请求处理全部 ❓ 块（每块是那几句英/中），返回每块的 `{"explain": "...", "q": "...", "a": "..."}`；任何失败返回 `[]`。
   `_lost_items` 在每块的原句后面追加 `  - 💡 讲解：…` 和一行闪卡（格式照抄 `_qa_items` 现在用的 `问题::答案` 写法）。只在有 key 时调用；用户点了「跳过精修」（`cancel` 为真）就不再发起 `explain_lost` 请求；闪卡的问题文本做 `.replace("::", ": :")`（与 `_qa_items` 一致，防止伪造卡片边界）；fail-soft 纪律同 `_keypoint_items`。
4. **测试**：`polish.explain_lost` 的解析用替身 HTTP（坏 JSON / 空返回 / 部分缺字段都返回可用结果或 `[]`）；`_lost_items` 在 `explain` 为空时与现在逐字相同。overlay 部分用 `scripts/probe_test_mode.py` 那种拍图方式出图给 Owen 看。
5. **不做**（这一轮）：自动迷失雷达、点字幕行标记 —— 等 G 上线后看 ❓ 用量再定。

## H. 离线实验：硬切点往回找能量低谷（已跑，结论见 RESEARCH §14）
实测（OCW 真值，157 个硬切点）：WER 10.1% → 9.9%（噪声内），删除率 4.0% → 3.6%，「前段末尾无标点」95 → 83、「后段小写开头」118 → 95。收益小但方向一致、改动小。
**建议作为 3.8.11 的可选项 I，由 Owen 决定是否带上。**

## I. （可选）硬切点往回找能量低谷 — `vad.Segmenter`

在 `accept()` 里 `hard_cut and not silence_cut` 的分支（现在是 `buf = self._buf().copy(); self._reset()`）改为：
1. `buf = self._buf()`；在 `buf[-int(1.5*SR):]` 里按 30 ms 帧算 RMS，3 帧滑动平均，取最低点 `k`；`cut = len(buf) - int(1.5*SR) + k*F + F//2`（`F = int(0.03*SR)`）。若 `len(buf) < 1.5*SR`、计算出错、或窗口**没有明显低谷**（`smooth.max() - smooth.min() < 1e-6`，否则全程等幅时 `argmin` 返回 0 会无故回退 1.5 s），`cut = len(buf)`（等于旧行为）。
2. `head, rest = buf[:cut].copy(), buf[cut:].copy()`；`self._reset()` 后**仅当 `len(rest) > 0`** 才把 `rest` 放回：`self._parts = [rest]`、`self._n = len(rest)`、`self.has_speech = True`、`silence_run = 0.0`（`rest` 为空时保持 `_reset()` 后的状态 = 旧行为；否则之后 `flush()` 会误记一次 `diag["dropped"]`）；其余 `_reset` 清掉的状态保持清掉。⚠️ 先读 `_reset()` 和构造器里的全部字段，逐个确认哪些要为「段中续接」保留；拿不准就停下来问。
3. `self.on_utterance_end(head)`，`diag["hard_cuts"] += 1` 不变；新增 `diag["valley_back_s"]` 累计回退秒数（只计数，不参与判定）。
**测试**：合成信号（两段 6.5 s 正弦 + 中间 0.2 s 近静音，总长 >12 s）→ 切点落在近静音处；`len(head)+len(rest)` 等于原长（**不丢样本**）；低谷找不到（全程等幅）时等于旧行为。再用 `ocw_segs.py` 同款离线复刻跑一遍 OCW，切段数与本节数字同量级。
⚠️ 这是唯一碰音频切分的改动：默认闸门 + `scripts/test_pipeline.py` 真录音跑通 + `probe_testmode_report.py` 三样都要过。
