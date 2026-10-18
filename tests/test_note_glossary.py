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
import note_terms as nt             # noqa: E402
import prep                         # noqa: E402

#: ⚠️ 用例换成 **SOC10020** 的句子（作者第六轮：新判据不许再用 10-15 那节课的语料）。
ENTRIES = [
    {"ts": "13:09:00", "en": "start from the research question, not from the method",
     "zh": "从研究问题出发，而不是从方法出发", "asr": "", "star": False},
    {"ts": "13:10:00", "en": "your research question decides the methodology",
     "zh": "研究问题决定方法论", "asr": "", "star": False},
    {"ts": "13:11:00", "en": "and the methodology decides which research methods fit",
     "zh": "方法论决定哪些研究方法合适", "asr": "", "star": False},
]

REVIEW = {
    "sections": [{"heading": "研究问题（research question）",
                  "terms": [{"en": "research question", "zh": "研究问题"},
                            {"en": "Masha", "zh": "同学的名字"}]}],
    "terms": [{"en": "methodology", "zh": "方法论"},
              {"en": "Interesting", "zh": "有趣的"},
              {"en": "opportunity cost", "zh": "机会成本"}],   # 最后一个课上没出现
}


class _Mats:
    """课件替身：`candidates` 只读 `_blob`（全文，小写）。"""

    def __init__(self, blob: str = ""):
        self._blob = blob


class Candidates(unittest.TestCase):
    def test_只收真出现过的(self):
        keep, rejected = ng.candidates(REVIEW, ENTRIES)
        self.assertIn("research question", keep)
        self.assertIn("methodology", keep)
        self.assertNotIn("opportunity cost", keep, "课上没讲过的词不该进库")
        self.assertIn("只出现过 0 次", dict(rejected).get("opportunity cost", ""))

    def test_只出现一次不收(self):
        """⭐ 第六轮第 4 条：要出现过 **≥2 次**（一次多半是顺口一说）。"""
        rev = {"terms": [{"en": "validity", "zh": "效度"}]}
        entries = [{"ts": "13:12:00", "en": "this is about validity only once", "zh": "", "asr": ""}]
        keep, rejected = ng.candidates(rev, entries)
        self.assertEqual(keep, [])
        self.assertIn("只出现过 1 次", dict(rejected).get("validity", ""))
        # 出现两次就进（学科词不该被词形判据挡在门外）
        entries *= 2
        keep2, _r = ng.candidates(rev, entries)
        self.assertEqual(keep2, ["validity"])

    def test_机构平台学位课型词不收(self):
        """作者点名的 `Brightspace` / `PhD` / `tutorial`：**真的**出现在课上和课件里，
        只有那张小表能挡住（实测 ECON10740 材料里 `Brightspace` 出现 23 次）。"""
        rev = {"terms": [{"en": t, "zh": ""} for t in
                         ("Brightspace", "PhD", "tutorial", "evidence", "AI", "director")]}
        entries = [{"ts": "13:12:00", "en": "upload to Brightspace for the tutorial. "
                                             "the tutorial is on Brightspace. evidence and AI "
                                             "again: evidence, AI. PhD director PhD director.",
                    "zh": "", "asr": ""}]
        keep, rejected = ng.candidates(rev, entries)
        self.assertEqual(keep, [], keep)
        for t in ("Brightspace", "PhD", "tutorial", "evidence", "director"):
            self.assertIn("机构", dict(rejected).get(t, ""), t)

    def test_学科词优先_课件里的排前面(self):
        """⭐ 第 4 条：学科词优先 —— 课件（这门课的教材）里出现过的排前面。"""
        rev = {"terms": [{"en": "sampling frame", "zh": "抽样框"},
                         {"en": "mixed methods", "zh": "混合方法"}]}
        entries = [{"ts": "13:13:00",
                    "en": "sampling frame twice: sampling frame", "zh": "", "asr": ""}]
        keep, _r = ng.candidates(rev, entries, materials=_Mats("mixed methods in chapter 3, "
                                                               "mixed methods again"))
        self.assertEqual(keep, ["mixed methods", "sampling frame"])

    def test_每节上限_15(self):
        terms = [{"en": f"subject term {i}", "zh": ""} for i in range(18)]
        blob = " ".join(f"subject term {i} twice: subject term {i}" for i in range(18))
        keep, rejected = ng.candidates({"terms": terms}, [],
                                       materials=_Mats(blob))
        self.assertEqual(len(keep), nt.MAX_TERMS)
        self.assertTrue(any("上限" in why for _t, why in rejected), rejected)

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
        keep, _rej = ng.candidates(REVIEW, ENTRIES,
                                   materials=_Mats("opportunity cost: opportunity cost"))
        self.assertIn("opportunity cost", keep)

    def test_去重(self):
        dup = {"terms": [{"en": "research question", "zh": "x"},
                         {"en": "Research Question", "zh": "y"}]}
        keep, _ = ng.candidates(dup, ENTRIES)
        self.assertEqual(keep, ["research question"])


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


class 泛词过滤_F8f(unittest.TestCase):
    """F8-f（作者点名）：本课术语表里"任何课都出现"的**单单词泛词**要过滤 ——
    除非术语库里真有它（规则原文：`unless the glossary has them`）。"""

    def test_F9c_材料里有的词留(self):
        """F9c 域中立：`substitution` 在化学课是正词 —— 本课材料里出现就留；
        `minimum` 哪门课都算泛词（本课材料里有也要过库那关）。"""
        import note_terms as nt2
        self.assertTrue(nt2.too_common("minimum", verified=True), "minimum 哪门课都是泛词")
        self.assertFalse(nt2.too_common("substitution", verified=True),
                         "本课材料里出现 → 留着（化学课的正词）")
        self.assertTrue(nt2.too_common("substitution"),
                        "没有出处时仍按泛词处理（经济学口头词）")

    def test_F9c_writer_按材料核实(self):
        """写作层真的把 `Materials.verify` 喂给了判据：同一个词，材料里有就留、没有就丢。"""
        d = tempfile.TemporaryDirectory()
        try:
            base = pathlib.Path(d.name)
            (base / "glossary").mkdir(parents=True, exist_ok=True)
            (base / "glossary.txt").write_text("# 公共术语表\n", encoding="utf-8")
            import obsidian_writer as ow
            w = ow.ObsidianWriter(None, "TESTX", mode="no",
                                  glossary_path=str(base / "glossary.txt"))

            class _Mat:
                def verify(self, term, *, max_n=2):
                    return term.strip().lower() == "substitution"

            review = {"sections": [{"ts": "13:16", "terms": [
                {"en": "substitution", "zh": "取代反应"},
            ]}]}
            w._materials = None
            self.assertNotIn("substitution",
                             {h.term.lower() for h in w._review_term_hits(review, [])},
                             "没有材料出处 → 按泛词处理（经济学口头词）")
            w._materials = _Mat()
            self.assertIn("substitution",
                          {h.term.lower() for h in w._review_term_hits(review, [])},
                          "材料里有 → 留（化学课的正词）")
        finally:
            d.cleanup()

    def test_泛词丢掉_库里有就留(self):
        d = tempfile.TemporaryDirectory()
        try:
            base = pathlib.Path(d.name)
            (base / "glossary").mkdir(parents=True, exist_ok=True)
            (base / "glossary.txt").write_text("# 公共术语表\n", encoding="utf-8")
            # 本课词表里只放 derivative（模拟真库：derivative 在，别的不在）
            (base / "glossary" / "TESTX.txt").write_text("derivative\n", encoding="utf-8")
            import obsidian_writer as ow
            w = ow.ObsidianWriter(None, "TESTX", mode="no",
                                  glossary_path=str(base / "glossary.txt"))
            review = {"sections": [{"ts": "13:16", "terms": [
                {"en": "substitute", "zh": "替代品"},
                {"en": "minimum", "zh": "最小值"},
                {"en": "derivative", "zh": "导数"},
                {"en": "marginal cost", "zh": "边际成本"},
            ]}]}
            words = {h.term.lower() for h in w._review_term_hits(review, [])}
            self.assertNotIn("substitute", words)
            self.assertNotIn("minimum", words)
            self.assertIn("derivative", words, "术语库（glossary/TESTX.txt）里有就必须留")
            self.assertIn("marginal cost", words, "多词短语永远不算泛词")
        finally:
            d.cleanup()

    def test_同一中文只留一行_库行优先(self):
        """F8-e（作者点名）：`shutting down price` 与 `shutdown price` 同为"停产价" → 只留一行。"""
        sel = nt.Selection(hits=[nt.Hit(term="shutdown price", gloss="停产价", ts="13:25")],
                           dropped=[])
        extra = [nt.Hit(term="shutting down price", gloss="停产价", ts="13:17")]
        text = " / ".join(nt.render_table(sel, extra=extra))
        self.assertIn("shutdown price", text)
        self.assertNotIn("shutting down price", text)

    def test_占位gloss不参与合并(self):
        """没有中文注的条目（占位 gloss）不许被并成一行 —— 那会把一堆词吞掉。"""
        sel = nt.Selection(hits=[nt.Hit(term="alpha", gloss="（本课术语）"),
                                 nt.Hit(term="beta", gloss="（本课术语）")], dropped=[])
        text = " / ".join(nt.render_table(sel))
        self.assertIn("alpha", text)
        self.assertIn("beta", text)


if __name__ == "__main__":
    unittest.main()
