import sys, json, time, urllib.error
import qcn
model, tag, fmt = sys.argv[1], sys.argv[2], sys.argv[3]
res=[]
for i,s in enumerate(qcn.segs):
    t, l, err, u = "", None, None, None
    for k in range(3):
        try:
            r, l = qcn.call(model, s, fmt); t = (qcn.text_of(r) or "").strip(); u = r.get("usage"); break
        except urllib.error.HTTPError as e:
            b = e.read()[:200]; err = f"{e.code} {b!r}"
            if b"NO_WORDS" in b or b"no words" in b.lower(): l = 0.0; break
            time.sleep(2)
        except Exception as e: err = repr(e); time.sleep(2)
    res.append({"i":i,"text":t,"lat":l,"err":err,"usage":u})
json.dump(res,open(f"ocw/raw_{tag}.json","w")); open(f"ocw/hyp_{tag}.txt","w").write(" ".join(r["text"] for r in res))
lat=sorted(r["lat"] for r in res if r["lat"])
print(f"{tag} 失败 {sum(r['lat'] is None for r in res)} p50 {lat[len(lat)//2]:.2f} p95 {lat[int(len(lat)*.95)]:.2f} max {lat[-1]:.1f}", flush=True)
