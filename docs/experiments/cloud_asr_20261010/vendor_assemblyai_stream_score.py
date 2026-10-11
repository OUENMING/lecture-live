"""把 vendor_assemblyai_stream.py 的 jsonl 归并成按段 hyp + 延迟统计。
词按 words[].start/end（音频毫秒）的中点分到第一个满足 mid <= t1 的段。
延迟 = 含该段词的最后一条 end_of_turn 到达时刻 − 该段 ForceEndpoint 发出时刻（到达早于发出记 0）。
另给「force 回执延迟」= ForceEndpoint 之后第一条 end_of_turn 到达 − 发出。
用法: python vendor_assemblyai_stream_score.py <jsonl> <输出hyp.txt>
"""
import sys, json, bisect
segs = json.load(open(__import__("os").path.expanduser("~/.classlive/eval/ocw/segs.json")))
t1s = [s["t1"] for s in segs]
force_w = {}; finals = []; err = []
for l in open(sys.argv[1]):
    j = json.loads(l)
    if "force" in j: force_w[j["force"]] = j["w"]
    m = j.get("msg")
    if m and m.get("type") == "Turn" and m.get("end_of_turn"): finals.append((j["w"], m))
    elif m and m.get("type") not in ("Begin", "SpeechStarted", "Turn", "Termination"): err.append(m)
    if "recv_exc" in j: err.append(j)
words = [[] for _ in segs]; last_arr = [None] * len(segs)
for w, m in finals:
    for x in m.get("words", []):
        mid = (x["start"] + x["end"]) / 2000
        k = bisect.bisect_left(t1s, mid)
        if k < len(segs):
            words[k].append((x["start"], x["text"])); last_arr[k] = max(last_arr[k] or 0, w)
hyp = [" ".join(t for _, t in sorted(ws)) for ws in words]
open(sys.argv[2], "w").write(" ".join(hyp))
lat = sorted(max(0, last_arr[k] - force_w[k]) for k in range(len(segs)) if last_arr[k] is not None and k in force_w)
ack = []
fw = sorted(force_w.items(), key=lambda kv: kv[0])
for k, f in fw:
    nxt = [w for w, m in finals if w >= f]
    if nxt: ack.append(min(nxt) - f)
ack.sort()
def p(a, q): return a[min(len(a)-1, int(len(a)*q))] if a else float("nan")
print(f"finals {len(finals)} forces {len(force_w)} 空段 {sum(1 for h in hyp if not h)} 错误消息 {len(err)}")
print("词级延迟(含该段词的最后final): n %d p50 %.2f p95 %.2f max %.2f" % (len(lat), p(lat, .5), p(lat, .95), lat[-1] if lat else 0), f"· 其中提前到达(=0) {sum(1 for x in lat if x==0)}")
print("force 回执延迟(下一条final): n %d p50 %.2f p95 %.2f max %.2f" % (len(ack), p(ack, .5), p(ack, .95), ack[-1] if ack else 0))
for e in err[:3]: print("ERR", str(e)[:200])
