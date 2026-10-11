"""离线复刻生产切段：PeakNormalizer → vad.Segmenter（与生产同一份代码），记下每段的音频起止。"""
import sys, json, subprocess, numpy as np
ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]  # 仓库根（docs/experiments/<本目录>/x.py）
sys.path.insert(0, str(ROOT))
import capture, vad
backs=[]
_o=vad.find_valley_cut
def _w(b,*a,**k):
    c=_o(b,*a,**k); backs.append((len(b)-c)/16000); return c
vad.find_valley_cut=_w
wav, out = sys.argv[1], sys.argv[2]
y = np.frombuffer(subprocess.run(["ffmpeg","-v","error","-i",wav,"-f","f32le","-ac","1","-ar","16000","-"],capture_output=True,check=True).stdout,"<f4")
norm = capture.PeakNormalizer()
pos = [0]; segs = []; normed = []
def end(buf):
    s1 = pos[0] - sg._n
    segs.append({"s1": s1, "n": len(buf)})
sg = vad.Segmenter(lambda b: None, end)
for i in range(0, len(y) - 1599, 1600):
    c = norm.process(y[i:i+1600].copy()); normed.append(c)
    pos[0] = i + 1600
    sg.accept(c)
sg.flush()
z = np.concatenate(normed); np.save(out + ".norm.npy", z)
for s in segs: s["s0"] = s["s1"] - s["n"]
json.dump([{"t0": s["s0"]/16000, "t1": s["s1"]/16000} for s in segs], open(out, "w"))
print(len(segs), "段", sg.diag)

import statistics as st
L=[x['t1']-x['t0'] for x in json.load(open(out))]
print('backoff median',st.median(backs),'mean',st.mean(backs),'max',max(backs),'zero-backoff',sum(b==0 for b in backs),'of',len(backs));print('maxseg',max(L),'segs',len(L))
