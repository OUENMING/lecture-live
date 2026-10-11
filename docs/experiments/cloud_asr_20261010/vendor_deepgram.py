"""Deepgram Nova-3 评测（key 从 ~/.classlive/deepgram-token 读，不打印）。
用法：
  python vendor_deepgram.py a <OUT> <host> <n_segs|all> [ogg|wav]    # 形态 A 逐段 HTTP
  python vendor_deepgram.py b <OUT> <host> <start_s> <dur_s|all> [speed]  # 形态 B 流式 WebSocket
语料 ~/.classlive/eval/ocw。文档：
  https://developers.deepgram.com/docs/finalize  /docs/audio-keep-alive  /reference/regional-endpoints
"""
import sys, os, io, json, time, asyncio
import numpy as np, soundfile as sf, httpx
D = os.path.expanduser("~/.classlive/eval/ocw")
TOK = open(os.path.expanduser("~/.classlive/deepgram-token")).read().strip()
z = np.load(D + "/segs.json.norm.npy", mmap_mode="r"); segs = json.load(open(D + "/segs.json"))
PARAMS = {"model": "nova-3", "language": "en", "smart_format": os.environ.get("DG_SF", "true"), "mip_opt_out": "true"}

def enc(s, fmt):
    x = np.asarray(z[int(s["t0"]*16000):int(s["t1"]*16000)], dtype="float32"); b = io.BytesIO()
    if fmt == "ogg": sf.write(b, x, 16000, format="OGG", subtype="OPUS", compression_level=0.88); ct = "audio/ogg"
    else: sf.write(b, x, 16000, format="WAV", subtype="PCM_16"); ct = "audio/wav"
    return b.getvalue(), ct

def mode_a(out, host, n, fmt):
    os.makedirs(out, exist_ok=True)
    sel = segs if n == "all" else segs[:int(n)]
    res = []
    with httpx.Client(timeout=30, headers={"Authorization": "Token " + TOK}) as c:
        for i, s in enumerate(sel):
            data, ct = enc(s, fmt); txt, lat, err = "", None, None
            for k in range(3):
                t = time.time()
                try:
                    r = c.post(f"https://{host}/v1/listen", params=PARAMS, content=data, headers={"Content-Type": ct})
                    lat = time.time() - t
                    if r.status_code != 200: err = f"{r.status_code} {r.text[:100]}"; lat = None; time.sleep(2); continue
                    j = r.json(); txt = j["results"]["channels"][0]["alternatives"][0]["transcript"].strip(); err = None; break
                except Exception as e: err = repr(e); lat = None; time.sleep(2)
            res.append({"i": i, "text": txt, "lat": lat, "err": err, "bytes": len(data)})
    tag = f"A{'_nosf' if PARAMS['smart_format']=='false' else ''}_{host.split('.')[1] if host.count('.')>2 else 'global'}_{fmt}_{len(sel)}"
    json.dump(res, open(f"{out}/raw_{tag}.json", "w"))
    open(f"{out}/hyp_{tag}.txt", "w").write(" ".join(r["text"] for r in res))
    lat = sorted(r["lat"] for r in res if r["lat"])
    print(tag, "失败", sum(r["lat"] is None for r in res), "p50 %.3f p95 %.3f max %.3f first %.3f" % (lat[len(lat)//2], lat[int(len(lat)*.95)], lat[-1], res[0]["lat"] or -1), flush=True)

async def mode_b(out, host, start, dur, speed):
    import websockets
    os.makedirs(out, exist_ok=True)
    total = len(z) / 16000
    end = total if dur == "all" else min(total, start + float(dur))
    q = "&".join(f"{k}={v}" for k, v in {**PARAMS, "encoding": "linear16", "sample_rate": 16000, "channels": 1, "interim_results": "true"}.items())
    url = f"wss://{host}/v1/listen?{q}"
    sel = [(i, s) for i, s in enumerate(segs) if s["t0"] >= start and s["t1"] <= end]
    brk = sorted({int(s["t1"]*16000) for _, s in sel} | {int(end*16000)})
    finals = []   # (arrival_mono, abs_start, abs_end, text, from_finalize)
    fin_sent = {} # seg i -> mono time of Finalize send (right after t1 chunk)
    fin_ack = []  # (arrival, mono)
    log = open(f"{out}/b_log.txt", "a")
    def L(*a): print(time.strftime("%H:%M:%S"), *a, file=log, flush=True)
    pos = int(start * 16000); t_wall0 = time.monotonic(); pos0 = pos
    byt1 = {int(s["t1"]*16000): i for i, s in sel}
    while pos < int(end*16000):
        base = pos / 16000
        try:
            async with websockets.connect(url, additional_headers={"Authorization": "Token " + TOK}, max_size=None, ping_interval=None) as ws:
                L("connected at", base)
                async def recv():
                    async for m in ws:
                        arr = time.monotonic()
                        if isinstance(m, bytes): continue
                        j = json.loads(m)
                        if j.get("type") == "Results":
                            if j.get("is_final") or j.get("from_finalize"):
                                alt = j["channel"]["alternatives"][0]
                                finals.append((arr, base + j["start"], base + j["start"] + j["duration"], alt["transcript"].strip(), bool(j.get("from_finalize")), bool(j.get("speech_final"))))
                        elif j.get("type") not in ("Metadata", "SpeechStarted", "UtteranceEnd"): L("msg", m[:200])
                rt = asyncio.create_task(recv())
                while pos < int(end*16000):
                    nxt = min([b for b in brk if b > pos] + [pos + 1600])
                    nxt = min(nxt, pos + 1600)
                    chunk = (np.clip(np.asarray(z[pos:nxt]), -1, 1) * 32767).astype("<i2").tobytes()
                    due = t_wall0 + (nxt - pos0) / 16000 / speed
                    d = due - time.monotonic()
                    if d > 0: await asyncio.sleep(d)
                    await ws.send(chunk); pos = nxt
                    if pos in byt1:
                        fin_sent[byt1[pos]] = time.monotonic()
                        await ws.send(json.dumps({"type": "Finalize"}))
                await asyncio.sleep(3)
                await ws.send(json.dumps({"type": "CloseStream"})); await asyncio.sleep(2); rt.cancel()
        except Exception as e:
            L("connection ended", repr(e)[:200], "at pos", pos / 16000)
            if pos >= int(end*16000): break
            await asyncio.sleep(1)
            t_wall0 = time.monotonic() - (pos - pos0) / 16000 / speed  # 重连后继续按实时
    # 归并：按时间戳中点分配到段
    hyp = {i: [] for i, _ in sel}; lastarr = {i: None for i, _ in sel}; ffarr = {i: None for i, _ in sel}; dropped = []
    for arr, a, b, txt, ff, sfin in finals:
        if not txt: continue
        mid = (a + b) / 2; hit = None
        for i, s in sel:
            if s["t0"] - 0.5 <= mid <= s["t1"] + 0.5: hit = i; break
        if hit is None: dropped.append(txt); continue
        hyp[hit].append(txt); lastarr[hit] = max(lastarr[hit] or 0, arr)
    # from_finalize 到达时间：Finalize 发出后第一条 from_finalize
    ffs = sorted(arr for arr, _, _, _, ff, _ in finals if ff)
    for i, ts in fin_sent.items():
        nxt = [a for a in ffs if a >= ts]; ffarr[i] = nxt[0] - ts if nxt and (nxt[0] - ts) < 10 else None
    rows = []
    for i, s in sel:
        ls = (lastarr[i] - fin_sent[i]) if (lastarr[i] and i in fin_sent) else None
        rows.append({"i": i, "text": " ".join(hyp[i]), "lat_last_final": ls, "lat_from_finalize": ffarr.get(i)})
    tag = f"B_{host.split('.')[1] if host.count('.')>2 else 'global'}_{int(start)}_{dur}_x{speed:g}"
    json.dump({"rows": rows, "dropped": dropped, "finals": finals}, open(f"{out}/raw_{tag}.json", "w"))
    open(f"{out}/hyp_{tag}.txt", "w").write(" ".join(r["text"] for r in rows))
    for key in ("lat_last_final", "lat_from_finalize"):
        v = sorted(r[key] for r in rows if r[key] is not None)
        if v: print(tag, key, "n", len(v), "/", len(rows), "p50 %.3f p95 %.3f max %.3f" % (v[len(v)//2], v[int(len(v)*.95)], v[-1]))
    print(tag, "dropped finals", len(dropped), "empty segs", sum(1 for r in rows if not r["text"]))

if __name__ == "__main__":
    m = sys.argv[1]
    if m == "a": mode_a(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else "ogg")
    else: asyncio.run(mode_b(sys.argv[2], sys.argv[3], float(sys.argv[4]), sys.argv[5], float(sys.argv[6]) if len(sys.argv) > 6 else 1.0))
