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
                   on_progress=None, stats: dict | None = None) -> list[dict]:
    """返回精修后的新 entries 列表(不改入参)。无 key / 无内容 -> 原样返回。

    stats: 可选 dict, 函数把 {"batches", "failed", "applied"} 写进去。精修失败是
    **fail-soft** 的 —— 失败的批次静默保留原文, 返回值与全成功时看不出差别;
    调用方(落盘笔记)只能靠这三个数判断这课到底精修了没有。"""
    if not api_key or not entries:
        if stats is not None:
            stats.update({"batches": 0, "failed": 0, "applied": 0})
        return entries
    out = [dict(e) for e in entries]
    n = len(out)
    terms = "\n".join(course_terms or []) or "(无)"
    batches = failed = applied = 0
    for start in range(0, n, batch):
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
        except Exception:                                 # noqa: BLE001
            failed += 1
            if on_progress:
                on_progress(f"  ⚠ 精修批次 [{start}-{start+len(chunk)}] 失败, 保留原样")
            continue
        items = obj.get("items")
        if not isinstance(items, list):
            failed += 1
            if on_progress:
                on_progress(f"  ⚠ 精修批次 [{start}-{start+len(chunk)}] 返回结构异常"
                            f"(items 不是列表), 保留原样")
            continue
        got = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            i = it.get("i")
            if not isinstance(i, int) or not (start <= i < start + len(chunk)):
                continue
            en = " ".join((it.get("en") or "").split())
            zh = " ".join((it.get("zh") or "").split())
            if en:
                out[i]["en"] = en
            if zh:
                out[i]["zh"] = zh
            got += 1
        applied += got
        if on_progress:
            on_progress(f"  [{min(start + len(chunk), n)}/{n}] 精修 +{got}")
    if stats is not None:
        stats.update({"batches": batches, "failed": failed, "applied": applied})
    return out
