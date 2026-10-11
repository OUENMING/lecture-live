import json,base64,urllib.request,urllib.error,time,qcn
GL=open("glossary_prompt.txt").read().strip(); res=[]
for i,s in enumerate(qcn.segs):
    body={"model":"qwen-audio-3.1-asr-flash","input":{"messages":[{"role":"system","content":[{"type":"text","text":GL}]},{"role":"user","content":[{"type":"input_audio","input_audio":{"data":"data:audio/ogg;base64,"+base64.b64encode(qcn.opus(s)).decode()}}]}]},"parameters":{"format":"opus","sample_rate":"16000","language_hints":["en"]}}
    t=time.time(); txt=""; lat=None; err=None
    try:
        r=json.load(urllib.request.urlopen(urllib.request.Request(f"https://{qcn.HOST}/api/v1/services/aigc/multimodal-generation/generation",data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+qcn.tok,"Content-Type":"application/json","X-DashScope-SSE":"disable"}),timeout=60))
        txt=(qcn.text_of(r) or "").strip(); lat=time.time()-t
    except urllib.error.HTTPError as e: err=str(e.code)+str(e.read()[:80]); lat=0.0 if b"NO_WORDS" in err.encode() else None
    res.append({"i":i,"text":txt,"lat":lat,"err":err})
json.dump(res,open("ocw/raw_cn_q31_ctx.json","w")); open("ocw/hyp_cn_q31_ctx.txt","w").write(" ".join(r["text"] for r in res))
l=sorted(r["lat"] for r in res if r["lat"]); print("done p50 %.2f"%l[len(l)//2], "fail",sum(r["lat"] is None for r in res))
