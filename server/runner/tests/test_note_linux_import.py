# test_note_linux_import.py —— Linux 可行性实测：子进程塞 AppKit/Foundation/objc/mlx/mlx_lm
# = None（import 必失败）→ import main 必挂 → note_job 走底层模块 fallback，完整 run_note 必须过。
# 跑法（仓库根）：python3 server/runner/tests/test_note_linux_import.py
#   （或 python3 -m unittest discover -s server/runner/tests 里的一项）
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

_SCRIPT = r"""
import json, os, sys
from pathlib import Path

for m in ("AppKit", "Foundation", "objc", "mlx", "mlx_lm"):
    sys.modules[m] = None                      # import 必失败

home = Path(os.environ["HOME"])

# 证据一：桌面顶层链 import main 必挂（overlay/power/notice → AppKit/Foundation）
try:
    import main
    print("MAIN_IMPORT=ok")                    # 有些平台能 import 成也不算失败
except Exception as e:
    print(f"MAIN_IMPORT=fail:{type(e).__name__}")

from server.runner import note_job
import llm_client

CALLS = []
def chat(key, model, system, user, **kw):
    CALLS.append(str(kw.get("call_class") or ""))
    if kw.get("return_meta"):
        return {"sections": []}, "stop", 2
    return {"sections": []}
llm_client.chat_json = chat

ROWS = [
    {"seq": 1, "t": 3, "en": "Price elasticity of demand.", "zh": "需求的价格弹性。", "src": "asr"},
    {"seq": 2, "t": 61, "en": "It measures responsiveness of quantity.", "zh": "", "src": "asr"},
]

class FakeClient:
    def __init__(self): self.oks = []; self.fails = []
    def lines(self, h, s, after=0): return [r for r in ROWS if r["seq"] > after]
    def complete_ok(self, job, md, html, meta):
        self.oks.append((len(md), len(html), dict(meta)))
    def complete_fail(self, job, reason): self.fails.append(str(reason))

job = {"job_id": "jlin", "kind": "note", "attempt": 1, "lease": "L",
       "input": {"hash": "0123456789abcdef", "session_id": "s_lin",
                 "course": "ECON10770", "tz": "Europe/Dublin",
                 "started_at": "2026-11-20T09:00:00Z", "ended_at": "2026-11-20T10:00:00Z",
                 "lines_total": 2, "level": "normal"}}
c = FakeClient()
note_job.run_note(c, job, home=home, log=lambda s: print("  |", s))
out = {"oks": len(c.oks), "fails": c.fails,
       "md_len": c.oks[0][0] if c.oks else 0, "html_len": c.oks[0][1] if c.oks else 0,
       "meta": c.oks[0][2] if c.oks else {}, "calls": len(CALLS)}
Path(os.environ["OUT"]).write_text(json.dumps(out), encoding="utf-8")
print("LINUX_IMPORT_TEST_DONE")
"""


class LinuxImportTest(unittest.TestCase):
    def test_full_run_note_with_mac_modules_stubbed(self):
        home = tempfile.mkdtemp(prefix="cl-runner-linux-")
        cred = Path(home) / ".classlive" / "credentials"
        cred.parent.mkdir(parents=True, exist_ok=True)
        cred.write_text("sk-test-runner-key", encoding="utf-8")   # sk- 形：Jev 不联网
        outp = Path(home) / "out.json"
        env = dict(os.environ)
        env.update({"HOME": home, "RUNNER_HOME": home,
                    "CLASSLIVE_RUNNER_NO_MAIN": "1",
                    "OUT": str(outp),
                    "PYTHONPATH": str(_REPO)})
        env.pop("DEEPSEEK_API_KEY", None)
        env.pop("OBSIDIAN_VAULT", None)
        r = subprocess.run([sys.executable, "-c", _SCRIPT], env=env,
                           capture_output=True, text=True, timeout=300, cwd=str(_REPO))
        tail = "\n".join((r.stdout or "").splitlines()[-8:])
        self.assertEqual(r.returncode, 0, f"子进程挂了：\n{tail}\n{r.stderr[-2000:]}")
        self.assertIn("LINUX_IMPORT_TEST_DONE", r.stdout)
        # main 能不能 import 取决于装了哪些依赖（app 自带 python 能、裸 python3 缺 numpy 不能）——
        # 两条路都必须交得出笔记，所以只断言走了其中一条，不钉哪一条（Claude 11-20 验收时发现原断言依赖环境）
        self.assertRegex(r.stdout, r"MAIN_IMPORT=(ok|fail)")
        got = json.loads(outp.read_text(encoding="utf-8"))
        self.assertEqual(got["oks"], 1, f"没交回成功：{got}")
        self.assertEqual(got["fails"], [])
        self.assertGreater(got["md_len"], 200)
        self.assertGreater(got["html_len"], 200)
        self.assertGreaterEqual(got["calls"], 1)      # 复习层出口被假模型接管


if __name__ == "__main__":
    unittest.main()
