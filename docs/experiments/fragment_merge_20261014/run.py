"""碎句（跨段半句）的三种译法离线对照：B 单译 / L 带下一段草稿当「后文」/ M 等下一段合并。

用法（在仓库根）:
  ClassLive.app/Contents/MacOS/python docs/experiments/fragment_merge_20261014/run.py OUT.json A.live.jsonl B.live.jsonl …
产物（OUT.json）含课堂原文，**别放进仓库**。会联网（DeepSeek，走 `load_test_key()`，即 test1 通行码）。

- 碎句 = 定稿没有句末 `.?!`（或是 carry 超时的 `…`），且下一条定稿属于下一段。
- L 的「后文」= 碎句定稿上屏那一刻、下一段**已经在屏上的草稿**（`.live.jsonl` 的 draft 事件，t ≤ 定稿 t）——
  不多等一秒；没有草稿时 L 退化成 B（记 `la_empty`）。
- 评判：同一个模型、另一套 prompt，三个版本**打乱顺序、不给标签**，按「整段 F+C 的中文读起来对不对、顺不顺」1–5 分。
"""
import json, os, random, re, sys, threading, concurrent.futures as cf
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
import cloud_translator as CT                      # noqa: E402

END = ('.', '?', '!')
N_PER_FILE = 40

def pairs(path):
    ev = [json.loads(l) for l in open(path, encoding="utf-8")]
    fin = [e for e in ev if e['kind'] == 'final']
    dr = [e for e in ev if e['kind'] == 'draft']
    out = []
    for i in range(len(fin) - 1):
        f, c = fin[i], fin[i + 1]
        ft = f['text'].strip()
        if ft.endswith(END) and not ft.endswith('…'):
            continue
        if c.get('seg') == f.get('seg'):
            continue
        cand = [d for d in dr if d.get('seg') == c.get('seg') and d['t'] <= f['t']]
        out.append(dict(src=Path(path).name, i=i, F=ft, C=c['text'].strip(), live_F=f.get('zh', ''),
                        live_C=c.get('zh', ''), la=cand[-1]['text'] if cand else '',
                        ctx=[x['text'] for x in fin[max(0, i - 20):i]]))
    return out

TAIL = ("Now translate the ASR utterance above into Chinese and output exactly:\n"
        "ZH: <Chinese translation of the CORRECTED sentence>\n"
        "EN: <same utterance with ASR mishearings fixed>\n"
        "Do not repeat the course-terms list. No headings.")
LA = ("The utterance above was cut off mid-sentence. The speaker goes on with the words below — "
      "they are NOT part of the utterance: do not translate them and do not add their meaning, "
      "use them only to decide how the cut-off part should read in Chinese.\n"
      "Next words: {la}\n\n")

def tr(t, en, ctx, la=""):
    user = t._user_content(en, ctx, (LA.format(la=la) if la else "") + TAIL)
    msgs = [{"role": "system", "content": CT.SYSTEM_PROMPT_CLOUD}, {"role": "user", "content": user}]
    txt = "".join(t._stream_chat(msgs, max_tokens=260))
    m = re.search(r"ZH:\s*(.*)", txt)
    return (m.group(1).strip() if m else txt.strip())

JUDGE = """你是英译中字幕的评审。下面是课堂上老师说的一段英文（它在字幕里被切成了前后两行），以及三个中文版本。
每个版本都是观众**按顺序读到的两行（或一行）中文**。请分别打分：
- acc：意思对不对（1–5，5=完全准确；漏译、错译、凭空补出原文没有的意思都扣分）
- flu：读起来顺不顺、像不像完整通顺的中文（1–5）
只输出 JSON：{{"A":{{"acc":n,"flu":n}},"B":{{...}},"C":{{...}}}}

前文（仅供理解）：{ctx}
英文：{en}
A：{a}
B：{b}
C：{c}"""

def judge(t, p, vers):
    keys = list(vers); random.Random(p['src'] + str(p['i'])).shuffle(keys)
    lab = dict(zip("ABC", keys))
    msg = JUDGE.format(ctx=" ".join(p['ctx'][-3:]), en=p['F'] + " " + p['C'],
                       a=vers[lab['A']], b=vers[lab['B']], c=vers[lab['C']])
    txt = "".join(t._stream_chat([{"role": "user", "content": msg}], max_tokens=120))
    j = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    return {lab[k]: v for k, v in j.items()}

def one(t, p):
    p['B_F'] = tr(t, p['F'], p['ctx'])
    p['B_C'] = tr(t, p['C'], p['ctx'] + [p['F']])
    p['L_F'] = tr(t, p['F'], p['ctx'], p['la']) if p['la'] else p['B_F']
    p['M'] = tr(t, p['F'] + " " + p['C'], p['ctx'])
    vers = {"B": p['B_F'] + " ／ " + p['B_C'], "L": p['L_F'] + " ／ " + p['B_C'], "M": p['M']}
    p['score'] = judge(t, p, vers)
    return p

if __name__ == "__main__":
    out, files = sys.argv[1], sys.argv[2:]
    ps = []
    for f in files:
        a = pairs(f); random.Random(7).shuffle(a); ps += a[:N_PER_FILE]
    t = CT.CloudTranslator(CT.load_test_key(), "deepseek-flash")
    res, lock = [], threading.Lock()
    with cf.ThreadPoolExecutor(6) as ex:
        for fut in cf.as_completed([ex.submit(one, t, p) for p in ps]):
            try:
                r = fut.result()
            except Exception as e:                       # noqa: BLE001
                print("失败", type(e).__name__, e); continue
            with lock:
                res.append(r)
                if len(res) % 20 == 0: print(len(res), flush=True)
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for k in "BLM":
        acc = [r['score'][k]['acc'] for r in res]; flu = [r['score'][k]['flu'] for r in res]
        print(k, f"acc {sum(acc)/len(acc):.2f}  flu {sum(flu)/len(flu):.2f}  n={len(res)}")
    print("L 有后文", sum(1 for r in res if r['la']), "/", len(res),
          " L 与 B 碎句译文不同", sum(1 for r in res if r['la'] and r['L_F'] != r['B_F']))
