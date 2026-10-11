import sys, json, os, io, wave, base64, time, urllib.request, urllib.error, numpy as np
D="ocw"; z=np.load(D+"/segs.json.norm.npy"); segs=json.load(open(D+"/segs.json"))
tok=open(os.path.expanduser("~/.classlive/qwen-token")).read().strip()
HOST=sys.argv[1] if len(sys.argv)>1 else "maas.qwencloudapi.com"
def wav(s):
    b=io.BytesIO(); w=wave.open(b,"wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes((np.clip(z[int(s["t0"]*16000):int(s["t1"]*16000)],-1,1)*32767).astype("<i2").tobytes()); w.close(); return b.getvalue()
def call(s):
    body={"model":"qwen-audio-3.1-asr-flash","input":{"messages":[{"role":"user","content":[{"type":"input_audio","input_audio":{"data":"data:audio/wav;base64,"+base64.b64encode(wav(s)).decode()}}]}]},"parameters":{"format":"wav","sample_rate":"16000","language_hints":["en"]}}
    req=urllib.request.Request(f"https://{HOST}/api/v1/services/aigc/multimodal-generation/generation",data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+tok,"Content-Type":"application/json","X-DashScope-SSE":"disable","User-Agent":"classlive-eval/1.0"})
    t=time.time(); r=json.load(urllib.request.urlopen(req,timeout=60)); return r, time.time()-t
if __name__=="__main__":
    try:
        r,l=call(segs[1]); print(round(l,2), json.dumps(r)[:700])
    except urllib.error.HTTPError as e: print(e.code, e.read()[:500])
