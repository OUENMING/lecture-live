"""后台收尾进程 —— 下课后独立生成笔记，主进程可以先退。

    python note_worker.py <stem> [--jobs-root DIR] [--log FILE]

为什么要它（`docs/PLAN-note-background-20261103.md` §4）：一节 50 分钟的课，收尾要
跑精修 / 复习层 / 重点句 / 核对，实测 3–15 分钟。这段时间里主进程占着单实例锁 ——
下一节课开不了、合盖就断。拆出来之后：主进程写完 `sessions/` 就退，锁释放；
这个进程 `setsid` 脱离父进程，主进程退出也照样跑完。

## 契约（跨进程的那一份）

- **输入**：`~/.classlive/note-jobs/<stem>.json` 里的 `spec`（父进程 `claim()` 时写的）。
  规格里只有**能序列化的东西**：路径、课号、档位、模型名、问答快照。**不含任何 key**
  —— 要云端就现读 `~/.classlive/credentials`（`use_cloud` 是父进程那一刻的档位决定，
  纯转录 / 本地档记 False，worker 就一个模型请求都不发）。
- **输出**：笔记本身（`Lectures/` 或桌面笔记库，原子写）；`note-jobs/<stem>.json` 收工
  状态；`~/.classlive/note-outbox/<stem>.json`（要补传的文件清单，由主程序下次启动入队）。
- **进度**：每 `note_jobs.HEARTBEAT_S` 秒刷一次 `stage` —— 判活靠它，不靠总时长。
- **电源**：`power.keep_system_awake()` 全程持有，否则合盖 / 空闲睡眠会把生成挂起。

⚠️ 与主进程里的收尾**同源不同壳**：写笔记走的是同一个 `ObsidianWriter.close()`。
  三个回调（❓讲解 / 补译 / 课前回顾）在主进程里是闭包，这里按 spec 重建一份 ——
  **判据必须与 `main.py` 那三个 `_…_for` 一致**（改那边要回来对一遍），因为不能
  把闭包序列化过进程边界。
"""
from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

import note_jobs as _jobs

#: 问答快照最多带多少条过进程边界（spec 文件要小；超出的截断并记在 `qa_truncated`）。
QA_MAX = 200
#: 跑完之后留给非 daemon 线程的收尾时间（秒）。超了直接 `os._exit` ——
#: 见 §12-9：`review()` 预算到点返回后线程还在跑，`threading._shutdown` 会 join 它们，
#: 最坏再拖 90 秒，而笔记已经写完、没必要等。
EXIT_GRACE_S = 5.0


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------- 回调重建
def build_callbacks(spec: dict) -> dict:
    """spec → `ObsidianWriter` 的三个回调。**判据逐条对齐 `main.py`**（见文件头）。

    ⚠️ 三条都**只在真的会调云端时**才生效：
       `use_cloud` = 这节课有没有可用的 key；`trans_mode` = 收尾那一刻的翻译档位。
       纯转录（`raw`）是「一个模型请求都不发」的硬契约（`DESIGN.md`）。
    """
    use_cloud = bool(spec.get("use_cloud"))
    tmode = str(spec.get("trans_mode") or "both")
    ever_off = bool(spec.get("ever_off"))
    course = str(spec.get("course") or "")
    glossary = spec.get("glossary") or None
    model = str(spec.get("cloud_model") or "deepseek-flash")
    polish_model = spec.get("polish_model") or None

    def _lost_explain(blocks):
        if not use_cloud or tmode == "raw":
            return []
        try:
            import polish
            from translator import course_term_list, course_title
            return polish.explain_lost(blocks, _cloud_key(), polish_model or model,
                                       course_term_list(glossary, course),
                                       course_title(glossary, course))
        except Exception:                                 # noqa: BLE001
            return []

    def _backfill(entries):
        if not (use_cloud and tmode == "both" and not ever_off):
            return entries, {"need": 0, "done": 0}
        try:
            import backfill
            from translator import course_term_list, course_title
            return backfill.backfill_entries(
                entries, _cloud_key(), model,
                course_terms=course_term_list(glossary, course),
                domain=course_title(glossary, course))
        except Exception:                                 # noqa: BLE001
            return entries, {"need": 0, "done": 0}

    def _recap(payload: dict) -> dict:
        if not use_cloud or tmode == "raw":
            return {}
        try:
            import recap as _recap_mod
            data = payload if isinstance(payload, dict) else {}
            duties = spec.get("duties") or []

            def _chat(system, user, max_tokens):
                import llm_client
                return llm_client.chat_json(_cloud_key(), model, system, user,
                                            max_tokens=max_tokens, temperature=0.3,
                                            timeout=120.0, call_class="recap")

            return _recap_mod.build(
                course=data.get("course") or course, date=data.get("date") or "",
                session=data.get("session") or "",
                chapters=(data.get("chapters") or {}).get("chapters") or [],
                keypoints=data.get("keypoints") or [],
                lost=[x for x in (data.get("lost") or [])],
                duties=duties, tail=data.get("tail") or "",
                chat=(_chat if use_cloud else None))
        except Exception:                                 # noqa: BLE001
            return {}

    return {"lost_explain_fn": _lost_explain, "backfill_fn": _backfill,
            "recap_fn": _recap}


def _cloud_key() -> str:
    """云端那把 key —— **现读**，不在 spec 里传（`~/.classlive/credentials`，600）。"""
    import cloud_translator
    return cloud_translator.load_api_key(None) or ""


def build_writer(spec: dict, *, callbacks: dict | None = None):
    """spec → 一个「已跑完这一节」的 `ObsidianWriter`（不新建会话文件，指到既有那份）。

    ⚠️ 与 `main.rebuild_note_for` 同一条接管手法（那里有两段血泪注释：`errors="replace"`
      读会话文件、必须接 `keypoints_fn`）—— 但这里多带 spec 里的档位与回调。
    """
    import obsidian_writer as ow
    import keypoints as kp

    cb = callbacks if callbacks is not None else build_callbacks(spec)
    sess = pathlib.Path(str(spec.get("session") or ""))
    if not sess.is_file():
        raise FileNotFoundError(f"没有会话文件：{sess}")
    text = sess.read_text(encoding="utf-8", errors="replace")
    n = len(ow.ObsidianWriter._parse(text))
    if n == 0:
        raise ValueError("会话文件里没有能读懂的句段（拒绝生成空笔记）")
    w = ow.ObsidianWriter(
        vault=spec.get("vault") or None,
        course=str(spec.get("course") or ""),
        mode="no",                                   # 接管成既有会话 → 不新建
        api_key=(_cloud_key() if spec.get("use_cloud") else None),
        model=str(spec.get("cloud_model") or "deepseek-flash"),
        glossary_path=spec.get("glossary") or None,
        polish=bool(spec.get("polish")),
        polish_model=spec.get("polish_model") or None,
        # ⚠️ 纯转录档（raw）不许发模型请求；Jev 用自己的 token，与笔记 key 无关。
        keypoints_fn=(None if str(spec.get("trans_mode") or "") == "raw" else kp.pick),
        desk=bool(spec.get("desk", True)),
        desk_home=spec.get("desk_home") or None,
        # ⚠️ 保留基础版：面板「停止」只等 6 s 就 SIGKILL，而复习层收不到 cancel。
        #    只写完整版的话，这 6 s 里被杀 = 盘上一个字节都没有（还有一条路径：
        #    用户主动停止本就是想保住已拿到的内容）。
        base_note=True,
        # ⭐ 3.11.1：拆进程之后漏了这一头 —— 笔记照写，但不再自动打开详细版。
        #    （2026-11-09 作者报；实现与进程内收尾共用 `open_in_browser`。）
        open_fn=ow.open_in_browser,
        **cb)
    w.enabled = True
    w.mode = "yes"
    w.session_path = sess
    w._course = str(spec.get("course") or "")
    w._date = str(spec.get("date") or "")
    w._n = n
    return w


# ---------------------------------------------------------------- 跑
def run(stem: str, *, root=None, log=None, heartbeat_s: float | None = None,
        cancel=None) -> dict:
    """跑一节：读 spec → 写笔记 → 收工状态 → outbox。**永不抛**（返回 `{ok, note, error}`）。

    `cancel`：一个 `threading.Event`（SIGTERM 那条路会置它）。置位后**在批次边界**
    放弃精修 / 复习层，**但笔记照写**（用已经拿到的部分）—— 与 Ctrl+C 的语义逐字相同
    （`obsidian_writer.close(cancel=…)` 的 docstring）。不传 = 不取消。
    """
    out = {"ok": False, "note": "", "error": "",
           "review_total": 0, "review_ok": 0, "degraded": False,
           "review_state": "off"}

    def _say(s: str) -> None:
        if log is not None:
            try:
                log(s)
            except Exception:                             # noqa: BLE001
                pass

    job = _jobs.read(stem, root=root)
    spec = job.get("spec") or {}
    if not spec:
        out["error"] = "没有 spec（父进程没写，或不是它派生的任务）"
        _jobs.finish(stem, _jobs.FAILED, root=root, error=out["error"])
        return out

    # ⭐ 2026-11-10：**per-stem 持有式锁**。`claim()` 只是写一条记录，进程死了记录还在；
    #    flock 由内核托管，进程不管怎么死都会释放。没有它，下面这条现实路径会撞车：
    #    上一节的 worker 还在写 → 用户重开 app → 启动恢复看到「父 pid 已死」→ 两条
    #    rebuild 同时写同一节（重复调用 + 两份笔记 + 撞 `.tmp`）。
    _lock = _jobs.try_lock(stem, root=root)
    if _lock is None:
        out["error"] = "拿不到 per-stem 锁（被别的进程占用，或 note-jobs 目录不可写）"
        _say(f"· {stem}：{out['error']} —— 跳过")
        return out
    # ⭐ pid 必须记成**本进程**：父进程 `claim()` 写的是父 pid，父进程一退
    #    `alive()` 就会把还在跑的 worker 判成死的（下一节课启动时误补第二份）。
    _jobs.write(stem, {"pid": os.getpid(), "state": _jobs.RUNNING}, root=root)

    # 心跳线程：判活只看它（§12-10）。`stage` 由 writer 的 on_progress 推进。
    stop = threading.Event()
    stage_now = {"stage": "start"}

    def _beat() -> None:
        every = heartbeat_s if heartbeat_s else _jobs.HEARTBEAT_S
        while not stop.wait(every):
            _jobs.heartbeat(stem, root=root, stage=stage_now["stage"])

    beat = threading.Thread(target=_beat, name="cl-note-beat", daemon=True)
    try:
        beat.start()
    except BaseException as e:                        # noqa: BLE001
        # 线程起不来时锁还没交给后面的 finally —— 不放会自锁到进程退出。
        try:
            _lock.close()
        except Exception:                             # noqa: BLE001
            pass
        out["error"] = f"心跳线程起不来：{type(e).__name__}: {str(e)[:120]}"
        _jobs.finish(stem, _jobs.FAILED, root=root, error=out["error"])
        return out

    def _progress(stage: str, done: int = 0, total: int = 0) -> None:
        try:
            import note_progress as _np
            stage_now["stage"] = _np.stage_of(stage)
        except Exception:                                 # noqa: BLE001
            stage_now["stage"] = str(stage or "")
        _jobs.heartbeat(stem, root=root, stage=stage_now["stage"],
                        done=int(done or 0), total=int(total or 0))

    try:
        with _power_awake():
            w = build_writer(spec)
            _say(f"▶ {stem}：{spec.get('course')} · {spec.get('date')} · {w._n} 句")
            msg = w.close(ask=lambda _n: True,           # 收尾前已问过（用户说了要存）
                          qa=(spec.get("qa") or None),
                          on_progress=_progress, cancel=cancel)
            note = str(getattr(w, "vault_path", "") or "")
            out["note"] = note
            out["ok"] = bool(note) and pathlib.Path(note).is_file()
            if not out["ok"]:
                out["error"] = "close() 没写出笔记（多半是没设库 / 渲染失败）"
            # ⭐ 2026-11-10：**降级要看得出来**。复习层 0/N 时笔记仍然「写出来了」，
            #    但那是残次品（2026-11-10 本机合盖睡眠实测：Jev 全失败 + 复习层 0/8）。
            #    以前这里照样 `finish(DONE)`、日志一句不提，用户只能翻笔记才发现。
            try:
                import obsidian_writer as _ow
                _st = w.note_stats()
                out["review_total"] = int(_st.get("review_total") or 0)
                out["review_ok"] = int(_st.get("review_ok") or 0)
                # ⭐ 五态：off / cancelled / failed / partial / ok。`partial`（3/8）也是残次品，
                #    以前只抓 0/N，最常发生的「部分成功」反而被当成了成功。
                out["review_state"] = _ow.review_state(
                    _st, expected=(bool(spec.get("use_cloud"))
                                   and str(spec.get("trans_mode") or "") != "raw"),
                    cancelled=bool(cancel is not None and cancel.is_set()))
                out["degraded"] = out["review_state"] in ("failed", "partial")
            except Exception as e:                        # noqa: BLE001
                _say(f"⚠ {stem}：复习层统计取数失败，按未降级处理："
                     f"{type(e).__name__}: {str(e)[:80]}")
            _say(str(msg or ""))
    except BaseException as e:                            # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        stop.set()
        # 等在途的那一拍落地：`heartbeat()` 是「先查 ended、再 write」的两步，
        # 不 join 的话它可能在我们写 `note` / `finish(DONE)` 之后把 state 盖回 running。
        try:
            beat.join(timeout=2.0)
        except Exception:                                 # noqa: BLE001
            pass

    try:
        if out["ok"]:
            # ⭐ 先把落点记进 job：笔记已写完、还没 publish/finish 就被杀时，下次启动
            #    stale 能看到 `note` 直接收工，不会再生成第二份（窗口里有 sidecar/通知）。
            _jobs.write(stem, {"note": out["note"]}, root=root)
            # 与两条恢复路径**同一个出口**（出箱 + 对齐 journal），避免以后漂移。
            _pub = bool(publish_note(stem, out["note"],
                                     session=spec.get("session"), root=root,
                                     log=_say).get("outbox"))
            if not _pub:
                _say(f"⚠ {stem}：outbox 票没写成（note.html 这次不会上传，下次启动会重试）")
            _state = str(out.get("review_state") or "off")
            _course = str(spec.get("course") or "")
            _total, _ok = int(out["review_total"]), int(out["review_ok"])
            if out["degraded"]:
                _say(f"⚠ 笔记降级（{_state}）：复习层 {_ok}/{_total} —— 已保存残次版")
            # ⭐ 通知要**准确**，不能因为降级就静默：交接卡片承诺过「写好后会通知你」。
            #    也不能既发「写好了」又发「降级」（同一件事只发一条）。
            _title, _body = {
                "ok":        ("笔记写好了", f"{_course} · {pathlib.Path(out['note']).stem}"),
                "partial":   ("笔记已保存（部分）",
                              f"{_course} · 复习层 {_ok}/{_total}，缺 {_total - _ok} 段"),
                "failed":    ("笔记已保存（降级）",
                              f"{_course} · 复习层 0/{_total}，只含逐句转录"),
                "off":       ("笔记写好了", f"{_course} · {pathlib.Path(out['note']).stem}"),
                "cancelled": ("笔记已保存（已停止）",
                              f"{_course} · 复习层未生成；逐句转录完整"),
            }.get(_state, ("笔记写好了", _course))
            _notify(_title, _body)
            _jobs.finish(stem, _jobs.DONE, root=root, note=out["note"],
                         degraded=bool(out["degraded"]),
                         review_state=_state,
                         review_total=_total, review_ok=_ok,
                         note_upload=bool(_pub))
        else:
            _jobs.finish(stem, _jobs.FAILED, root=root, error=out["error"])
    finally:
        try:
            _lock.close()                            # 释放 per-stem 锁
        except Exception:                            # noqa: BLE001
            pass
    try:
        import client_events as _ce
        _ce.emit(spec.get("session"), "note_done", ok=bool(out["ok"]),
                 degraded=bool(out["degraded"]),
                 state=str(out.get("review_state") or ""),
                 review_ok=int(out["review_ok"]),
                 review_total=int(out["review_total"]))
    except Exception:                                 # noqa: BLE001
        pass
    return out


def publish_note(stem: str, note: str, *, session=None, root=None,
                 log=None) -> dict:
    """把**已经写好的**笔记送进 outbox，并（可选）对齐 journal —— 与 worker 同一套出口。

    ⚠️ 2026-11-10 事故：进程内恢复路径（`main.resume_stale_note_jobs` /
    `resume_pending_notes`）补出来的笔记**既不写 outbox、也不更新 journal** ——
    作者永远拿不到 `note.html`，下一次启动还会再补一份。这里把 worker 已有的两条
    出口收成一个函数，三个调用方共用，避免第三套实现。
    ⚠️ 只认**真的存在**的笔记文件：测试里的假 `note=""` 不许往真 `~/.classlive/` 写票。
    ⚠️ `log`：失败时**必须留痕**（journal 没对齐 → 下次启动会再生成一份），调用方传 `_say`。
    """
    out = {"outbox": False, "journal": False, "error": ""}

    def _l(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:                         # noqa: BLE001
                pass

    try:
        p = pathlib.Path(str(note or ""))
        if not note or not p.is_file():
            return out
        out["outbox"] = bool(_outbox(stem, str(p), root=root))
        if not out["outbox"]:
            _l(f"⚠ {stem}：note-outbox 票没写成（上传会等下次启动重试）")
        if session is not None:
            out["journal"] = bool(_mark_journal({"session": str(session)}, str(p)))
            if not out["journal"]:
                _l(f"⚠ {stem}：journal 没对齐（下次启动可能再生成一份笔记）")
    except Exception as e:                            # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        _l(f"⚠ {stem}：publish 失败：{out['error']}")
    return out


def _mark_journal(spec: dict, note: str) -> bool:
    """把这一节的残局 journal 记成「笔记已写成」（`steps.note = done` + `note_file`）。

    ⚠️ **必须做**：`main.resume_pending_notes` 只跳过**还活着**的任务（`note_jobs.alive`）。
       本进程一收工，`alive` 就是 False —— journal 还写着 `pending` 的话，下次开机会把
       这节**再生成一遍**（第二份笔记）。
    ⚠️ 认不出这节有没有 journal 是正常的（正常收尾不写 journal，只有测试模式写）。
    """
    try:
        import testmode as _tm
        import upload as _up
        sess = pathlib.Path(str(spec.get("session") or ""))
        sd = sess.parent
        hit = 0
        for mk, jstem in _up.residual_journals(sd):
            if jstem != sess.stem:
                continue
            _tm.journal_update(mk, steps={"note": "done"}, note_file=str(note or ""))
            hit += 1
        return bool(hit)
    except Exception:                                     # noqa: BLE001
        return False


def _notify(title: str, body: str, *, runner=None) -> bool:
    """系统通知（`osascript`）—— 笔记写完了用户人可能已经离开窗口。

    ⚠️ worker 是**脱离 AppKit 的子进程**：没有 run loop、没有 NSApplication，
       正经的 `UNUserNotificationCenter` 在这里用不了。`display notification`
       是唯一不引入依赖、也不需要登录项的路。
    ⚠️ 失败一律当没有（未授予通知权限 / 找不到 osascript / 超时）——
       **笔记已经写好了**，通知只是锦上添花。
    ⚠️ 文案不拼进 AppleScript 源里（同 `duty.SCRIPT` 的既有做法）：一条静态脚本 +
       argv，没有转义面。
    """
    script = ('on run argv\n'
              '  display notification (item 2 of argv) with title (item 1 of argv)\n'
              'end run')
    try:
        run = runner or (lambda a: subprocess.run(
            ["osascript", "-e", script, *a], capture_output=True, text=True, timeout=8))
        p = run([str(title), str(body)])
        return int(getattr(p, "returncode", 1) or 0) == 0
    except Exception:                                     # noqa: BLE001
        return False


def _outbox(stem: str, note: str, *, root=None) -> bool:
    """把「要补传的文件」写进 outbox —— **不碰上传队列**（§12-4：跨进程只有一个写者）。

    主程序下次启动（或还在跑时的 drainer）读它入队，然后删掉这张票。
    """
    try:
        d = _outbox_dir(root=root)
        d.mkdir(parents=True, exist_ok=True)
        # ⚠️ 只传 HTML：笔记 `.md` 与逐字转录在远端都叫 `transcript.md`，
        #    会把 transcript 覆盖掉（旧 bug）。markdown 版本留在本机/诊断包里。
        files = [p for p in (pathlib.Path(note).with_suffix(".html"),) if p.is_file()]
        if not files:
            # ⚠️ 空票 = drainer 挪进 `sent/` 后永不再试、且什么都没上传。宁可不写。
            return False
        payload = {"stem": stem, "files": [str(p) for p in files], "at": _now()}
        # ⚠️ tmp 名带 pid：旧实现固定 `.tmp`，两个写者会互相覆盖。
        tmp = d / f"{stem}.tmp{os.getpid()}"
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / f"{stem}.json")
        return True
    except Exception:                                    # noqa: BLE001
        return False


def _outbox_dir(*, root=None) -> pathlib.Path:
    if root is not None:
        return pathlib.Path(root) / "note-outbox"
    import paths
    return paths.STATE_ROOT / "note-outbox"


def _power_awake():
    """`power.keep_system_awake()`；拿不到就退化成空上下文（不因为电源断言拒绝干活）。"""
    try:
        import power
        return power.keep_system_awake()
    except Exception:                                    # noqa: BLE001
        import contextlib
        return contextlib.nullcontext()


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    jobs_root = None
    logf = None
    stems = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--jobs-root" and i + 1 < len(args):
            jobs_root = args[i + 1]
            i += 2
            continue
        if a == "--log" and i + 1 < len(args):
            logf = args[i + 1]
            i += 2
            continue
        stems.append(a)
        i += 1
    if not stems:
        print("用法: python note_worker.py <stem> [--jobs-root DIR] [--log FILE]", file=sys.stderr)
        return 2

    def _log(s: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {s}"
        print(line, flush=True)
        if logf:
            try:
                with open(logf, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:                            # noqa: BLE001
                pass

    # ⭐ SIGTERM = 面板上那个「停止」/ 系统关机：**在批次边界停**，笔记照写。
    #    ⚠️ 不装 handler 的话 python 的默认动作是立刻死 —— 盘上什么都没有
    #    （同 `obsidian_writer` 里那两处 KeyboardInterrupt 护栏要防的事，
    #     而信号只投递到主线程，所以在主线程信号 handler 里置位即可）。
    cancel = threading.Event()

    def _on_term(_sig, _frm):
        cancel.set()
        _log("⏹ 收到停止请求 —— 正把笔记写出来（再收到一次就强退）")

    try:
        signal.signal(signal.SIGTERM, _on_term)
    except Exception:                                     # noqa: BLE001
        pass

    rc = 0
    for stem in stems:
        r = run(stem, root=jobs_root, log=_log, cancel=cancel)
        _log(f"{'✅' if r['ok'] else '⚠'} {stem} {r['note'] or r['error']}")
        if not r["ok"]:
            rc = 1
    return rc


if __name__ == "__main__":
    _rc = main()
    # ⚠️ 不 join 残留线程（§12-9）：笔记已经写完，`review()` 到预算后线程可能还在跑，
    #    正常的解释器退出会 join 它们，最坏再拖几十秒 —— 而且主进程早已不在等我们了。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_rc)
