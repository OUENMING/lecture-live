"""R2 C0：客户端用 soundfile（libsndfile）压 Opus 后发 Groq，与 WAV / ffmpeg-Opus 基线比 WER。

生产客户端不能依赖 ffmpeg（.app 不打包），所以要确认 soundfile 的 Opus 不伤准确度。
用法（在 ~/.classlive/eval/ 下跑，需要装了 soundfile 的 python）：
    python sf_opus_ab.py ocw <输出目录> [compression_level]
串行、3.1 s 间隔（Groq 免费 20 RPM），322 段约 17 分钟。
"""
import sys, json, os, io, time, uuid, urllib.request, urllib.error
import numpy as np, soundfile as sf
D, OUT = sys.argv[1], sys.argv[2]; lvl = float(sys.argv[3]) if len(sys.argv) > 3 else None
tag = "groq_sf" + (f"_c{lvl:g}" if lvl is not None else "")
z = np.load(D + "/segs.json.norm.npy", mmap_mode="r"); segs = json.load(open(D + "/segs.json"))
def enc(s):
    b = io.BytesIO(); kw = {} if lvl is None else {"compression_level": lvl}
    sf.write(b, np.asarray(z[int(s["t0"]*16000):int(s["t1"]*16000)], dtype="float32"), 16000, format="OGG", subtype="OPUS", **kw)
    return b.getvalue()
tok = open(os.path.expanduser("~/.classlive/groq-token")).read().strip()
def call(data):
    bd = uuid.uuid4().hex; f = {"model": "whisper-large-v3", "language": "en", "response_format": "json", "temperature": "0"}
    body = b"".join(f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in f.items())
    body += f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="a.ogg"\r\nContent-Type: audio/ogg\r\n\r\n'.encode() + data + f"\r\n--{bd}--\r\n".encode()
    r = urllib.request.urlopen(urllib.request.Request("https://api.groq.com/openai/v1/audio/transcriptions", data=body, headers={"Authorization": "Bearer " + tok, "Content-Type": "multipart/form-data; boundary=" + bd, "User-Agent": "classlive-eval/1.0"}), timeout=30)
    return json.load(r).get("text", "")
res = []; last = 0; encs = []
for i, s in enumerate(segs):
    t = time.time(); data = enc(s); encs.append(time.time() - t); txt, lat, err = "", None, None
    for k in range(4):
        time.sleep(max(0, 3.1 - (time.time() - last))); last = time.time(); t = time.time()
        try: txt = call(data).strip(); lat = time.time() - t; break
        except urllib.error.HTTPError as e: err = f"{e.code} {e.read()[:120]!r}"; time.sleep(15 if e.code == 429 else 2)
        except Exception as e: err = repr(e); time.sleep(2)
    res.append({"i": i, "text": txt, "lat": lat, "err": None if lat else err, "bytes": len(data)})
os.makedirs(OUT, exist_ok=True)
json.dump(res, open(f"{OUT}/raw_{tag}.json", "w")); open(f"{OUT}/hyp_{tag}.txt", "w").write(" ".join(r["text"] for r in res))
lat = sorted(r["lat"] for r in res if r["lat"]); encs.sort()
print(f"{tag} 失败 {sum(r['lat'] is None for r in res)} · p50 {lat[len(lat)//2]:.2f}s · 编码 p50 {encs[len(encs)//2]*1000:.0f} ms p95 {encs[int(len(encs)*.95)]*1000:.0f} ms · 平均 {sum(r['bytes'] for r in res)/len(res)/1024:.0f} KB/段", flush=True)
