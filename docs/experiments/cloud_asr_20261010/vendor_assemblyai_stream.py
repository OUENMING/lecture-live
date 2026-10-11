"""AssemblyAI 流式 v3（形态 B）：1 倍速推 PCM16 16k，在每个生产段 t1 发 ForceEndpoint，记所有 Turn 消息（原始，含到达时间）。
用法: python vendor_assemblyai_stream.py <输出jsonl> <speech_model> <mode|-> <结束秒(默认整节)> [区域 eu|us] [speed=1.0]
文档: https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket
key 从 ~/.classlive/assemblyai-token 读，通过 Authorization 头发送。
"""
import sys, os, json, time, asyncio, urllib.parse
import numpy as np
from websockets.asyncio.client import connect
OUT, MODEL, MODE = sys.argv[1], sys.argv[2], sys.argv[3]
END = float(sys.argv[4]) if len(sys.argv) > 4 else 3040.0
REG = sys.argv[5] if len(sys.argv) > 5 else "eu"
SPEED = float(sys.argv[6]) if len(sys.argv) > 6 else 1.0
D = os.path.expanduser("~/.classlive/eval/ocw")
z = np.load(D + "/segs.json.norm.npy", mmap_mode="r"); segs = json.load(open(D + "/segs.json"))
key = open(os.path.expanduser("~/.classlive/assemblyai-token")).read().strip()
q = {"speech_model": MODEL, "sample_rate": 16000, "encoding": "pcm_s16le"}
if MODE != "-": q["mode"] = MODE
if MODEL.startswith("universal-streaming"): q["format_turns"] = "true"
url = f"wss://streaming.{REG}.assemblyai.com/v3/ws?" + urllib.parse.urlencode(q)
CH = 1600  # 100 ms
async def main():
    log = open(OUT, "w")
    def L(o): log.write(json.dumps(o, ensure_ascii=False) + "\n"); log.flush()
    async with connect(url, additional_headers={"Authorization": key}, max_size=None, ping_interval=20) as ws:
        t0 = None
        async def recv():
            try:
                async for m in ws:
                    w = time.time() - t0 if t0 else -1
                    j = json.loads(m)
                    if j.get("type") == "Turn" and not j.get("end_of_turn"):
                        L({"w": round(w, 3), "type": "Turn", "partial": True, "n": len(j.get("transcript", ""))})
                    else: L({"w": round(w, 3), "msg": j})
            except Exception as e: L({"recv_exc": repr(e)})
        rt = asyncio.create_task(recv())
        await asyncio.sleep(1.0)  # 等 Begin
        t0 = time.time()
        forces = [s["t1"] for s in segs if s["t1"] <= END]
        fi = 0; n = int(END * 16000 // CH)
        for k in range(n):
            target = t0 + (k + 1) * CH / 16000 / SPEED
            d = target - time.time()
            if d > 0: await asyncio.sleep(d)
            x = np.asarray(z[k*CH:(k+1)*CH], dtype="float32") if (k+1)*CH <= len(z) else np.zeros(CH, "float32")
            await ws.send((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
            now_a = (k + 1) * CH / 16000
            while fi < len(forces) and forces[fi] <= now_a:
                await ws.send(json.dumps({"type": "ForceEndpoint"}))
                L({"w": round(time.time() - t0, 3), "force": fi, "t1": forces[fi], "audio_s": now_a}); fi += 1
        await asyncio.sleep(3)
        await ws.send(json.dumps({"type": "Terminate"}))
        try: await asyncio.wait_for(rt, 10)
        except Exception: pass
asyncio.run(main())
