"""术语库闭环（2026-10-17 step 3）的判据：**从笔记抽词 → 只追加 → 下节课真的加载**。

⭐ 这一条判据回答的是作者的原话要求，一句一句对着来：
   ·「只收在本课转录或课件中真实出现过的、学科术语（过滤人名/常识词）」
     → `test_只收真出现过的` / `test_人名常识词挡下`
   ·「必须走 prep.py 现有的 append_terms（含锁与去重）」
     → `test_走的是_prep_的追加`（比对 `prep.append_terms` 的返回值语义）
   ·「作者删过的词不能加回来」
     → `test_删过的词不会复活`（墓碑 = `prep-state.json`，与 `prepare()` 同一条路）
   ·「确认下节课开课时实时翻译确实读到这些新术语」
     → `test_下节课的翻译请求里真的有这个词`（本机假服务器抓**真实请求体**）

⚠️ 写端全部在 `tempfile` 里（glossary 目录 + state 路径都是参数）；
   绝不碰真 `glossary/`、真 `~/.classlive/`。
"""
import json
import os
import pathlib
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import note_glossary as ng          # noqa: E402
import prep                         # noqa: E402

ENTRIES = [
    {"ts": "13:31:00", "en": "the marginal cost is the cost of one extra unit",
     "zh": "边际成本是多做一个的成本", "asr": "", "star": False},
    {"ts": "13:32:00", "en": "so the shutdown price equals the minimum average cost",
     "zh": "停业价格等于最小平均成本", "asr": "", "star": False},
]

REVIEW = {
    "sections": [{"heading": "边际成本（marginal cost）",
                  "terms": [{"en": "marginal cost", "zh": "边际成本"},
                            {"en": "Masha", "zh": "同学的名字"}]}],
    "terms": [{"en": "shutdown price", "zh": "停业价格"},
              {"en": "Interesting", "zh": "有趣的"},
              {"en": "opportunity cost", "zh": "机会成本"}],   # 最后一个没在课上出现
}


class Candidates(unittest.TestCase):
    def test_只收真出现过的(self):
        keep, rejected = ng.candidates(REVIEW, ENTRIES)
        self.assertIn("marginal cost", keep)
        self.assertIn("shutdown price", keep)
        self.assertNotIn("opportunity cost", keep, "课上没讲过的词不该进库")
        reasons = dict(rejected)
        self.assertIn("本课转录与课件里都没出现过", reasons.get("opportunity cost", ""))

    def test_人名常识词挡下(self):
        """`type_of` 就是运行时查词缓存里的类型 —— 人名**经常在课上出现**，
        光靠"没出现过"挡不住（`Masha asked a good question` 是真实课堂里的句子）。"""
        keep, rejected = ng.candidates(
            REVIEW, ENTRIES, type_of=lambda t: "person" if t == "Masha" else "")
        self.assertNotIn("Masha", keep)
        self.assertNotIn("Interesting", keep)
        self.assertEqual(dict(rejected).get("Masha"), "人名")

    def test_常识词靠词面挡下_不靠类型(self):
        keep, rejected = ng.candidates(REVIEW, ENTRIES)
        self.assertNotIn("Interesting", keep)
        self.assertTrue(dict(rejected).get("Interesting"), "Interesting 该被挡下")

    def test_课件里出现也算出现过(self):
        class M:
            def verify(self, term):
                return term == "opportunity cost"
        keep, _rej = ng.candidates(REVIEW, ENTRIES, materials=M())
        self.assertIn("opportunity cost", keep)

    def test_去重(self):
        dup = {"terms": [{"en": "marginal cost", "zh": "x"},
                         {"en": "Marginal Cost", "zh": "y"}]}
        keep, _ = ng.candidates(dup, ENTRIES)
        self.assertEqual(keep, ["marginal cost"])


class Append(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        self.glossary = self.dir / "glossary" / "ECON10790.txt"
        self.glossary.parent.mkdir(parents=True, exist_ok=True)
        self.glossary.write_text("# ECON10790\n", encoding="utf-8")
        self.state = self.dir / "prep-state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _append(self, terms):
        return ng.append(terms, course="ECON10790",
                         glossary_txt=self.dir / "glossary.txt",
                         glossary_path=self.glossary, state_path=self.state)

    def test_走的是_prep_的追加(self):
        out = self._append(["marginal cost", "shutdown price"])
        self.assertEqual(out.added, ["marginal cost", "shutdown price"])
        self.assertIn("marginal cost", self.glossary.read_text(encoding="utf-8"))
        self.assertEqual(out.aborted, "")

    def test_幂等_第二次一条不加(self):
        self._append(["marginal cost"])
        out2 = self._append(["marginal cost"])
        self.assertEqual(out2.added, [])
        self.assertEqual(out2.skipped, ["marginal cost"])
        self.assertEqual(self.glossary.read_text(encoding="utf-8").count("marginal cost"), 1)

    def test_墓碑_删过的词不会复活(self):
        """⭐ 作者从表里删掉一行**不动 state** —— 重跑时仍要挡住（`prep.py:369-375`）。

        这里手工造出那个状态：state 里记着 `marginal cost`（= prep 曾经加过、或本次加过），
        而表里没有它（= 作者删了）→ 再追加必须**一条不加**。
        """
        self._append(["marginal cost", "shutdown price"])
        text = self.glossary.read_text(encoding="utf-8")
        self.glossary.write_text(text.replace("marginal cost\n", ""), encoding="utf-8")
        out = self._append(["marginal cost"])
        self.assertEqual(out.added, [], "作者删过的词被加回来了")
        self.assertNotIn("marginal cost", self.glossary.read_text(encoding="utf-8"))
        # 而且墓碑真的在盘上（下次 `cl prep` 撞的也是它）
        self.assertIn("marginal cost", json.loads(
            self.state.read_text(encoding="utf-8"))["appended"])

    def test_state_写失败不出声也不炸(self):
        """state 是"删过的词别复活"的凭据；写不进去时要**出声**，但不能炸掉笔记。"""
        out = ng.append(["marginal cost"], course="ECON10790",
                        glossary_txt=self.dir / "glossary.txt",
                        glossary_path=self.glossary,
                        state_path=self.dir / "没有这个目录" / "sub" / "prep-state.json")
        self.assertEqual(out.added, ["marginal cost"], "state 写不进去不该挡着落表")

    def test_跟_prep_的追加同一语义(self):
        """⚠️ 判据不是"调用了 append_terms"，而是**结果与它一致**（含 skipped_long / dup）。"""
        mine = self._append(["marginal cost", "marginal cost", "a b c d e f g h i"])
        other = pathlib.Path(self.dir / "other.txt")
        theirs = prep.append_terms(other, ["marginal cost", "marginal cost",
                                          "a b c d e f g h i"])
        self.assertEqual(len(mine.added), len(theirs.added))
        self.assertEqual(len(mine.skipped), len(theirs.skipped_dup))
        self.assertEqual([r for _t, r in mine.rejected], ["超过词数上限"] * len(theirs.skipped_long))

    def test_空输入直接返回(self):
        out = ng.append([], course="ECON10790", glossary_txt=self.dir / "glossary.txt",
                        glossary_path=self.glossary, state_path=self.state)
        self.assertEqual((out.added, out.skipped), ([], []))


# ---------------------------------------------------------------- 「下节课加载」
def _fake_upstream(bodies: list) -> int:
    """本机假服务器：接一次 POST，把**请求体**记进 `bodies`，回一段合法 SSE。

    ⚠️ 照 `tests/test_cloud_timeout.py` 的成例 —— 不碰真网络、也不需要 key。
    """
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        try:
            conn, _ = srv.accept()
            buf = b""
            # ⚠️ 一次 `recv` 只保证"至少一字节" —— 头与体可能分两次到（实测第一次
            #    只拿到头），所以要按 `Content-Length` 读满。抓不到体的判据是**假红**。
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            head, _, body = buf.partition(b"\r\n\r\n")
            need = 0
            for ln in head.split(b"\r\n"):
                if ln.lower().startswith(b"content-length:"):
                    try:
                        need = int(ln.split(b":", 1)[1].strip())
                    except ValueError:
                        need = 0
            while len(body) < need:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                body += chunk
            bodies.append(body)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                         b"Transfer-Encoding: chunked\r\n\r\n")
            chunk = b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n\n'
            conn.sendall(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n0\r\n\r\n")
            conn.close()
        except OSError:
            pass
        finally:
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


class NextClassLoads(unittest.TestCase):
    """作者要求的「有感知」：新词**真的进了下节课的翻译 prompt**（不是只写进了文件）。"""

    def test_下节课的翻译请求里真的有这个词(self):
        import translator
        import cloud_translator as CT
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            # 上一节课的笔记抽出来 -> 落进这门课的术语表（走 prep.append_terms）
            outcome = ng.append(["marginal cost", "shutdown price"], course="ECON10790",
                                glossary_txt=d / "glossary.txt",
                                glossary_path=d / "glossary" / "ECON10790.txt",
                                state_path=d / "prep-state.json")
            self.assertEqual(len(outcome.added), 2)
            # ① 读端：下节课取术语表（运行时唯一的那条路）
            terms = translator.course_term_list(str(d / "glossary.txt"), "ECON10790")
            self.assertIn("marginal cost", terms)
            self.assertIn("shutdown price", terms)
            # ② 注入端：真的出现在发给模型的那条请求里
            bodies: list = []
            port = _fake_upstream(bodies)
            tr = CT.CloudTranslator("test-key", "m", base_url=f"http://127.0.0.1:{port}",
                                    course_terms=terms, timeout=10.0)
            # `fix_and_translate_stream` 返回 `Result`（不迭代）—— 照它的签名调
            tr.fix_and_translate_stream("the shutdown price equals min AC", [],
                                        lambda *_a: None)
            self.assertTrue(bodies, "假服务器没收到请求")
            payload = bodies[0].decode("utf-8", errors="replace")
            self.assertIn("shutdown price", payload)
            self.assertIn("marginal cost", payload)

    def test_没进库的词不会进_prompt(self):
        """反向判据（防"永远为真"）：库里没有的词不许出现在请求体里。"""
        import translator
        import cloud_translator as CT
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            ng.append(["marginal cost"], course="ECON10790",
                      glossary_txt=d / "glossary.txt",
                      glossary_path=d / "glossary" / "ECON10790.txt",
                      state_path=d / "prep-state.json")
            terms = translator.course_term_list(str(d / "glossary.txt"), "ECON10790")
            bodies: list = []
            port = _fake_upstream(bodies)
            tr = CT.CloudTranslator("test-key", "m", base_url=f"http://127.0.0.1:{port}",
                                    course_terms=terms, timeout=10.0)
            tr.fix_and_translate_stream("hello everyone", [], lambda *_a: None)
            payload = bodies[0].decode("utf-8", errors="replace")
            self.assertIn("marginal cost", payload)
            self.assertNotIn("shutdown price", payload)


if __name__ == "__main__":
    unittest.main()
