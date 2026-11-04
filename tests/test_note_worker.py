#!/usr/bin/env python3
"""后台收尾 worker（`note_worker` + `main` 那处分岔）判据 —— 派生用替身，不真起进程。

跑法: ClassLive.app/Contents/MacOS/python tests/test_note_worker.py

钉住的是计划 §4 的契约：**派生失败要退回进程内**（绝不因此丢笔记）、
**spec 里不许出现 key**、**回调判据与 main 那三个 `_…_for` 一致**（纯转录档一个请求都不发）、
**worker 自己写 outbox 而绝不碰上传队列**（§12-4）。
"""
import json
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import main                                                        # noqa: E402
import note_jobs as NJ                                             # noqa: E402
import note_worker as NW                                           # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


SPEC = {
    "session": "/tmp/nope.md", "course": "ECON10790", "vault": None,
    "date": "2026-11-04", "polish": False, "cloud_model": "deepseek-flash",
    "glossary": None, "use_cloud": True, "trans_mode": "both", "ever_off": False,
    "duties": [], "qa": [],
}


def t_spec_has_no_key() -> None:
    s = main.note_job_spec(session_path="/tmp/x.md", course="ECON10790",
                           use_cloud=True, cloud_model="deepseek-flash")
    blob = json.dumps(s, ensure_ascii=False)
    bad = [k for k in ("key", "token", "secret", "authorization") if k in blob.lower()]
    check("⭐⭐ spec 里不许出现 key / token（它落在 ~/.classlive/ 下的明文文件）",
          not bad, str(bad))
    check("⭐ spec 全部可 JSON 序列化（过进程边界的那一份）",
          json.loads(blob) == s)
    check("⭐ use_cloud 只记有没有 key 这个决定，不是 key 本身",
          s["use_cloud"] is True and "credential" not in blob)


def t_callbacks_respect_trans_mode() -> None:
    def _with(mode, use_cloud=True, ever_off=False):
        sp = {**SPEC, "use_cloud": use_cloud, "trans_mode": mode, "ever_off": ever_off}
        cb = NW.build_callbacks(sp)
        calls = []
        cb["lost_explain_fn"]([{"who": "x"}])
        cb["backfill_fn"]([])
        cb["recap_fn"]({})
        return cb

    cb = NW.build_callbacks({**SPEC, "use_cloud": False})
    check("⭐⭐ 没 key -> ❓讲解 / 补译 / 课前回顾 都直接返回空（一个请求都不发）",
          cb["lost_explain_fn"]([{}]) == []
          and cb["backfill_fn"]([]) == ([], {"need": 0, "done": 0})
          and cb["recap_fn"]({}) == {})

    cb = NW.build_callbacks({**SPEC, "trans_mode": "raw"})
    check("⭐⭐ 纯转录档 -> 同上：整节一个模型请求都不发（DESIGN.md 硬契约）",
          cb["lost_explain_fn"]([{}]) == []
          and cb["backfill_fn"]([]) == ([], {"need": 0, "done": 0})
          and cb["recap_fn"]({}) == {})


def t_callbacks_degrade_not_raise() -> None:
    # 真有 key 但网络 / 依赖坏掉：回调必须 fail-soft 返回空，不许把 note 那层带走。
    import llm_client
    orig = llm_client.chat_json
    llm_client.chat_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        cb = NW.build_callbacks({**SPEC, "use_cloud": True, "trans_mode": "both"})
        ok = True
        try:
            cb["recap_fn"]({"course": "X"})
            cb["lost_explain_fn"]([{"who": "x"}])
        except Exception:                                                    # noqa: BLE001
            ok = False
        check("⭐ 回调内部炸掉也**不抛**（额外一层不许带走笔记）", ok)
    finally:
        llm_client.chat_json = orig


def t_spawn_claims_then_popens() -> None:
    with tempfile.TemporaryDirectory() as d:
        jobs_root = str(pathlib.Path(d, "note-jobs"))
        seen = []

        def _fake_popen(args, kw):
            seen.append((list(args), dict(kw)))

        got = main.spawn_note_worker("stem1", SPEC, jobs_root=jobs_root,
                                     popen=_fake_popen)
        check("⭐ 派生成功", got is True and len(seen) == 1, str(seen))
        args, kw = seen[0]
        check("⭐⭐ `start_new_session=True`（父进程退出不带走它 —— 整件事的目的）",
              kw.get("start_new_session") is True, str(kw.get("start_new_session")))
        check("⭐ 命令行是 `-m note_worker <stem>`",
              args[1:4] == ["-m", "note_worker", "stem1"], str(args[:4]))
        check("⭐ stdout / stderr **都不是**原来的终端（父进程退出后终端没了）",
              kw.get("stdout") is not None and kw.get("stdout") != sys.stdout,
              str(kw.get("stdout")))
        # ⚠️⚠️ 这里原来给的是 `NJ.jobs_dir()`（**已含 `note-jobs`**），而 `jobs_root`
        #    是 `note_jobs` 的 root —— 两种口径混着用会写成 `…/note-jobs/note-jobs/`，
        #    worker 拿到 `--jobs-root` 也找不到自己那份 spec。这条就是抓它。
        j = NJ.read("stem1", root=jobs_root)
        check("⭐⭐ spec 写进了 job 记录（worker 唯一的输入）",
              (j.get("spec") or {}).get("course") == "ECON10790", str(list(j)))
        check("⭐ 占位状态是 running", j.get("state") == NJ.RUNNING, str(j.get("state")))

        # 第二次：已有活任务 -> 不再派
        seen.clear()
        again = main.spawn_note_worker("stem1", SPEC, jobs_root=jobs_root,
                                       popen=_fake_popen)
        check("⭐⭐ 同一节已经有人在跑 -> 不派第二个（§12-3）",
              again is False and not seen, str(again))


def t_spawn_failure_is_fail_soft() -> None:
    with tempfile.TemporaryDirectory() as d:
        jobs_root = str(pathlib.Path(d, "note-jobs"))

        def _boom(args, kw):
            raise OSError("no such file")

        got = main.spawn_note_worker("stem2", SPEC, jobs_root=jobs_root, popen=_boom)
        check("⭐⭐ Popen 抛 -> 返回 False（**不抛**，调用方据此退回进程内）", got is False)
        check("⭐ 起不来也不留一个假 running（不然恢复函数会一直跳过）",
              NJ.alive("stem2", root=jobs_root) is False
              or NJ.read("stem2", root=jobs_root).get("state") != NJ.RUNNING,
              str(NJ.read("stem2", root=jobs_root).get("state")))


def t_run_without_spec_fails_cleanly() -> None:
    with tempfile.TemporaryDirectory() as d:
        r = NW.run("nosuch", root=str(pathlib.Path(d, "jobs")))
        check("⭐⭐ 没有 spec -> 干净失败并记 failed（不是 traceback）",
              r["ok"] is False and "spec" in r["error"], str(r))
        j = NJ.read("nosuch", root=str(pathlib.Path(d, "jobs")))
        check("⭐ 收工状态记 failed", j.get("state") == NJ.FAILED, str(j.get("state")))


def t_run_missing_session_fails_cleanly() -> None:
    with tempfile.TemporaryDirectory() as d:
        jobs = str(pathlib.Path(d, "jobs"))
        NJ.claim("s1", root=jobs, spec={**SPEC, "session": str(pathlib.Path(d, "nope.md"))})
        r = NW.run("s1", root=jobs)
        check("⭐ 会话文件不在 -> 失败但不抛",
              r["ok"] is False and "会话文件" in r["error"], str(r))
        check("⭐ 也记 failed（恢复函数据此接手）",
              NJ.read("s1", root=jobs).get("state") == NJ.FAILED)


def t_outbox_written_only_on_success() -> None:
    with tempfile.TemporaryDirectory() as d:
        note = pathlib.Path(d, "Lectures") / "2026-11-04_ECON10790.md"
        note.parent.mkdir(parents=True)
        note.write_text("note", encoding="utf-8")
        (note.with_suffix(".html")).write_text("<html>", encoding="utf-8")
        root = pathlib.Path(d, "state")
        ok = NW._outbox("s1", str(note), root=str(root))
        check("⭐ 成功 -> 写 outbox（md + html 两条）", ok is True)
        got = json.loads((root / "note-outbox" / "s1.json").read_text(encoding="utf-8"))
        check("⭐⭐ outbox 只有**文件清单**，不碰上传队列（§12-4：跨进程只有一个写者）",
              len(got["files"]) == 2 and "queue" not in json.dumps(got),
              str(got["files"]))
        check("⭐ 原子写不留 .tmp",
              not [p for p in (root / "note-outbox").iterdir() if ".tmp" in p.name])


def t_writer_skips_base_note_when_asked() -> None:
    import obsidian_writer as ow
    src = pathlib.Path(ow.__file__).read_text(encoding="utf-8")
    i = src.index("import note_render\n        base_stem")
    seg = src[i:i + 1500]
    check("⭐⭐ `close()` 里那次基础版落盘被 `_base_note` 挡住了",
          "if self._base_note:" in seg, seg[:200])
    w = ow.ObsidianWriter(vault=None, course="T", mode="no", base_note=False)
    check("⭐ `base_note=False` 真的记下来了（默认 True = 今天的行为）",
          w._base_note is False
          and ow.ObsidianWriter(vault=None, course="T", mode="no")._base_note is True)
    check("⭐ 跳过基础版之后 `_html_name` 仍是字符串（两处读它）",
          isinstance(w._html_name, str), str(type(w._html_name)))


def t_worker_enabled_switch() -> None:
    saved = os.environ.get("CLASSLIVE_NOTE_WORKER")
    try:
        os.environ.pop("CLASSLIVE_NOTE_WORKER", None)
        check("⭐ 默认开", main.note_worker_enabled() is True)
        for off in ("0", "false", "no", "off"):
            os.environ["CLASSLIVE_NOTE_WORKER"] = off
            assert main.note_worker_enabled() is False, off
        check("⭐⭐ `CLASSLIVE_NOTE_WORKER=0` 退回进程内收尾（§10 的回滚开关）", True)
        os.environ["CLASSLIVE_NOTE_WORKER"] = "1"
        check("⭐ 1 / 别的值当开", main.note_worker_enabled() is True)
    finally:
        if saved is None:
            os.environ.pop("CLASSLIVE_NOTE_WORKER", None)
        else:
            os.environ["CLASSLIVE_NOTE_WORKER"] = saved


def t_handbook_registers_switch() -> None:
    hb = pathlib.Path(HERE, "docs", "HANDBOOK.md")
    if not hb.is_file():
        check("HANDBOOK 在", False)
        return
    check("⭐⭐ 新开关登记在 HANDBOOK §6.1（R30 钉着每个 CLASSLIVE_*）",
          "CLASSLIVE_NOTE_WORKER" in hb.read_text(encoding="utf-8"))


def t_resume_stale_note_jobs() -> None:
    """开机时把**标着 running 但其实死了**的任务补出来（普通课没有 journal，只能靠它）。"""
    with tempfile.TemporaryDirectory() as d:
        sd = pathlib.Path(d, "sessions")
        sd.mkdir()
        jobs = str(pathlib.Path(d, "jobs"))
        stem = "2026-11-04_160000_ECON10790"
        (sd / f"{stem}.md").write_text(
            "# ECON10790 · 2026-11-04 · 实时会话日志\n\n> [!abstract] 10:00:00\n> hi\n\n",
            encoding="utf-8")
        # 现场：worker 派了、spec 在、pid 是别人的（早死了）-> alive() 为 False
        NJ.claim(stem, root=jobs, spec={**SPEC, "session": str(sd / f"{stem}.md")},
                 pid=999_999_999)
        calls = []

        def _rb(sess, *, log=None, **k):
            calls.append(str(sess))
            return {"ok": True, "note": str(sd / "out.md"), "error": ""}

        r = main.resume_stale_note_jobs(str(sd), rebuild=_rb, jobs_root=jobs)
        check("⭐⭐ 死掉的后台任务会被补出来（判据是 pid/心跳，不是 journal）",
              r["found"] == 1 and r["done"] == 1 and calls, (r, calls))
        check("⭐ 每节只补一次：收工状态落到 done，再扫就不动了",
              NJ.read(stem, root=jobs).get("state") == NJ.DONE
              and main.resume_stale_note_jobs(str(sd), rebuild=_rb,
                                              jobs_root=jobs)["found"] == 0)
        # 活着的任务不许动
        stem2 = "2026-11-04_170000_ECON10790"
        (sd / f"{stem2}.md").write_text("x", encoding="utf-8")
        NJ.claim(stem2, root=jobs, spec={**SPEC, "session": str(sd / f"{stem2}.md")})
        calls.clear()
        r2 = main.resume_stale_note_jobs(str(sd), rebuild=_rb, jobs_root=jobs)
        check("⭐⭐ 还活着的任务一个都不碰", r2["found"] == 0 and not calls, (r2, calls))


def t_drain_note_outbox() -> None:
    """worker 的笔记出箱由**主进程**入队（§12-4：跨进程只有一个写者）。"""
    import upload
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d, "state")
        note = pathlib.Path(d, "Lectures", "2026-11-04_ECON10790.md")
        note.parent.mkdir(parents=True)
        note.write_text("n", encoding="utf-8")
        NJ.write("x", {}, root=root)                       # 建一次目录，免得手写路径
        assert NW._outbox("2026-11-04_160000_ECON10790", str(note), root=str(root))
        q = upload.UploadQueue(lambda *a: True, root=root)
        got = upload.drain_note_outbox(queue=q, root=str(root))
        check("⭐ 出箱里的笔记并进了本进程的上传队列",
              got["found"] == 1 and got["merged"] == 1, str(got))
        names = [pathlib.Path(f).name for i in q.items() for f in i["files"]]
        check("⭐ 队列里真的有一条笔记",
              any(n.endswith(".md") for n in names), str(names))
        check("⭐ 票挪进 `sent/`（不删 —— 同「要清理就挪不删」）",
              (root / "note-outbox" / "sent").is_dir()
              and not list((root / "note-outbox").glob("*.json")),
              str([p.name for p in (root / "note-outbox").iterdir()]))
        check("⭐ 空目录 / 没有票都不抛",
              upload.drain_note_outbox(root=str(root))["found"] == 0)


def t_notify_is_best_effort() -> None:
    seen = []

    class _P:
        returncode = 0

    ok = NW._notify("标题", "正文", runner=lambda a: (seen.append(list(a)), _P())[1])
    check("⭐ 通知走静态脚本 + argv（文案不拼进 AppleScript 源）",
          ok is True and len(seen) == 1 and seen[0] == ["标题", "正文"], str(seen))

    class _Bad:
        returncode = 1

    check("⭐ 通知失败返回 False，不抛",
          NW._notify("t", "b", runner=lambda a: _Bad()) is False)

    def _boom(_a):
        raise OSError("no osascript")

    check("⭐⭐ 没有 osascript 也不许影响 `run()`（笔记已经写好了）",
          NW._notify("t", "b", runner=_boom) is False)


def t_spawn_decision_wrapper() -> None:
    """`_spawn_note_worker` 的每一条「别拆」判据 —— 拆失败 = 整节课没笔记，越保守越好。

    ⚠️ **绝不真派生**：`main.spawn_note_worker` 全程打桩（不然这条判据会真的起一个
       worker 去写用户的笔记库）。
    """
    class _UI:
        def __init__(self):
            self.handed = 0

        def wrapup_handed_off(self):
            self.handed += 1

    class _W:
        writes_notes = True
        _vault = None
        _polish = False
        _model = "deepseek-flash"
        _polish_model = None
        _glossary_path = None
        _course = "ECON10790"

    class _NoNotes(_W):
        writes_notes = False

    saved = os.environ.pop("CLASSLIVE_NOTE_WORKER", None)
    orig = main.spawn_note_worker
    spawned: list = []
    main.spawn_note_worker = lambda stem, spec, **k: (spawned.append((stem, spec)), True)[1]
    try:
        with tempfile.TemporaryDirectory() as d:
            sess = pathlib.Path(d, "2026-11-04_160000_ECON10790.md")
            sess.write_text("x", encoding="utf-8")
            ui = _UI()
            got = main._spawn_note_worker(
                writer=_W(), ui=ui, stem=sess, qa_snapshot=lambda: [],
                live_pool=None, course="ECON10790", api_key_val=None,
                trans={"mode": "both"}, args=None, echo=lambda *_: None)
            check("⭐⭐ 条件都满足 -> True，并且卡片收到「已交给后台」",
                  got is True and ui.handed == 1 and len(spawned) == 1, str(got))
            check("⭐ spec 是 `note_job_spec` 那份（不是随便一个 dict）",
                  (spawned[0][1] or {}).get("course") == "ECON10790", str(spawned[0][1])[:80])

        os.environ["CLASSLIVE_NOTE_WORKER"] = "0"
        spawned.clear()
        check("⭐⭐ 开关关着 -> None（退回今天的行为，一次都不派）",
              main._spawn_note_worker(
                  writer=_W(), ui=_UI(), stem=sess, qa_snapshot=lambda: [],
                  live_pool=None, course="", api_key_val=None, trans={}, args=None,
                  echo=lambda *_: None) is None and not spawned)

        os.environ["CLASSLIVE_NOTE_WORKER"] = "1"
        check("⭐⭐ 没有会话文件 -> None（不许派一个注定失败的 worker）",
              main._spawn_note_worker(
                  writer=_W(), ui=_UI(), stem="/tmp/definitely-missing.md",
                  qa_snapshot=lambda: [], live_pool=None, course="", api_key_val=None,
                  trans={}, args=None, echo=lambda *_: None) is None)
        check("⭐ 不写笔记（没库也没桌面库）-> None",
              main._spawn_note_worker(
                  writer=_NoNotes(), ui=_UI(), stem=sess, qa_snapshot=lambda: [],
                  live_pool=None, course="", api_key_val=None, trans={}, args=None,
                  echo=lambda *_: None) is None)
        main.spawn_note_worker = lambda stem, spec, **k: False
        check("⭐⭐ 派生失败 -> None（调用方据此走进程内那条，不丢笔记）",
              main._spawn_note_worker(
                  writer=_W(), ui=_UI(), stem=sess, qa_snapshot=lambda: [],
                  live_pool=None, course="", api_key_val=None, trans={}, args=None,
                  echo=lambda *_: None) is None)
    finally:
        main.spawn_note_worker = orig
        if saved is None:
            os.environ.pop("CLASSLIVE_NOTE_WORKER", None)
        else:
            os.environ["CLASSLIVE_NOTE_WORKER"] = saved


def t_wrapup_card_never_claims_saved() -> None:
    """后台模式下卡片不许说「收尾结束 / 已保存」—— 笔记那时还没写出来。

    ⚠️ 这条是**源码判据**（`run()` 那个巨型收尾块没法在判据里真跑）——
       它钉的是两个具体的错：
       ① spawned 分支不设 `_box["msg"]` → `wrapup_done(_ok, msg or "收尾结束")`
          把卡片上的「已交给后台」覆盖成一句假话；
       ② spawned 分支不清 `_np_say` → `wrapup_progress_finish` 把一份**没量过任何
          阶段耗时**的假样本写进 ETA 历史。
    """
    src = pathlib.Path(main.__file__).read_text(encoding="utf-8")
    i = src.index("if _spawned is None:")
    seg = src[i:i + 1500]
    check("⭐⭐ 后台分支给了「已交给后台」的文案（不许落到『收尾结束』那条默认值）",
          '_box["msg"]' in seg and "后台" in seg, seg[:160])
    j = src.index("_npf = (locals().get(\"_np_say\")")
    seg2 = src[j:j + 400]
    check("⭐⭐ 后台模式不写 ETA 假样本（`_spawned is None` 才收口）",
          "_spawned is None" in seg2, seg2[:160])
    k = src.index("_spawned = None   # ⚠️")
    check("⭐ `_spawned` 在分岔之前就定义（`give_up` 那条路也要能读）",
          k < src.index("if not give_up:") + 2000 and k < src.index("if _spawned is None:"),
          "")


def main_() -> int:
    print("note_worker 判据（派生用替身）")
    for name, fn in [
        ("spec 不含 key", t_spec_has_no_key),
        ("回调遵守档位", t_callbacks_respect_trans_mode),
        ("回调 fail-soft", t_callbacks_degrade_not_raise),
        ("派生：占位 + setsid + 重定向", t_spawn_claims_then_popens),
        ("派不起来 fail-soft", t_spawn_failure_is_fail_soft),
        ("没有 spec", t_run_without_spec_fails_cleanly),
        ("会话文件不在", t_run_missing_session_fails_cleanly),
        ("outbox", t_outbox_written_only_on_success),
        ("跳过基础版", t_writer_skips_base_note_when_asked),
        ("开关", t_worker_enabled_switch),
        ("通知 best-effort", t_notify_is_best_effort),
        ("派生分岔判据", t_spawn_decision_wrapper),
        ("卡片不说假话", t_wrapup_card_never_claims_saved),
        ("开机补死任务", t_resume_stale_note_jobs),
        ("笔记出箱入队", t_drain_note_outbox),
        ("开关登记", t_handbook_registers_switch),
    ]:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:                                            # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(name, False, f"{type(e).__name__}: {e}")
    print(f"\n{'❌ 失败 ' + str(len(FAILED)) + '：' + '、'.join(FAILED) if FAILED else '✅ 全部通过'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main_())
