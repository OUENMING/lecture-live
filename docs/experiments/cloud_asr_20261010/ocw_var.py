"""前 LIM 秒切片上比较云端变体（逐段串行请求 = 生产形状，量真实延迟）。"""
import sys, json, os, io, wave, base64, time, urllib.request, numpy as np
D = sys.argv[1]; which = sys.argv[2]; LIM = float(sys.argv[3])
z = np.load(D + "/segs.json.norm.npy"); segs = [s for s in json.load(open(D + "/segs.json")) if s["t1"] <= LIM]
tok = open(os.path.expanduser("~/.classlive/cf-ai-token")).read().strip()
acc = open(os.path.expanduser("~/.classlive/cf-account-id")).read().strip()
BASE = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/"
def wavbytes(x):
    b = io.BytesIO(); w = wave.open(b, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()); w.close(); return b.getvalue()
def post(url, data, ctype):
    for k in range(3):
        t = time.time()
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, data=data, headers={"Authorization": "Bearer " + tok, "Content-Type": ctype}), timeout=60))
            return r["result"], time.time() - t, ""
        except urllib.error.HTTPError as e:
            err = f"{e.code} {e.read()[:200]}"
        except Exception as e:
            err = str(e)[:200]
        time.sleep(2)
    return None, None, err
cut = lambda a, b: z[int(a * 16000): int(b * 16000)].astype(np.float32)
out = []; lats = []; errs = []
if which == "merged30":
    chunks = []; cur = None
    for s in segs:
        if cur and s["t1"] - cur[0] <= 30: cur[1] = s["t1"]
        else:
            if cur: chunks.append(cur)
            cur = [s["t0"], s["t1"]]
    chunks.append(cur); units = chunks
else:
    units = [[s["t0"], s["t1"]] for s in segs]
prev = ""
for a, b in units:
    x = wavbytes(cut(a, b))
    if which == "nova3":
        res, lat, err = post(BASE + "@cf/deepgram/nova-3?smart_format=true&language=en", x, "audio/wav")
        txt = res["results"]["channels"][0]["alternatives"][0]["transcript"] if res else ""
    else:
        body = {"audio": base64.b64encode(x).decode(), "language": "en"}
        if which == "prompt" and prev: body["initial_prompt"] = prev[-300:]
        res, lat, err = post(BASE + "@cf/openai/whisper-large-v3-turbo", json.dumps(body).encode(), "application/json")
        txt = (res or {}).get("text", "").strip()
    if err: errs.append(err)
    if lat: lats.append(lat)
    out.append({"t0": a, "t1": b, "text": txt}); prev = (prev + " " + txt).strip()
json.dump(out, open(f"{D}/var_{which}.json", "w"), ensure_ascii=False)
lats.sort()
print(f"{which}: 单元 {len(units)} · 失败 {len(errs)} · 延迟 p50 {lats[len(lats)//2]:.2f}s p95 {lats[int(len(lats)*.95)]:.2f}s", errs[:2])
