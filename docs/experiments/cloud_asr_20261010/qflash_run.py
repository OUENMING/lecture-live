import sys, json, time, urllib.error
from concurrent.futures import ThreadPoolExecutor
sys.argv=[sys.argv[0]]+sys.argv[1:]
import qflash1 as Q
def text_of(r):
    for p in (("sentence","text"),("output","text"),("output","output","sentence","text")):
        x=r
        try:
            for k in p: x=x[k]
            return x.strip()
        except Exception: pass
    return None
def one(s):
    err=None
    for k in range(5):
        try:
            r,l=Q.call(s); t=text_of(r)
            return {"text":t or "","lat":l,"usage":r.get("usage"),"keys":list(r.keys()) if t is None else None}
        except urllib.error.HTTPError as e: err=f"{e.code} {e.read()[:200]!r}"; time.sleep(3*(k+1))
        except Exception as e: err=repr(e); time.sleep(2)
    return {"text":"","lat":None,"err":err}
with ThreadPoolExecutor(4) as ex: res=list(ex.map(one,Q.segs))
json.dump(res,open("ocw/raw_qflash.json","w")); open("ocw/hyp_qflash.txt","w").write(" ".join(r["text"] for r in res))
lat=sorted(r["lat"] for r in res if r["lat"]); print(f"失败 {sum(r['lat'] is None for r in res)} 无文本 {sum(1 for r in res if r.get('keys'))} p50 {lat[len(lat)//2]:.2f} p95 {lat[int(len(lat)*.95)]:.2f}")
print([r for r in res if r.get('err')][:2]); print(res[1].get("usage"))
