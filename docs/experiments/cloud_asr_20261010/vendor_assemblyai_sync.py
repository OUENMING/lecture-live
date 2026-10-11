"""AssemblyAI Sync STT (形态 A)：逐段 HTTP，串行。
用法: python vendor_assemblyai_sync.py <输出目录> <fmt:wav|pcm> [区域:eu|us] [N段]
文档: https://www.assemblyai.com/docs/sync-stt/getting-started/transcribe-a-short-audio-file
"""
import sys, io, os, json, time
import numpy as np, soundfile as sf, httpx
OUT, FMT = sys.argv[1], sys.argv[2]; REG = sys.argv[3] if len(sys.argv) > 3 else "eu"; N = int(sys.argv[4]) if len(sys.argv) > 4 else None
D = os.path.expanduser("~/.classlive/eval/ocw")
z = np.load(D + "/segs.json.norm.npy", mmap_mode="r"); segs = json.load(open(D + "/segs.json"))[:N]
key = open(os.path.expanduser("~/.classlive/assemblyai-token")).read().strip()
host = f"https://sync.{REG}.assemblyai.com"
H = {"Authorization": key, "X-AAI-Model": "universal-3-5-pro"}
def audio(s):
    x = np.asarray(z[int(s["t0"]*16000):int(s["t1"]*16000)], dtype="float32")
    if FMT == "wav":
        b = io.BytesIO(); sf.write(b, x, 16000, format="WAV", subtype="PCM_16"); return {"audio": ("a.wav", b.getvalue(), "audio/wav"), "config": (None, '{"language_codes":["en"]}', "application/json")}
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    return {"audio": ("a.pcm", pcm, "audio/pcm"), "config": (None, '{"language_codes":["en"],"sample_rate":16000,"channels":1}', "application/json")}
cl = httpx.Client(timeout=60, headers=H)
try: cl.get(host + "/v1/warm")
except Exception as e: print("warm fail", repr(e))
res = []
for i, s in enumerate(segs):
    f = audio(s); txt, lat, err = "", None, None
    for k in range(3):
        t = time.time()
        try:
            r = cl.post(host + "/v1/transcribe", files=f); lat = time.time() - t
            if r.status_code == 200: j = r.json(); txt = j.get("text", "").strip(); break
            err = f"{r.status_code} {r.text[:150]}"; lat = None; time.sleep(2 if r.status_code != 429 else 10)
        except Exception as e: err = repr(e); lat = None; time.sleep(2)
    res.append({"i": i, "text": txt, "lat": lat, "err": None if lat else err, "req_ms": (j.get("request_time_ms") if lat else None)})
tag = f"sync_{FMT}_{REG}"
os.makedirs(OUT, exist_ok=True)
json.dump(res, open(f"{OUT}/raw_{tag}.json", "w")); open(f"{OUT}/hyp_{tag}.txt", "w").write(" ".join(r["text"] for r in res))
lat = sorted(r["lat"] for r in res if r["lat"])
print(tag, "n", len(res), "失败", sum(r["lat"] is None for r in res), "空文本", sum(1 for r in res if r["lat"] and not r["text"]),
      "p50 %.2f p95 %.2f max %.2f" % (lat[len(lat)//2], lat[int(len(lat)*.95)], lat[-1]) if lat else "")
if N and N <= 5:
    for r in res: print(r)
