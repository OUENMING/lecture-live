"""课上候选词（链路体检 ⭐2）的判据：**纯函数** + 接线点。全离线、不碰真盘。

⭐ 每条判据都对着 `scratchpad/chain/report.md` 里的实数或风险：
   · `≥2 次` 才算候选（§2.3 建议 4「只收 ≥2 次」）；
   · **听错的词不许进池**（§2.2：校正层"音近就改"会把错词固化 —— `votes→boats` 里的
     `boats` 一节课出现 11 次，按"≥2 次"就收等于教模型把它当术语）；
   · 与笔记侧同一套门（人名/常识词/作者删过）；
   · 池子大小有上限、截断**出声**。
"""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live_terms as lt          # noqa: E402


def finals(*lines):
    return list(lines)


def mats_yes(_phrase):
    """判据替身：生产上这里是 `materials.Materials.verify`（课件里真写过这个短语）。

    ⚠️ 单测里没有课件索引，而 `verify is None` 时**多词短语一律不收**（只认全大写的
       专有名词短语）—— 那是给"用户没归档课件"用的保守档。所以凡是拿小写短语当判据的
       用例都要显式喂一个替身，否则测的是保守档而不是生产档。
    """
    return True


def mats_no(_phrase):
    return False


class Extract(unittest.TestCase):
    def test_出现两次才收(self):
        got = lt.extract(finals("so the shutdown price is low",
                                "the shutdown price equals min AC",
                                "hello everyone"), verify=mats_yes)
        self.assertIn("shutdown price", got.terms)
        self.assertNotIn("hello everyone", got.terms, "只出现一次的短语不该进池")

    def test_同一句里说三遍不算三次(self):
        """计数单位是**出现过的句子数**（同一句连说三遍只是重复）。"""
        got = lt.extract(finals("RSI RSI RSI", "nothing here", "nothing here"))
        self.assertEqual([t for t in got.terms if t == "RSI"], [], "同一句里的重复被当成了跨句复现")

    def test_缩写收(self):
        got = lt.extract(finals("the RSI is above 70", "RSI means relative strength"))
        self.assertIn("RSI", got.terms)

    def test_句中大写专有名词收(self):
        got = lt.extract(finals("we follow Coase here", "as Coase said"))
        self.assertIn("Coase", got.terms)

    def test_句首大写不算专有名词(self):
        """句首词天然大写 —— 只有**句中**大写才是专有名词的信号。"""
        got = lt.extract(finals("Hello there everyone", "Hello again folks"))
        self.assertNotIn("Hello", got.terms)

    def test_连字符复合词收(self):
        got = lt.extract(finals("a cost-benefit analysis", "the cost-benefit ratio"))
        self.assertIn("cost-benefit", got.terms)

    def test_单个小写词一律不收_这就是听错词的门(self):
        """⭐⭐ 这条是**池子安全**的关键：`votes→boats` 里的 `boats` 一节课出现 11 次
        （`report.md` §2.2），若按"≥2 次 + 在课件里"就收，等于教校正层把错词固化。
        单小写词与常识词/动词没有任何判据能分开（实测：`calculate`/`given`/`equal`
        全在课件里），所以**一律不收** —— 代价写在模块头「成本」里。"""
        seen = finals("the boats are counted", "more boats than votes",
                      "votes are counted too")
        for verify in (None, mats_yes):
            got = lt.extract(seen, verify=verify)
            self.assertNotIn("boats", got.terms)
            self.assertNotIn("votes", [t.lower() for t in got.terms],
                             "单个小写词被收进来了（不管有没有课件复核）")

    def test_多词短语要过课件复核(self):
        """`understand the question` 这类句式模板靠它挡下（实测：不过复核）。"""
        seen = finals("we understand the question", "you understand the question here",
                      "they understand the question too")
        self.assertNotIn("understand the question",
                         [t.lower() for t in lt.extract(seen, verify=mats_no).terms])

    def test_没有课件索引时只认全大写短语(self):
        """用户没归档课件 = 保守档：小写短语不收，专有名词短语收。"""
        got = lt.extract(finals("so the Sunk Cost said", "the Sunk Cost again"),
                         verify=None)
        low = lt.extract(finals("so the shutdown price is low",
                                "the shutdown price again"), verify=None)
        self.assertEqual([w for w in got.terms if w == "Sunk Cost"], ["Sunk Cost"])
        self.assertNotIn("shutdown price", [w.lower() for w in low.terms])

    def test_人名与常识词挡下(self):
        got = lt.extract(finals("so Masha asked a question", "yes Masha again"),
                         type_of=lambda t: "person" if t == "Masha" else "")
        self.assertNotIn("Masha", got.terms)
        self.assertTrue(any(w == "Masha" for w, _r in got.rejected))
        got2 = lt.extract(finals("Interesting point here", "very Interesting indeed"))
        self.assertNotIn("Interesting", got2.terms)

    def test_作者删过的词不进池(self):
        got = lt.extract(finals("the marginal cost is low", "marginal cost again"),
                         verify=mats_yes, deleted={"marginal cost"})
        self.assertNotIn("marginal cost", got.terms)
        self.assertIn(("marginal cost", "作者删过"), got.rejected)

    def test_已在术语表的不重复收(self):
        got = lt.extract(finals("the shutdown price is low", "shutdown price again"),
                         known={"shutdown price"})
        self.assertEqual(got.terms, [])

    def test_功能词短语挡下(self):
        got = lt.extract(finals("part of the story", "part of the problem"))
        self.assertNotIn("part of the", [t.lower() for t in got.terms])
        self.assertNotIn("of the", [t.lower() for t in got.terms])

    def test_上限截断有记账(self):
        # ⚠️ 两个坑都在这里避开了：`_WORD` 不认数字（`Alpha0`/`Alpha1` 会退化成同一个词），
        #    而每个短语**必须真的出现 ≥2 次**才够格（一格只出现一次 = 不算候选）。
        lines = []
        for ph in ("Shutdown price", "Marginal revenue", "Total revenue",
                   "Average cost", "Fixed cost", "Sunk cost"):
            lines += [f"{ph} is what we look at", f"so {ph} matters here"]
        got = lt.extract(finals(*lines), verify=mats_yes, limit=2)
        self.assertEqual(len(got.terms), 2, got.terms)
        # ⚠️ 断言**相对关系**而不是写死 4：抽取本来就会顺带捞出句式模板里的短语
        #    （实测：`so {词} matters here` 那种重复句式会带出 `revenue matters`）——
        #    要把"截了多少"钉死就必须连模板一起当判据，那是判据在管实现。
        #    这里钉的是**记账**：截掉的条数 = 全部候选 − 留下的。
        all_terms = lt.extract(finals(*lines), verify=mats_yes).terms
        self.assertEqual(got.trimmed, len(all_terms) - 2,
                         "截断没记账（静默丢东西）")
        self.assertIn("Shutdown price", got.terms, "该留的领域短语没留下")

    def test_空输入(self):
        self.assertEqual(lt.extract([]).terms, [])
        self.assertEqual(lt.extract([""]).terms, [])


class Pool(unittest.TestCase):
    def test_步长_没到就立刻返回(self):
        pool = lt.LivePool(step=5, verify=mats_yes)
        first = pool.update(finals(*["Shutdown price is low"] * 6))
        self.assertTrue(first, "第一批该抽到词")
        second = pool.update(finals(*["Shutdown price is low"] * 7))
        self.assertEqual(second, [], "没到步长却又抽了一遍")

    def test_起点那批词不重复进(self):
        pool = lt.LivePool(initial=["marginal cost"], step=1)
        got = pool.update(finals("marginal cost is low", "marginal cost again"))
        self.assertNotIn("marginal cost", got)

    def test_池子上限(self):
        pool = lt.LivePool(step=1, limit=3, verify=mats_yes)
        lines = []
        for ph in ("Shutdown price", "Marginal revenue", "Total revenue",
                   "Average cost", "Fixed cost"):
            lines += [f"the {ph} is here", f"and {ph} again"]
        pool.update(finals(*lines))
        self.assertLessEqual(len(pool.terms()), 3)

    def test_报告一行永远有话可说(self):
        pool = lt.LivePool(step=99)
        self.assertIn("暂无", pool.report())
        pool.update(finals("shutdown price is low", "shutdown price again"))
        self.assertIn("候选词", pool.report())

    def test_课件核对懒加载(self):
        called = []

        def factory():
            called.append(1)
            return lambda w: False
        pool = lt.LivePool(step=1, verify_factory=factory)
        self.assertEqual(called, [], "构造时就建了课件索引（启动路径上的开销）")
        pool.update(finals("the boats are here", "boats again"))
        self.assertEqual(len(called), 1)

    def test_课件索引炸了不影响池子(self):
        def boom():
            raise RuntimeError("index failed")
        pool = lt.LivePool(step=1, verify_factory=boom)
        # 专有名词不需要课件复核 —— 判据用的是它，免得测到"保守档"上
        got = pool.update(finals("we follow Coase here", "as Coase said"))
        self.assertIn("Coase", got, "课件索引炸了把整池带下水")


class EngineSeam(unittest.TestCase):
    """`add_terms` 那条缝：两个引擎接口一致、去重、**中途追加立刻生效**。"""

    def test_云端的池子真的会读到新词(self):
        import cloud_translator as CT
        tr = CT.CloudTranslator("k", "m", glossary_terms=["linear function"])
        new = tr.add_terms(["shutdown price", "LINEAR FUNCTION"])
        self.assertEqual(new, ["shutdown price"], "重复的词没去掉（会白占一个动态名额）")
        self.assertIn("shutdown price", tr._terms)
        # ⭐ 判据指向**注入**：这个词必须能被 `select_term_parts` 召回
        from translator import select_term_parts
        _stable, dyn = select_term_parts("the shutdown price is the minimum",
                                         tr._terms)
        self.assertIn("shutdown price", dyn)

    def test_本地引擎同名接口(self):
        import translator as T
        tr = T.Translator.__new__(T.Translator)          # 不加载 mlx 模型
        tr._terms = ["linear function"]
        self.assertEqual(tr.add_terms(["shutdown price", "linear function"]),
                         ["shutdown price"])

    def test_路由器两个引擎都喂(self):
        import main as M

        class Eng:
            def __init__(self):
                self.got = []

            def add_terms(self, words):
                self.got += list(words)
                return list(words)

        cloud, local = Eng(), Eng()
        r = M.EngineRouter.__new__(M.EngineRouter)       # 不跑 __init__（要 notify 等）
        r._cloud, r._local = cloud, local
        r.add_terms(["shutdown price"])
        self.assertEqual(cloud.got, ["shutdown price"])
        self.assertEqual(local.got, ["shutdown price"], "降级后读的是本地那份池子")

    def test_引擎报错不抛给上课(self):
        import main as M
        r = M.EngineRouter.__new__(M.EngineRouter)

        class Boom:
            def add_terms(self, w):
                raise RuntimeError("engine down")
        r._cloud, r._local = Boom(), Boom()
        self.assertEqual(r.add_terms(["x"]), [])


class Wiring(unittest.TestCase):
    """接线点 `main.live_terms_step`：过门的新词才进池、任何异常都不带走上课。"""

    def test_新词进池并送到引擎(self):
        import main as M
        sent = []

        class R:
            def add_terms(self, w):
                sent.append(list(w))
                return list(w)

        pool = lt.LivePool(step=1, verify=mats_yes)
        got = M.live_terms_step(pool, R(), finals("the shutdown price is low",
                                                  "shutdown price again"))
        self.assertIn("shutdown price", got)
        self.assertEqual(sent, [got])

    def test_没到步长不进引擎(self):
        import main as M
        sent = []

        class R:
            def add_terms(self, w):
                sent.append(list(w))

        pool = lt.LivePool(step=99, verify=mats_yes)
        pool.update(finals("alpha beta"))                # 先吃掉第一步
        got = M.live_terms_step(pool, R(), finals("the shutdown price is low",
                                                  "shutdown price again"))
        self.assertEqual(got, [])
        self.assertEqual(sent, [])

    def test_池子炸了不抛(self):
        import main as M

        class BoomPool:
            def update(self, finals):
                raise RuntimeError("boom")
        self.assertEqual(M.live_terms_step(BoomPool(), None, ["x"]), [])

    def test_没有池子就是空操作(self):
        import main as M
        self.assertEqual(M.live_terms_step(None, None, ["x"]), [])


class 上限_按频次与新近度(unittest.TestCase):
    """⭐ 作者 2026-10-17：抽词池设上限，**按本课出现频次 + 新近度**淘汰最旧/最少的。"""

    def _pool(self, limit, lines):
        pool = lt.LivePool(step=1, limit=limit, verify=mats_yes)
        pool.update(finals(*lines))
        return pool

    def test_说得多说得晚的词留得住(self):
        """池子满了以后，让位的是"说得少 + 最旧"的那个。

        ⚠️ 判据里的词都得是**真能当候选**的：`Old One` 这种全是常识词的短语连
           `extract` 那关都过不了（实测被 `常识词` 挡下）——那测的就不是淘汰了。
        """
        early = ["Sunk Cost appears here", "Sunk Cost again"]            # 2 次、最前
        hot = ["Marginal Revenue is what matters", "Marginal Revenue again",
               "Marginal Revenue at the end"]                           # 3 次、靠后
        later = ["Elasticity Curve now", "Elasticity Curve again",
                 "Elasticity Curve finally", "Elasticity Curve yes"]     # 4 次、最新
        pool = lt.LivePool(step=1, limit=2, verify=mats_yes)
        pool.update(finals(*(early + hot)))
        self.assertEqual(sorted(w.lower() for w in pool.terms()),
                         ["marginal revenue", "sunk cost"])
        pool.update(finals(*(early + hot + later)))
        kept = [w.lower() for w in pool.terms()]
        self.assertIn("elasticity curve", kept)
        self.assertIn("marginal revenue", kept)
        self.assertNotIn("sunk cost", kept,
                         "淘汰的不是\"最少/最旧\"（先到先得又回来了）")

    def test_淘汰有记账_不许静默(self):
        lines = []
        for i, ph in enumerate(("Alpha Beta", "Gamma Delta", "Epsilon Zeta",
                                "Eta Theta", "Iota Kappa")):
            lines += [f"the {ph} is here", f"and {ph} again"]
        pool = self._pool(3, lines)
        self.assertEqual(len(pool.terms()), 3)
        self.assertTrue(pool.evicted(), "淘汰了却没记账")
        self.assertIn("淘汰", pool.report())
        self.assertIn("上限 3", pool.report())

    def test_池子里的词会被这节课更新计数(self):
        """起步那批（`initial`，上次课的词）计数为 0 —— 它这节课又被说到了就该升上来。"""
        pool = lt.LivePool(initial=["Shutdown price"], step=1, limit=2,
                           verify=mats_yes)
        pool.update(finals("Shutdown price is low", "Shutdown price again",
                           "Shutdown price once more"))
        self.assertIn("Shutdown price", pool.terms())
        st = pool._stat["shutdown price"]
        self.assertEqual(st["count"], 3, "池子里已有的词没吃到这节课的计数")

    def test_上限可配_环境变量(self):
        with mock.patch.dict(os.environ, {"CLASSLIVE_LIVE_TERMS_MAX": "7"}):
            self.assertEqual(lt._env_limit(), 7)
            self.assertEqual(lt.LivePool(step=1)._limit, 7)
        with mock.patch.dict(os.environ, {"CLASSLIVE_LIVE_TERMS_MAX": "abc"}):
            self.assertEqual(lt._env_limit(), lt.MAX_TERMS, "坏值不许把上限变成 0/异常")
        with mock.patch.dict(os.environ, {"CLASSLIVE_LIVE_TERMS_MAX": ""}):
            self.assertEqual(lt._env_limit(), lt.MAX_TERMS)

    def test_排序_先频次后新近(self):
        """`injected order` 有意义：出现多的在前，平手时新近的在前。"""
        pool = lt.LivePool(step=1, verify=mats_yes)
        pool.update(finals("Hot Term here", "Hot Term again", "Cold Term here",
                           "Hot Term later", "Cold Term at the very end"))
        order = [w.lower() for w in pool.terms()]
        self.assertLess(order.index("hot term"), order.index("cold term"))


class LoadCap(unittest.TestCase):
    """⭐ 作者 2026-10-17：**下节课加载**按相关度/新近度取前 N，而不是全表加载。

    定义点在 `translator.course_term_list`（四五个调用点全走它：逐句翻译 `always`、
    云端构造、草稿/点拨前 40、Soniox `terms=`）。这里钉三件事：
    ① 上限是 `TERM_LOAD_LIMIT`；② 手写的在前、自动追加按日期倒序；
    ③ 拿不到 state 时退化成文件顺序（仍然受上限管）。
    """

    def _fixture(self, d, n_hand=3, n_auto=5):
        """手写 n_hand 条 + 自动追加 n_auto 条（日期递减：越靠后越旧）。"""
        import json
        d = pathlib.Path(d)
        gl = d / "glossary/ECON99999.txt"
        gl.parent.mkdir(parents=True)
        hand = [f"hand term {i}" for i in range(n_hand)]
        auto = [f"auto term {i}" for i in range(n_auto)]
        gl.write_text("# ECON99999 Test\n" + "\n".join(hand + auto) + "\n",
                      encoding="utf-8")
        # 新近度：`auto term 0` 最新（日期最大），越靠后越旧
        tombst = {w: f"2026-10-{20 - i:02d}" for i, w in enumerate(auto)}
        st = d / "prep-state.json"
        st.write_text(json.dumps({"appended": tombst}), encoding="utf-8")
        return gl, st

    def test_上限就是_TERM_LOAD_LIMIT(self):
        import translator
        self.assertEqual(translator.TERM_LOAD_LIMIT, 60)
        with tempfile.TemporaryDirectory() as d:
            gl, st = self._fixture(d, n_hand=70, n_auto=0)
            got = translator.course_term_list(str(pathlib.Path(d) / "glossary.txt"),
                                              "ECON99999", state_path=st)
            self.assertEqual(len(got), 60)

    def test_顺序_手写在前_自动按新近度倒序(self):
        import translator
        with tempfile.TemporaryDirectory() as d:
            gl, st = self._fixture(d, n_hand=2, n_auto=4)
            got = translator.course_term_list(str(pathlib.Path(d) / "glossary.txt"),
                                              "ECON99999", state_path=st)
            self.assertEqual(got[:2], ["hand term 0", "hand term 1"],
                             "手写的领域先验该在最前")
            self.assertEqual(got[2:], ["auto term 0", "auto term 1",
                                       "auto term 2", "auto term 3"],
                             "自动追加的该按新近度（日期倒序）")

    def test_超上限时掉的是最旧的自动词(self):
        import translator
        with tempfile.TemporaryDirectory() as d:
            gl, st = self._fixture(d, n_hand=2, n_auto=6)
            got = translator.course_term_list(str(pathlib.Path(d) / "glossary.txt"),
                                              "ECON99999", state_path=st, limit=4)
            self.assertEqual(got, ["hand term 0", "hand term 1",
                                   "auto term 0", "auto term 1"],
                             "被挤掉的应该是日期最旧的那几个")

    def test_没有_state_退化成文件顺序(self):
        import translator
        with tempfile.TemporaryDirectory() as d:
            gl, st = self._fixture(d, n_hand=2, n_auto=3)
            got = translator.course_term_list(str(pathlib.Path(d) / "glossary.txt"),
                                              "ECON99999", state_path=None, limit=3)
            # `state_path=None` 会去解析真实状态路径（隔离 HOME 下没有）→ 文件顺序
            self.assertEqual(got[:2], ["hand term 0", "hand term 1"])
            self.assertEqual(len(got), 3)

    def test_limit_None_给全表(self):
        """面板/导出这类"要全表"的调用点用得上（默认则按上限）。"""
        import translator
        with tempfile.TemporaryDirectory() as d:
            gl, st = self._fixture(d, n_hand=61, n_auto=1)
            all_ = translator.course_term_list(str(pathlib.Path(d) / "glossary.txt"),
                                               "ECON99999", state_path=st, limit=None)
            self.assertEqual(len(all_), 62)

    def test_load_terms_那一半也带上限(self):
        """动态召回池（`load_terms`）不许绕过上限 —— 否则"池子全表"从后门回来。"""
        import translator
        with tempfile.TemporaryDirectory() as d:
            gl, st = self._fixture(d, n_hand=61, n_auto=1)
            with mock.patch.object(translator, "course_term_list",
                                   side_effect=translator.course_term_list) as m:
                translator.load_terms(str(pathlib.Path(d) / "glossary.txt"),
                                      "ECON99999")
            self.assertTrue(m.called, "load_terms 没走 course_term_list（上限被绕过）")

    def test_每个调用点都走同一条路(self):
        """四五个注入点全都是 `course_term_list`（没有第二个读表口径）。"""
        import pathlib as _p
        root = _p.Path(__file__).resolve().parent.parent
        offenders = []
        for name in ("main.py", "cloud_translator.py", "obsidian_writer.py",
                     "stream_asr.py"):
            text = (root / name).read_text(encoding="utf-8")
            for line in text.splitlines():
                s = line.strip()
                if "course_terms_path(" in s and not s.startswith("#"):
                    offenders.append(f"{name}: {s}")
        self.assertEqual(offenders, [],
                         "有地方直接读 glossary/<课号>.txt 而不走 course_term_list")


class DeletedTerms(unittest.TestCase):
    """`note_glossary.deleted_terms`：墓碑 − 现有表 = 作者删过的（唯一定义点）。"""

    def test_墓碑减现有表(self):
        import json
        import pathlib
        import tempfile
        import note_glossary as ng
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            gl = d / "glossary/ECON99999.txt"
            gl.parent.mkdir(parents=True)
            gl.write_text("# c\nkeep me\n", encoding="utf-8")
            st = d / "prep-state.json"
            st.write_text(json.dumps({"appended": {"keep me": "2026-10-01",
                                                   "deleted one": "2026-10-02"}}),
                          encoding="utf-8")
            got = ng.deleted_terms("ECON99999", glossary_txt=d / "glossary.txt",
                                   glossary_path=gl, state_path=st)
            self.assertEqual(got, {"deleted one"}, "还在表里的词被当成了删过")

    def test_没有墓碑就是空集(self):
        import pathlib
        import tempfile
        import note_glossary as ng
        with tempfile.TemporaryDirectory() as d:
            got = ng.deleted_terms("ECON99999", glossary_txt=pathlib.Path(d) / "g.txt",
                                   glossary_path=pathlib.Path(d) / "none.txt",
                                   state_path=pathlib.Path(d) / "none.json")
            self.assertEqual(got, set())


if __name__ == "__main__":
    unittest.main()
