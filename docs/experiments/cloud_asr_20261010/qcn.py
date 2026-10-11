import sys, json, os, io, wave, base64, time, urllib.request, urllib.error, subprocess, numpy as np
D="ocw"; z=np.load(D+"/segs.json.norm.npy"); segs=json.load(open(D+"/segs.json"))
tok=open(os.path.expanduser("~/.classlive/qwen-cn-token")).read().strip()
HOST="dashscope.aliyuncs.com"
def wav(s):
    b=io.BytesIO(); w=wave.open(b,"wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes((np.clip(z[int(s["t0"]*16000):int(s["t1"]*16000)],-1,1)*32767).astype("<i2").tobytes()); w.close(); return b.getvalue()
def opus(s): return subprocess.run(["ffmpeg","-v","error","-i","-","-c:a","libopus","-b:a","24k","-f","ogg","-"],input=wav(s),capture_output=True).stdout
def call(model, s, fmt="wav"):
    data = wav(s) if fmt=="wav" else opus(s); mime = "audio/wav" if fmt=="wav" else "audio/ogg"
    if model.startswith("qwen-audio"):
        body={"model":model,"input":{"messages":[{"role":"user","content":[{"type":"input_audio","input_audio":{"data":f"data:{mime};base64,"+base64.b64encode(data).decode()}}]}]},"parameters":{"format":fmt if fmt=="wav" else "opus","sample_rate":"16000","language_hints":["en"]}}
    else:
        body={"model":model,"input":{"messages":[{"role":"user","content":[{"audio":f"data:{mime};base64,"+base64.b64encode(data).decode()}]}]},"parameters":{"asr_options":{"language":"en","enable_itn":False}}}
    req=urllib.request.Request(f"https://{HOST}/api/v1/services/aigc/multimodal-generation/generation",data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+tok,"Content-Type":"application/json","X-DashScope-SSE":"disable","User-Agent":"classlive-eval/1.0"})
    t=time.time(); r=json.load(urllib.request.urlopen(req,timeout=60)); return r, time.time()-t
def text_of(r):
    o=r.get("output",{})
    if "text" in o: return o["text"]
    if "sentence" in r: return r["sentence"]["text"]
    try: return o["choices"][0]["message"]["content"][0]["text"]
    except Exception: return None
if __name__=="__main__":
    for m in ["qwen3-asr-flash","qwen-audio-3.1-asr-flash","qwen-audio-3.0-asr-flash"]:
        try:
            r,l=call(m,segs[1]); print(m,round(l,2),(text_of(r) or json.dumps(r))[:120], r.get("usage"))
        except urllib.error.HTTPError as e: print(m,e.code,e.read()[:200])
