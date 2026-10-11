"""Opus 24k 上传：Groq（可选课程术语 prompt）/ Cloudflare turbo。串行 = 生产形状。"""
import sys, json, os, io, wave, time, uuid, base64, subprocess, urllib.request, urllib.error, numpy as np
D, which, tag = sys.argv[1], sys.argv[2], sys.argv[3]; prompt = sys.argv[4] if len(sys.argv) > 4 else ""
z = np.load(D + "/segs.json.norm.npy"); segs = json.load(open(D + "/segs.json"))
def opus(s, br="24k"):
    b = io.BytesIO(); w = wave.open(b, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes((np.clip(z[int(s["t0"]*16000):int(s["t1"]*16000)], -1, 1)*32767).astype("<i2").tobytes()); w.close()
    return subprocess.run(["ffmpeg","-v","error","-i","-","-c:a","libopus","-b:a",br,"-f","ogg","-"],input=b.getvalue(),capture_output=True,check=True).stdout
UA = {"User-Agent": "classlive-eval/1.0"}
if which == "groq":
    tok = open(os.path.expanduser("~/.classlive/groq-token")).read().strip(); gap = 3.1
    def call(data):
        bd = uuid.uuid4().hex; f = {"model": "whisper-large-v3", "language": "en", "response_format": "json", "temperature": "0"}
        if prompt: f["prompt"] = prompt
        body = b"".join(f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in f.items())
        body += f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="a.ogg"\r\nContent-Type: audio/ogg\r\n\r\n'.encode() + data + f"\r\n--{bd}--\r\n".encode()
        r = urllib.request.urlopen(urllib.request.Request("https://api.groq.com/openai/v1/audio/transcriptions", data=body, headers={"Authorization": "Bearer " + tok, "Content-Type": "multipart/form-data; boundary=" + bd, **UA}), timeout=30)
        return json.load(r).get("text", "")
else:
    tok = open(os.path.expanduser("~/.classlive/cf-ai-token")).read().strip(); acc = open(os.path.expanduser("~/.classlive/cf-account-id")).read().strip(); gap = 0
    def call(data):
        body = json.dumps({"audio": base64.b64encode(data).decode(), "language": "en"}).encode()
        r = urllib.request.urlopen(urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/openai/whisper-large-v3-turbo", data=body, headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json", **UA}), timeout=30)
        return json.load(r)["result"].get("text", "")
res = []; last = 0
for i, s in enumerate(segs):
    data = opus(s); txt, lat, err = "", None, None
    for k in range(4):
        time.sleep(max(0, gap - (time.time() - last))); last = time.time(); t = time.time()
        try: txt = call(data).strip(); lat = time.time() - t; break
        except urllib.error.HTTPError as e: err = f"{e.code} {e.read()[:120]!r}"; time.sleep(15 if e.code == 429 else 2)
        except Exception as e: err = repr(e); time.sleep(2)
    res.append({"i": i, "text": txt, "lat": lat, "err": None if lat else err, "bytes": len(data)})
json.dump(res, open(f"{D}/raw_{tag}.json", "w")); open(f"{D}/hyp_{tag}.txt", "w").write(" ".join(r["text"] for r in res))
lat = sorted(r["lat"] for r in res if r["lat"])
print(f"{tag} 失败 {sum(r['lat'] is None for r in res)} · p50 {lat[len(lat)//2]:.2f}s p95 {lat[int(len(lat)*.95)]:.2f}s 最慢 {lat[-1]:.1f}s · 平均 {sum(r['bytes'] for r in res)/len(res)/1024:.0f} KB/段", flush=True)
