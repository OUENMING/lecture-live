#!/usr/bin/env python3
"""`upload.py` 的判据 —— 课后上传的队列 / 退避 / 幂等 / 脱敏。

    ClassLive.app/Contents/MacOS/python tests/test_upload.py

## 这个文件钉住了什么

按 `docs/PLAN-test-mode.md` §7 的 T6–T9：

1. ⭐ **入队去重**（同一节课排两遍只算一遍）· **台账**写下来了
2. ⭐⭐ **退避按指数走且封顶 120 min** · **14 天过期丢弃**
3. ⭐⭐ **脱敏**（`session_file` 里的 home 前缀换成 `<home>`）
4. ⭐ **幂等**：`key` 是会话名哈希 —— 同名永远同前缀，重传**覆盖**不是新增
5. ⚠️ **落盘用 tmp→rename**（不留 `.tmp`）· 队列文件坏了当空队列、**不抛**

⚠️ 语料全是 `tempfile`，**不碰 `~/.classlive/`、不碰真服务器、不联网**。
⚠️ 每条先问「把实现改坏它会不会红」—— 见每条注释。
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import upload                                                         # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


class _Srv:
    """本机 HTTP 服务器：收 PUT，记录 `{path, auth, ct, body}`；状态码可配。

    ⚠️ 照 `tests/test_fetch_model.py` 的本机服务器形状 —— **不碰真服务器、
       不需要凭据、不联网**（生产那个 `make_http_send` 走的就是这条 PUT 契约）。
    """

    def __init__(self, status: int = 200):
        self.reqs: list[dict] = []
        reqs_, status_ = self.reqs, status

        class H(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):                                     # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                reqs_.append({
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "ct": self.headers.get("Content-Type"),
                    "clen": self.headers.get("Content-Length"),
                    "body": self.rfile.read(n) if n else b"",
                })
                self.send_response(status_)
                self.end_headers()

            def log_message(self, *a):                            # noqa: D401
                pass

        self.s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.s.server_address[1]}"
        threading.Thread(target=self.s.serve_forever, daemon=True).start()

    def close(self):
        self.s.shutdown()
        self.s.server_close()


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl_up_"))
    try:
        print("\n--- T6 ⭐ `session_key`：同名同前缀，且够短 ---")
        k1 = upload.session_key("2026-09-30_154742_ECON10770")
        k2 = upload.session_key("2026-09-30_154742_ECON10770")
        k3 = upload.session_key("2026-09-30_154742_ECON10771")
        check("⭐ 同一个名字永远是同一个 key（**幂等的根据**）", k1 == k2)
        check("⭐ 不同名字不同 key", k1 != k3)
        check("⚠️ 16 位十六进制", len(k1) == 16 and all(c in "0123456789abcdef" for c in k1),
              k1)

        print("\n--- T7 ⭐⭐ 脱敏：home 前缀换成 `<home>` ---")
        home = str(pathlib.Path.home())
        src = {"session_file": f"{home}/lecture-live/sessions/x.md",
               "note_file": "", "other": f"{home}/somewhere",
               "nested": [{"session_file": f"{home}/a/b"}]}
        got = upload.scrub(src)
        # ⚠️ 变异验证：把 `k in _SCRUB_KEYS` 去掉 -> 前两条红。
        check("⭐⭐ `session_file` 的**目录结构整个被换掉**（不只换用户名）",
              got["session_file"] == "<path>/x.md", got["session_file"])
        check("⭐ 嵌套里的也换（不是只看最外层）",
              got["nested"][0]["session_file"] == "<path>/b", str(got["nested"]))
        # ⚠️⚠️ **这条是端到端实测逼出来的**：第一版只换 `Path.home()` 开头的，
        #    而隔离跑的路径在 `/tmp/...` → **一个字节都没换**，真传上去的还是完整路径。
        #    变异验证：改回「只换 home 开头」-> 这条红。
        _tmp_path = {"session_file": "/tmp/somewhere/sessions/x.md"}
        check("⭐⭐ **不是 home 开头的绝对路径也得换**（换个位置不许漏）",
              upload.scrub(_tmp_path)["session_file"] == "<path>/x.md",
              upload.scrub(_tmp_path)["session_file"])
        # ⚠️ 变异验证：把 `k in _SCRUB_KEYS` 改成「所有键」-> 这条红。
        check("⚠️ **不在名单里的键不动**（脱敏不是「把所有路径都换掉」）",
              got["other"] == src["other"], got["other"])
        check("⚠️ 空串 / 不存在的键原样（不许造出 `<home>`）", got["note_file"] == "")
        # ⚠️⚠️ 列表那格也不能漏（端到端实测：session_file 换了、audio_files 没换）
        _lst = upload.scrub({"audio_files": ["/tmp/a/x.opus", "/Users/owen/b/y.opus", ""]})
        check("⭐⭐ `audio_files` 是**列表**，里面每条都得换",
              _lst["audio_files"] == ["<path>/x.opus", "<path>/y.opus", ""],
              str(_lst["audio_files"]))

        print("\n--- T8 ⭐⭐ 入队：去重 + 台账 ---")
        home_root = tmp / "cl"
        _now = [1000.0]
        q = upload.UploadQueue(upload.make_local_send(str(tmp / "srv")),
                               root=home_root, now=lambda: _now[0])
        f1 = tmp / "r.json"
        f1.write_text('{"a": 1}', encoding="utf-8")
        n1 = q.enqueue([f1], "sess-A")
        check("⭐ 入队成功，返回文件数", n1 == 1 and q.pending == 1, f"n={n1} pending={q.pending}")
        n2 = q.enqueue([f1], "sess-A")
        # ⚠️ 变异验证：把那段 `if key in {...}: return 0` 删掉 -> 这条红
        #    （崩溃后重跑会排两遍 → 同一节课传两次）。
        check("⭐⭐ **同一节课排两遍只算一遍**（不然崩溃重跑会排双份）",
              n2 == 0 and q.pending == 1, f"n2={n2} pending={q.pending}")
        _led = json.loads((home_root / "upload-ledger.json").read_text(encoding="utf-8"))
        check("⭐⭐ 台账写下来了（`会话名 -> 前缀`）—— **没它就删不掉远端那一节**",
              _led.get("sess-A", {}).get("key") == upload.session_key("sess-A"),
              str(_led))
        check("⚠️ 落盘用的是 tmp→rename（**不留 `.tmp`**）",
              not list(home_root.glob("*.tmp")), str(list(home_root.glob("*.tmp"))))

        print("\n--- T9 ⭐⭐⭐ 退避：指数 + 封顶 120 min；14 天过期 ---")
        _ok = [True]

        def _flaky(local, key):
            return _ok[0]

        # ⚠️ **独立 root** —— 上面那条 `q` 在 `home_root` 里留了一条 `sess-A`，
        #    共用的话队列里会有两条，`expired`/`failed` 的数就对不上了
        #    （第一版就是这么假红的）。
        broot = tmp / "cl2"
        q2 = upload.UploadQueue(_flaky, root=broot, now=lambda: _now[0])
        q2.enqueue([f1], "sess-B")
        _ok[0] = False
        r = q2.pump()
        it = q2.items()[0]
        check("⭐ 失败 -> 留着，且 `tries=1`", r["failed"] == 1 and it["tries"] == 1, str(it))
        check("⭐⭐ 第一次退避 = **60 秒**", it["next_at"] - _now[0] == 60.0, str(it["next_at"] - _now[0]))
        # 没到点 -> 不试
        r2 = q2.pump()
        check("⭐ 还没到 `next_at` -> 这一轮**不试**（不然退避等于没有）",
              r2["failed"] == 0 and r2["sent"] == 0 and r2["pending"] == 1, str(r2))
        # 到了点再失败 -> 翻倍
        _now[0] += 60.0
        q2.pump()
        it = q2.items()[0]
        check("⭐⭐ 第二次退避 = **120 秒**（翻倍）", it["next_at"] - _now[0] == 120.0,
              str(it["next_at"] - _now[0]))
        # 一路失败到封顶
        for _ in range(20):
            _now[0] += upload.BACKOFF_MAX_S
            q2.pump()
        it = q2.items()[0]
        # ⚠️ 变异验证：把 `min(..., BACKOFF_MAX_S)` 去掉 -> 这条红（会涨到天文数字）。
        check(f"⭐⭐⭐ 退避**封顶 {upload.BACKOFF_MAX_S:.0f} 秒（120 min）**",
              it["next_at"] - _now[0] == upload.BACKOFF_MAX_S,
              f"{it['next_at'] - _now[0]:.0f} 秒 / tries={it['tries']}")

        # 过期
        _now[0] += upload.EXPIRE_S + 1
        r3 = q2.pump()
        check("⭐⭐ 超过 14 天 -> **丢弃**（不是无限重试）",
              r3["expired"] == 1 and q2.pending == 0, str(r3))

        print("\n--- T10 ⭐⭐ 幂等：重传**覆盖同一个 key**，不是新增 ---")
        srv = tmp / "srv"
        q3 = upload.UploadQueue(upload.make_local_send(str(srv)), root=home_root,
                                now=lambda: _now[0])
        q3.enqueue([f1], "sess-C")
        q3.pump()
        key = upload.session_key("sess-C")
        first = (srv / key / "r.json").read_text(encoding="utf-8")
        _now[0] += 10
        q3.enqueue([f1], "sess-C")            # 再来一次
        q3.pump()
        all_files = list((srv / key).iterdir())
        check("⭐⭐ 重传之后**还是那一个文件**（key 确定 → 覆盖）",
              len(all_files) == 1 and first == '{"a": 1}',
              str([p.name for p in all_files]))

        print("\n--- T11 ⭐⭐ 发出去的那份**已经脱敏**（本地那份不动） ---")
        rep = tmp / "x.report.json"
        rep.write_text(json.dumps({"session_file": f"{home}/lecture-live/sessions/x.md",
                                   "n": 1}), encoding="utf-8")
        q4 = upload.UploadQueue(upload.make_local_send(str(srv)), root=home_root,
                                now=lambda: _now[0])
        q4.enqueue([rep], "sess-D")
        q4.pump()
        sent = json.loads((srv / upload.session_key("sess-D") / "report.json")
                          .read_text(encoding="utf-8"))
        check("⭐⭐ 发出去的那份里是 `<path>/…`",
              sent["session_file"] == "<path>/x.md",
              sent["session_file"])
        # ⚠️ F26（2026-10-01）：远端对象名改成**按类型**的稳定名 —— 文件头声称
        #    哈希目录是为了藏元数据，而原来 `x.report.json` 把课号+时间戳又带回去了。
        check("⭐⭐ 远端名**不再带课号/时间戳**（`x.report.json` → `report.json`）",
              not (srv / upload.session_key("sess-D") / "x.report.json").exists(),
              str([p.name for p in (srv / upload.session_key("sess-D")).iterdir()]))
        check("⚠️ 而**本机那份一个字节没动**（脱敏只发生在发送时）",
              home in rep.read_text(encoding="utf-8"))
        check("⚠️ 服务端**不该**多出 `.scrubbed` 临时文件",
              not list((srv / upload.session_key("sess-D")).glob("*.scrubbed")),
              str([p.name for p in (srv / upload.session_key("sess-D")).iterdir()]))

        print("\n--- T12 ⚠️ 坏掉的队列文件：当空队列，**不许抛** ---")
        (home_root / "upload-queue.json").write_text("这不是 json", encoding="utf-8")
        q5 = upload.UploadQueue(_flaky, root=home_root, now=lambda: _now[0])
        check("⚠️ 队列文件坏了 -> 空队列（系统边界要宽容）", q5.pending == 0,
              str(q5.pending))

        print("\n--- T13 ⭐⭐ `_remote_name`：远端名不带课号/时间戳 ---")
        # ⚠️ 文件名带课号 + 精确时间戳 = 一份课表 → 远端对象名必须是**按类型**的
        #    稳定名。⚠️ 崩溃补传造的 `<stem>.meta.json` **也要认**，不然它原名透传
        #    → 课号+时间戳又漏回去（2026-10-05 抓到的既有泄漏）。
        #    变异验证：把 `.meta.json` 那条删掉 -> 最后一格红。
        _stem = "2026-09-24_203440_ECON10770"
        for _src, _want in ((f"{_stem}.report.json", "report.json"),
                            (f"{_stem}.md", "transcript.md"),
                            (f"{_stem}.001.opus", "audio.001.opus"),
                            (f"{_stem}.002.wav", "audio.002.wav"),
                            (f"{_stem}.meta.json", "meta.json"),
                            ("2026-10-21_CHE002A.html", "note.html")):
            _got = upload._remote_name(pathlib.Path(_src))
            check(f"⭐ `{_want}`", _got == _want, _got)
            check(f"⚠️ `{_want}` 里**不含 stem**（不泄漏课号/时间戳）",
                  _stem not in _got, _got)
        # ⚠️ 交互版 HTML 的本地名带**日期 + 课号**（`2026-10-21_CHE002A.html`）——
        #    原样透传就是把一份课表漏回去（和 `.md` 那条同一件事）。
        _html = upload._remote_name(pathlib.Path("2026-10-21_CHE002A.html"))
        check("⭐⭐ 交互版 HTML 也按**类型**给稳定名（不漏课号+日期）",
              _html == "note.html" and "CHE002A" not in _html and "2026-10-21" not in _html,
              _html)

        print("\n--- T15 ⭐⭐ `make_http_send`：HTTPS PUT 的契约 ---")
        # 生产那条路 = PUT 到 Worker（**用户零配置**）。判据用一个本机 HTTP 服务器
        # 钉住「拼 URL / 带 Authorization / body 一致 / 非 2xx 与连不上都 False 且不抛」。
        # ⚠️ 变异验证：把 `Authorization` 头删掉 -> 头那条红；把返回判据改成 `== 200`
        #    -> 非 2xx 那几条里 204 会漏（这里用 2xx 区间）。
        _srv = _Srv(200)
        try:
            _f = tmp / "audio.001.opus"
            _f.write_bytes(b"OPUSDATA" * 100)
            _key = upload.session_key("sess-H") + "/audio.001.opus"
            check("⭐⭐ 成功（2xx）-> True",
                  upload.make_http_send(_srv.url, "tok-abc")(_f, _key) is True)
            _r = _srv.reqs[0]
            check("⭐⭐ PUT 到了 `<endpoint>/<key>`", _r["path"] == f"/{_key}", _r["path"])
            check("⭐⭐ `Authorization: Bearer <token>` 头对",
                  _r["auth"] == "Bearer tok-abc", str(_r["auth"]))
            check("⭐⭐ 带了 `Content-Length`（Worker 体积闸门读它；chunked 会被当 0 拒）",
                  _r["clen"] == str(len(_f.read_bytes())), str(_r["clen"]))
            check("⭐ body 字节一致", _r["body"] == _f.read_bytes(), str(len(_r["body"])))
        finally:
            _srv.close()

        for _code in (401, 413, 400):
            _s = _Srv(_code)
            try:
                check(f"⭐ 服务端 {_code} -> False",
                      upload.make_http_send(_s.url, "t")(_f, "x/y") is False)
            finally:
                _s.close()

        try:
            check("⭐⭐ 连不上 -> False（**不抛** —— 调用方是退避循环）",
                  upload.make_http_send("http://127.0.0.1:1", "t")(_f, "x/y") is False)
        except Exception as _e:                                   # noqa: BLE001
            check("⭐⭐ 连不上 -> False（**不抛**）", False, f"抛了 {type(_e).__name__}")

        print("\n--- T14 ⭐⭐ 崩溃恢复：开机扫残留的 `.pending` 标记 ---")
        # ⚠️⚠️ 这条守的是**整类数据不丢**：`TestSession.finish()` 跑不到
        #    （崩溃/强杀/关机）→ 没有 report → 而 enqueue 是在 finish 里调的
        #    → 那一整类数据全丢。`.pending` 是**开录时就写**的，比 finish 活得久。
        # ⚠️ 变异验证：把 `recover_pending` 里的 `q.enqueue(...)` 删掉 -> 这条红。
        rsess = tmp / "sess"
        rsess.mkdir()
        stem = "2026-09-30_160000_CRASHED"
        # 崩在现场：有半截音频、**没有 report**
        (rsess / f"{stem}.001.opus").write_bytes(b"FAKEOPUS" * 100)
        (rsess / f"{stem}.002.opus").write_bytes(b"FAKEOPUS" * 20)
        (rsess / f"{stem}{upload.PENDING_TAIL}").write_text(
            '{"stem": "x", "started": "2026-09-30 16:00:00"}', encoding="utf-8")
        rroot = tmp / "cl3"
        # ⚠️ check.py 跑判据时设 `CLASSLIVE_UPLOAD=0`（判据不许真上传）—— 而这一条
        #    测的正是"上传开着时怎么补传"，所以这里把开关显式打开、完事还原。
        _saved_up14 = os.environ.get("CLASSLIVE_UPLOAD")
        os.environ["CLASSLIVE_UPLOAD"] = "1"
        try:
            got = upload.recover_pending(
                rsess, upload.make_local_send(str(tmp / "srv2")), root=rroot,
                now=lambda: _now[0])
        finally:
            if _saved_up14 is None:
                os.environ.pop("CLASSLIVE_UPLOAD", None)
            else:
                os.environ["CLASSLIVE_UPLOAD"] = _saved_up14
        check("⭐⭐ 找到残留标记并**排进了队列**（不然崩溃那类数据全丢）",
              got["found"] == 1 and got["queued"] >= 2,
              f"found={got['found']} queued={got['queued']}")
        q6 = upload.UploadQueue(lambda *a: True, root=rroot, now=lambda: _now[0])
        _names = [pathlib.Path(f).name for i in q6.items() for f in i["files"]]
        check("⭐ 半截音频也在里面（**那也是数据**）",
              any(n.endswith(".001.opus") for n in _names), str(_names))
        check("⭐ 写了一份 `meta.json` 标明这次是**崩过的**",
              any(n.endswith(".meta.json") for n in _names), str(_names))
        # ⚠️⚠️ 正文不许带课号+时间戳（`_prepare` 的 scrub 只认路径，认不出 stem/文件名）。
        _meta_body = (rsess / f"{stem}.meta.json").read_text(encoding="utf-8")
        check("⭐⭐ 崩溃标记**正文不含 stem**（课号+时间戳不外漏）",
              stem not in _meta_body, _meta_body[:100])
        # ⚠️ 变异验证：把 `.replace(...)` 那句删掉 -> 这条红（会每次启动都重复排）。
        check("⭐⭐ 标记**改名**而不是删（不再重复触发，且留痕）",
              not (rsess / f"{stem}{upload.PENDING_TAIL}").exists()
              and (rsess / f"{stem}.recovered.json").exists(),
              str([p.name for p in rsess.iterdir()]))

        # ── ⭐ 台账 `_save_ledger` 必须在锁里（2026-10-12 全仓审查）──────────────
        # ⚠️ 它是「读当前 → 合并 → 写回」的复合更新，两个并发 `enqueue` 在锁外跑就会互相覆盖；
        #    而台账丢一条 = 远端那一节课**删不掉**（`key` 是哈希、不可逆）。
        #    ⚠️ 判据钉 `_lock.locked()` 是**有意的**：这里要钉的就是「互斥」这件事本身，
        #       而靠两线程撞时序去复现覆盖，是一个必然会 flaky 的判据。
        _rq = pathlib.Path(tmp) / "ledgerlock"
        _q = upload.UploadQueue(lambda *a: True, root=_rq)
        _f = _rq / "a.json"
        _rq.mkdir(parents=True, exist_ok=True)
        _f.write_text("{}", encoding="utf-8")
        _seen: list = []
        _orig_led = _q._save_ledger

        def _spy_led(stem, key):
            _seen.append(_q._lock.locked())
            return _orig_led(stem, key)

        _q._save_ledger = _spy_led
        _n_enq = _q.enqueue([_f], "2026-10-12_ECON10740")
        check("⭐⭐ `enqueue` 是**持锁**调 `_save_ledger` 的",
              _seen == [True] and _n_enq == 1, f"持锁={_seen} 入队={_n_enq}")
        _led = json.loads(upload.paths.upload_ledger(root=_rq).read_text(encoding="utf-8"))
        check("⭐ 台账真写进去了（读回来是那一节）",
              "2026-10-12_ECON10740" in _led, str(list(_led)[:3]))

        print("\n--- T16 ⭐⭐ 3.10：逐文件进度（重试只补缺的）/ 睡醒提前 / 并文件 ---")
        # ① 传到一半失败 -> 只记住成功的那几个；重试**只补缺的**
        _root4 = tmp / "cl4"
        _sends: list = []

        def _flaky2(local, key):
            _sends.append(key)
            return len(_sends) < 3                # 前两个成，第三个败

        f2 = [tmp / f"a{i}.json" for i in (1, 2, 3)]
        for p in f2:
            p.write_text('{"a": 1}', encoding="utf-8")
        q7 = upload.UploadQueue(_flaky2, root=_root4, now=lambda: _now[0])
        q7.enqueue(f2, "sess-E")
        q7.pump()
        it = q7.items()[0]
        # ⚠️ 变异验证：把 `sent_files` 那段记档删掉 -> 这条红（重试整条重来）。
        check("⭐⭐ 传成的文件记进 `sent_files`（逐文件进度**落盘**）",
              sorted(it.get("sent_files") or []) == ["a1.json", "a2.json"],
              str(it.get("sent_files")))
        _now[0] += upload.BACKOFF_MIN_S + 1.0
        _again: list = []
        q7._send = lambda local, key: (_again.append(key), True)[1]
        q7.pump()
        # ⚠️ 变异验证：把 `if remote in (it.get("sent_files") ...)` 删掉 -> 这条红
        #    （a1/a2 会被重发 —— 对象名确定不会重复，但 9 MB 白传一遍）。
        check("⭐⭐ 重试**只补没传成的那个**（原来整条重来）",
              _again == [f"{upload.session_key('sess-E')}/a3.json"], str(_again))
        check("⭐ 补完 -> 整条出队", q7.pending == 0, str(q7.pending))

        # ② `retry_now`：睡醒把没到点的提前 —— **有界**
        _root5 = tmp / "cl5"
        q8 = upload.UploadQueue(lambda *a: False, root=_root5, now=lambda: _now[0])
        q8.enqueue([f2[0]], "sess-F")
        q8.pump()                                 # 失败 -> next_at = now + 60
        check("⭐ 还没到点 -> 不试（退避生效）", q8.pump()["failed"] == 0)
        n = q8.retry_now()
        it = q8.items()[0]
        # ⚠️ 变异验证：`retry_now` 里把 `wake_retries` 上限判断删掉 -> 下面"到顶不动"那条红。
        check("⭐⭐ 睡醒：提前到「现在」（`wake_retries` 记账）",
              n == 1 and it["next_at"] == _now[0] and it["wake_retries"] == 1, str(it))
        for _ in range(upload.WAKE_RETRY_MAX + 2):
            q8.retry_now()
            q8.pump()                             # 每次都失败 -> next_at 又推到未来
        it = q8.items()[0]
        check("⭐⭐ 提前超过上限 -> **不动**（退回正常退避，别把服务端当靶子）",
              q8.retry_now() == 0 and it["wake_retries"] == upload.WAKE_RETRY_MAX
              and it["next_at"] > _now[0], str(it))

        # ③ `add_files`：并进**同一条**（`enqueue` 会按 key 拒绝）
        _root6 = tmp / "cl6"
        q9 = upload.UploadQueue(lambda *a: True, root=_root6, now=lambda: _now[0])
        q9.enqueue([f2[0]], "sess-G")
        n3 = q9.add_files([f2[1], f2[2]], "sess-G")
        # ⚠️ 变异验证：`add_files` 改用 `enqueue` -> 返回 0、文件永远排不进去 -> 这条红。
        check("⭐⭐ `add_files` 并进**同一条**（后台转出的 opus 补进崩溃那条）",
              n3 == 2 and q9.pending == 1 and len(q9.items()[0]["files"]) == 3,
              str(q9.items()))
        check("⚠️ 同一路径再来一次 -> 0（去重）", q9.add_files([f2[1]], "sess-G") == 0)
        check("⭐ 没有条目时 `add_files` 自己开一条",
              q9.add_files([f2[0]], "sess-H") == 1 and q9.pending == 2,
              str(q9.pending))

        print("\n--- T17 ⭐⭐ 3.10：崩溃残段**先转码再排**（不再原样传 wav）---")
        _rs2 = tmp / "sess2"
        _rs2.mkdir()
        _stem2 = "2026-10-14_160000_CRASHED2"
        for _i in (1, 2):
            (_rs2 / f"{_stem2}.{_i:03d}.wav").write_bytes(b"RIFF" + b"\0" * 64)
        (_rs2 / f"{_stem2}{upload.PENDING_TAIL}").write_text(json.dumps(
            {"stem": _stem2, "started": "x",
             "steps": {"transcode": "pending", "report": "pending",
                       "upload": "pending"}}), encoding="utf-8")
        _rroot2 = tmp / "cl7"
        _tc: list = []

        def _fake_tc(wav, opus=None, *, delete=True):
            _tc.append(pathlib.Path(wav).name)
            o = pathlib.Path(wav).with_suffix(".opus")
            o.write_bytes(b"OggS" + b"\0" * 8 + b"OpusHead" + b"\0" * 8)
            if delete:
                pathlib.Path(wav).unlink(missing_ok=True)
            return o, ""

        # ⚠️ 判据是在 check.py 里跑的，而它设 `CLASSLIVE_UPLOAD=0` —— 这一节要的是
        #    "上传**开着**"那半（残段补传是上传开着才做的事）。
        _saved_up2 = os.environ.get("CLASSLIVE_UPLOAD")
        os.environ["CLASSLIVE_UPLOAD"] = "1"
        try:
            got2 = upload.recover_pending(_rs2, lambda local, key: False,  # 永不成功 -> 条目留着
                                          transcode=_fake_tc, root=_rroot2,
                                          now=lambda: _now[0])
        finally:
            if _saved_up2 is None:
                os.environ.pop("CLASSLIVE_UPLOAD", None)
            else:
                os.environ["CLASSLIVE_UPLOAD"] = _saved_up2
        # ⚠️ 变异验证：`recover_pending` 里不看 `transcode`（把残段直接排上）-> 这条红。
        check("⭐⭐ 有残段 -> 交给后台转码（`jobs` 记账；wav 先不排）",
              got2["found"] == 1 and got2["jobs"] == 1, str(got2))
        _deadline = time.time() + 5.0
        while time.time() < _deadline and (_rs2 / f"{_stem2}{upload.PENDING_TAIL}").exists():
            time.sleep(0.05)
        q10 = upload.UploadQueue(lambda *a: True, root=_rroot2, now=lambda: _now[0])
        _names2 = [pathlib.Path(f).name for i in q10.items() for f in i["files"]]
        check("⭐⭐ 转完的 opus 进了**同一条**条目（不是新开一条）",
              q10.pending == 1 and sum(n.endswith(".opus") for n in _names2) == 2,
              str(_names2))
        check("⭐ 两个残段都过了一遍转码", len(_tc) == 2, str(_tc))
        # ⚠️ 变异验证：把后台线程里那次改名删掉 -> 这条红（残局永远不结案）。
        check("⭐⭐ 后台活干完才**改名**标记（半路被杀 -> 下次重来）",
              not (_rs2 / f"{_stem2}{upload.PENDING_TAIL}").exists()
              and (_rs2 / f"{_stem2}.recovered.json").exists(),
              str([p.name for p in _rs2.iterdir()]))
        _meta2 = json.loads((_rs2 / f"{_stem2}.meta.json").read_text(encoding="utf-8"))
        check("⭐ meta 带上 journal 的 `steps`（收件端看得出这节欠过什么）",
              _meta2.get("steps", {}).get("transcode") == "pending", str(_meta2))

        print("\n--- T18 ⭐⭐ 上传关着：不排、也不改名（留着残局，等开着再补）---")
        _rs3 = tmp / "sess3"
        _rs3.mkdir()
        _stem3 = "2026-10-15_160000_CRASHED3"
        (_rs3 / f"{_stem3}.001.opus").write_bytes(
            b"OggS" + b"\0" * 8 + b"OpusHead" + b"\0" * 8)
        (_rs3 / f"{_stem3}{upload.PENDING_TAIL}").write_text("{}", encoding="utf-8")
        _saved_env = os.environ.get("CLASSLIVE_UPLOAD")
        os.environ["CLASSLIVE_UPLOAD"] = "0"
        try:
            _r3 = upload.recover_pending(_rs3, upload.make_local_send(str(tmp / "srv4")),
                                         root=tmp / "cl8", now=lambda: _now[0])
            # ⚠️ 变异验证：把 `if not upload_enabled(): return` 删掉 -> 这条红
            #    （一次离线启动会把残局"吃掉"却不上传）。
            check("⭐⭐ 关着 -> 一条都不排，标记**还留着**",
                  _r3["queued"] == 0
                  and (_rs3 / f"{_stem3}{upload.PENDING_TAIL}").exists(), str(_r3))
            check("⭐ `upload_enabled()` 说的是同一件事（唯一定义点）",
                  upload.upload_enabled() is False)
        finally:
            if _saved_env is None:
                os.environ.pop("CLASSLIVE_UPLOAD", None)
            else:
                os.environ["CLASSLIVE_UPLOAD"] = _saved_env
        _saved3 = os.environ.get("CLASSLIVE_UPLOAD")
        os.environ["CLASSLIVE_UPLOAD"] = "1"
        _on1 = upload.upload_enabled()
        os.environ.pop("CLASSLIVE_UPLOAD", None)          # 不设 -> 默认开
        _on2 = upload.upload_enabled()
        if _saved3 is not None:
            os.environ["CLASSLIVE_UPLOAD"] = _saved3
        check("⭐ `upload_enabled()`：`1` 与**不设**都算开（默认就是开）",
              _on1 is True and _on2 is True)

        print("\n--- T19 ⭐⭐ 2026-10-18 审查 D6/D7/D16 ---")
        # D16: 条目已按 key 立好（只排了 meta），随后补上的 opus 曾被 key 去重丢掉
        _rs4 = tmp / "sess4"
        _rs4.mkdir()
        _stem4 = "2026-10-18_160000_CRASHED4"
        (_rs4 / f"{_stem4}.001.opus").write_bytes(b"OggS" + b"\0" * 8 + b"OpusHead" + b"\0" * 8)
        _pre_meta = _rs4 / f"{_stem4}.meta.json"
        _pre_meta.write_text("{}", encoding="utf-8")
        _root7 = tmp / "cl9"
        _qp = upload.UploadQueue(lambda *a: True, root=_root7, now=lambda: _now[0])
        _qp.enqueue([_pre_meta], _stem4)                 # 上一次已把这条立起来（只排了 meta）
        (_rs4 / f"{_stem4}{upload.PENDING_TAIL}").write_text(
            json.dumps({"steps": {"transcode": "pending"}}), encoding="utf-8")
        _su4 = os.environ.get("CLASSLIVE_UPLOAD")
        os.environ["CLASSLIVE_UPLOAD"] = "1"
        try:
            _r4 = upload.recover_pending(_rs4, lambda l, k: False, root=_root7,
                                         now=lambda: _now[0])
        finally:
            if _su4 is None:
                os.environ.pop("CLASSLIVE_UPLOAD", None)
            else:
                os.environ["CLASSLIVE_UPLOAD"] = _su4
        _q4 = upload.UploadQueue(lambda *a: True, root=_root7, now=lambda: _now[0])
        _names4 = [pathlib.Path(f).name for i in _q4.items() for f in i["files"]]
        # ⚠️ 变异验证：`recover_pending` 里 `q.add_files` 改回 `q.enqueue` -> 这条红
        #    （key 命中 -> 返回 0，那段 opus 永远排不进去）。
        check("⭐⭐ D16 条目已在也把新 opus **并进同一条**（key 去重不许丢文件）",
              _q4.pending == 1 and f"{_stem4}.001.opus" in _names4 and _r4["queued"] >= 1,
              f"queued={_r4['queued']} pending={_q4.pending} names={_names4}")

        # D6: 传进来的那**一份**实例必须就是被收进的实例（跨线程同一把锁）
        _rs5 = tmp / "sess5"
        _rs5.mkdir()
        _stem5 = "2026-10-18_160000_CRASHED5"
        (_rs5 / f"{_stem5}.001.opus").write_bytes(b"OggS" + b"\0" * 8 + b"OpusHead" + b"\0" * 8)
        (_rs5 / f"{_stem5}{upload.PENDING_TAIL}").write_text("{}", encoding="utf-8")
        _root8 = tmp / "cl10"
        _shared = upload.UploadQueue(lambda *a: True, root=_root8, now=lambda: _now[0])
        _su5 = os.environ.get("CLASSLIVE_UPLOAD")
        os.environ["CLASSLIVE_UPLOAD"] = "1"
        try:
            # ⚠️ 注意 `send=None`：给了 `queue` 就不该再另起一个发送器
            _r5 = upload.recover_pending(_rs5, None, queue=_shared, now=lambda: _now[0])
        finally:
            if _su5 is None:
                os.environ.pop("CLASSLIVE_UPLOAD", None)
            else:
                os.environ["CLASSLIVE_UPLOAD"] = _su5
        # ⚠️ 变异验证：`recover_pending` 里不看 `queue`（自己 new 一份）-> 这条红
        #    （主线程那份共享实例 pending 还是 0 —— 两个实例各写同一文件 = 锁形同虚设）。
        check("⭐⭐ D6 收进的是**传进来的那一份实例**（D6：跨线程同一把锁）",
              _r5["found"] == 1 and _shared.pending == 1, f"{_r5} shared={_shared.pending}")

        # D7: 两条 pump 线程并发时，同一个对象只许发一次（原子占位）
        _root9 = tmp / "cl11"
        _d7f = tmp / "d7.json"
        _d7f.write_text("{}", encoding="utf-8")
        _calls7: list = []
        _b7 = threading.Barrier(2, timeout=0.6)

        def _slow7(local, key):
            _calls7.append(key)
            try:
                _b7.wait()                       # 逼两条线程在"发送中"相遇
            except Exception:                    # noqa: BLE001
                pass
            return True

        _q11 = upload.UploadQueue(_slow7, root=_root9, now=lambda: _now[0])
        _q11.enqueue([_d7f], "sess-D7")
        _go7 = threading.Barrier(3)

        def _run7():
            _go7.wait()
            _q11.pump()

        _t1 = threading.Thread(target=_run7)
        _t2 = threading.Thread(target=_run7)
        _t1.start(); _t2.start(); _go7.wait()
        _t1.join(timeout=5); _t2.join(timeout=5)
        _key7 = f"{upload.session_key('sess-D7')}/d7.json"
        # ⚠️ 变异验证：把 `pump` 里那段 `_inflight` 占位删掉 -> 这条红（两条线程各发一遍）。
        check("⭐⭐ D7 并发两条 pump：同一个对象**只发一次**（先原子占位再发）",
              _calls7.count(_key7) == 1, f"calls={_calls7}")

        bad = [n for n, ok, _ in RESULTS if not ok]
        print("\n" + "=" * 60)
        print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
        for n in bad:
            print(f"  ❌ {n}")
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
