import re, json, sys
src, out = sys.argv[1], sys.argv[2]
txt = open(src, encoding="utf-8", errors="replace").read()
cues = []
for blk in re.split(r"\n\s*\n", txt):
    ls = blk.strip().splitlines()
    if len(ls) < 2 or "-->" not in ls[1]: continue
    a, b = ls[1].split("-->")
    ts = lambda s: sum(float(x) * m for x, m in zip(s.strip().replace(",", ".").split(":"), (3600, 60, 1)))
    body = " ".join(ls[2:])
    body = re.sub(r"\[[^\]]*\]", " ", body)
    body = re.sub(r"\b[A-Z][A-Z .'-]+:\s*", " ", body)   # 说话人标签
    body = body.strip()
    if body: cues.append({"t0": ts(a), "t1": ts(b), "text": body})
json.dump(cues, open(out, "w"), ensure_ascii=False)
print(len(cues), "cues", sum(len(c["text"].split()) for c in cues), "words; last", cues[-1])
