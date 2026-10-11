"""Speechmatics 实时 WebSocket（形态 B）：1x 实时推整课，在每个生产段 t1 发 ForceEndOfUtterance。
用法: python vendor_speechmatics_rt.py <model:enhanced|standard> <max_delay> <start_s> <dur_s|0=全部> <out_prefix> [speed=1]
key 从 ~/.classlive/speechmatics-token 读，不打印。"""
import sys, json, os, time, asyncio, numpy as np, websockets
model, maxd, start, dur, outp = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
speed = float(sys.argv[6]) if len(sys.argv) > 6 else 1.0
D = os.path.expanduser("~/.classlive/eval/ocw")
z = np.load(D + "/segs.json.norm.npy", mmap_mode="r"); segs = json.load(open(D + "/segs.json"))
end = len(z)/16000 if dur == 0 else start + dur
segs = [s for s in segs if s["t0"] >= start and s["t1"] <= end]
pcm = (np.clip(np.asarray(z[int(start*16000):int(end*16000)]), -1, 1) * 32767).astype("<i2").tobytes()
tok = open(os.path.expanduser("~/.classlive/speechmatics-token")).read().strip()
CH = 1600  # 0.1 s
async def main():
    ws = await websockets.connect("wss://eu.rt.speechmatics.com/v2/", additional_headers={"Authorization": "Bearer " + tok}, max_size=None)
    await ws.send(json.dumps({"message": "StartRecognition", "audio_format": {"type": "raw", "encoding": "pcm_s16le", "sample_rate": 16000},
        "transcription_config": {"language": "en", "model": model, "max_delay": maxd, "max_delay_mode": "flexible", "enable_partials": False,
        "enable_entities": False, "conversation_config": {"end_of_utterance_silence_trigger": 0}}}))
    ev = []  # (wall, msg)
    t_send = {}  # seg idx -> wall FEOU sent
    done = asyncio.Event()
    async def rx():
        async for m in ws:
            j = json.loads(m); ev.append((time.time(), j))
            if j.get("message") == "RecognitionStarted": started.set()
            if j.get("message") == "EndOfTranscript": done.set()
            if j.get("message") in ("Error", "Warning"): print("MSG", {k: v for k, v in j.items() if k != "results"}, flush=True)
    started = asyncio.Event(); r = asyncio.create_task(rx()); await asyncio.wait_for(started.wait(), 20)
    t0 = time.time(); nseg = 0; si = 0; seq = 0
    nchunks = len(pcm) // (CH*2)
    for c in range(nchunks):
        tgt = t0 + (c+1) * 0.1 / speed
        await asyncio.sleep(max(0, tgt - time.time()))
        await ws.send(pcm[c*CH*2:(c+1)*CH*2]); seq += 1
        now_a = start + (c+1)*0.1
        while si < len(segs) and segs[si]["t1"] <= now_a + 1e-6:
            await ws.send(json.dumps({"message": "ForceEndOfUtterance"})); t_send[si] = time.time(); si += 1
    await ws.send(json.dumps({"message": "EndOfStream", "last_seq_no": seq}))
    await asyncio.wait_for(done.wait(), 60); await ws.close()
    # 归并：按 EndOfUtterance 顺序切段
    res = []; cur = []; k = 0; lastw = None
    for w, j in ev:
        m = j.get("message")
        if m == "AddTranscript": cur.append((w, j["metadata"]["transcript"])); lastw = w
        elif m == "EndOfUtterance":
            if k < len(segs):
                txt = "".join(t for _, t in cur).strip()
                res.append({"i": k, "text": txt, "lat": w - t_send[k] if k in t_send else None, "lat_last_add": (lastw - t_send[k]) if cur and k in t_send else None, "forced": j.get("forced")}); k += 1
            cur = []
    tail = "".join(t for _, t in cur).strip()
    if tail and res: res[-1]["text"] += " " + tail
    json.dump({"events": [(w - t0, j) for w, j in ev], "res": res, "sent": {str(a): b - t0 for a, b in t_send.items()}}, open(outp + "_raw.json", "w"))
    open(outp + "_hyp.txt", "w").write(" ".join(x["text"] for x in res))
    lat = sorted(x["lat"] for x in res if x["lat"] is not None)
    print(f"model={model} maxd={maxd} segs={len(segs)} 归并段={len(res)} p50 {lat[len(lat)//2]:.2f} p95 {lat[int(len(lat)*.95)]:.2f} max {lat[-1]:.2f}" if lat else "no lat", flush=True)
asyncio.run(main())
