"""课后二次精修: 拿整节课的转录重矫正 + 重翻译一遍, 只用它生成笔记。

直播字幕求快 —— 逐句、上下文 5 句、单次 220 token; 落笔记求准, 可以做慢工:
按批喂入, 每批带上**课程领域 + 全课术语表 + 前几行已精修上下文**, 让模型把整批英文改对、
中文重译一遍。实测一堂真实热力学课 65% 的句子直播矫正后与原文逐字相同, 这里能补回来。

⚠️ 输入必须同时给**直播已矫正的 EN** 与**原始 ASR**: 只喂 ASR 会让模型对着原始噪声重新
判断, 把直播已经改对的词又抄回错的(实测 lattice→lacker、statistics→stalcy、
binding→burn)。EN 是基准, ASR 只作证据。

⚠️ 只改**笔记里**的 en/zh; 会话文件里实时落盘的原始 ASR 与直播译文保持不动 ——
原始 ASR 是复核凭据(见 obsidian_writer 的说明), 精修版只是"更好读的那一版"。
失败批次原样保留, 绝不因 API 问题丢内容。
"""
from __future__ import annotations
import json

from translator import domain_block

POLISH_BATCH = 30           # 每次请求精修多少句(太长会漏行/被截断)
POLISH_MAX_TOKENS = 4000

# `on_progress(stage, done, total)` 里 stage 的**唯一定义点**（形状对齐 `prep.py:952`，
# 显示名表对齐 `prep.STAGE_NAME` —— 面板那边照抄这张表就会漂，`entry_panel` 栽过一次）。
# `polish_partial` 是**这一批没成**：它仍然算「这一批做完了」，但要让人当场看见，
# 否则一节精修全失败时进度条照样走到 100%，读起来像成功。
_STAGE_NAME = {"polish": "精修", "polish_partial": "精修（有批次没成）",
               "review": "生成复习层"}
STAGE_NAME = _STAGE_NAME


def progress_text(stage: str, done: int, total: int) -> str:
    """`(stage, done, total)` -> 一行人话。

    ⚠️ 表只有一份（上面那张），这里只做显示。不认识的 stage **显示原名**，
       别显示空白 —— 空白看起来像卡住了（同 `entry_panel.progress_text` 的取舍）。
    """
    label = STAGE_NAME.get(stage, stage)
    return f"{label} {done}/{total}…" if total > 0 else f"{label}…"

POLISH_SYS = """你是英文课堂笔记的精修助手。输入是一门课**一段**的逐句记录, 每行形如:
[序号] 时间戳 | EN: <直播时已做初步矫正的英文> | ASR: <原始语音识别>
另附课程领域、全课术语表、前几行已精修的上下文。

EN 是直播时**已经改过**的版本(可能仍有漏改的听错词); ASR 是底层识别原文, 供你判断。
请逐句做两件事:
1) **以 EN 为基础**继续矫正: 只改你确信仍是听错的词。若某词在 EN 与 ASR 里**都**是错的,
   且音近某个课程术语, 几乎可以断定是听错, 改成该术语。EN 已正确的地方**原样保留**。
2) 依据最终英文重写中文译文, 准确、自然、专业术语用中文习惯表达。

硬性要求:
- **逐句一一对应**: 不合并、不拆分、不增删句; 返回的序号 i 必须与输入一致。
- **禁止倒退**: EN 里已经是正确词的地方原样保留, 绝不退回 ASR 的原始噪声
  (例如 EN 的 lattice 不许变回 lacker、statistics 不许变回 stalcy)。
- **要真的改**: 逐句扫描, 凡 EN 里读不通的片段就是漏改的听错, 必须修正。判断标准是
  **在本课语境里讲不讲得通**, 而不是"它是不是一个真实英文单词" —— 很多听错恰好落在真实
  单词上(如 "chocolate" 是单词, 但物理课里的 "max chocolate properties" 毫无意义)。
  标志是**词在本课语境里讲不通, 而读音接近术语表里的某个词**, 这时无条件换成该术语。
  例: "max chocolate properties" → "macroscopic properties"; "chocolate bonds"
  → "covalent bonds"; "how we measure the stalcy" → "how we measure the statistics";
  "to the as well as uh extensive" → 结合上下文还原成通顺表达。
  EN 与 ASR 相同**不代表**这句就对了 —— 直播同样没改出来, 正需要你补。
- 只有**通顺、在本课语境里讲得通**的句子才与 EN 逐字相同。
- 只依据输入内容与术语表, 不要补充外部知识。
- en / zh 都是**单行**, 不含换行、markdown 或引号。

输出严格 JSON: {"items": [{"i": 序号, "en": "最终英文", "zh": "中文译文"}, ...]}
只输出 JSON。"""


def _chat(key: str, model: str, system: str, user: str,
          max_tokens: int, timeout: float) -> dict:
    import httpx
    payload = {"model": model, "stream": False, "max_tokens": max_tokens,
               "temperature": 0.2, "thinking": {"type": "disabled"},
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    r = httpx.post("https://api.deepseek.com/v1/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json=payload, timeout=timeout)
    r.raise_for_status()
    obj = json.loads(r.json()["choices"][0]["message"]["content"])
    return obj if isinstance(obj, dict) else {}


def polish_entries(entries: list[dict], api_key: str, model: str,
                   course_terms: list[str] | None = None, domain: str = "",
                   batch: int = POLISH_BATCH, timeout: float = 180.0,
                   on_progress=None, stats: dict | None = None,
                   cancel=None) -> list[dict]:
    """返回精修后的新 entries 列表(不改入参)。无 key / 无内容 -> 原样返回。

    stats: 可选 dict, 函数把 {"batches", "failed", "applied"} 写进去。精修失败是
    **fail-soft** 的 —— 失败的批次静默保留原文, 返回值与全成功时看不出差别;
    调用方(落盘笔记)只能靠这三个数判断这课到底精修了没有。

    on_progress: `(stage, done, total)` —— 形状照 `prep.py:952` 的既有约定来,
    stage 的取值见本文件的 `STAGE_NAME`。**逐条消息不是回调的形状** —— 那种写法
    每加一个事件就要多一种字符串, 调用方只能靠认字。

    ⚠️ 这个回调**在工作线程里被调**(收尾阶段)，UI 回写一律回主线程 —— 见
       `entry_panel._make_batch_card` 同款注释。所以它**不许**碰 AppKit、不许 sleep。
    ⚠️ 批次的失败/异常**也走这个回调**(`polish_partial`) —— 只报进度不报失败的话,
       一整节精修全挂掉时进度照样走到 100%, 读起来像成功了。"""
    if not api_key or not entries:
        if stats is not None:
            stats.update({"batches": 0, "failed": 0, "applied": 0})
        return entries
    out = [dict(e) for e in entries]
    n = len(out)
    terms = "\n".join(course_terms or []) or "(无)"
    batches = failed = applied = 0
    #: ⚠️ **第一条失败的原因** —— 见下面 except 里那段（2026-09-29 修）。
    first_err = None
    for start in range(0, n, batch):
        # ⚠️ **取消只在批次边界生效** —— 一次请求已经在飞了就不再掐它（掐了也是一样的
        #    等待时间，却会白白丢掉这一批的结果）。所以「跳过」最多晚一批。
        #    ⚠️ 2026-09-28 由 OCR 审计逼出来的：收尾搬进 worker 之后，**Ctrl+C 只投递到
        #    主线程**，worker 再也收不到 KeyboardInterrupt —— 那道
        #    `except KeyboardInterrupt` 护栏在真流程里是死的。取消标志是唯一能跨线程
        #    让「跑了一半的长活」停下来的东西（卡上那个 [跳过精修] 按钮走同一条路）。
        if cancel is not None and cancel.is_set():
            if stats is not None:
                stats["cancelled"] = True
            break
        batches += 1
        chunk = out[start:start + batch]
        lines = []
        for k, e in enumerate(chunk):
            en = e["en"] or e["asr"]
            asr = e["asr"]
            # EN 是基准(直播已改过); ASR 仅在与 EN 不同时附上作证据。
            row = f"[{start + k}] {e['ts']} | EN: {en}"
            if asr and asr != en:
                row += f" | ASR: {asr}"
            lines.append(row)
        ctx = out[max(0, start - 3):start]
        ctx_block = "\n".join(f"- {c['en']}" for c in ctx if c["en"]) or "(无)"
        user = (f"{domain_block(domain)}"
                f"Course terms:\n{terms}\n\n"
                f"Recent polished context:\n{ctx_block}\n\n"
                f"Transcript batch:\n" + "\n".join(lines))
        try:
            obj = _chat(api_key, model, POLISH_SYS, user,
                        POLISH_MAX_TOKENS, timeout)
        except Exception as e:                            # noqa: BLE001
            failed += 1
            # ⚠️⚠️ **把原因记下来**（2026-09-29 修）。原来只 `failed += 1` ——
            #    整节精修全挂时调用方只看得到 `failed/batches` 两个数字，
            #    分不清是「API key 失效」/「429 或 5xx」/「`POLISH_MAX_TOKENS`
            #    截断导致 JSON 解析失败」—— 排障成本很高。
            #    ⚠️ **只留第一条**：同一种失败通常整节重复，全存会撑爆 stats，
            #       而排障只需要知道"是哪一类的"。
            if first_err is None:
                first_err = f"{type(e).__name__}: {str(e)[:120]}"
            if on_progress:
                on_progress("polish_partial", min(start + len(chunk), n), n)
            continue
        items = obj.get("items")
        if not isinstance(items, list):
            failed += 1
            if on_progress:
                on_progress("polish_partial", min(start + len(chunk), n), n)
            continue
        got = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            i = it.get("i")
            if not isinstance(i, int) or not (start <= i < start + len(chunk)):
                continue                    # 序号非法: 定位不到句子, 只能丢
            # ⚠️⚠️ **类型也要挡**（2026-09-29 修）：模型可能给数字 / 列表
            #    （`{"i": 3, "en": 12}` 或 `"en": ["a","b"]`）——
            #    原来 `(it.get("en") or "").split()` 对它们**直接抛 AttributeError**
            #    （`12 or ""` 是 `12`，不是 `""`），而这一段在 `_chat` 的 try
            #    **之外** → 整节课的精修中断。
            #    ⚠️ 上面已经挡了 `it` 不是 dict —— 同一条纪律，这里挡字段。
            _en, _zh = it.get("en"), it.get("zh")
            en = " ".join(_en.split()) if isinstance(_en, str) else ""
            zh = " ".join(_zh.split()) if isinstance(_zh, str) else ""
            if not (en or zh):
                continue                    # 两个字段都空: 一个字都没写回, 不算精修过
            if en:
                out[i]["en"] = en
            if zh:
                out[i]["zh"] = zh
            got += 1
        applied += got
        if got == 0:
            # 请求成功但**一条都没采纳**(空条目 / 序号全越界 / items 为空)——
            # 对这一批而言与失败等价。不记的话调用方会把它当"精修过了"。
            failed += 1
        if on_progress:
            # ⚠️ `got == 0`（请求成功但**一条都没采纳**）也算这一批没成 —— 与上面两个
            #    失败分支**口径一致**。只报 `polish` 的话，一整节课精修全挂掉时
            #    进度条照样走到 100%，读起来像成功了。（2026-09-28 OCR 审计发现。）
            on_progress("polish_partial" if got == 0 else "polish",
                        min(start + len(chunk), n), n)
    if stats is not None:
        stats.update({"batches": batches, "failed": failed, "applied": applied,
                      "error": first_err or ""})
    return out
