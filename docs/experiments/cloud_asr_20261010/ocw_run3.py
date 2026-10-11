import sys, json, os, io, wave, base64, time, urllib.request, numpy as np
from concurrent.futures import ThreadPoolExecutor
ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]  # 仓库根（docs/experiments/<本目录>/x.py）
sys.path.insert(0, str(ROOT))
D = sys.argv[1]; which = sys.argv[2]
z = np.load(D + "/segs.json.norm.npy"); segs = json.load(open(D + "/segs.json"))
cut = lambda s: z[int(s["t0"]*16000): int(s["t1"]*16000)].astype(np.float32)
if which == "cf":
    tok = open(os.path.expanduser("~/.classlive/cf-ai-token")).read().strip()
    acc = open(os.path.expanduser("~/.classlive/cf-account-id")).read().strip()
    URL = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/openai/whisper-large-v3-turbo"
    def one(s):
        b = io.BytesIO(); w = wave.open(b, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((np.clip(cut(s), -1, 1)*32767).astype("<i2").tobytes()); w.close()
        body = json.dumps({"audio": base64.b64encode(b.getvalue()).decode(), "language": "en"}).encode()
        for k in range(4):
            t = time.time()
            try:
                r = json.load(urllib.request.urlopen(urllib.request.Request(URL, data=body, headers={"Authorization": "Bearer "+tok, "Content-Type": "application/json"}), timeout=60))
                return r["result"].get("text", "").strip(), time.time()-t
            except Exception as e:
                time.sleep(2*(k+1))
        return "", None
    with ThreadPoolExecutor(4) as ex: res = list(ex.map(one, segs))
    lat = sorted(l for _, l in res if l); print(f"CF 失败 {sum(l is None for _,l in res)} · 延迟 p50 {lat[len(lat)//2]:.2f}s p95 {lat[int(len(lat)*.95)]:.2f}s")
    open(D + "/hyp_cf.txt", "w").write(" ".join(t for t, _ in res))
elif which == "pk":
    import asr; P = asr.load_asr(os.path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8")); W = asr.load_final_asr(os.path.expanduser("~/models/sherpa-onnx-whisper-turbo"))
    tp = []; tw = []; outp = []; outw = []
    for s in segs:
        a = time.time(); outp.append(P.transcribe(cut(s))); tp.append(time.time()-a)
        a = time.time(); outw.append(W.transcribe(cut(s))); tw.append(time.time()-a)
    open(D + "/hyp_pk.txt", "w").write(" ".join(outp)); open(D + "/hyp_wl.txt", "w").write(" ".join(outw))
    print(f"Parakeet p50 {sorted(tp)[len(tp)//2]:.2f}s · 本地Whisper p50 {sorted(tw)[len(tw)//2]:.2f}s p95 {sorted(tw)[int(len(tw)*.95)]:.2f}s")
elif which == "nem":
    import sherpa_onnx
    md = "/private/tmp/claude-501/-Users-owen/cefdb8c2-0aef-4610-952d-8bb019b96f2f/scratchpad/nemotron/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25"
    rec = sherpa_onnx.OnlineRecognizer.from_transducer(tokens=f"{md}/tokens.txt", encoder=f"{md}/encoder.int8.onnx", decoder=f"{md}/decoder.int8.onnx", joiner=f"{md}/joiner.int8.onnx", num_threads=2, sample_rate=16000, feature_dim=80, enable_endpoint_detection=True, rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=1.2, rule3_min_utterance_length=20, decoding_method="greedy_search")
    s = rec.create_stream(); out = []; comp = 0
    for i in range(0, len(z), 1600):
        s.accept_waveform(16000, z[i:i+1600].astype(np.float32)); a = time.time()
        while rec.is_ready(s): rec.decode_stream(s)
        comp += time.time() - a
        if rec.is_endpoint(s):
            t = rec.get_result(s).strip()
            if t: out.append(t)
            rec.reset(s)
    s.input_finished()
    while rec.is_ready(s): rec.decode_stream(s)
    t = rec.get_result(s).strip(); out.append(t) if t else None
    open(D + "/hyp_nem.txt", "w").write(" ".join(out)); print(f"Nemotron RTF {comp/(len(z)/16000):.3f}（2 线程）")
