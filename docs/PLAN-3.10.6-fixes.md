# 3.10.6 修复清单（2026-11-03 从美国用户 11-02 数据里记下来的）

> 3.11 总计划（含本清单的 C1–C7 编号、根因复核、验收与本机安装）见
> `docs/PLAN-3.11-20261103.md`；本文件保留当时的原始记录。

来源：`32b6d636756386d9`（CHE002A，3.10.5）+ 同用户 10-26…10-30 六节对照。
完整数据见 Obsidian `Projects/ClassLive-美国用户数据复盘-20261102.md`。

> 原则：每条都要能落到一个**可判的对错**（判据怎么写、拿哪份数据验）。
> 「初步原因」是按代码和云端数据推的，标了 ⚠️ 的是还没拿用户机器上的文件确认。

## P1-1 笔记 md 的「课堂纲要」没吃到术语英文化

- **现象**：3.10.5 说「正文 English（中文）」。今天 HTML 里 46 处英文在前，md 里 31 处仍是中文在前。
- **位置**：`note_render.render()` 只对 `d.review` 调了 `note_v2.announce_terms_in_review`；
  「课堂纲要」走 `obsidian_writer._render_outline_section(d.chapter_items, …)`（`note_render.py:284-291`），
  这条路径没有经过 `announce_terms`。HTML 的纲要在 review 里，所以 HTML 对、md 不对。
- **修**：给 `_render_outline_section` 的 bullet / 标题也过一遍同一份 `announce_terms`（或把 chapter_items
  在 `_note_data` 里先规范化一次，锁 `seen` 与 review 共用）。注意「同一节后续只写英文」的 seen 要跨
  纲要与讲解共用，否则同一术语会括注两次。
- **判据**：拿今天这份 `note.md` 跑 `term_en_residue()`，残留数 = 0；再跑 `tests/test_note_v2.py::AnnounceTermsEn`
  与 `test_note_glossary.py`。

## P1-2 地区探测预热与开课抢跑（今天没有用上测速结果）

- **现象**：`region_probe.late=true / cached=false / selected="" / ordinal=1`；us 325.5+192 ms 是好的，jp 超时。
- **已确认**：预热**确实跑了**——Analytics Engine 里 18:01Z 这一分钟有 2 次 `soniox-us:201:probe`
  + 1 次 `soniox-jp:201:probe`，同分钟还有开课那次 `soniox-us:201`（正常流）。
- **初步原因**：3.10.5 自动更新在 10:01:18 完成、10:01:31 开课，中间只有 13 s。
  `region_route.state_for()` 在版本变化时**整份重置**（含 cache），预热要重新测 3 s；
  开课的 `Router` 在预热 `_persist()` 之前读了状态 → `ordinal=1`、`cached=false`，随后探测回来只能记 `late=true`。
  下一节会命中缓存，所以只有「更新后的第一节」最吃亏。
- **修**（二选一）：
  1. 开课路径若发现 `warm` 正在飞，`join` ≤1 s 等它落盘再 `plan()`（不能让开课等满 3 s）；
  2. `run_probe` 每测完一个区就把结果写进内存，`plan()` 在构造后允许补一次 `replan()`。
  另外 `_warm_probe()` 取不到 key 时**没有任何日志**——先补一行，否则下次还是瞎猜。
- **判据**：更新后第一节的 report 满足 `cached=true` 或 `late=false`；日志里有 `region-warm` 的完成行。

## P1-3 收尾时 Jev 判官整批失败，且没留下原因

- **现象**：速览两次空回；支持性核对 8 批 0 回执；Jev 24/24 批失败（`keypoints.py:237` 那行）。
- **已确认**：AE 里今天 `/v1/jev` 只有 **6 次、全 200**，都发生在 18:36–18:43（课中，实时判要点）；
  收尾段（18:49 之后）**一次都没有**。说明 24+8 批要么根本没发到 Worker，要么发去了另一家。
- **初步原因**：`keypoints.resolve()` 按「官方 key → CommandCode → 通行码」选供应商；课中走的是通行码（Worker 有记录），
  收尾却没有任何 Worker 记录。两种可能：① 收尾进程解析到 CommandCode / 空 token；② 客户端 DNS/网络在那一刻失败。
  现在 `score_batch()` 把异常文本丢掉（只判 transient），所以**无法区分**。
- **修**：`score_batch` 与 `support_check` 各记一次「最后一条异常文本 + provider name」，进日志与 report；
  全失败时在笔记页脚降级为「本轮未核」，不要静默少一层。
- **判据**：下一次失败能在这行日志里看到 `Jev 401/429/连不上/…`；单元测试注入 fake ask 抛 `RuntimeError("x")` 断言日志含 x。

## P2-1 `semantic_seal` 仍未登记进报告块

- **现象**：report 的 `events` 里又是 `bad_block: 未登记的块名 'semantic_seal'`。
- **位置**：`testmode.BLOCKS`（`testmode.py:381`）没有 `semantic_seal`；`live.jsonl` 里 summary 事件是有的。
- **修**：BLOCKS 加 `"semantic_seal"` + 把 `SemanticSealer.stats()` 接进 report；面板白名单放行 `bad_block` 计数。
- **判据**：下一份 report 的 `block_names` 含 semantic_seal；`tests/test_testmode.py` 加一条。

## P2-2 数字核对只标不改，错误值留在正文

- **现象**：`0.160−0.667` 写成 `0.33`（正确是 −0.507），核表标「⚠ 有出入」，正文仍写 0.33。
- **位置**：`note_html.py:1386-1390` 的注释表明这是**有意**：只有别处真改过字才写「已修正」。
- **要定**：这是产品取舍，不是 bug。两条路——① 允许核对层回改正文（要记录改动）；② 保持只标，但把
  「有出入」的算式从题干里挪走或加删除线。别让它停在「标了但学生仍会读错」。
- **判据**：定下来之后，拿今天这份 note.html 复跑，错误值要么被修正要么不可读。

## P2-3 上传「还没传完（超过 10 秒）」是假警报

- **现象**：控制台打印「还没传完（超过 10 秒）」，而 R2 里 9 个对象 19:09:24–36Z 全部到齐，
  这条消息之后约 3 s 就传完了。
- **位置**：`main.py:4527-4530`：`join(UPLOAD_JOIN_S=10)` 后只判 `is_alive()`，打印「下次启动接着传」，之后不再复查。
- **修**：打印前再判一次 `pending` 数（或 `thread.is_alive()` + 已传对象数），只有真没传完才提示；
  文案改成「已传 X/Y，剩余下次启动接着传」。
- **判据**：用一节 9 对象的真上传跑，消息只在确实有剩余时出现。

## P3-1 语意封口对「Soniox 自己切好但没标点」的 18–24 词段不覆盖

- **现象**：今天 62/518 个块无标点，85% 集中在最后 9 分钟；单个块 18–33 词，没有冻结（零 draft 的两段是课堂静音）。
- **原因**：`GATE_WORDS=25`，且这些是 Soniox 已经 final 的短段，本来就不会进 sealer 的未封口缓冲。
- **判断**：这不是 3.10.4 的目标（目标是「字幕几分钟不动」），**可能不用修**；若觉得可读性差，
  只能在最终文本层补标点，属于另一件事。先记为观察项。

## 已完成（本次）

- **[x] CHANGELOG 3.10.5 整节重复两次**：commit `09c8800` 一次粘贴了两份（66 行 = 33×2）。
  已删掉第二份（原 101–133 行），保留第一份；`git diff` 仅 33 行删除。发布前再看一眼更新卡片。
