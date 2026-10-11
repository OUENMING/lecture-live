"""Speechmatics 批量 Jobs API（形态 A）：逐段提交 -> 轮询 -> 取 txt。
用法: python vendor_speechmatics_batch.py <model> <n_segs> <out_prefix>   (取均匀分布的 n 段；n=0 全部)"""
import sys, json, os, io, time, numpy as np, soundfile as sf, httpx
model, n, outp = sys.argv[1], int(sys.argv[2]), sys.argv[3]
D = os.path.expanduser("~/.classlive/eval/ocw"); z = np.load(D + "/segs.json.norm.npy", mmap_mode="r"); segs = json.load(open(D + "/segs.json"))
idx = list(range(len(segs))) if n == 0 else [int(i) for i in np.linspace(0, len(segs)-1, n)]
tok = open(os.path.expanduser("~/.classlive/speechmatics-token")).read().strip()
H = {"Authorization": "Bearer " + tok}; B = "https://eu1.asr.api.speechmatics.com/v2"
c = httpx.Client(timeout=30, headers=H); res = []
for i in idx:
    s = segs[i]; b = io.BytesIO()
    sf.write(b, np.asarray(z[int(s["t0"]*16000):int(s["t1"]*16000)], dtype="float32"), 16000, format="OGG", subtype="OPUS", compression_level=0.88)
    cfg = {"type": "transcription", "transcription_config": {"language": "en", "model": model, **({} if model == "melia-1" else {"enable_entities": False})}}
    t = time.time(); txt = err = None
    try:
        r = c.post(B + "/jobs", files={"data_file": ("a.ogg", b.getvalue(), "audio/ogg")}, data={"config": json.dumps(cfg)}); r.raise_for_status(); jid = r.json()["id"]; t_sub = time.time() - t
        while True:
            st = c.get(f"{B}/jobs/{jid}").json()["job"]["status"]
            if st == "done": break
            if st in ("rejected", "deleted", "expired"): raise RuntimeError(st)
            time.sleep(0.2)
        txt = c.get(f"{B}/jobs/{jid}/transcript", params={"format": "txt"}).text.strip(); lat = time.time() - t
        c.delete(f"{B}/jobs/{jid}")
    except Exception as e: err = repr(e); lat = None
    res.append({"i": i, "text": txt or "", "lat": lat, "t_sub": t_sub if not err else None, "err": err})
json.dump(res, open(outp + "_raw.json", "w")); open(outp + "_hyp.txt", "w").write(" ".join(r["text"] for r in res))
l = sorted(r["lat"] for r in res if r["lat"]); print(f"{model} n={len(res)} 失败 {sum(r['lat'] is None for r in res)} p50 {l[len(l)//2]:.2f} p95 {l[int(len(l)*.95)]:.2f} max {l[-1]:.2f}; errs {[r['err'] for r in res if r['err']][:2]}")
