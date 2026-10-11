"""逐段（生产切段 322 段）跑 Groq / Mistral。串行 + 限速，记录延迟与原始返回。"""
import sys, json, os, io, wave, time, uuid, urllib.request, urllib.error, numpy as np
D, which = sys.argv[1], sys.argv[2]
z = np.load(D + "/segs.json.norm.npy"); segs = json.load(open(D + "/segs.json"))
def wav(s):
    b = io.BytesIO(); w = wave.open(b, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes((np.clip(z[int(s["t0"]*16000):int(s["t1"]*16000)], -1, 1)*32767).astype("<i2").tobytes()); w.close(); return b.getvalue()
CFG = {"groq": ("https://api.groq.com/openai/v1/audio/transcriptions", "groq-token", {"model": "whisper-large-v3", "language": "en", "response_format": "json", "temperature": "0"}, 3.1),
       "cohere": ("https://api.cohere.com/v2/audio/transcriptions", "cohere-token", {"model": "cohere-transcribe-03-2026", "language": "en"}, 1.0),
       "mistral": ("https://api.mistral.ai/v1/audio/transcriptions", "mistral-token", {"model": "voxtral-mini-2602", "language": "en"}, 1.1)}
URL, tokf, fields, gap = CFG[which]
tok = open(os.path.expanduser("~/.classlive/" + tokf)).read().strip()
def multipart(fields, data):
    bd = uuid.uuid4().hex; out = b""
    for k, v in fields.items(): out += f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    out += f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode() + data + f"\r\n--{bd}--\r\n".encode()
    return out, "multipart/form-data; boundary=" + bd
res = []; last = 0
for i, s in enumerate(segs):
    body, ct = multipart(fields, wav(s)); txt, lat, err = "", None, None
    for k in range(6):
        time.sleep(max(0, gap - (time.time() - last))); last = time.time(); t = time.time()
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(URL, data=body, headers={"Authorization": "Bearer " + tok, "Content-Type": ct, "User-Agent": "classlive-eval/1.0"}), timeout=60))
            txt, lat = r.get("text", "").strip(), time.time() - t; break
        except urllib.error.HTTPError as e:
            err = f"{e.code} {e.read()[:200]!r}"; (globals().__setitem__("gap", max(gap, 12.5)) if e.code == 429 else None); time.sleep(13 if e.code == 429 else 2)
        except Exception as e:
            err = repr(e); time.sleep(2)
    res.append({"i": i, "text": txt, "lat": lat, "err": None if lat else err})
    if i % 40 == 0: print(i, lat, err, txt[:60], flush=True)
json.dump(res, open(f"{D}/raw_{which}.json", "w"))
open(f"{D}/hyp_{which}.txt", "w").write(" ".join(r["text"] for r in res))
lat = sorted(r["lat"] for r in res if r["lat"])
print(f"{which} 失败 {sum(r['lat'] is None for r in res)} · 延迟 p50 {lat[len(lat)//2]:.2f}s p95 {lat[int(len(lat)*.95)]:.2f}s", flush=True)
