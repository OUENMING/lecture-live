#!/usr/bin/env python3
"""调试通道（`debug_ctl.py` + `tools/clctl.py`）的判据 —— D-1：通道本体，不接线。

全部进程内、不碰真 `~/.classlive/`：每条用例自己建临时根目录传给 `Ctl(root=…)` / `clctl --root`。
测试线程里另起一个「假主线程」循环 `poll()`；起的线程和 socket 都在 tearDown 里收掉，
不遗留 `/tmp/classlive-ctl-*`。

钉住的东西（每条对应任务单的一句话）：
  关着 = 没有 · 命令在 poll() 的线程上跑 · 没人 poll 就 5 s 回 timeout（并且之后不会再被执行）·
  --wait 能等状态也能等事件计数 · 长路径落 /tmp 且 ctl.path 找得到、关了都清掉 ·
  目录 0700 / socket 0600 · 哨兵（key）不进任何回复。
"""
import io
import json
import os
import pathlib
import stat
import sys
import tempfile
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import clctl  # noqa: E402
import debug_ctl  # noqa: E402
import paths  # noqa: E402

SENTINEL = "cl-SENTINEL-KEY"


def run_clctl(*argv):
    out, err = io.StringIO(), io.StringIO()
    rc = clctl.main(list(argv), out=out, err=err)
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    flag = "1"

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in (debug_ctl.FLAG, debug_ctl.FLAG_TEXT, "CLASSLIVE_CTL_SOCK")}
        os.environ.pop(debug_ctl.FLAG_TEXT, None)
        os.environ.pop("CLASSLIVE_CTL_SOCK", None)
        if self.flag is None:
            os.environ.pop(debug_ctl.FLAG, None)
        else:
            os.environ[debug_ctl.FLAG] = self.flag
        self._tmp = tempfile.TemporaryDirectory(prefix="clt", dir="/tmp")
        self.root = pathlib.Path(self._tmp.name)
        self.ctls = []
        self.pumps = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for stop, th in self.pumps:
            stop.set()
            th.join(timeout=3)
        for c in self.ctls:
            c.close()
        self._tmp.cleanup()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def make(self, root=None, **kw):
        c = debug_ctl.Ctl(root=root or self.root, **kw)
        self.ctls.append(c)
        return c

    def pump(self, ctl):
        """假主线程：循环 poll()。返回它的线程 id 容器。"""
        stop = threading.Event()
        ident = []

        def loop():
            ident.append(threading.get_ident())
            while not stop.is_set():
                ctl.poll()
                time.sleep(0.005)

        th = threading.Thread(target=loop, name="fake-main", daemon=True)
        th.start()
        self.pumps.append((stop, th))
        while not ident:
            time.sleep(0.001)
        return ident[0]

    def sock(self, ctl):
        self.assertIsNotNone(ctl.sock_path)
        return str(ctl.sock_path)


class Disabled(Base):
    flag = None

    def test_off_means_nothing(self):
        ctl = self.make()
        before = threading.active_count()
        self.assertFalse(debug_ctl.enabled())
        self.assertFalse(ctl.start())
        self.assertEqual(threading.active_count(), before, "开关关着不许起线程")
        ctl.emit("final", n=1)
        ctl.poll()
        d = paths.ctl_dir(root=self.root)
        self.assertEqual(list(d.glob("*")) if d.exists() else [], [], "开关关着不许建 socket / 目录里的东西")

    def test_flag_must_be_exactly_1(self):
        os.environ[debug_ctl.FLAG] = "0"
        self.assertFalse(debug_ctl.enabled())
        os.environ[debug_ctl.FLAG] = "yes"
        self.assertFalse(debug_ctl.enabled())


class Commands(Base):
    def setUp(self):
        super().setUp()
        self.ctl = self.make()
        self.mode = {"mode": "both"}
        self.ctl.register_state("trans", lambda: dict(self.mode))
        self.ctl.register_state("build", lambda: {"pid": os.getpid(), "ok": True, "x": 1.5, "n": None})
        self.ctl.register_command("mode", self._set_mode, "mode:<both|en|raw>")
        self.assertTrue(self.ctl.start())
        self.main_id = self.pump(self.ctl)
        self.s = self.sock(self.ctl)

    def _set_mode(self, args):
        if args not in ("both", "en", "raw"):
            raise debug_ctl.CtlError("bad_args", "mode 只认 both|en|raw")
        self.mode["mode"] = args
        return {"mode": args}

    def test_ping(self):
        rc, out, _ = run_clctl("--sock", self.s, "ping")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["pong"])

    def test_state_all_and_sections(self):
        rc, out, _ = run_clctl("--sock", self.s, "state")
        d = json.loads(out)
        self.assertEqual((rc, d["schema"], d["trans"], d["build"]["pid"]), (0, 1, {"mode": "both"}, os.getpid()))
        rc, out, _ = run_clctl("--sock", self.s, "state:trans")
        d = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertNotIn("build", d)
        rc, _, err = run_clctl("--sock", self.s, "state:nope")
        self.assertEqual(rc, 1)
        self.assertIn("nope", err)

    def test_help_lists_commands_and_field_types(self):
        rc, out, _ = run_clctl("--sock", self.s, "help")
        d = json.loads(out)
        self.assertEqual(rc, 0)
        for name in ("ping", "help", "state", "events", "stall", "mode"):
            self.assertIn(name, d["commands"])
        self.assertEqual(d["state"]["build"], {"pid": "int", "ok": "bool", "x": "float", "n": "null"})
        self.assertEqual(d["state"]["trans"], {"mode": "str"})

    def test_unknown_command_exit_3(self):
        rc, _, err = run_clctl("--sock", self.s, "frobnicate")
        self.assertEqual(rc, 3)
        self.assertIn("unknown_command", err)

    def test_command_failure_exit_1_and_ok_exit_0(self):
        rc, _, err = run_clctl("--sock", self.s, "mode:weird")
        self.assertEqual(rc, 1)
        self.assertIn("bad_args", err)
        rc, out, _ = run_clctl("--sock", self.s, "mode:raw")
        self.assertEqual((rc, json.loads(out)), (0, {"mode": "raw"}))

    def test_command_exception_does_not_kill_poll(self):
        def boom(_):
            raise RuntimeError("x")
        self.ctl.register_command("boom", boom, "炸")
        rc, _, err = run_clctl("--sock", self.s, "boom")
        self.assertEqual(rc, 1)
        self.assertIn("RuntimeError", err)
        self.assertEqual(run_clctl("--sock", self.s, "ping")[0], 0)

    def test_state_section_error_is_contained(self):
        self.ctl.register_state("bad", lambda: 1 / 0)
        rc, out, _ = run_clctl("--sock", self.s, "state:bad")
        self.assertEqual((rc, json.loads(out)["bad"]), (0, {"_error": "ZeroDivisionError"}))

    def test_events_since(self):
        for i in range(5):
            self.ctl.emit("final", n=i)
        self.ctl.emit("seal")
        rc, out, _ = run_clctl("--sock", self.s, "events")
        d = json.loads(out)
        self.assertEqual((rc, d["seq"], d["counts"]), (0, 6, {"final": 5, "seal": 1}))
        self.assertEqual([e["seq"] for e in d["items"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual(d["items"][0]["n"], 0)
        self.assertNotIn("n", d["items"][5])
        _, out, _ = run_clctl("--sock", self.s, "events:since=4")
        d = json.loads(out)
        self.assertEqual([(e["seq"], e["kind"]) for e in d["items"]], [(5, "final"), (6, "seal")])
        _, out, _ = run_clctl("--sock", self.s, "events:limit=0")
        self.assertEqual(json.loads(out)["items"], [])
        self.assertEqual(run_clctl("--sock", self.s, "events:since=x")[0], 1)
        self.assertEqual(run_clctl("--sock", self.s, "events:path=/etc/passwd")[0], 1)

    def test_events_ring_is_bounded_and_counts_are_not(self):
        for _ in range(debug_ctl.RING + 50):
            self.ctl.emit("x")
        _, out, _ = run_clctl("--sock", self.s, "events:since=0,limit=5000")
        d = json.loads(out)
        self.assertEqual(len(d["items"]), debug_ctl.RING)
        self.assertEqual(d["counts"]["x"], debug_ctl.RING + 50)
        self.assertEqual(d["lost"], 50)

    def test_ring_false_counts_but_stays_out_of_ring(self):
        self.ctl.emit("final")
        for _ in range(debug_ctl.RING + 10):
            self.ctl.emit("zh", ring=False)
        _, out, _ = run_clctl("--sock", self.s, "events:since=0,limit=5000")
        d = json.loads(out)
        self.assertEqual([e["kind"] for e in d["items"]], ["final"])
        self.assertEqual(d["counts"]["zh"], debug_ctl.RING + 10)

    def test_reserved_state_names(self):
        for n in ("schema", "at"):
            with self.assertRaises(ValueError):
                self.ctl.register_state(n, lambda: {})

    def test_events_carry_no_text_by_default(self):
        self.ctl.emit("final", text={"en": "hello", "zh": "你好"})
        _, out, _ = run_clctl("--sock", self.s, "events")
        ev = json.loads(out)["items"][0]
        self.assertNotIn("en", ev)
        self.assertNotIn("zh", ev)

    def test_commands_run_on_the_poll_thread(self):
        seen = []
        self.ctl.register_command("who", lambda a: seen.append(threading.get_ident()) or {}, "记线程")
        self.assertEqual(run_clctl("--sock", self.s, "who")[0], 0)
        self.assertEqual(seen, [self.main_id], "命令必须在调 poll() 的线程上跑")
        self.assertNotIn(threading.get_ident(), seen)

    def test_poll_takes_at_most_8_per_call(self):
        ctl = self.make(root=self.root / "b")
        ctl.register_command("one", lambda a: {}, "x")
        self.assertTrue(ctl.start())
        # 不起假主线程：手动 poll，数每次执行了几条
        done = []
        ctl.register_command("mark", lambda a: done.append(a) or {}, "x")
        s = self.sock(ctl)
        ths = [threading.Thread(target=run_clctl, args=("--sock", s, f"mark:{i}"), daemon=True) for i in range(12)]
        for t in ths:
            t.start()
        deadline = time.monotonic() + 3
        while ctl._q.qsize() < 12 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(ctl._q.qsize(), 12)
        ctl.poll()
        self.assertEqual(len(done), debug_ctl.MAX_PER_POLL)
        ctl.poll()
        for t in ths:
            t.join(timeout=3)
        self.assertEqual(len(done), 12)

    def test_stall_blocks_the_poll_thread_and_reports_slow_ms(self):
        t0 = time.monotonic()
        rc, out, _ = run_clctl("--sock", self.s, "--json", "stall:0.3")
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(time.monotonic() - t0, 0.29)
        r = json.loads(out)
        self.assertGreaterEqual(r["slow_ms"], 250)
        self.assertEqual(run_clctl("--sock", self.s, "stall:9")[0], 1)
        self.assertEqual(run_clctl("--sock", self.s, "stall:0")[0], 1)
        self.assertEqual(run_clctl("--sock", self.s, "stall:abc")[0], 1)

    def test_usage_errors_exit_64(self):
        self.assertEqual(run_clctl()[0], 64)
        self.assertEqual(run_clctl("--sock", self.s, "ping", "state")[0], 64)
        self.assertEqual(run_clctl("--sock", self.s, "--wait", "nonsense")[0], 64)
        self.assertEqual(run_clctl("--sock", self.s, "--wait", "trans.mode > raw")[0], 64)
        self.assertEqual(run_clctl("--bogus")[0], 64)

    def test_no_socket_exit_2(self):
        rc, _, err = run_clctl("--root", str(self.root / "nothing"), "ping")
        self.assertEqual(rc, 2)
        self.assertIn("找不到 socket", err)

    def test_find_socket_order(self):
        # root/run 下最新的 ctl-*.sock（短路径时就是 ctl.sock_path 本身）
        rc, out, _ = run_clctl("--root", str(self.root), "ping")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["pong"])
        # $CLASSLIVE_CTL_SOCK 优先于 root；--sock 优先于环境变量
        os.environ["CLASSLIVE_CTL_SOCK"] = self.s
        self.assertEqual(run_clctl("--root", str(self.root / "nothing"), "ping")[0], 0)
        self.assertEqual(run_clctl("--sock", str(self.root / "no.sock"), "ping")[0], 2)

    def test_wait_state(self):
        threading.Timer(0.3, lambda: self.mode.update(mode="raw")).start()
        t0 = time.monotonic()
        rc, _, _ = run_clctl("--sock", self.s, "--wait", "trans.mode == raw", "--timeout", "5")
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(time.monotonic() - t0, 0.25)
        rc, _, err = run_clctl("--sock", self.s, "--wait", "trans.mode == en", "--timeout", "0.5")
        self.assertEqual(rc, 2)
        self.assertIn("raw", err)                       # 超时要说出当时的值
        rc, _, err = run_clctl("--sock", self.s, "--wait", "trans.nope == 1", "--timeout", "0.3")
        self.assertEqual(rc, 2)
        self.assertIn("路径不存在", err)

    def test_wait_numeric_and_bool_comparisons(self):
        self.assertEqual(run_clctl("--sock", self.s, "--wait", "build.x >= 1.5", "--timeout", "1")[0], 0)
        self.assertEqual(run_clctl("--sock", self.s, "--wait", "build.x > 1.5", "--timeout", "0.3")[0], 2)
        self.assertEqual(run_clctl("--sock", self.s, "--wait", "build.ok == true", "--timeout", "1")[0], 0)
        self.assertEqual(run_clctl("--sock", self.s, "--wait", "build.ok != false", "--timeout", "1")[0], 0)
        self.assertEqual(run_clctl("--sock", self.s, "--wait", "build.n == null", "--timeout", "1")[0], 0)

    def test_wait_event_count(self):
        def burst():
            for _ in range(3):
                time.sleep(0.1)
                self.ctl.emit("final")
        th = threading.Thread(target=burst, daemon=True)
        th.start()
        rc, _, _ = run_clctl("--sock", self.s, "--wait", "events.final >= 3", "--timeout", "5")
        th.join(timeout=3)
        self.assertEqual(rc, 0)
        self.assertEqual(self.ctl._counts["final"], 3)

    def test_wait_event_count_times_out_when_not_enough(self):
        self.ctl.emit("final")
        self.ctl.emit("final")
        t0 = time.monotonic()
        rc, _, err = run_clctl("--sock", self.s, "--wait", "events.final >= 3", "--timeout", "0.6")
        self.assertEqual(rc, 2)
        self.assertGreaterEqual(time.monotonic() - t0, 0.55)
        self.assertIn("2", err)
        # 一次都没发生过的 kind 是 0，不是「路径不存在」
        self.assertEqual(run_clctl("--sock", self.s, "--wait", "events.never == 0", "--timeout", "1")[0], 0)

    def test_record_writes_jsonl(self):
        out = self.root / "rec.jsonl"
        rc, _, _ = run_clctl("--sock", self.s, "--record", str(out), "--every", "0.1", "--for", "0.45")
        self.assertEqual(rc, 0)
        lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
        self.assertGreaterEqual(len(lines), 3)
        self.assertEqual(lines[0]["state"]["trans"], {"mode": "both"})
        self.assertLess(lines[0]["t"], lines[-1]["t"])

    def test_text_flag_adds_truncated_text(self):
        ctl = self.make(root=self.root / "t")
        os.environ[debug_ctl.FLAG_TEXT] = "1"
        self.assertTrue(ctl.start())
        self.pump(ctl)
        ctl.emit("final", text={"en": "e" * 500, "zh": "你好", "secret": "no"})
        _, out, _ = run_clctl("--sock", self.sock(ctl), "events")
        ev = json.loads(out)["items"][0]
        self.assertEqual((len(ev["en"]), ev["zh"]), (debug_ctl.TEXT_MAX, "你好"))
        self.assertNotIn("secret", ev)


class NoPoll(Base):
    def test_timeout_when_nobody_polls_and_stale_request_is_dropped(self):
        ctl = self.make()                                # 默认 5 s
        ran = []
        ctl.register_command("mark", lambda a: ran.append(a) or {}, "x")
        self.assertTrue(ctl.start())
        t0 = time.monotonic()
        rc, _, err = run_clctl("--sock", self.sock(ctl), "mark:late")
        dt = time.monotonic() - t0
        self.assertEqual(rc, 2)
        self.assertIn("timeout", err)
        self.assertGreaterEqual(dt, 4.9)
        self.assertLess(dt, 8)
        ctl.poll()                                       # 迟到的 poll 不许再执行已超时的请求
        self.assertEqual(ran, [])

    def test_close_releases_waiting_requests_quickly(self):
        ctl = self.make(timeout_s=30)
        self.assertTrue(ctl.start())
        res = []
        th = threading.Thread(target=lambda: res.append(run_clctl("--sock", self.sock(ctl), "ping")), daemon=True)
        th.start()
        time.sleep(0.3)
        t0 = time.monotonic()
        ctl.close()
        th.join(timeout=5)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertEqual(res[0][0], 2)


class LongPath(Base):
    def test_long_state_root_falls_back_to_tmp_with_pointer(self):
        root = self.root
        while len(str(root)) < 120:
            root = root / ("d" * 30)
        root.mkdir(parents=True)
        self.assertGreaterEqual(len(str(root)), 120)
        ctl = self.make(root=root)
        self.assertTrue(ctl.start(), ctl.error)
        sock = ctl.sock_path
        self.assertTrue(str(sock).startswith(f"/tmp/classlive-ctl-{os.getuid()}/"), sock)
        ptr = paths.ctl_dir(root=root) / "ctl.path"
        self.assertEqual(ptr.read_text(encoding="utf-8").strip(), str(sock))
        self.pump(ctl)
        rc, out, _ = run_clctl("--root", str(root), "ping")        # 靠 ctl.path 找到
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["pong"])
        ctl.close()
        self.assertFalse(sock.exists(), "close() 后 /tmp 里的 socket 要没了")
        self.assertFalse(ptr.exists(), "close() 后 ctl.path 要没了")
        tmpdir = pathlib.Path(f"/tmp/classlive-ctl-{os.getuid()}")
        self.assertFalse((tmpdir / f"{os.getpid()}.sock").exists())

    def test_short_path_stays_in_ctl_dir_without_pointer(self):
        ctl = self.make()
        self.assertTrue(ctl.start())
        d = paths.ctl_dir(root=self.root)
        self.assertEqual(ctl.sock_path, d / f"ctl-{os.getpid()}.sock")
        self.assertFalse((d / "ctl.path").exists())
        sock = ctl.sock_path
        ctl.close()
        self.assertFalse(sock.exists())


class Perms(Base):
    def test_dir_0700_socket_0600(self):
        ctl = self.make()
        self.assertTrue(ctl.start())
        d = paths.ctl_dir(root=self.root)
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(ctl.sock_path.stat().st_mode), 0o600)

    def test_existing_loose_dir_is_tightened(self):
        d = paths.ctl_dir(root=self.root)
        d.mkdir(parents=True)
        os.chmod(d, 0o755)
        ctl = self.make()
        self.assertTrue(ctl.start())
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)

    def test_fallback_tmp_dir_and_socket_perms(self):
        root = self.root
        while len(os.fsencode(str(root / "run" / f"ctl-{os.getpid()}.sock"))) <= debug_ctl.SOCK_PATH_MAX + 5:
            root = root / ("d" * 30)
        root.mkdir(parents=True)
        ctl = self.make(root=root)
        self.assertTrue(ctl.start(), ctl.error)
        self.assertEqual(stat.S_IMODE(ctl.sock_path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(ctl.sock_path.stat().st_mode), 0o600)


class Sentinel(Base):
    def test_key_never_appears_in_any_reply(self):
        os.environ["CLASSLIVE_TEST_SENTINEL"] = SENTINEL
        os.environ["DEEPSEEK_API_KEY"] = SENTINEL
        self.addCleanup(os.environ.pop, "CLASSLIVE_TEST_SENTINEL", None)
        self.addCleanup(os.environ.pop, "DEEPSEEK_API_KEY", None)
        key = paths.test_key(root=self.root)
        key.write_text(SENTINEL + "\n", encoding="utf-8")
        os.chmod(key, 0o600)
        (self.root / "install-id").write_text(SENTINEL, encoding="utf-8")

        ctl = self.make()
        ctl.register_state("trans", lambda: {"mode": "both"})
        ctl.register_command("mode", lambda a: {"mode": a}, "mode:<…>")
        self.assertTrue(ctl.start())
        self.pump(ctl)
        s = self.sock(ctl)
        ctl.emit("final", n=1, text={"en": "hi"})
        replies = []
        for cmd in ("ping", "help", "state", "state:trans", "events", "events:since=0", "mode:raw",
                    "stall:0.01", "nosuch", "state:bad", "events:x=1", "mode"):
            replies.append(clctl.request(s, *cmd.partition(":")[::2]))
        text = json.dumps(replies, ensure_ascii=False)
        self.assertGreater(len(replies), 10)
        self.assertIn('"pong": true', text)               # 确实拿到了真回复，不是空转
        self.assertNotIn(SENTINEL, text)
        self.assertNotIn("DEEPSEEK", text)

    def test_no_eval_or_path_commands(self):
        ctl = self.make()
        self.assertTrue(ctl.start())
        self.pump(ctl)
        s = self.sock(ctl)
        for cmd in ("eval", "exec", "read", "write", "open", "cat"):
            self.assertEqual(clctl.exit_code(clctl.request(s, cmd, "/etc/passwd")), 3, cmd)
        with self.assertRaises(ValueError):
            ctl.register_command("state", lambda a: {}, "覆盖内置")
        with self.assertRaises(ValueError):
            ctl.register_command("a/b", lambda a: {}, "非法名")


class Protocol(Base):
    def test_malformed_request_gets_bad_request(self):
        import socket as sk
        ctl = self.make()
        self.assertTrue(ctl.start())
        self.pump(ctl)
        c = sk.socket(sk.AF_UNIX, sk.SOCK_STREAM)
        c.settimeout(5)
        try:
            c.connect(self.sock(ctl))
            c.sendall(b"not json\n" + json.dumps({"id": "z", "cmd": "ping"}).encode() + b"\n")
            buf = b""
            while buf.count(b"\n") < 2:
                buf += c.recv(4096)
        finally:
            c.close()
        a, b = [json.loads(x) for x in buf.split(b"\n")[:2]]
        self.assertEqual((a["ok"], a["error"]["code"]), (False, "bad_request"))
        self.assertEqual((b["id"], b["ok"]), ("z", True))        # 同一连接上继续可用

    def test_close_is_idempotent_and_cleans_up(self):
        ctl = self.make()
        self.assertTrue(ctl.start())
        before = threading.active_count()
        sock = ctl.sock_path
        ctl.close()
        ctl.close()
        self.assertFalse(sock.exists())
        self.assertLess(threading.active_count(), before)
        self.assertEqual(run_clctl("--sock", str(sock), "ping")[0], 2)


class _FakeQ:
    def __init__(self, n):
        self._n = n

    def qsize(self):
        return self._n


class _FakeStream:
    def metrics(self):
        return {"engine": "soniox", "sealed_n": 3}


class _FakeSeg:
    is_speaking = True


class _FakeSrc:
    def is_done(self):
        return False


class _FakeTester:
    def _loop_block(self):
        return {"n_ge_0.3s": 2, "n_ge_1s": 1, "n_ge_3s": 0, "total_s": 2.5, "items": [{"x": 1}]}


class _Args:
    source, ui, course, speed = "file", "terminal", "TESTISO", 4.0


class Wiring(Base):
    """D-2：`main._wire_debug_ctl` 注册的分区 / 命令（进程内，用假对象）。"""

    def setUp(self):
        super().setUp()
        import main
        self.main = main
        self.trans = {"mode": "both"}
        self.running, self.stopping = threading.Event(), threading.Event()
        self.running.set()
        self.mode_calls = []

    def wire(self, **over):
        kw = dict(args=_Args(), version="9.9.9", running=self.running, stopping=self.stopping,
                  trans=self.trans, queues={"streamq": _FakeQ(3), "sealedq": _FakeQ(0), "finalq": _FakeQ(1)},
                  get_src=lambda: _FakeSrc(), get_seg=lambda: _FakeSeg(), get_stream=lambda: _FakeStream(),
                  get_tester=lambda: _FakeTester(),
                  set_mode=lambda m: (self.mode_calls.append(m), self.trans.update(mode=m)))
        kw.update(over)
        ctl = self.make()
        self.main._wire_debug_ctl(ctl, **kw)
        self.assertTrue(ctl.start())
        self.pump(ctl)
        return ctl, self.sock(ctl)

    def test_state_sections(self):
        ctl, s = self.wire()
        d = clctl.request(s, "state", "")["data"]
        self.assertEqual(set(d) - {"schema", "at"}, {"build", "listen", "stream", "trans", "queues", "loop"})
        self.assertEqual(d["build"]["version"], "9.9.9")
        self.assertEqual(d["build"]["course"], "TESTISO")
        self.assertEqual(d["listen"], {"running": True, "stopping": False, "source": "file",
                                       "audio_done": False, "speaking": True})
        self.assertEqual(d["stream"]["sealed_n"], 3)
        self.assertEqual(d["queues"], {"streamq": 3, "sealedq": 0, "finalq": 1})
        self.assertEqual(d["loop"], {"n_ge_0.3s": 2, "n_ge_1s": 1, "n_ge_3s": 0, "total_s": 2.5})

    def test_missing_objects_are_null_not_errors(self):
        ctl, s = self.wire(get_src=lambda: None, get_seg=lambda: None, get_stream=lambda: None,
                           get_tester=lambda: None)
        d = clctl.request(s, "state", "")["data"]
        self.assertIsNone(d["stream"])
        self.assertIsNone(d["loop"])
        self.assertIsNone(d["listen"]["audio_done"])
        self.assertIsNone(d["listen"]["speaking"])
        self.assertNotIn("_error", json.dumps(d))

    def test_mode_goes_through_setter_and_validates(self):
        ctl, s = self.wire()
        r = clctl.request(s, "mode", "raw")
        self.assertTrue(r["ok"])
        self.assertEqual(self.mode_calls, ["raw"])
        self.assertEqual(clctl.request(s, "state", "trans")["data"]["trans"]["mode"], "raw")
        bad = clctl.request(s, "mode", "wat")
        self.assertFalse(bad["ok"])
        self.assertEqual(clctl.exit_code(bad), 1)
        self.assertEqual(self.mode_calls, ["raw"], "非法档位不许走到 setter")

    def test_stop_sets_the_same_event_as_the_quit_button(self):
        ctl, s = self.wire()
        self.assertFalse(self.stopping.is_set())
        self.assertTrue(clctl.request(s, "stop", "")["ok"])
        self.assertTrue(self.stopping.is_set())

    def test_sentinel_not_in_state_or_help(self):
        os.environ["CLASSLIVE_TEST_SENTINEL"] = SENTINEL
        os.environ["DEEPSEEK_API_KEY"] = SENTINEL
        self.addCleanup(os.environ.pop, "CLASSLIVE_TEST_SENTINEL", None)
        self.addCleanup(os.environ.pop, "DEEPSEEK_API_KEY", None)
        key = paths.test_key(root=self.root)
        key.write_text(SENTINEL + "\n", encoding="utf-8")
        (self.root / "install-id").write_text(SENTINEL, encoding="utf-8")
        ctl, s = self.wire()
        text = json.dumps([clctl.request(s, "state", ""), clctl.request(s, "help", ""),
                           clctl.request(s, "events", "")], ensure_ascii=False)
        self.assertIn('"queues"', text)                    # 真拿到了全部分区
        self.assertNotIn(SENTINEL, text)


class MainSource(unittest.TestCase):
    """D-2 接线点的文本判据（接线位置错了这些会红）。"""

    def test_main_polls_after_drain_before_probe_stamp_and_in_wrapup(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        i = src.index("            drain()\n            ctl.poll()")
        self.assertIn("_l3 = _tm()", src[i:i + 200], "poll() 要在 drain() 之后、_l3 打点之前")
        self.assertGreaterEqual(src.count("ctl.poll()"), 3, "主循环 + 收尾冲刷 + 收尾 tick 都要 poll")
        self.assertIn("ctl.close()", src)
        self.assertIn("_ctl_ev(item)", src)

    def test_run_isolated_uses_test_key_before_state_root_swap(self):
        src = (ROOT / "docs" / "experiments" / "run_isolated.py").read_text(encoding="utf-8")
        self.assertLess(src.index("load_test_key()"), src.index("paths.STATE_ROOT = "))
        self.assertLess(src.index("paths.install_id()"), src.index("paths.STATE_ROOT = "))
        self.assertIn('os.environ["CLASSLIVE_DEBUG_CTL"] = "1"', src)
        self.assertNotIn(".deepseek_key\")", src.split("# ⚠️ **必须在改")[1])


if __name__ == "__main__":
    unittest.main()
