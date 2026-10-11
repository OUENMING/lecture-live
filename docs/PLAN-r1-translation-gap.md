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
| G | ❓ 课中一句话点拨 + 课后讲解卡（3.8.12，单独一轮；§G 已按现行代码重写） | `overlay.py`、`main.py`、`cloud_translator.py`、`obsidian_writer.py`、`polish.py` | ~250 | 开 |

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

## G. ❓ 轻点拨（3.8.12）—— 2026-10-11 按现行代码重写，可直接执行

**依据**：10-09 那节 `report.json` 的 `blocks.ui`：`outline: 6`、`quit: 1`，❓ **0 次**。按下 ❓ 当场没有任何反馈，课后笔记只把那几句原字幕再贴一遍；旁边的「讲一下」当场讲透、课后还有卡片。
**定位**：不想切走字幕时按 ❓（一行点拨，继续听）；想停下来细看时按「讲一下」。
**同类做法**（2026-10-11 查）：Learnica（实时课堂翻译）「点一行标成 Important / Confused，然后继续听」+ 课后把标记和 AI 笔记放一起复习；Glean（现 Genio）课上打 label、课后复习；Otter 高亮进 takeaways。共同点：**课中只做轻标记、不打断听课，解释放课后**。我们多做一步「一行点拨」，因为本工具的用户是二语听者（理解卡在词汇覆盖率，一句中文要点就能接上）。
**不做**（这一轮）：自动迷失雷达、点字幕行标记、「我刚走神了，讲了啥」—— 看 ❓ 用量再定。**不加环境开关**（纯转录档本来就零请求；不按就零成本）。

### G0. 硬约束（违反任何一条都算没做完）

1. **纯转录档一个模型请求都不发**（`DESIGN.md` 的契约）：课中点拨与课后讲解都要挡在发请求之前。「只英·校」档也不发点拨（它是中文的）。
2. 主线程回调（`_lost` / `on_lost`）**只做 list 复制 + `queue.put`**，不联网、不 sleep。
3. 新 tag `"hint"` 必须在 `main.drain()` 加分支（R28 会自动钉住）。
4. 往 `streamq` 写之前判 `stopping.is_set()`（它是 `all_settled()` 的输入，照 `answer_worker`）。
5. 本地引擎不讲（1.7B 只会编，与 `EngineRouter.answer` 同一决定）；云端失败**不走 `_fallback`**（同 `answer`：一次点拨失败不能把整节课翻译降到本地）。
6. 课后讲解住在 `ObsidianWriter.close()` 的护栏区，fail-soft；`cancel`（跳过精修）置位就不发请求；没有讲解时笔记**逐字等于现在**。
7. 闪卡问题文本 `.replace("::", ": :")`（同 `_qa_items`）。

### G1. 课中：按下即反馈 + 一行点拨

**`overlay.py`**
- `__init__`：`self._hint = None   # (文字, 到期的 monotonic)`。
- 新方法 `show_hint(self, text: str, ttl: float) -> None`：空串 → 清掉；否则存 `(text, time.monotonic() + ttl)`；`_mark_dirty(urgent=True)`。
- `_gloss_text()` 最前面：hint 存在且未到期就返回它（术语行那一格，钉住的术语暂时让位，到期自动回来）。
- `pump()`：在 `self._tv.tick()` 之前，hint 到期 → `self._hint = None; self._mark_dirty()`。
- `_on_gloss_click()` 最前面：hint 在显示 → 清掉、`_mark_dirty(urgent=True)`、return（点一下就收起，不去切钉住的术语）。
- `_lost()`：`queued = self._on_lost()`；然后
  `self.show_hint("❓ 已标记 · 正在提炼一句话…", ttl=20) if queued else self.show_hint("❓ 已标记 · 课后笔记里能找到这几句", ttl=6)`。
  （默认的 `_on_lost` 返回 `None` → 走第二句，老调用方不受影响。）

**`cloud_translator.py`**
- 常量 `HINT_SYSTEM`（中文）：你在帮一个母语是中文、正在听英文课的学生。下面是老师刚讲的几句（英文，可能有语音识别错误）。用**一句不超过 35 个汉字**的中文说出这段的核心意思，让他马上跟上。只依据这几句，不补充外部知识；拿不准就说得保守。只输出这一句，不要前缀、引号、换行。
- `CloudTranslator.hint(self, lines: list[str]) -> str`：system = `HINT_SYSTEM` + `domain_block(self._domain)` + 课程术语前 40 个（同 `_answer_system` 的拼法）；user = 各行用换行连接；走 `self._stream_chat(msgs, max_tokens=80)` 拼完整段；取第一行、去首尾引号与空白，超过 60 字截断加「…」。

**`main.py`**
- `EngineRouter.hint(self, lines) -> str`：`not self._use_cloud` → `""`；`try: return self._cloud.hint(lines)` / `except Exception: return ""`（**不调 `_fallback`**，理由同 `answer` 的注释）。
- `TerminalUI.show_hint(self, text, ttl)`：有字就 `echo` 一行（终端版不需要到期）。
- `run()` 里：`hintq: "queue.Queue[list[str]]" = queue.Queue()`；`last_draft = {"en": ""}`（`drain()` 里每次 `ui.add_draft(_shown)` 时顺手记 `_shown`）。
- 新函数 `_on_lost_pressed() -> bool`（主线程）：先 `writer.mark_lost()`；`trans["mode"] != "both"` 或 `not translator.uses_cloud` → `return False`；否则 `hintq.put([t for t in (*finals[-4:], last_draft["en"]) if t])`，`return True`。`_load_overlay(on_lost=_on_lost_pressed, …)`（`writer` 在它之前已建好，确认一下顺序）。
- 线程 `hint_worker`（`daemon=True, name="lost-hint"`）：`hintq.get(timeout=0.5)`；**把队列里积压的取空、只留最新一份**（连按几下只讲一次）；`text = translator.hint(lines)`；`if not stopping.is_set(): streamq.put(("hint", text))`。`None` 哨兵退出。与 `t4` 一起启动。
- `drain()` 加分支：`elif item[0] == "hint":` 有字 → `ui.show_hint("💡 " + item[1], ttl=15)`；空 → `ui.show_hint("❓ 已标记 · 课后笔记里能找到这几句", ttl=6)`。`streamq` 那行注释里的 tag 列表补上 `"hint"`。

### G2. 课后：讲解卡

**`polish.py`**
- 常量 `EXPLAIN_SYS`：输入是一节课里学生按了「没听懂」的若干段（每段几句英文 + 中文译文，带编号）。逐段输出：`explain`（中文 ≤120 字，讲清这段在说什么、为什么；**可以用通用学科知识把概念讲明白，但不许编造这节课没说过的具体数字、例子或结论**）、`q`（一句自测问题 ≤40 字）、`a`（答案 ≤80 字）。严格 JSON：`{"items":[{"i":编号,"explain":"…","q":"…","a":"…"}]}`。
- `explain_lost(blocks, api_key, model, course_terms=None, domain="", timeout=60.0, chat=None) -> list[dict]`
  - `blocks`：`list[list[{"en","zh"}]]`；`chat` 默认 `_chat`（测试注入替身）。
  - 每 20 块一次请求；user 里先放 `domain_block` 与课程术语（同 `polish_entries` 的写法），再放编号的块。
  - 返回**与 `blocks` 等长**的列表，每项 `{"explain","q","a"}` 都是单行字符串（缺字段 → `""`，去换行）；**任何异常 / JSON 不对 / 条数对不上 → 返回 `[]`**（整体不用，绝不错位）。

**`obsidian_writer.py`**
- 构造函数加 `lost_explain_fn=None`（与 `keypoints_fn` 同形状，存 `self._lost_explain_fn`）。
- `_lost_items(self, entries, explain: bool = True)`：先照现在的逻辑算出每块的 `lines` 与对应的 entries 切片；`explain and self._lost_explain_fn and blocks` 时调一次 `fn([[{"en": e["en"], "zh": e["zh"]} …] …])`（自己 try，失败当 `[]`）。返回长度等于块数时，每块末尾追加：
  `  - 💡 讲解：{explain}`（explain 非空才加）和 `  - {q 去 ::}::{a}`（q、a 都非空才加）。否则**一字不加**。
  - 调用前 `print("🤔 正在给 ❓ 标记写讲解…", flush=True)`。
- `close()`：`self._lost_items(entries, explain=not (cancel is not None and cancel.is_set()))`。

**`main.py`**（由 G1 的执行者一起接线）
- 照 `_keypoints_for` 写 `_lost_explain_for(blocks)`：`args.save_notes == "no"` 或 `trans.get("mode") == "raw"` 或没有 `api_key_val` → `[]`；否则
  `polish.explain_lost(blocks, api_key_val, args.polish_model or args.cloud_model, course_term_list(args.glossary, args.course), course_title(args.glossary, args.course))`，异常 → `[]`。
- `ObsidianWriter(…, lost_explain_fn=_lost_explain_for)`。

### G3. 测试（先红后绿，贴两次输出）

- `tests/test_hint.py`（unittest）：`CloudTranslator.hint` 用替身 `_stream_chat`（多行 → 只取第一行、去引号、超长截断）；`EngineRouter.hint` 本地 → `""`、云端抛错 → `""` 且 `_use_cloud` 仍为 True；`Overlay`（构造不了就 skip）：`show_hint` 后 `_gloss_text()` 是 hint、把到期时间改成过去再 `pump()` 后回到术语行、点术语行在 hint 显示时只清 hint 不钉术语、`_lost()` 在 `on_lost` 返回 True/None 时显示两种字。
- `tests/test_lost_explain.py`（unittest）：`explain_lost` 替身 `chat`：正常 / 坏 JSON / 条数不对 / 缺字段 / 抛异常；21 块分两次请求；`_lost_items` 没有 `lost_explain_fn` 时与现在逐字相同（同一份夹具对拍）、有讲解时追加的两行格式、`q` 含 `::` 被拆开、`explain=False` 不调用 fn。夹具照 `test_audit_regressions` R4 的写法**同时换掉 `SESSIONS`**（写端隔离）。
- `scripts/probe_hint.py`：建一个 `Overlay`，放两句字幕 + 一个术语，分别 `show_hint` 两种文字，各拍一张图到临时目录（拍法照 `scripts/probe_test_mode.py`：先拍基线验字节数，防显示器睡眠出黑图），打印路径。**推送前给 Owen 看图。**
- 收尾：`scripts/check.py --changed` 全绿；`probe_testmode_report.py` 由主会话跑（子代理不跑 `main.py`）。

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
