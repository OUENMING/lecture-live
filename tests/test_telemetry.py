#!/usr/bin/env python3
"""元数据脊柱（`telemetry.py`）判据 —— 全部离线、写进临时目录。

跑法: ClassLive.app/Contents/MacOS/python tests/test_telemetry.py

盯住四件事：① 信封形状与白名单（超长 / 深表被砍，路径脱敏）；② 有界队列丢最旧；
③ 心跳原子写 + `recover_info` 的残局判定（没 end / boot 变了 / 心跳太老）；
④ 内容与元数据分文件、模块级 no-op 安全。
"""
import json
import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import telemetry as T                                                    # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def _read(p: pathlib.Path) -> list:
    try:
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception:                                                    # noqa: BLE001
        return []


def _mk(tmp: str, sid: str = "20261104_120000_abc123", **kw) -> T.Telemetry:
    return T.Telemetry(session_id=sid, root=tmp, **kw)


def _wait_lines(p: pathlib.Path, n: int, timeout: float = 2.0) -> None:
    """等 jsonl 落够 `n` 行（写盘在后台线程上；判据不许靠 `sleep` 赌时序）。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if len(_read(p)) >= n:
            return
        time.sleep(0.01)


def t_envelope_and_whitelist() -> None:
    with tempfile.TemporaryDirectory() as d:
        t = _mk(d)
        t.start({"mode": "normal"})
        t.event("net.connect", {"attempt": 1, "host": "asr.bldcam.page",
                                "long": "x" * 500,
                                "path": str(pathlib.Path.home() / "secret"),
                                "deep": {"a": {"b": {"c": "砍掉"}}},
                                "hostname": T.platform.node()})
        t.end("ok")
        t.close()
        rows = _read(t.events_path)
        check("⭐ session.start / net.connect / session.end 三条都在",
              [r["event"] for r in rows] == ["session.start", "net.connect", "session.end"],
              str([r["event"] for r in rows]))
        ev = next(r for r in rows if r["event"] == "net.connect")
        for k in ("schema_version", "event_id", "session_id", "seq", "t_wall",
                  "t_mono_ms", "t_awake_ms", "boot_id", "app_version", "os_version",
                  "arch", "install_id"):
            check(f"⭐ 信封有 {k}", k in ev, str(sorted(ev)))
        check("⭐ 序号单调 +1", [r["seq"] for r in rows] == [1, 2, 3], str([r["seq"] for r in rows]))
        check("⭐ 超长串被截到 200", len(ev["attrs"]["long"]) == 200, str(len(ev["attrs"]["long"])))
        check("⭐ 绝对路径里的用户名被抹掉",
              not str(ev["attrs"]["path"]).startswith(str(pathlib.Path.home())),
              ev["attrs"]["path"])
        check("⭐ 超过两层深度的表被砍", "deep" in ev["attrs"] and ev["attrs"]["deep"].get("a") is None,
              str(ev["attrs"].get("deep")))
        check("⭐ attrs 里的主机名被换成 <host>",
              (not T.platform.node()) or ev["attrs"]["hostname"] == "<host>",
              f"node={T.platform.node()!r} got={ev['attrs']['hostname']!r}")


def t_content_separate_file() -> None:
    with tempfile.TemporaryDirectory() as d:
        t = _mk(d)
        t.content("translate.pair", {"en": "hello", "zh": "你好"})
        t.event("translate.batch", {"n": 1})
        t.close()
        con = _read(t.content_path)
        ev = _read(t.events_path)
        check("⭐⭐ 内容进 content.jsonl（正文原样）",
              len(con) == 1 and con[0]["attrs"]["zh"] == "你好", str(con))
        check("⭐⭐ 元数据文件里**没有**正文",
              all("你好" not in json.dumps(r, ensure_ascii=False) for r in ev), str(ev))
        check("⭐ 内容事件带 session_id / t_wall", "session_id" in con[0] and "t_wall" in con[0],
              str(sorted(con[0])))


def t_queue_bounded_drops_oldest() -> None:
    with tempfile.TemporaryDirectory() as d:
        t = T.Telemetry(session_id="s", root=d, max_queue=2)
        for i in range(50):
            t.event("breadcrumb", {"i": i})
        t.close()
        rows = _read(t.events_path)
        check("⭐⭐ 队列满了丢**最旧**（保留的是最后几条）",
              rows and rows[-1]["attrs"]["i"] == 49 and rows[0]["attrs"]["i"] > 0,
              f"n={len(rows)} first={rows[0]['attrs']['i'] if rows else None}")
        check("⭐ 丢的条数记在 dropped", t.dropped > 0, str(t.dropped))


def t_heartbeat_and_recover() -> None:
    with tempfile.TemporaryDirectory() as d:
        t = _mk(d)
        t.start()
        t.heartbeat(lambda: {"counters": {"sealed": 7}})
        _wait_lines(t.events_path, 2)            # start + heartbeat（写盘在后台）
        hb = json.loads((t.dir / "heartbeat.json").read_text(encoding="utf-8"))
        check("⭐ 心跳是原子替换的结果（无 .tmp 残留）",
              hb.get("session_id") and not (t.dir / "heartbeat.json.tmp").exists(), str(hb))
        check("⭐ 心跳带 seq / t_mono / pid / boot_id",
              all(k in hb for k in ("seq", "t_mono_ms", "pid", "boot_id")), str(sorted(hb)))
        # 上一节没写 end -> 要能判出残局（`last_seq` = 最后那条事件的序号，
        # 这里 start=1、heartbeat=2）
        info = T.recover_info(root=d, now=hb["t_wall"] + 1.0)
        check("⭐⭐ 有 start、没 end -> recover_info 认残局",
              info.get("prev_session_id") == t.session_id and info.get("last_seq") == 2,
              str(info))
        t.close()
        # 写了 end 之后不再是残局
        t2 = _mk(d)
        t2.start()
        t2.end("ok")
        t2.close()
        hb2 = json.loads((t2.dir / "heartbeat.json").read_text(encoding="utf-8"))
        check("⭐ 有 end 且心跳新鲜 -> 不认残局",
              T.recover_info(root=d, now=hb2["t_wall"] + 1.0) == {},
              str(T.recover_info(root=d, now=hb2["t_wall"] + 1.0)))
        check("⭐⭐ 心跳太老 -> 认残局（合盖 / 强杀）",
              T.recover_info(root=d, now=hb2["t_wall"] + 999.0).get("prev_session_id")
              == t2.session_id, str(T.recover_info(root=d, now=hb2["t_wall"] + 999.0)))
        # 直接改 boot_id 再判（跨重启）
        hb2["boot_id"] = "not-this-boot"
        (t2.dir / "heartbeat.json").write_text(json.dumps(hb2), encoding="utf-8")
        check("⭐⭐ boot 变了 -> 认残局（跨重启）",
              T.recover_info(root=d, now=hb2["t_wall"] + 1.0).get("boot_changed") is True,
              str(T.recover_info(root=d, now=hb2["t_wall"] + 1.0)))


def t_start_emits_recovered() -> None:
    with tempfile.TemporaryDirectory() as d:
        t = _mk(d, sid="first")
        t.start()
        t.heartbeat()
        _wait_lines(t.events_path, 2)            # 写盘在后台：等它落全再判残局
        t.close()                                # 没写 end = 残局
        t2 = _mk(d, sid="next")
        t2.start()
        t2.close()
        rows = _read(t2.events_path)
        names = [r["event"] for r in rows]
        check("⭐⭐ 新会话先补 session.recovered（因果在前）",
              names[:2] == ["session.recovered", "session.start"], str(names))
        rec = rows[0]
        check("⭐ recovered 带 prev_session_id / last_seq / outcome=abnormal",
              rec["attrs"]["prev_session_id"] == "first"
              and rec["attrs"]["outcome"] == "abnormal", str(rec["attrs"]))


def t_module_level_noop_safe() -> None:
    with tempfile.TemporaryDirectory() as d:
        os.environ["CLASSLIVE_TEL_TEST_HOME"] = d
        T.emit("before.start")                   # 没 start -> 静默 no-op
        T.content("x", {"a": 1})
        T.heartbeat()
        T.end("ok")
        T.close()
        check("⭐ 没 start 时模块级调用全是 no-op（不抛、不建文件）",
              not list(pathlib.Path(d).glob("*.jsonl")), str(list(pathlib.Path(d).iterdir())))


def t_start_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as d:
        a = T.start(root=d, attrs={"mode": "normal"})
        b = T.start(root=d)
        check("⭐ start() 重复调用返回同一实例（不建第二个会话）", a is b, f"{a!r} {b!r}")
        T.close()
        check("⭐ close 之后 current() 为空", T.current() is None, str(T.current()))


def main() -> int:
    print("telemetry 判据（全部离线）")
    for name, fn in [
        ("信封 / 白名单 / 路径脱敏", t_envelope_and_whitelist),
        ("内容与元数据分文件", t_content_separate_file),
        ("有界队列丢最旧", t_queue_bounded_drops_oldest),
        ("心跳 + 残局判定", t_heartbeat_and_recover),
        ("新会话补 recovered", t_start_emits_recovered),
        ("模块级 no-op 安全", t_module_level_noop_safe),
        ("start 幂等 / close 收口", t_start_is_idempotent),
    ]:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:                                           # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(name, False, f"{type(e).__name__}: {e}")
    print(f"\n{'❌ 失败 ' + str(len(FAILED)) + '：' + '、'.join(FAILED) if FAILED else '✅ 全部通过'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
