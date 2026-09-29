#!/usr/bin/env python3
"""课程清单与准备度：并集规则 / 解析的歧义拒绝 / 「未知 ≠ 零」。

    ClassLive.app/Contents/MacOS/python tests/test_courses.py

## 这个文件钉住什么（判据的唯一定义点，别在别处再抄一份）

1. **`list_courses` 是两边的并集** —— 只看 `glossary/` 会漏「拖了课件没跑 prep」的课，
   只看 `~/.classlive/` 会漏「写了术语表没拖课件」的课。
2. ⭐ **`resolve` 遇歧义必须返回 `None`，不许挑一个。**
   原始动机是 `cl` 的 `find | head -1`：实测 `cl course 107` 会**静默选中 ECON10770**。
3. ⭐ **「读不出」与「零」必须分开**（`None` vs `0`）—— 把读失败显示成 0
   等于对用户谎称「这门课一个词都没有」。同 `prep._load_state` 那条规矩。
4. **`sessions/` 的课号是短号**（`10730`，不是 `ECON10730`）→ 匹配必须容错。

⚠️ 本模块**只读**，所以不需要「隔离写端」那一套；但仍然**只碰 tempdir**，
   绝不读真实的 `~/.classlive`（那会把测试结果绑在这台机器的当前状态上）。
   ⚠️ `courses.main()` 默认就指向真实 `~/.classlive` —— 所以要跑 CLI 那组，
      **必须显式传 `--state-root`**。这条被违反过一次（测试里混进了真机上存在的课）。

## ⚠️⚠️ 做变异测试时，两条会骗你的东西（都踩过）

1. **`.pyc` 会让「还原」变成假的。** CPython 判缓存看 `(源文件 mtime, 源文件 size)`。
   `return 2` → `return 0` **size 不变**，而 `cp` 还原的 mtime 又和变异写入落在同一秒
   → 缓存校验**通过** → 跑的还是变异版。症状是 `cmp` 说「源码逐字相同」，
   但**正在跑的不是那个文件**（2026-09-26 被骗了三轮）。
   → 每个变异前 `rm __pycache__/courses*.pyc`，并用 `PYTHONDONTWRITEBYTECODE=1` 跑。
2. ⭐ **测试数据会把自己的断言遮住。** 「短号能被认到」那条原本是假测试 ——
   夹具里同时放了 `…_ECON10730.md`，于是即使去掉 `endswith` 容错，
   全名文件也顶上去了，**照样绿**。修法是加一门**只以短号出现过**的课
   （`ECON10800`），让它没有替代路径可走。
   → **夹具里放多个等价来源，等于给断言留了后门。**
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import courses                                                     # noqa: E402
import paths as paths_mod                                          # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def _mk_glossary(root: pathlib.Path, *names: str) -> pathlib.Path:
    d = root / "glossary"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text("", encoding="utf-8")
    return root / "glossary.txt"


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)

        print("\n--- ① glossary_dir 的推导 ---")
        g = _mk_glossary(tmp, "ECON10740.txt")
        check("glossary.txt -> 它旁边的 glossary/",
              courses.glossary_dir(g) == tmp / "glossary",
              str(courses.glossary_dir(g)))

        print("\n--- ② list_courses 必须是两边并集 ---")
        state = tmp / ".classlive"
        (state / "courses" / "SOC10020").mkdir(parents=True)
        (state / "courses" / "ECON99999").mkdir(parents=True)   # 只在状态侧
        (state / "courses" / ".hidden").mkdir(parents=True)      # 点开头 = 不是课
        got = courses.list_courses(g, state_root=state)
        check("并集：术语表侧 + 状态侧都在",
              got == ["ECON10740", "ECON99999", "SOC10020"], str(got))
        check("点开头的目录不算课程", ".hidden" not in got)
        empty = tmp / "completely-empty"
        empty.mkdir()
        check("两边都没有 -> 空列表", courses.list_courses(
            empty / "nope.txt", state_root=empty / "nope") == [],
              str(courses.list_courses(empty / "nope.txt", state_root=empty / "nope")))

        print("\n--- ②b ⭐ 同一门课的**两个写法**不许变成两张卡 ---")
        # ⚠️ 这一组是 OCR 抓出来的真缺口：`cl` 的兜底分支会把**用户原样输入**
        #    写进 `.course`，所以 `~/.classlive/courses/10740/` 与
        #    `glossary/ECON10740.txt` 会**同时存在**（说的是同一门课）。
        #    裸并集 -> 面板上两张同一门课的卡，而短代号那张按裸 join 定位术语表
        #    -> 报「0 条术语」（模块头口径 3：0 就是谎报）。
        g2root = tmp / "dup"
        g2 = _mk_glossary(g2root, "ECON10740.txt", "ECON10770.txt")
        st2 = tmp / "dup_state"
        (st2 / "courses" / "10740").mkdir(parents=True)      # 短代号 = 同一门课
        (st2 / "courses" / "10770").mkdir(parents=True)
        (st2 / "courses" / "ZZ99999").mkdir(parents=True)    # 真没术语表 -> 保留原名
        got2 = courses.list_courses(g2, state_root=st2)
        check("⭐ 短代号目录被对账到规范课号（不出两张卡）",
              got2 == ["ECON10740", "ECON10770", "ZZ99999"], str(got2))

        check("glossary_file：精确命中",
              courses.glossary_file(g2, "ECON10740")
              == g2root / "glossary" / "ECON10740.txt")
        check("glossary_file：短代号**容错**到全名（不是裸 join）",
              courses.glossary_file(g2, "10740")
              == g2root / "glossary" / "ECON10740.txt",
              str(courses.glossary_file(g2, "10740")))
        check("glossary_file：找不到就回退成路径（不抛）",
              courses.glossary_file(g2, "nope") == g2root / "glossary" / "nope.txt")

        # ⚠️ **歧义时不许归并** —— 并错了是**静默用错术语表**，比多一张卡糟得多
        g3root = tmp / "amb"
        g3 = _mk_glossary(g3root, "ECON202.txt", "SOC202.txt")
        check("⚠️ 归并有歧义时**保留原名**（宁可多一张卡，也不并错课）",
              courses.canonical_course(g3, "202") == "202",
              courses.canonical_course(g3, "202"))

        # ⭐ 短代号那张卡的术语数必须是**真数**：0 = 谎报
        (g2root / "glossary" / "ECON10740.txt").write_text(
            "# ECON10740 X\n\nelasticity\n", encoding="utf-8")
        r_short = courses.readiness(g2, "10740", state_root=st2)
        check("⭐ 短代号的 readiness 也数得到术语（不是 0 —— 0 就是谎报）",
              r_short.terms == 1, f"terms={r_short.terms}")

        print("\n--- ③ resolve：精确 -> 唯一片段 -> 否则 None ---")
        known = ["ECON10730", "ECON10740", "ECON10770", "ECON10790", "SOC10020"]
        check("精确命中", courses.resolve("ECON10740", known) == "ECON10740")
        check("两边空白也认", courses.resolve("  ECON10740 ", known) == "ECON10740")
        check("短号唯一命中 -> 补全", courses.resolve("1077", known) == "ECON10770")
        check("空串 -> None", courses.resolve("", known) is None)
        check("全不相干 -> None", courses.resolve("ZZZ", known) is None)
        # ⭐ 这两条是原始动机：歧义时**必须拒绝**
        check("⭐ 歧义（107 命中 4 门）-> None，不许挑一个",
              courses.resolve("107", known) is None,
              f"返回了 {courses.resolve('107', known)}")
        check("⭐ 歧义（ECON 命中 4 门）-> None",
              courses.resolve("ECON", known) is None)
        check("歧义但多打一位就唯一了（10770）",
              courses.resolve("10770", known) == "ECON10770")
        check("candidates：歧义时列全 4 门（供报错文案用）",
              courses.candidates("107", known) == ["ECON10730", "ECON10740",
                                                   "ECON10770", "ECON10790"],
              str(courses.candidates("107", known)))
        check("candidates：精确命中只回它自己，不列同族",
              courses.candidates("ECON10740", known) == ["ECON10740"],
              str(courses.candidates("ECON10740", known)))
        check("candidates：没命中 -> 空", courses.candidates("ZZZ", known) == [])
        check("candidates：空串 -> 空", courses.candidates("   ", known) == [])
        # ⭐⭐ 这一条才是「精确命中短路」的真判据。
        #    上面那几条**分辨不出**短路有没有 —— 因为 `known` 里没有哪门课包含另一门，
        #    去掉短路后结果照样一样（实测：变异后仍 36/36 全绿 = 假测试）。
        #    要触发差异，需要一个**精确命中同时又是另一门课的子串**的输入，
        #    而那在真实数据里是可能的（`SOC1002` / `SOC10020`）。
        collide = ["ECON1073", "ECON10730"]
        check("⭐ 精确命中必须压过片段命中（否则把课号写全了反而被判歧义）",
              courses.resolve("ECON1073", collide) == "ECON1073",
              str(courses.resolve("ECON1073", collide)))
        check("⭐ candidates：精确命中时不带上更长的同族",
              courses.candidates("ECON1073", collide) == ["ECON1073"],
              str(courses.candidates("ECON1073", collide)))
        check("回到片段语义：前缀仍然命中两个",
              courses.candidates("1073", collide) == ["ECON1073", "ECON10730"])
        check("⭐ resolve 与 candidates 是同一规则的两个视图（防漂移）",
              all((courses.resolve(w, known) == (c[0] if len(c) == 1 else None))
                  for w in ("107", "1077", "10740", "ECON", "ZZZ", "" )
                  for c in [courses.candidates(w, known)]))

        print("\n--- ④ 术语计数：注释/空行/读不出 ---")
        d = tmp / "glossary"
        (d / "T1.txt").write_text("# ECON10740 课名\n\napple\nbanana\n  # 注释\ncherry\n",
                                  encoding="utf-8")
        r = courses.readiness(g, "T1", sessions_dir=None, state_root=state)
        check("跳过空行与 # 注释 -> 3 条", r.terms == 3, str(r.terms))
        check("标题取首行并去掉 #", r.title == "ECON10740 课名", r.title)
        (d / "B1.txt").write_bytes(b"\xff\xfe\x00bad")           # 非法 UTF-8
        r2 = courses.readiness(g, "B1", sessions_dir=None, state_root=state)
        check("⭐ 术语表读不出 -> None（不是 0）", r2.terms is None, str(r2.terms))
        r3 = courses.readiness(g, "不存在", sessions_dir=None, state_root=state)
        check("术语表不存在 -> 0（真的是零）", r3.terms == 0, str(r3.terms))
        check("没有术语表时标题退回课号", r3.title == "不存在", r3.title)

        print("\n--- ⑤ prep-state：墓碑条数，读不出不是零 ---")
        pdir = state / "courses" / "T1"
        pdir.mkdir(parents=True, exist_ok=True)
        check("没跑过 prep -> 0", courses.readiness(
            g, "T1", sessions_dir=None, state_root=state).auto_added == 0)
        (pdir / "prep-state.json").write_text(
            '{"appended": {"a": 1, "b": 2, "c": 3}}', encoding="utf-8")
        check("墓碑 3 条 -> 3", courses.readiness(
            g, "T1", sessions_dir=None, state_root=state).auto_added == 3)
        (pdir / "prep-state.json").write_text("{ 坏掉的 json", encoding="utf-8")
        check("⭐ 墓碑读不出 -> None（不是 0）", courses.readiness(
            g, "T1", sessions_dir=None, state_root=state).auto_added is None)

        print("\n--- ⑥ 课件份数 ---")
        md = state / "courses" / "T1" / "materials"
        md.mkdir(parents=True, exist_ok=True)
        check("空 materials/ -> 0", courses.readiness(
            g, "T1", sessions_dir=None, state_root=state).materials == 0)
        (md / "w1.pdf").write_text("x"); (md / "w2.pptx").write_text("x")
        (md / ".DS_Store").write_text("x")
        check("数文件、跳过点开头的 -> 2", courses.readiness(
            g, "T1", sessions_dir=None, state_root=state).materials == 2)

        print("\n--- ⑦ last_session：短号容错 + 取最大日期 ---")
        sd = tmp / "sessions"
        sd.mkdir()
        for n in ("2026-09-10_140200_10730.md",
                  "2026-09-20_090000_10730.md",
                  "2026-09-25_120000_ECON10730.md",   # 全名也要认
                  "2026-09-18_120000_10770.md",       # 别的课，不许混进来
                  "2026-09-11_134227_TEST.md",        # 测试残留
                  # ⚠️ 那个 .txt 的日期**故意大于** 09-25（OCR 指出：原来它是 09-19，
                  #    比最大日期还早 —— 于是「把 .md 过滤整个删掉」它照样绿，
                  #    夹具根本没覆盖它声称要覆盖的行为 = 给断言留的后门）。
                  #    现在它只要能漏进来就会**顶掉** 09-25，断言才有区分能力。
                  "2026-09-30_120000_10730.txt",      # 不是 .md（日期最晚）
                  # ⚠️ **这一条是补充，不是主力**：`_session_course` 把它解析成
                  #    `10730_TEST`，而 `course.endswith("10730_TEST")` 对 ECON10730 是 False
                  #    —— 所以它**永远匹配不上真课**，加不加都不改变结果。
                  #    它的作用是挡住「放松匹配」那一类改法（比如只看第一个下划线段）。
                  #    ⚠️ 真正的区分能力来自上面那条 .txt（独立审查指出这点）。
                  "2026-09-28_120000_10730_TEST.md",  # 残留，日期也晚于 09-25
                  # ⭐ **只以短号出现**的课 —— 容错的真判据。
                  #    上面 10730 那组同时有全名文件，会把差异**遮住**：
                  #    实测把 endswith 容错去掉后，「短号能被认到」那条**照样绿**
                  #    （因为 `2026-09-25_120000_ECON10730.md` 顶了上去）。
                  "2026-09-22_120000_10800.md"):
            (sd / n).write_text("x")
        # ⭐ 空课号段（`…_140200_.md`）：**不许混进任何一门课**。
        #    `course.endswith("")` 恒为真 → 漏进去的话它属于每一门课，
        #    而且日期（09-30）故意排在所有真记录之后 —— 它会顶掉下面每一条日期断言。
        #    2026-09-28 OCR 指出；作者当天真实 sessions/ 里实测 0 个这种文件。
        (sd / "2026-09-30_235959_.md").write_text("x")
        ls = courses.last_session(sd, "ECON10730")
        check("⭐ 短号与全名混在一起也认（取最大日期）",
              ls == "2026-09-25", str(ls))
        check("⭐ 只以短号出现过的课也能认到（去掉容错就红）",
              courses.last_session(sd, "ECON10800") == "2026-09-22",
              str(courses.last_session(sd, "ECON10800")))
        check("别的课不混进来（10770 的日期更早，没顶掉）",
              courses.last_session(sd, "ECON10770") == "2026-09-18")
        check("没上过的课 -> None", courses.last_session(sd, "SOC10020") is None)
        check("sessions 目录不存在 -> None", courses.last_session(tmp / "nope", "X") is None)
        check("⭐ 课号中含 _ 也能切对", courses._session_course(
            "2026-09-10_140200_MY_COURSE") == "MY_COURSE")
        check("形状不对 -> None", courses._session_course("nonsense") is None)
        check("⭐ 空课号段算「形状不对」（当成课号的话它属于每一门课）",
              courses._session_course("2026-09-30_235959_") is None,
              str(courses._session_course("2026-09-30_235959_")))
        # ⚠️ 原来这条是**恒真**的（`!= "2026-09-11"` —— 上一行已经确定结果是 09-25，
        #    这个不等式无论如何都成立）。改成有区分能力的形状：`.txt` 与 `*_TEST.md`
        #    的日期都排在 09-25 **之后**，于是「两者都被正确排除」是唯一能得出 09-25 的解释：
        #    去掉 `.md` 过滤 -> 09-30 顶上来；残留的课号匹配一旦放松 -> 09-28 顶上来。
        check("⭐ 非 .md 与 TEST 残留都不会顶掉真日期（两个都晚于 09-25）",
              courses.last_session(sd, "ECON10730") == "2026-09-25",
              str(courses.last_session(sd, "ECON10730")))

        print("\n--- ⑧ Readiness 是冻结的（界面拿到的是一份快照）---")
        rr = courses.readiness(g, "T1", sessions_dir=sd, state_root=state)
        try:
            # ⚠️ 必须用**普通赋值**测 —— `object.__setattr__` 本来就是绕过 frozen 的
            #    正规姿势，拿它当判据会永远「通过」，是个假量具。
            rr.terms = 999
            check("Readiness 不可变", False, "竟然改成功了")
        except Exception as e:                                   # noqa: BLE001
            check("Readiness 不可变", type(e).__name__ == "FrozenInstanceError",
                  type(e).__name__)
        check("冻结的是类型，不是我不小心漏了字段", rr.terms == 3, str(rr.terms))

        print("\n--- ⑨ main() 的退出码（bash 只读得动这个，所以它是真判据）---")
        import contextlib
        import io
        empty2 = tmp / "cli"
        empty2.mkdir()
        gg = _mk_glossary(empty2, "ECON10730.txt", "ECON10740.txt", "SOC10020.txt")
        # ⚠️ **必须显式指向空 state root。** 不给的话 `list_courses` 会去读真实的
        #    `~/.classlive`，于是列出来的课里会混进这台机器上真实存在的课 ——
        #    本文件开头那句「绝不读真实 ~/.classlive」就是被这一条违反过。
        fake_state = empty2 / "state"
        fake_state.mkdir()

        def cli(*argv):
            so, se = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
                rc = courses.main(["--glossary", str(gg),
                                   "--state-root", str(fake_state), *argv])
            return rc, so.getvalue(), se.getvalue()

        rc, out, _ = cli("--resolve", "ECON10740")
        check("唯一命中 -> rc=0 且 stdout 是规范课号",
              rc == 0 and out.strip() == "ECON10740", f"rc={rc} out={out!r}")
        # ⚠️ 用 `107`（同时命中 ECON10**7**30 与 ECON10740）—— `1073` 只命中一门，
        #    拿它测歧义会假绿。
        rc, out, err = cli("--resolve", "107")
        check("⭐ 歧义 -> rc=2，候选走 stderr（不是 stdout）",
              rc == 2 and out.strip() == ""
              and err.split() == ["ECON10730", "ECON10740"],
              f"rc={rc} out={out!r} err={err!r}")
        rc, out, err = cli("--resolve", "ZZZ")
        check("⭐ 没命中 -> rc=1 且**两边都空**（非空输出留给「解析炸了」）",
              rc == 1 and out.strip() == "" and err.strip() == "",
              f"rc={rc} out={out!r} err={err!r}")
        rc, out, _ = cli("--list", "--current", "SOC10020")
        lines = out.splitlines()
        check("--list 列出全部并给当前课打星",
              rc == 0 and lines == ["  ECON10730", "  ECON10740", "* SOC10020"],
              str(lines))
        rc, out, _ = cli()
        check("不给参数 -> rc=1（打印用法）", rc == 1, f"rc={rc}")

        # ⭐ 隔离性本身也要钉住：只在 fake root 里存在的课**必须**被列出来，
        #    而真实 ~/.classlive 里的课**必须不出现**（上面 --list 那条已经在管后者）。
        (fake_state / "courses" / "ONLYHERE").mkdir(parents=True)
        rc, out, _ = cli("--list")
        check("⭐ --state-root 真的生效（只在该根里的课也列出来）",
              "ONLYHERE" in out, out.replace("\n", "|"))

    print("\n--- ⑩ 新增课程：四个动作各走各的 ---")
    # ⚠️ 这一节全部是**纯函数**，只碰 tempdir。
    K = ["ECON10730", "ECON10740", "ECON10770"]

    r = courses.plan_add("  ECON10999  ", K)
    check("全新课号 -> create（两头空格会 strip）",
          r["action"] == "create" and r["course"] == "ECON10999", str(r))

    # ⭐⭐ 下面两条是这一节存在的理由：**已经有的绝不许再建一次**。
    #    重名的后果不是"少建一门课"，是两门课共用一份术语表 —— 静默用错术语表。
    r = courses.plan_add("ECON10740", K)
    check("⭐ 已有课号 -> exists，**不是** create",
          r["action"] == "exists" and r["course"] == "ECON10740", str(r))
    r = courses.plan_add("1074", K)          # 只被 ECON10740 含
    check("⭐ 片段唯一命中 -> exists（不是拿短号去建一门新课）",
          r["action"] == "exists" and r["course"] == "ECON10740", str(r))

    r = courses.plan_add("107", K)           # 三门都含
    check("⭐ 歧义 -> pick，**不替用户挑一个**（同 `resolve` 的取向）",
          r["action"] == "pick" and r["course"] == "" and len(r["hits"]) == 3,
          str(r))
    _t = courses.plan_add("1", ["A1", "B1", "C1", "D1", "E1"])["text"]
    check("候选超过 3 门时：只列 3 门，但要说清一共几门（截断不许像穷尽）",
          _t.count("·") == 2 and "5 门" in _t, _t)

    check("空 -> bad", courses.plan_add("   ", K)["action"] == "bad")
    check("带斜杠 -> bad（会变成路径）", courses.plan_add("A/B", K)["action"] == "bad")
    check("带冒号 -> bad（Finder 会把它显示成斜杠）",
          courses.plan_add("A:B", K)["action"] == "bad")
    check("以点开头 -> bad（建出来是隐藏文件，面板上永远不出现）",
          courses.plan_add(".ECON", K)["action"] == "bad"
          and courses.plan_add(".ECON", K)["action"] != "create")

    # 边界：正好 MAX_CODE 可以，多一个就不行（防 off-by-one）
    check(f"正好 {courses.MAX_CODE} 个字 -> 可以",
          courses.valid_code("A" * courses.MAX_CODE) == "")
    check(f"{courses.MAX_CODE + 1} 个字 -> 不行",
          courses.valid_code("A" * (courses.MAX_CODE + 1)) != "")

    print("\n--- ⑪ create：只建不覆盖 + 建完真的会出现 ---")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        new_p = tdp / "glossary" / "ECON10999.txt"
        check("建出新文件，首行是 `# 课号`（与 `prep.append_terms` 同一约定）",
              courses.create(new_p, "ECON10999") is True
              and new_p.read_text(encoding="utf-8") == "# ECON10999\n",
              repr(new_p.read_text(encoding="utf-8") if new_p.exists() else None))

        # ⭐⭐ 这条是本节的重头：判完到真写之间隔着一次界面往返，用户完全可能在
        #     别处（cl course / Finder）已经把课建好了 —— 覆盖 = 删掉他刚写的东西。
        new_p.write_text("# 手写的\nKuhn-Tucker 条件\n", encoding="utf-8")
        check("⭐⭐ 已存在时**一个字都不动**，且返回 False",
              courses.create(new_p, "ECON10999") is False
              and new_p.read_text(encoding="utf-8") == "# 手写的\nKuhn-Tucker 条件\n",
              repr(new_p.read_text(encoding="utf-8")))

        # 建完必须能在清单里看见 —— 否则「建好了」但面板上一张卡都不多（谎报）
        names = courses.list_courses(tdp / "glossary.txt",
                                     state_root=tdp / "state")
        check("⭐ 建完就出现在 `list_courses` 里（面板上真的会多一张卡）",
              "ECON10999" in names, str(names))

    print("\n--- ⑫ facts：确认框要说的那几个数 ---")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        gdir = tdp / "glossary"
        gdir.mkdir()
        gl = gdir / "ECON10770.txt"
        gl.write_text("# ECON10770\n句子\n", encoding="utf-8")
        st = tdp / "state"
        mats = st / "courses" / "ECON10770" / "materials"
        mats.mkdir(parents=True)
        for n in ("a.pdf", "b.pdf"):
            (mats / n).write_bytes(b"x" * 10)
        ses = tdp / "sessions"
        ses.mkdir()
        for i in range(3):
            (ses / f"2026-09-0{i + 1}_090000_10770.md").write_text("x", encoding="utf-8")

        f = courses.facts(tdp / "glossary.txt", "ECON10770",
                          sessions_dir=ses, state_root=st)
        check("术语表字节数 = 文件真实大小",
              f["glossary_bytes"] == gl.stat().st_size, str(f["glossary_bytes"]))
        check("课件份数 = 2", f["materials"] == 2, str(f["materials"]))
        check("⭐ 上课记录节数 = 3（文件名是短号也要认出来）",
              f["sessions"] == 3, str(f["sessions"]))

    print("\n--- ⑬ delete：唯一会删东西的入口（**假 trash，绝不碰真废纸篓**）---")

    def _fixture(tdp, name="ECON10770"):
        """造一门「三样俱全」的课：术语表 + 课件 + 一节上课记录。"""
        gdir = tdp / "glossary"
        gdir.mkdir(exist_ok=True)
        (gdir / f"{name}.txt").write_text(f"# {name}\n句子\n", encoding="utf-8")
        st = tdp / "state"
        mats = st / "courses" / name / "materials"
        mats.mkdir(parents=True, exist_ok=True)
        (mats / "讲义.pdf").write_bytes(b"x" * 10)
        ses = tdp / "sessions"
        ses.mkdir(exist_ok=True)
        rec = ses / f"2026-09-01_090000_{name[-5:]}.md"
        rec.write_text("# 一节课\n", encoding="utf-8")
        return tdp / "glossary.txt", st, ses, rec, gdir / f"{name}.txt", mats

    def _fake_trash(box, bin_dir):
        """假废纸篓：**真把东西搬走**，只是目的地是 tempdir 里的一个桶。

        ⚠️ 第一版只 `box.append(p)` 就返回 —— 于是 `list_courses` 当然还看得见它，
        三条判据全红，而红的是**量具**不是被测的东西（"东西真的不在了"这件事
        根本没被模拟）。同 `test_voice.py` 那次：**变异体没拿去跑判据**。
        """
        bin_dir.mkdir(exist_ok=True)

        def fn(p):
            pp = pathlib.Path(p)
            box.append(str(pp))
            if pp.exists():
                dest = bin_dir / pp.name
                n = 2
                while dest.exists():
                    dest = bin_dir / f"{pp.name}-{n}"
                    n += 1
                import shutil
                shutil.move(str(pp), str(dest))
            return True, ""
        return fn

    # ── ① 全部删除 ────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        gl, st, ses, rec, gfile, mats = _fixture(tdp)
        box = []
        res = courses.delete(gl, "ECON10770", sessions_dir=ses, state_root=st,
                             keep_materials=False,
                             trash_fn=_fake_trash(box, tdp / "_trashbox"))
        check("全部删除：术语表 + 课程目录**两样**都进了废纸篓",
              str(gfile) in box and str(st / "courses" / "ECON10770") in box,
              str(box))
        check("没有错误", res["errors"] == [] and res["kept"] is None, str(res))
        check("⭐⭐ **`sessions/` 一个字节都不动**（硬规矩：上课记录是历史不是配置）",
              rec.exists() and rec.read_text(encoding="utf-8") == "# 一节课\n")
        check("⭐ 删完就不在 `list_courses` 里了（面板上那张卡真的消失）",
              "ECON10770" not in courses.list_courses(gl, state_root=st),
              str(courses.list_courses(gl, state_root=st)))

    # ── ② 只删课号 ────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        gl, st, ses, rec, gfile, mats = _fixture(tdp)
        box = []
        res = courses.delete(gl, "ECON10770", sessions_dir=ses, state_root=st,
                             keep_materials=True,
                             trash_fn=_fake_trash(box, tdp / "_trashbox"))
        check("只删课号：术语表进废纸篓", str(gfile) in box, str(box))
        check("⭐ 课程目录**没进废纸篓**（它去保留区）",
              str(st / "courses" / "ECON10770") not in box, str(box))
        kept = res["kept"]
        check("⭐ 课件**真的搬到了保留区**，原件还在",
              kept and (pathlib.Path(kept) / "materials" / "讲义.pdf").exists(),
              str(kept))
        check("保留区在 `courses/.removed/` 下",
              kept and pathlib.Path(kept).parent == st / "courses" / ".removed",
              str(kept))
        check("⭐⭐ 上课记录照样没动", rec.exists())
        check("⭐ 搬运之后 **`list_courses` 里也没有它了**（这才是「只删课号」能生效的原因）",
              "ECON10770" not in courses.list_courses(gl, state_root=st),
              str(courses.list_courses(gl, state_root=st)))

        # ── ③ 幂等 ───────────────────────────────────────────────────
        box2 = []
        res2 = courses.delete(gl, "ECON10770", sessions_dir=ses, state_root=st,
                              keep_materials=True,
                              trash_fn=_fake_trash(box2, tdp / "_trashbox"))
        check("幂等：再删一次不炸、也不算错误、什么都不用动",
              res2["errors"] == [] and res2["trashed"] == []
              and res2["kept"] is None and box2 == [], str(res2))

    # ── ④ 保留区重名不覆盖 ────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        gl, st, ses, rec, gfile, mats = _fixture(tdp)
        old = st / "courses" / ".removed" / "ECON10770"
        old.mkdir(parents=True)
        (old / "上一次搬来的.txt").write_text("别动我", encoding="utf-8")
        res = courses.delete(gl, "ECON10770", sessions_dir=ses, state_root=st,
                             keep_materials=True,
                             trash_fn=_fake_trash([], tdp / "_trashbox"))
        check("⭐ 保留区已有同名 -> 加 `-2` 后缀（**绝不覆盖上一次那批课件**，"
              "同 `prep._archive` 的约定）",
              pathlib.Path(res["kept"]).name == "ECON10770-2"
              and (old / "上一次搬来的.txt").read_text(encoding="utf-8") == "别动我",
              str(res["kept"]))

    # ── ⑤ 失败要报出来，不许静默 ──────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        gl, st, ses, rec, gfile, mats = _fixture(tdp)
        res = courses.delete(gl, "ECON10770", sessions_dir=ses, state_root=st,
                             keep_materials=False,
                             trash_fn=lambda p: (False, "磁盘满了"))
        check("⭐ 删不掉时 `errors` 里有**人话原因**（不许静默当成功）",
              len(res["errors"]) == 2 and all("磁盘满了" in e for e in res["errors"]),
              str(res["errors"]))

    print("\n--- ⑭ 纠正日志：免费拿到的标注集 ---")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        n = courses.record_batch(
            [("/x/a.pdf", "ECON10770"), ("/x/b.pdf", "SOC10020")],
            root=tdp, ai={"/x/a.pdf": "SOC10020", "/x/b.pdf": "SOC10020"})
        check("⭐ 记了 2 条", n == 2, str(n))
        got = courses.corrections(root=tdp)
        check("读回来 2 条，路径/课号都对",
              len(got) == 2 and got[0]["path"] == "/x/a.pdf"
              and got[0]["course"] == "ECON10770", str(got))
        check("⚠️ 空课号的行被挡掉（明显无意义的那种）—— "
              "但**「哪些才算标注」不归这里管**（那是 group_for_archive 的事）",
              courses.record_batch([("/x/z.pdf", "")], root=tdp) == 0
              and len(courses.corrections(root=tdp)) == 2)
        check("⭐ 同时记下**模型当时说的**（有它才算得出「改对了几条」）",
              got[0]["ai"] == "SOC10020", str(got[0]))
        check("每条都带时间戳", all(r.get("at") for r in got), str(got))

        # ⭐ 追加而不是覆盖
        courses.record_batch([("/x/d.pdf", "ECON10730")], root=tdp)
        check("⭐ 是**追加**不是覆盖（第二次之后 3 条）",
              len(courses.corrections(root=tdp)) == 3,
              str(len(courses.corrections(root=tdp))))

        # ⭐⭐ 读不出来时**不许覆盖**（这是唯一一份）
        p2 = paths_mod.corrections_log(root=tdp)
        p2.write_text("{ 这不是 JSON", encoding="utf-8")
        try:
            courses.record_batch([("/x/e.pdf", "X")], root=tdp)
            check("⭐⭐ 日志坏了 -> **抛**，不静默覆盖真数据", False, "没抛")
        except Exception as e:                                # noqa: BLE001
            check("⭐⭐ 日志坏了 -> **抛**，不静默覆盖真数据",
                  # ⚠️ 删掉恒假子句（2026-09-28 审查指出）：`"不是 JSON"` 全仓库
                  #    没有任何代码会产出它（`store.load_json` 报的是「状态文件读不出来」），
                  #    留着会让读者以为这条在核对消息内容。
                  "状态文件读不出来" in str(e), type(e).__name__)
        check("⭐⭐ 而且**真内容一个字没动**",
              p2.read_text(encoding="utf-8") == "{ 这不是 JSON")

    print("\n--- orphan_files 与 session_files 必须走**同一条**匹配规则 ---")
    # ⭐⭐ 2026-09-29 修：`orphan_files` 原来用精确集合判定（`c in known`），
    #    而 `session_files` 用容错匹配（`course == c or course.endswith(c)`）→
    #    **短号文件同时出现在两组列表里**（既算进这门课的 rows，又被当成"没归课"），
    #    还会诱使用户把它「判给」错误的课。
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _d:
        _dd = pathlib.Path(_d)
        (_dd / "2026-09-10_140200_10730.md").write_text("# x", encoding="utf-8")
        (_dd / "2026-09-11_140200_LECTURE.md").write_text("# x", encoding="utf-8")
        _known = ["ECON10730"]
        _rows = courses.session_files(_dd, "ECON10730")
        _orph = courses.orphan_files(_dd, _known)
        check("⭐ 短号文件算进这门课的课次（`session_files`）",
              [p.name for p in _rows] == ["2026-09-10_140200_10730.md"],
              str([p.name for p in _rows]))
        check("⭐⭐ 同一个短号文件**不许**同时出现在孤儿组里（`orphan_files`）",
              [p.name for p in _orph] == ["2026-09-11_140200_LECTURE.md"],
              f"孤儿组拿到 {[p.name for p in _orph]}")
        check("⭐ 两组**没有交集**（这才是这两条判据真正要的）",
              not (set(_rows) & set(_orph)))
        check("`course_matches` 就是那条规则（单一定义点）",
              courses.course_matches("10730", "ECON10730")
              and not courses.course_matches("99999", "ECON10730"))

    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
