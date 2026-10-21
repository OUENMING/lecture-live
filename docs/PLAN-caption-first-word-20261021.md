# PLAN · 新句首词切行 + 清旧中文（2026-10-21）

> Owen 10-21 拍板：**保留 `JOIN_ON` 和 `HOLD_MAX_S=2.5`**；只保证「首词立即出现 /
> 该新行时首词就新行」；`Okay?` / `So` 这类口头禅并进上一行（`TAIL_WORDS=3`），
> 上一行冻结。方向依据见 `docs/demo/caption-switch-demo.html` 与
> `[[live-caption-linebreak-standards-20261021]]`。

## 改动

1. `caption_flow.py`
   - `MIN_WORDS: 3 → 1`：有英文的草稿第一个词就占新行，不再攒 3 词。
   - 新增 `TAIL_WORDS = 3`：`is_tail` 只用于「短尾巴并进上一行」，不再兼任新行门槛。
   - 新增 `TAIL_STARTERS`（okay/ok/right/yeah/well/so/um/uh/hmm/mm/ah/oh；**不含 now**）
     + `is_tail_start()`：草稿首词是口头禅时先并进上一行，避免 1 词新行再被 final 并回去。
   - 只有中文、英文还没到时不再显示孤立中文；`HOLD_MAX_S` 不变。
2. `draft_translate.py`
   - `DraftView`：同 seg 但 `continues()` 为假也清中文；句子代次 `gen` +1；
     `on_final` 给下一句置 `_clear_next`；`accept()` 丢掉上一代晚到译文。
   - `continues()` 先认完整前缀，短句 `Okay?` → `Okay? so` 不再反复清。
   - `DraftTranslator.submit(..., gen=)`：代次变了作废封口锁，避免同 seg 新句复用旧中文前缀。
   - `note_submitted()` 记录送翻 seq 的代次；主线程在 `on_draft()` **之后**重记，
     否则 OCR 2026-10-21 high：新句第一份译文会被当旧代次丢掉（~2.5s 空拍）。
3. `main.py`
   - `drafts` 元组末项加送翻 `seq`（8 元组）；`_pair` 兼容旧 7 元组。
   - 三处草稿产出：先 `drafts.put`，再 `draft_tr.submit(..., gen=generation())`。
   - `drain()`：`on_draft()` 清中文后 `note_submitted(seq, generation())`。
4. `overlay.py`
   - `_join_decide()`：口头禅短开头即使有端点也先并进上一行；`Now a new topic...`
     等内容开头仍按端点/写满另起一行；`JOIN_ON` 保留。

## 验证

- `scripts/check.py --all`：117 个测试文件全过；ruff / pyright 基线 / 写端隔离全过。
- 针对性：`test_draft_translate`（43）、`test_caption_flow`（36）、
  `test_join_rows`（13）、`test_d_layout`（22）。
- 真 `Overlay` 实拍：`scripts/probe_caption_ui.py` → 7 张图
  （旧中文 before/after、`Okay?` 并进上一行、首词新行、增长不跳、`JOIN_ON`）；
  联系表 `~/Desktop/classlive-review/20261021-caption/real-overlay-sheet.png`。
- OCR 2026-10-21 第一轮：high（代次时序）、medium（`MIN_WORDS` 死分支）、low（演示标签）
  三条均已处理；high 补了 `test_main_renotes_after_clear_keeps_first_translation`
  + 静态接线判据。
- OCR 2026-10-21 第二轮（targeted `draft_translate.py` / `main.py`）：又抓 6 条并全部修：
  `continues` 字符前缀误判（`I`/`Is`、`the`/`there`）→ 词边界；标点插入误清 →
  归一化；空草稿没消费 `_clear_next` → 只清一次；`gen` 默认 0 歧义 → `None`；
  `on_final` 同 seg 不应硬置 `_clear_next` → 改 `s > cur_seg`；3 元组回退 `gen=0` → `None`。
  每条都有红-绿测试（`test_continues_uses_word_boundary` /
  `test_same_segment_punctuation_insertion_does_not_clear` /
  `test_empty_draft_after_final_clears_once` /
  `test_same_seg_final_does_not_force_next_draft_clear`）。

## 状态

- 分支 `fix/stream-stall-20261021`，**未提交、未推送**；等 Owen 看实拍说「行」再 commit。
- 不涉及 `HOLD_MAX_S` / `JOIN_ON` / `JOIN_MAX` 的语义变更；不碰上传、笔记、收尾。
