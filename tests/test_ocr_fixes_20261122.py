"""OCR 审查（2026-11-22）确认的笔记链路 bug 的回归测试 —— 每条一个最小复现。

⚠️ 写端全部隔离：用临时目录，不碰真 `sessions/` / `~/.classlive/`。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class WriterAppendDiskError(unittest.TestCase):
    def test_append_write_failure_does_not_raise(self):
        import obsidian_writer as ow
        with tempfile.TemporaryDirectory() as tmp:
            old = ow.SESSIONS
            ow.SESSIONS = Path(tmp) / "sessions"
            try:
                w = ow.ObsidianWriter(vault=tmp, course="TEST", mode="yes")
                w.append("one", "一")
                real_open = Path.open

                def boom(self, *a, **k):
                    if self == w.session_path:
                        raise OSError(28, "No space left on device")
                    return real_open(self, *a, **k)
                with mock.patch.object(Path, "open", boom), \
                        mock.patch("builtins.print"):
                    self.assertEqual(w.append("two", "二"), "")
                self.assertEqual(w.count, 1)          # 写失败句号不前进
                w.append("three", "三")
                self.assertEqual(w.count, 2)
            finally:
                ow.SESSIONS = old


class NoteJobsConcurrency(unittest.TestCase):
    def test_two_threads_write_same_record(self):
        import threading
        import note_jobs as nj
        with tempfile.TemporaryDirectory() as tmp:
            nj.write("s", {"a": 0}, root=tmp)
            bad = []

            def run(k):
                for i in range(300):
                    if not nj.write("s", {k: i}, root=tmp):
                        bad.append((k, i))
            ts = [threading.Thread(target=run, args=(k,)) for k in ("x", "y")]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(bad, [])
            rec = nj.read("s", root=tmp)
            self.assertEqual((rec.get("x"), rec.get("y")), (299, 299))

    def test_claim_stale_record_only_one_winner(self):
        import threading
        import note_jobs as nj
        for _ in range(20):
            with tempfile.TemporaryDirectory() as tmp:
                nj.write("s", {"state": nj.DONE}, root=tmp)
                go = threading.Barrier(8)
                wins = []

                def run(i):
                    go.wait()
                    if nj.claim("s", root=tmp, pid=os.getpid()):
                        wins.append(i)
                ts = [threading.Thread(target=run, args=(i,)) for i in range(8)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
                self.assertEqual(len(wins), 1, wins)


class NoteMergeDeadline(unittest.TestCase):
    def test_chat_json_gets_wall_clock_deadline(self):
        import llm_client
        import note_merge as nm
        seen = {}

        def fake(*a, **k):
            seen.update(k)
            return {"zh": "x"}
        with mock.patch.object(llm_client, "chat_json", fake):
            nm._chat("k", "m", "s", "u", 400, 20.0)
        self.assertEqual(seen.get("deadline"), 20.0)


class GuardsSafeDelete(unittest.TestCase):
    def test_multiple_spans_all_deleted(self):
        import note_guards as g
        s = "第一句话。这里是噪声A。中间好句。这里是噪声B。结尾。"
        a, b = s.index("这里是噪声A。"), s.index("这里是噪声B。")
        out, blocked, kept = g.safe_delete(s, [(a, a + 7), (b, b + 7)])
        self.assertEqual(out, "第一句话。中间好句。结尾。")
        self.assertEqual(len(kept), 2)


class GuardsAnnotateSecondMismatch(unittest.TestCase):
    def test_second_wrong_division_in_same_field_is_marked(self):
        import note_guards as g
        rv = {"sections": [{"title": "t", "prose": [
            "总量 9 除以 3 得到 4，这是第一步。然后 10 除以 2 得到 6，这是第二步。"]}],
              "checks": [
                  {"claim": "总量 9 除以 3 得到 4", "status": "mismatch", "how": "9/3=3"},
                  {"claim": "然后 10 除以 2 得到 6", "status": "mismatch", "how": "10/2=5"}]}
        out, _, _ = g.annotate_mismatches(rv)
        p = out["sections"][0]["prose"][0]
        self.assertIn("第一步。（⚠ 数字对不上：9/3=3）", p)
        self.assertIn("第二步。（⚠ 数字对不上：10/2=5）", p)
        again, _, _ = g.annotate_mismatches(
            {**out, "checks": [{**c, "annotated": False} for c in out["checks"]]})
        self.assertEqual(again["sections"][0]["prose"][0], p)      # 再跑一次不重复标


class GuardsRetimeFirstSection(unittest.TestCase):
    def test_first_section_component_earlier_than_ts_is_kept(self):
        import note_guards as g
        comp = {"title": "🎙 指标（09:05–09:10）", "rows": []}
        rv = {"sections": [{"title": "a", "ts": "09:20:00", "compare": comp},
                           {"title": "b", "ts": "09:40:00"}]}
        out, st, _ = g.retime_components(rv)
        self.assertEqual(out["sections"][0].get("compare"), comp)
        self.assertEqual(st, [{"repaired": 0, "dropped": 0}])


class GuardsFalseSourcePageSuffix(unittest.TestCase):
    def test_jian_kejian_moye_has_no_leftover(self):
        import note_guards as g
        for src, want in (("公式见课件末页。", "公式见📚 标准教材。"),
                          ("见课件第 3 页的推导", "见📚 标准教材的推导")):
            out, _, _ = g.strip_false_sources(
                {"sections": [{"title": "x", "prose": [src]}]}, has_materials=False)
            self.assertEqual(out["sections"][0]["prose"][0], want)


class LedgerSuperscripts(unittest.TestCase):
    def test_unicode_superscripts_become_powers(self):
        import note_ledger as L
        self.assertEqual(L.normalize_math("TC(q) = q³ − 12q² + 60q"),
                         "TC(q) = q^3 − 12q^2 + 60q")
        self.assertEqual(L.normalize_math("x⁻¹"), "x^{-1}")


class PlanBracketTimestamp(unittest.TestCase):
    def test_bracketed_start_parses(self):
        import note_v2 as nv
        self.assertEqual(nv._ts_seconds("[13:16:28]"), 13 * 3600 + 16 * 60 + 28)
        self.assertEqual(nv._ts_seconds("【13:16】"), 13 * 3600 + 16 * 60)
        self.assertIsNone(nv._ts_seconds("13点16"))


class MatrixZero(unittest.TestCase):
    def test_numeric_zero_and_false_mean_lo(self):
        import note_v2 as nv
        self.assertEqual([nv._lh(x) for x in (0, False, 1, True, None, "0")],
                         ["lo", "lo", "hi", "hi", "", "lo"])


class GlossFirstZhPhrase(unittest.TestCase):
    def test_term_inside_longer_english_phrase_untouched(self):
        import note_v2 as nv
        self.assertEqual(nv._gloss_first_zh("marginal cost（边际成本）随产量上升。", "cost", "成本"),
                         "marginal cost（边际成本）随产量上升。")
        self.assertEqual(nv._gloss_first_zh("这里的 cost 很高", "cost", "成本"),
                         "这里的成本（cost）很高")


class MergeUnitsKeepsComponents(unittest.TestCase):
    def test_second_member_components_not_lost(self):
        import note_v2 as nv
        d1 = {"kind": "matrix", "title": "图一"}
        d2 = {"kind": "matrix", "title": "图二"}
        cmp2 = {"title": "对照", "cols": ["", "A", "B"], "rows": []}
        rv = {"sections": [
            {"heading": "需求弹性：一", "ts": "10:00", "prose": ["a"], "diagram": d1},
            {"heading": "需求弹性：二", "ts": "10:05", "prose": ["b"], "diagram": d2,
             "compare": cmp2}]}
        out = nv.merge_units(rv, end_ts="10:10")
        got = [s.get("diagram") for s in out["sections"]]
        self.assertIn(d1, got)
        self.assertIn(d2, got)
        self.assertIn(cmp2, [s.get("compare") for s in out["sections"]])

    def test_compare_from_later_member_survives_merge(self):
        import note_v2 as nv
        cmp2 = {"title": "对照", "cols": ["", "A", "B"], "rows": []}
        rv = {"sections": [
            {"heading": "需求弹性：一", "ts": "10:00", "prose": ["a"]},
            {"heading": "需求弹性：二", "ts": "10:05", "prose": ["b"], "compare": cmp2}]}
        out = nv.merge_units(rv, end_ts="10:10")
        self.assertEqual(len(out["sections"]), 1)
        self.assertEqual(out["sections"][0].get("compare"), cmp2)

    def test_why_clash_still_merges(self):
        # 第六轮：想一想几乎每节都有，算进撞件会让同主干小节全拆开、标题重复。
        import note_v2 as nv
        w1 = {"q": "为什么一？", "a": "因为一"}
        w2 = {"q": "为什么二？", "a": "因为二"}
        rv = {"sections": [
            {"heading": "需求弹性：一", "ts": "10:00", "prose": ["a"], "why": w1},
            {"heading": "需求弹性：二", "ts": "10:05", "prose": ["b"], "why": w2}]}
        out = nv.merge_units(rv, end_ts="10:10")
        self.assertEqual(len(out["sections"]), 1)
        self.assertEqual(out["sections"][0].get("why"), w1)


class MathFixes(unittest.TestCase):
    def test_frac_inside_bare_cases_no_nested_dollar(self):
        import note_math as nm
        t = r"C(q)=\begin{cases} \frac{1}{2}q & q>0 \\ 0 & q\le 0 \end{cases} 而 t = \frac{a}{b}"
        out = nm.auto_tex(t)
        self.assertEqual(out, r"C(q)=$\begin{cases} \frac{1}{2}q & q>0 \\ 0 & q\le 0 \end{cases}$"
                              r" 而 t = $\frac{a}{b}$")
        self.assertEqual(nm.auto_tex(out), out)                    # 幂等

    def test_second_derivative_zero_is_not_negative(self):
        import note_math as nm
        self.assertEqual(nm._sign_rows("二阶导数为 0，所以是极小值。"), [])
        self.assertEqual(nm._sign_rows("二阶导数为 0，因此该点为极大值点。"), [])


class ChemistryAsciiCharge(unittest.TestCase):
    def test_balanced_equations_not_flagged(self):
        import note_math as nm
        for eq in ("Fe → Fe2+ + 2e-", "Fe^{2+}+2e-→Fe", "Fe2++2e-→Fe",
                   "2Fe2+ + Cl2 → 2Fe3+ + 2Cl-", "NH3 + H+ → NH4+", "NH3+H+→NH4+",
                   "MnO4- + 8H+ + 5e- → Mn2+ + 4H2O"):
            self.assertEqual(nm._check_equation(eq), "", eq)

    def test_unbalanced_still_flagged(self):
        import note_math as nm
        self.assertIn("元素不守恒", nm._check_equation("2H2 + O2 → H2O"))
        self.assertTrue(nm._check_equation("Fe → Fe2+ + e-"))


class BuildNotesBatchFailure(unittest.TestCase):
    def test_failed_classify_batch_not_saved_as_skip(self):
        import json
        import build_notes as bn
        with tempfile.TemporaryDirectory() as tmp:
            nf = Path(tmp) / "term_notes.json"
            nf.write_text(json.dumps({"terms": {}}), encoding="utf-8")

            def boom(*a, **k):
                raise RuntimeError("429")
            with mock.patch.object(bn, "NOTES_FILE", nf), \
                    mock.patch.object(bn, "collect_terms", lambda c: ["elasticity"]), \
                    mock.patch.object(bn, "KEEP_EXTRA", []), \
                    mock.patch.object(bn, "_chat_json", boom), \
                    mock.patch("cloud_translator.load_api_key", lambda k=None: "k"), \
                    mock.patch("builtins.print"):
                bn.build()
            saved = json.loads(nf.read_text(encoding="utf-8")).get("terms", {})
            self.assertNotEqual(bn.normalize_entry(saved.get("elasticity", None)).get("level")
                                if "elasticity" in saved else None, "skip")


class WorkerRecapDuties(unittest.TestCase):
    def test_split_process_recap_uses_sidecar_duties(self):
        import note_worker as nw
        import recap
        got = {}

        def fake_build(**kw):
            got.update(kw)
            return {"ok": 1}
        item = {"id": "a", "kind": "item", "type": "作业", "title": "交 PS3",
                "date": "2026-11-25", "action": "saved"}
        with mock.patch.object(recap, "build", fake_build), \
                mock.patch.object(recap, "load", lambda c, root=None: {}):
            cb = nw.build_callbacks({"use_cloud": True, "trans_mode": "both",
                                     "course": "X", "duties": []})
            cb["recap_fn"]({"course": "X", "date": "2026-11-22", "duties": [item]})
        self.assertEqual([d["title"] for d in got.get("duties") or []], ["交 PS3"])


class OutlineUnicodeMinus(unittest.TestCase):
    def test_unicode_minus_division_checked(self):
        import note_outline as no
        t, fixes, _ = no._check_text("−5.3 除以 3 等于 −2.89。", {}, [])
        self.assertTrue(fixes)
        self.assertIn("-1.767", t)
        t2, fixes2, _ = no._check_text("−5.3 除以 3 等于 −1.767。", {}, [])
        self.assertEqual(fixes2, [])

    def test_no_double_sign(self):
        import note_outline as no
        self.assertEqual(no._replace_number("结果是 −2.89", "2.89", "-1.767"), "结果是 −2.89")


class HtmlAssetsResume(unittest.TestCase):
    def test_half_copied_assets_get_completed(self):
        import note_html as nh
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            src.mkdir()
            (src / "a.js").write_text("AAAA", encoding="utf-8")
            (src / "b.css").write_text("BBBBBB", encoding="utf-8")
            dst.mkdir()
            (dst / "a.js").write_text("AAAA", encoding="utf-8")      # probe 在
            (dst / "b.css").write_text("BB", encoding="utf-8")       # 半截
            self.assertTrue(nh._copy_flat(src, dst, "a.js"))
            self.assertEqual((dst / "b.css").read_text(encoding="utf-8"), "BBBBBB")
            self.assertEqual(sorted(p.name for p in dst.iterdir()), ["a.js", "b.css"])

    def test_install_assets_full(self):
        import note_html as nh
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "n.html"
            self.assertTrue(nh.install_assets(page))
            self.assertTrue((Path(tmp) / nh.KATEX_DIR / "fonts").is_dir())
            self.assertTrue((Path(tmp) / nh.HETI_DIR / "heti-addon.min.js").is_file())


class HtmlSliderJs(unittest.TestCase):
    """滑杆：多曲线 + 断点，连拖几次不抛、每条曲线按自己的 series 重画（node + 假 DOM）。"""

    def test_slider_multi_series_with_breaks(self):
        import json
        import shutil
        import subprocess
        import note_html as nh
        node = shutil.which("node")
        if not node:
            self.skipTest("没有 node")
        js = nh._JS
        a, b = js.index(" d.querySelectorAll('.slider')"), js.index(" var bar=")
        harness = Path(__file__).parent / "fixtures" / "js" / "slider_harness.js"
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "slider.js"
            src.write_text(js[a:b], encoding="utf-8")
            r = subprocess.run([node, str(harness), str(src)], capture_output=True,
                               text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertEqual(sorted(json.loads(r.stdout)),
                         ["c0 userline=1,1", "c0=0,1", "c2 userline=6,1", "c2=5,1"])

    def test_theme_first_click_when_system_dark(self):
        import json
        import shutil
        import subprocess
        import note_html as nh
        node = shutil.which("node")
        if not node:
            self.skipTest("没有 node")
        js = nh._JS
        code = js[js.index(" var tbtn="):js.index(" // ---- 版式 A")]
        h = ("const attrs={};const btn={textContent:'',l:{},addEventListener(t,f){this.l[t]=f}};"
             "const d={documentElement:{getAttribute:k=>attrs[k]||null,"
             "setAttribute:(k,v)=>{attrs[k]=v}},getElementById:()=>btn};"
             "global.window={matchMedia:q=>({matches:true})};"
             "new Function('d'," + json.dumps(code) + ")(d);"
             "const seq=[btn.textContent];btn.l.click();seq.push(attrs['data-theme']);"
             "console.log(JSON.stringify(seq));")
        r = subprocess.run([node, "-e", h], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertEqual(json.loads(r.stdout), ["☀︎ 亮色", "light"])

    def test_break_segments_marked_userline_in_svg(self):
        import note_html as nh
        out = nh.svg("function_cubic", {"a": 1.0, "b": 0.0, "c": -3.0, "d": 0.0})
        self.assertNotIn('class="c0 userline"', out.split("<polyline", 2)[1])  # 首段不是续段


class HtmlBoldLeadTags(unittest.TestCase):
    def test_sentence_end_inside_tag_keeps_tags_paired(self):
        import note_html as nh
        self.assertEqual(nh._bold_lead("<strong>第一句。第二句</strong>后面。", keyline=False),
                         "<strong>第一句。第二句后面。</strong>")
        self.assertEqual(nh._bold_lead("前 <code>a。b</code> 后。尾巴。", keyline=False),
                         "<strong>前 <code>a。b</code> 后。</strong>尾巴。")
        self.assertEqual(nh._bold_lead("第一句。第二句。", keyline=True),
                         '<strong class="keyline">第一句。</strong>第二句。')


class VgateCountedInLedger(unittest.TestCase):
    """「图该不该画」撤的图要进守卫台账的 dropped（结构判据：计数分支的标签集合里有它）。"""

    def test_label_in_dropped_branch(self):
        src = (Path(__file__).resolve().parents[1] / "obsidian_writer.py").read_text(encoding="utf-8")
        i = src.index('elif label == "图该不该画" and isinstance(extra_items, list):')
        self.assertIn('self._guard_counts["dropped"] += len(extra_items)', src[i:i + 300])


if __name__ == "__main__":
    unittest.main()
