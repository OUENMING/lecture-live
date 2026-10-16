"""🩹 漏译句课后补译：把**只有 ASR** 的句子在写笔记之前补上英文矫正与中文（3.9.7 第 2 项）。

## 为什么要它

云端翻译失败时定稿条目只有 `> **ASR**: …`（`obsidian_writer.append` 只在 EN / ZH
有值时才写那两行）。R31 修掉了「一次异常就把整段降级」的熔断洞，但**单句失败**仍会
留下少量这种条目 —— 实测（中国用户，8 节课）3529 条里丢了 300 条，正稿笔记里那些
句子只剩一行原始转写。

## 边界（照仓库那几条硬规矩）

⚠️⚠️ **只改笔记里的 entries**：`sessions/<名>.md` 一个字都不动。会话 Markdown 是
   `obsidian_writer` 写 / `_parse` 读回 / `cl last` grep 的**三方契约** —— 抬头多一个
   字段会把这一条静默盖到上一条头上（见 `obsidian_writer._lost_path` 的 docstring）。
⚠️ **不落旁路文件**（`.lost.jsonl` 那种在**课前**就得活下来的数据才需要它）：这批
   结果**只在这节课的笔记里用一次** —— `close()` 手里那份 entries 就是目的地，
   落盘等于造第二份真源，还得让 `_parse` / 重建笔记跟着懂它。所以 `_parse` **一个字
   都不用改**；重建笔记（`rebuild_note.py` → `close()`）会照原样再补一次。
⚠️ fail-soft：没网 / 没 key / 到点 / 回来的条目对不上 → **静默跳过**，笔记照旧生成。
⚠️ 有**总截止** `DEADLINE_S`：它跑在收尾里（用户正等着看笔记），不许被它拖长。
   最坏情况 = `DEADLINE_S` + 一次 `TIMEOUT_S`（在飞的那次请求掐不掉）。
⚠️ **档位判据不在这里**：纯转录档是「一个模型请求都不发」的硬契约、只英·校档的
   用户明确不要中文 —— 两条都由 `main._backfill_for` 挡在发请求之前（那里才知道
   这节课的档位）。这里只认「**EN 与 ZH 都空、ASR 有字**」这个漏译的形状。

## 复用

prompt 与批次解析**整段复用 `polish`**：`polish.POLISH_SYS` 就是为「拿 ASR / EN
重矫正 + 重译」写的那一份，`polish.polish_entries` 已经带 `_clean_fix` 闸门（防倒退 /
防改写）与失败批次原样保留。同一件事不写第二份实现。
"""
from __future__ import annotations

import time

DEADLINE_S = 20.0            #: 整段补译的**总截止**（到点收手，剩下的不补）
TIMEOUT_S = 15.0             #: 单次请求超时


def missing(entries: list[dict]) -> list[int]:
    """需要补译的下标：**EN 与 ZH 都空、而 ASR 有字**。纯函数（判据直接指它）。

    ⚠️ 判据是「**两个都空**」而不是「zh 空」：只英·校档（`en` 有值、`zh` 空）的用户
       明确不要中文，而 `en` 有值也说明这句**不是**漏译。窄这一档是为了别把
       别人的选择改掉 —— 档位那层判据在 `main`（见文件头）。
    """
    out: list[int] = []
    for i, e in enumerate(entries):
        if not (e.get("en") or "").strip() and not (e.get("zh") or "").strip() \
                and (e.get("asr") or "").strip():
            out.append(i)
    return out


def backfill_entries(entries: list[dict], key: str | None, model: str, *,
                     course_terms: list[str] | None = None, domain: str = "",
                     cancel=None, timeout: float = TIMEOUT_S,
                     deadline_s: float = DEADLINE_S) -> tuple[list[dict], dict]:
    """补译漏译句 → `(新 entries, {"need": n, "done": m})`。**绝不外抛**。

    - 只碰漏译的那几条：其余条目**原对象原样**放进返回值（dict 的 `is` 不变），漏译那几条
      换成新 dict（`bf=True` 标记，见 `obsidian_writer` 的 `·补`）—— 入参一个都不改。
      ⚠️ **别依赖返回列表的 `is`**：没事可做时返回入参那个列表，补过之后是新列表
      （同 `note_merge.merge_entries` 的形状）。
    - `need` = 需要补的条数；`done` = **真补出中文**的条数（`need - done` 就是这次没成的）。
    - `cancel` = `threading.Event`，置位后在**批次边界**收手（同 `polish_entries`）。
    """
    st: dict = {"need": 0, "done": 0}
    if not key or not entries:
        return entries, st
    import polish
    batch = polish.POLISH_BATCH
    ids = missing(entries)
    st["need"] = len(ids)
    if not ids:
        return entries, st
    out = list(entries)
    deadline = time.monotonic() + deadline_s
    for start in range(0, len(ids), batch):
        if cancel is not None and cancel.is_set():
            break
        if time.monotonic() >= deadline:
            print(f"⏱ 补译到点（{deadline_s:.0f}s），还剩 {len(ids) - start} 句没补；"
                  f"笔记照常生成", flush=True)
            break
        # ⚠️⚠️ 送进去的 `en` **故意填 ASR 原文**：`polish_entries` 拿 `out[i]["en"]`
        #    当 `_clean_fix` 的**基准**（防改写 / 防倒退），空基准会把任何超过 3 个词的
        #    矫正英文判成"改写"退回 → 英文那一半就白补了。对漏译句来说「直播矫正过的
        #    英文」本来就等于 ASR（这句压根没矫正过）—— 填它既是真话，也把那道闸门
        #    重新变得有意义（要退回就退回 ASR，而下面又把"与 ASR 相同"挡掉）。
        chunk = [dict(entries[i], en=(entries[i].get("asr") or ""))
                 for i in ids[start:start + batch]]
        try:
            got = polish.polish_entries(chunk, key, model, course_terms, domain,
                                        batch=batch, timeout=timeout)
        except Exception as e:                        # noqa: BLE001  polish 自己不抛, 兜底
            print(f"⚠ 补译失败({type(e).__name__}: {str(e)[:60]}); 笔记照旧", flush=True)
            continue
        if len(got) != len(chunk):
            continue                 # 条数对不上就整批不用 —— 绝不错位（同 explain_lost）
        for row, i in zip(got, ids[start:start + batch]):
            zh = (row.get("zh") or "").strip()
            if not zh:
                continue             # 这一句没补出来（批次失败 / 模型没给）→ 原样留着
            src = entries[i]
            new = dict(src, zh=zh, bf=True)
            en = (row.get("en") or "").strip()
            if en and en != (src.get("asr") or "").strip():
                new["en"] = en       # 与 ASR 逐字相同的那份"矫正"不写：那是同一行 noise
            out[i] = new
            st["done"] += 1
    return out, st
