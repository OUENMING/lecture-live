# note_job —— 一条笔记任务的全流程：拉逐句 → 会话底稿 → 桌面现成管道 → 交回。
# 管什么：`run_note(client, job, *, home, log)`（永不抛；`job["_cancel"]` 置位 = 租约丢了，
#   各阶段边界停手、**不交回**）；其余异常 → `complete_fail(类名: 前 200 字)`。
# 对外接口：run_note 之外只有 `install_bill_to`（把 `X-ClassLive-Bill-To` 塞进模型请求头）。
# ⭐ 计费 seam 的选择：桌面所有模型调用都经 `llm_client.headers` —— `chat_json` 在
#   llm_client.py 里以**模块全局**调 `headers(key, call_class)`，Jev/CommandCode（keypoints.py
#   的 urllib 那条）也是 `{**llm_client.headers(token), ...}`。改这一个模块属性 = 两条路全覆盖、
#   且都是**调用时**查名字（懒 import），不依赖 httpx 内部结构；比包 httpx.request 脆度低
#   （后者会误伤 final_asr / 翻译流量，还要区分 stream/Client/urllib 三种形态）。
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_COURSE_DATE = re.compile(r"^#\s*(.+?)\s*·\s*(\d{4}-\d{2}-\d{2})\s*·")


class _Cancelled(Exception):
    """`job["_cancel"]` 置位（主循环心跳发现租约丢了）：停手、不交回。"""


def _say(log, s: str) -> None:
    try:
        if log is not None:
            log(s)
    except Exception:                                       # noqa: BLE001
        pass


def _check_cancel(job: dict) -> None:
    ev = (job or {}).get("_cancel")
    if ev is not None and ev.is_set():
        raise _Cancelled()


def install_bill_to(bill_to: str, log=None) -> None:
    """把 `X-ClassLive-Bill-To: <hash16>` 设成本进程默认计费头（实现在 billing.py，与 summary_job 共用一层包装）。"""
    from server.runner import billing
    if not billing.set_default(bill_to):
        _say(log, f"bill_to 不是 16 hex（{bill_to!r}），不加计费头")


def _prepare_env(home: Path, log) -> None:
    """HOME 指到隔离家（必须在 import 任何桌面模块**之前**）+ 拔掉真 Obsidian 库。

    ⚠️ `paths.STATE_ROOT` 是 import 时算的 —— 主进程（loop）启动时就设好 HOME；
       这里只兜「直调」：paths 还没 import 就设；已 import 且对不上 → reload + 告警。
    """
    os.environ["HOME"] = str(home)
    os.environ.pop("OBSIDIAN_VAULT", None)     # runner 永远不用真库；防本机 zshrc 泄漏
    import paths
    if str(paths.STATE_ROOT) != str(home / ".classlive"):
        import importlib
        importlib.reload(paths)
        _say(log, f"⚠ paths.STATE_ROOT 与 HOME 不符，已 reload（进程内 HOME 应当固定）")


def _runner_key(home: Path) -> str | None:
    """runner 通行码：`<home>/.classlive/credentials`（与桌面读码同一条路，root 显式）。"""
    try:
        return (home / ".classlive" / "credentials").read_text(
            encoding="utf-8").strip() or None
    except OSError:
        return None


def _try_import_main(log):
    """桌面优先走 `main.rebuild_note_for`（in-process 补笔记，永不抛）。Linux 上 import
    main 会因 AppKit / Foundation 失败 —— 捕获后走底层模块（见 `_fallback_pipeline`）。
    `CLASSLIVE_RUNNER_NO_MAIN=1` 只给测试强制走 fallback。"""
    if os.environ.get("CLASSLIVE_RUNNER_NO_MAIN"):
        return None
    try:
        import main                                   # noqa: PLC0415
        return main
    except Exception as e:                            # noqa: BLE001
        _say(log, f"import main 失败（{type(e).__name__}: {str(e)[:120]}）→ 走底层模块")
        return None


def _fallback_pipeline(session_path: Path, *, home: Path, key, raw: bool, log):
    """`scripts/rebuild_note.py` 的 in-process 版（main 不可用时）。

    只抄它的**必要骨架**（构造 mode="no" 的 writer → 接管成已跑完的一节 → close()），
    细节（errors="replace"、`keypoints_fn` 不能省）在两处原文件各有血泪注释，不复制。
    """
    import keypoints
    import obsidian_writer as ow
    text = session_path.read_text(encoding="utf-8", errors="replace")
    n = len(ow.ObsidianWriter._parse(text))
    if n == 0:
        raise ValueError("会话文件里没有能读懂的句段")
    m = _COURSE_DATE.match(text)
    if not m:
        raise ValueError("认不出课号 / 日期")
    course, date = m.group(1).strip(), m.group(2)
    _say(log, f"fallback 管道：{course} · {date} · {n} 句")
    w = ow.ObsidianWriter(vault=None, course=course, mode="no",
                          api_key=(None if raw else key), model="deepseek-flash",
                          glossary_path=str(_REPO_ROOT / "glossary.txt"),
                          polish=False,
                          keypoints_fn=(None if raw else keypoints.pick),
                          desk=True, desk_home=home)
    w.enabled = True
    w.mode = "yes"
    w.session_path = session_path
    w._course = course
    w._date = date
    w._n = n
    msg = w.close()
    md = Path(str(w.vault_path or ""))
    if not md.is_file():
        raise RuntimeError(f"close() 没写出笔记：{msg}")
    html = Path(str(w.desk_html or ""))
    if not html.is_file():
        html = md.with_suffix(".html")     # 同 stem 的详细版 HTML（_write_sidecars 的落点）
    try:
        stats = w.note_stats()
    except Exception:                               # noqa: BLE001
        stats = {}
    return {"md": md, "html": html, "stats": stats, "msg": str(msg or "")}


def _run_pipeline(session_path: Path, *, home: Path, key, raw: bool, log) -> dict:
    main = _try_import_main(log)
    if main is not None:
        out = main.rebuild_note_for(session_path, vault=None, polish=False,
                                    log=log, desk_home=home, raw=raw)
        if not out.get("ok"):
            raise RuntimeError(str(out.get("error") or "close() 没写出笔记"))
        md = Path(out["note"])
        html = md.with_suffix(".html")     # vault=None 时 md 与 html 同 stem（desk 落点）
        if not html.is_file():
            raise RuntimeError(f"桌面管道没出 HTML：{html}")
        stats = {k: out.get(k) for k in ("review_total", "review_ok", "degraded")}
        return {"md": md, "html": html, "stats": stats,
                "msg": str(out.get("msg") or "")}
    return _fallback_pipeline(session_path, home=home, key=key, raw=raw, log=log)


def run_note(client, job: dict, *, home: Path, log=None) -> None:
    """跑完一条笔记任务并交回。永不抛；`_cancel` 置位时停手不交。"""
    home = Path(home)
    inp = (job or {}).get("input") or {}
    hash16 = str(inp.get("hash") or "")
    session_id = str(inp.get("session_id") or "")
    course = str(inp.get("course") or "LECTURE")
    tz = str(inp.get("tz") or "UTC")
    started_at = str(inp.get("started_at") or "")
    raw = str(inp.get("level") or "") == "raw"
    jid = str((job or {}).get("job_id") or "?")
    work: Path | None = None
    try:
        _check_cancel(job)
        key = _runner_key(home)
        _prepare_env(home, log)
        if raw:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        elif key:
            # env 优先级高于 credentials 路径 → rebuild_note_for 一定用 runner 码
            os.environ["DEEPSEEK_API_KEY"] = key
        else:
            raise RuntimeError(
                f"缺 runner 通行码：{home / '.classlive' / 'credentials'}"
                "（不放 key 就只能出纯转录，见 README）")
        if not raw and key:
            install_bill_to(hash16, log)

        lines = client.lines(hash16, session_id)
        _say(log, f"[{jid}] 拉到 {len(lines)} 句（{course} · {tz}）")
        if not lines:
            raise ValueError("这节课一句都没有，拒绝生成空笔记")
        _check_cancel(job)

        from server.runner import session_md
        # 每条任务一个独立目录（Claude 11-20）：runner 替**所有**用户出笔记，同日同课名的两位用户
        # 在共用的桌面笔记库里会撞文件名 / 互相当「上一份」；做完就删，用户数据不在 VPS 上留存。
        work = home / "jobs" / re.sub(r"[^A-Za-z0-9_-]", "_", jid)
        shutil.rmtree(work, ignore_errors=True)
        md_session = session_md.write_session(
            lines, course=course, started_at=started_at, tz=tz,
            out_dir=work / "sessions")
        _say(log, f"[{jid}] 会话底稿 → {md_session.name}")
        _check_cancel(job)

        out = _run_pipeline(md_session, home=work, key=key, raw=raw, log=log)
        _check_cancel(job)
        md = out["md"].read_text(encoding="utf-8", errors="replace")
        html = out["html"].read_text(encoding="utf-8", errors="replace")
        stats = out.get("stats") or {}
        meta = {"course": course, "date": out["md"].stem.split("_")[0],
                "lines": len(lines),
                "review_total": int(stats.get("review_total") or 0),
                "review_ok": int(stats.get("review_ok") or 0),
                "degraded": bool(stats.get("degraded")),
                "msg": str(out.get("msg") or "")[:200]}
        client.complete_ok(job, md, html, meta)
        _say(log, f"[{jid}] 完成：md {len(md)} 字 · html {len(html)} 字"
                  f"（复习层 {meta['review_ok']}/{meta['review_total']}）")
    except _Cancelled:
        _say(log, f"[{jid}] 租约丢了 / 被叫停：停手不交")
        return
    except Exception as e:                                # noqa: BLE001
        reason = f"{type(e).__name__}: {str(e)[:200]}"
        _say(log, f"[{jid}] 失败：{reason}")
        try:
            client.complete_fail(job, reason)
        except Exception as e2:                           # noqa: BLE001
            _say(log, f"[{jid}] complete_fail 也失败（不重试）:"
                      f" {type(e2).__name__}: {str(e2)[:120]}")
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
