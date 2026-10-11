"""DeepInfra 逐段（同 322 段），串行 = 生产形状（定稿线程一次一段），记延迟与计费。"""
import sys, json, os, io, wave, time, uuid, urllib.request, urllib.error, numpy as np
D, model, tag = sys.argv[1], sys.argv[2], sys.argv[3]
z = np.load(D + "/segs.json.norm.npy"); segs = json.load(open(D + "/segs.json"))
tok = open(os.path.expanduser("~/.classlive/deepinfra-token")).read().strip()
def wav(s):
    b = io.BytesIO(); w = wave.open(b, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes((np.clip(z[int(s["t0"]*16000):int(s["t1"]*16000)], -1, 1)*32767).astype("<i2").tobytes()); w.close(); return b.getvalue()
res = []
for i, s in enumerate(segs):
    bd = uuid.uuid4().hex; body = b""
    for k, v in {"model": model, "language": "en", "response_format": "verbose_json"}.items():
        body += f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode() + wav(s) + f"\r\n--{bd}--\r\n".encode()
    txt, lat, err, usage = "", None, None, None
    for k in range(4):
        t = time.time()
        try:
            r = urllib.request.urlopen(urllib.request.Request("https://api.deepinfra.com/v1/openai/audio/transcriptions", data=body,
                headers={"Authorization": "Bearer " + tok, "Content-Type": "multipart/form-data; boundary=" + bd, "User-Agent": "classlive-eval/1.0"}), timeout=90)
            d = json.load(r); txt, lat = (d.get("text") or "").strip(), time.time() - t; usage = d.get("usage") or d.get("inference_status"); break
        except urllib.error.HTTPError as e: err = f"{e.code} {e.read()[:150]!r}"; time.sleep(3)
        except Exception as e: err = repr(e); time.sleep(3)
    res.append({"i": i, "text": txt, "lat": lat, "err": None if lat else err, "usage": usage})
json.dump(res, open(f"{D}/raw_di_{tag}.json", "w"))
open(f"{D}/hyp_di_{tag}.txt", "w").write(" ".join(r["text"] for r in res))
lat = sorted(r["lat"] for r in res if r["lat"])
print(f"{tag} 失败 {sum(r['lat'] is None for r in res)} · p50 {lat[len(lat)//2]:.2f}s p95 {lat[int(len(lat)*.95)]:.2f}s 最慢 {lat[-1]:.1f}s · usage样例 {res[5]['usage']}", flush=True)
