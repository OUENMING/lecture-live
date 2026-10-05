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
import pathlib
import shutil
import sys
import tempfile
import threading

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
                            (f"{_stem}.meta.json", "meta.json")):
            _got = upload._remote_name(pathlib.Path(_src))
            check(f"⭐ `{_want}`", _got == _want, _got)
            check(f"⚠️ `{_want}` 里**不含 stem**（不泄漏课号/时间戳）",
                  _stem not in _got, _got)

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
        got = upload.recover_pending(
            rsess, upload.make_local_send(str(tmp / "srv2")), root=rroot,
            now=lambda: _now[0])
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
