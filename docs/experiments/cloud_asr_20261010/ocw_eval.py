"""对人工字幕算 WER + 「漏句」（参考里连续 ≥5 词整段没出现）。用仓库自己的 normalize。"""
import sys, json, difflib
ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]  # 仓库根（docs/experiments/<本目录>/x.py）
sys.path.insert(0, str(ROOT / "scripts"))
from stream_metrics import normalize
def score(ref_text, hyp_text, minrun=5):
    r, h = normalize(ref_text), normalize(hyp_text)
    sm = difflib.SequenceMatcher(None, r, h, autojunk=False)
    S = D = I = 0; runs = []; lost = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        a, b = i2 - i1, j2 - j1
        if tag == "replace": S += min(a, b); D += max(0, a - b); I += max(0, b - a)
        elif tag == "delete": D += a
        elif tag == "insert": I += b
        if tag in ("delete", "replace") and a >= minrun and b <= a * 0.3:
            runs.append(" ".join(r[i1:i2])); lost += a
    n = len(r)
    return dict(n=n, wer=(S + D + I) / n, S=S / n, D=D / n, I=I / n, lost_runs=len(runs), lost_words=lost, lost_pct=lost / n, examples=runs[:6])
if __name__ == "__main__":
    ref = " ".join(c["text"] for c in json.load(open(sys.argv[1])))
    for path in sys.argv[2:]:
        name, p = path.split("=", 1)
        s = score(ref, open(p).read())
        print(f"{name:28s} WER {s['wer']:.1%}（替 {s['S']:.1%} 删 {s['D']:.1%} 插 {s['I']:.1%}）· 漏段 {s['lost_runs']} 处 / {s['lost_words']} 词（{s['lost_pct']:.1%}）")
        for e in s["examples"][:3]: print("     漏:", e[:120])
