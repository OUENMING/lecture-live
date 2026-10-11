import sys,json,os,io,wave,base64,subprocess,urllib.request,numpy as np
z=np.load("ocw/segs.json.norm.npy");segs=json.load(open("ocw/segs.json"))
tok=open(os.path.expanduser("~/.classlive/cf-ai-token")).read().strip();acc=open(os.path.expanduser("~/.classlive/cf-account-id")).read().strip()
fmt=sys.argv[1]; dur=0
for s in segs[10:30]:
    b=io.BytesIO();w=wave.open(b,"wb");w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes((np.clip(z[int(s["t0"]*16000):int(s["t1"]*16000)],-1,1)*32767).astype("<i2").tobytes());w.close()
    d=b.getvalue() if fmt=="wav" else subprocess.run(["ffmpeg","-v","error","-i","-","-c:a","libopus","-b:a","24k","-f","ogg","-"],input=b.getvalue(),capture_output=True).stdout
    dur+=s["t1"]-s["t0"]
    urllib.request.urlopen(urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/openai/whisper-large-v3-turbo",data=json.dumps({"audio":base64.b64encode(d).decode(),"language":"en"}).encode(),headers={"Authorization":"Bearer "+tok,"Content-Type":"application/json","User-Agent":"x"}),timeout=60).read()
print(fmt,"20 段, 音频",round(dur,1),"s → 官方应耗",round(dur/60*46.63,1),"neurons")
