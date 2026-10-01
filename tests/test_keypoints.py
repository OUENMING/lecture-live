#!/usr/bin/env python3
"""`keypoints.py`（Jev 重点句）的判据 —— **全部离线**，不发任何请求。

    ClassLive.app/Contents/MacOS/python tests/test_keypoints.py

## 这个文件钉住了什么

1. ⭐⭐ **`parse_answers` 按 `S<i>` 编号回填，不按返回顺序** —— 少一条就整体错位
   一格，而错位在「取 top-k」下**完全看不出来**（还是 k 条，只是全偏了）。
2. ⭐ **`top_k` 把「没答」(`None`) 排除**，不当 0 —— 那是两件事；并列按句子下标
   排（否则结果不可复现）。
3. ⭐ CLI 的 `--top` / `--unit` **写在末尾不许崩**（原来 `IndexError`，
   而 `--unit` 那行因 `and` 短路**一定会**求值右侧 → 末尾出现它必崩）。
4. ⭐ 网络层故障（`URLError`）**要说人话**，不是裸 traceback。

⚠️ 这个文件是 2026-09-29 补的 —— 在此之前 `keypoints.py` 一个判据都没有
   （全仓没有 `test_keypoints.py`），而它是唯一会**联网**的模块。
   `ask` 是注入的依赖，所以这些判据**不用网络也不用 token**。
"""
from __future__ import annotations

import io
import pathlib
import sys
import urllib.error

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import keypoints as kp                                                 # noqa: E402

CASES: list[tuple[str, object]] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---------------------------------------------------------------- 解析
@case("⭐⭐ 按 `S<i>` 编号回填，不按返回顺序（错位在 top-k 下看不出来）")
def t_parse_backfills_by_index():
    # ⚠️ 故意**乱序**返回 + 少一条 —— 按返回顺序回填会把 0.9 装到 S1 上
    payload = {"answers": {"S3": {"noul": 0.3}, "S1": {"noul": 0.9}}}
    got = kp.parse_answers(payload, 3)
    assert got == [0.9, None, 0.3], f"该按编号回填、缺的补 None，得到 {got!r}"


@case("⚠️ 越界 / 形状不对的键一律忽略，不许把表撑长")
def t_parse_ignores_junk():
    payload = {"answers": {"S9": {"noul": 0.9}, "X1": {"noul": 0.9},
                           "S1": {"noul": "不是数字"}, "S2": {"noul": 0.5}}}
    got = kp.parse_answers(payload, 2)
    assert got == [None, 0.5], f"得到 {got!r}"
    assert kp.parse_answers(None, 2) == [None, None]
    assert kp.parse_answers({}, 0) == []


# ---------------------------------------------------------------- top-k
@case("⭐ `None`（没拿到）**排除**，不当 0 —— 「没答」和「答了 0」是两件事")
def t_top_k_excludes_none():
    got = kp.top_k([0.9, None, 0.1], ["a", "b", "c"], k=3)
    assert [i for _, i, _ in got] == [0, 2], f"None 该被排除，得到 {got!r}"


@case("⭐ 并列按下标排 —— 否则同一份输入两次跑出的顺序不同")
def t_top_k_ties_are_reproducible():
    rows = [(0.5, "a"), (0.5, "b"), (0.5, "c")]
    got = [i for _, i, _ in kp.top_k([0.5] * 3, ["a", "b", "c"], k=3)]
    assert got == [0, 1, 2], f"得到 {got!r}"
    assert kp.top_k([0.1], ["a"], k=0) == [], "k=0 该给空表"


@case("`k` 超过可用条数时给多少算多少（不补 None）")
def t_top_k_short():
    got = kp.top_k([0.9, None], ["a", "b"], k=10)
    assert [i for _, i, _ in got] == [0], f"得到 {got!r}"


# ---------------------------------------------------------------- 问题/状态
@case("`build_questions` 一句一个判据；`build_state` 只放这一段")
def t_builders():
    qs = kp.build_questions(3)
    assert sorted(qs) == ["S1", "S2", "S3"], f"得到 {sorted(qs)}"
    assert all(v["type"] == "noul" for v in qs.values())
    st = kp.build_state(["hello", "world"])
    assert "S1. hello" in st and "S2. world" in st, st[:120]


# ---------------------------------------------------------------- CLI
@case("⭐⭐ `--top` / `--unit` 写在**末尾**不许崩（原来 `IndexError`）")
def t_cli_missing_value():
    """⚠️ `--unit` 那行原来因 `and` 的短路顺序**一定**会求值右侧索引 →
    命令行末尾只要出现 `--unit` 就必崩。这是本文件要钉住的核心形状。"""
    for argv in (["nope.md", "--unit"], ["nope.md", "--top"],
                 ["nope.md", "--unit", "thought", "--top"]):
        try:
            kp.main(argv)
        except IndexError as e:
            raise AssertionError(f"{argv} 抛了 IndexError: {e}")
        except Exception:                                      # noqa: BLE001
            pass                    # 别的失败没关系（文件不存在等），**只许不是 IndexError**


@case("⚠️ `--top 不是数字` -> 退回 2，不许把 traceback 甩给用户")
def t_cli_bad_top():
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = kp.main(["nope.md", "--top", "abc"])
    assert rc == 2, f"该返回 2，得到 {rc}"
    assert "--top" in buf.getvalue(), f"该说清哪个参数不对：{buf.getvalue()!r}"


@case("没参数 -> 打用法、返回 2")
def t_cli_no_args():
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = kp.main([])
    assert rc == 2 and "用法" in buf.getvalue() or "keypoints" in buf.getvalue()


# ---------------------------------------------------------------- 网络错误
@case("⭐ 网络层故障（`URLError`）-> 说人话的 RuntimeError，不是裸 traceback")
def t_urlerror_is_wrapped():
    """⚠️ `HTTPError` **是** `URLError` 的子类，反过来不成立 ——
    只接 `HTTPError` 时，断网 / DNS 失败 / 连接被拒全部裸崩。"""
    import urllib.request
    orig = urllib.request.urlopen

    def boom(*a, **kw):
        raise urllib.error.URLError("Name or service not known")
    urllib.request.urlopen = boom
    try:
        kp.ask_commandcode("state", {}, token="x")
    except RuntimeError as e:
        assert "连不上" in str(e), f"该说清是连不上：{e}"
    except urllib.error.URLError as e:
        raise AssertionError(f"URLError 逃出去了（没被包成 RuntimeError）：{e}")
    finally:
        urllib.request.urlopen = orig


@case("⭐ HTTP 错误要把**响应体**带出来（错误信息全在 body 里）")
def t_http_error_keeps_body():
    import urllib.error as _ue
    import urllib.request
    orig = urllib.request.urlopen

    class _E(_ue.HTTPError):
        def __init__(self):
            super().__init__("u", 400, "Bad Request", {}, None)

        def read(self):
            return b"at most 20 questions per call"
    urllib.request.urlopen = lambda *a, **kw: (_ for _ in ()).throw(_E())
    try:
        kp.ask_commandcode("state", {}, token="x")
    except RuntimeError as e:
        assert "at most 20 questions" in str(e), f"body 丢了：{e}"
        assert "400" in str(e), f"状态码丢了：{e}"
    finally:
        urllib.request.urlopen = orig


# ---------------------------------------------------------------- 没配 token
@case("⭐⭐ 没配 token -> `pick()` 返回空表，**一个请求都不发**")
def t_pick_without_token():
    """⚠️ **必须 patch `kp.token`**，不能只传 `token_value=""` ——
    空串的语义是「去查」，而查的是 `~/.classlive/jev-token`；**在作者本机上
    那个文件是配好的**，于是"没配"根本没被测到（这条判据第一版就是这么错的，
    它自己当场红了）。判据不能依赖跑它的那台机器上配没配。"""
    import urllib.request
    orig_url, orig_tok = urllib.request.urlopen, kp.token
    calls = []

    def boom(*a, **kw):
        calls.append(1)
        raise AssertionError("没配 token 却发了请求")
    urllib.request.urlopen = boom
    kp.token = lambda **kw: ""
    try:
        assert kp.token() == "", "patch 没生效"
        assert kp.pick(["hello"]) == [], "没配 token 该给空表"
        assert not calls, "没配 token 却发了请求"
    finally:
        urllib.request.urlopen = orig_url
        kp.token = orig_tok


@case("⭐⭐ `rebuild_note.py` 必须接 `keypoints_fn` —— 走假写入器真跑一遍 main()")
def t_rebuild_wires_keypoints():
    # 动机（2026-10-01 作者实测）：补生成那条路漏了这一行接线 →
    # 补出来的笔记**少一整节**「🎯 这节课最值得记的几句」，而且不报任何错。
    import tempfile

    import rebuild_note as rb

    _real_parse = rb.ObsidianWriter._parse          # 真解析器（数句靠它）

    class _FakeW:
        _parse = _real_parse
        seen: dict = {}

        def __init__(self, **kw):
            _FakeW.seen = kw

        def close(self):
            return "（假的，不落盘）"

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        sess = td / "2026-01-02_000000_X.md"
        sess.write_text(
            "# X · 2026-01-02 · 实时会话日志\n\n"
            "> [!abstract] 10:00:00\n> **EN**: hello world\n> **ZH**: 你好\n"
            "> **ASR**: hello world\n", encoding="utf-8")
        orig_w, orig_argv = rb.ObsidianWriter, sys.argv
        rb.ObsidianWriter = _FakeW
        sys.argv = ["rebuild_note.py", str(sess), "X",
                    "--vault", str(td / "v"), "--no-polish"]
        try:
            rc = rb.main()
        finally:
            rb.ObsidianWriter = orig_w
            sys.argv = orig_argv
        assert rc == 0, f"（假写入器那一趟不该失败 rc={rc}）"
        assert "keypoints_fn" in _FakeW.seen, (
            "rebuild 没把 `keypoints_fn` 传给写入器 —— 补出来的笔记会少一整节"
            "「🎯 这节课最值得记的几句」，而且**不报任何错**")
        assert callable(_FakeW.seen.get("keypoints_fn")), _FakeW.seen.get("keypoints_fn")


def main_() -> int:
    print("=" * 60)
    fail: list[str] = []
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as e:
            print(f"  ❌ {name}\n      {str(e)[:220]}")
            fail.append(name)
        except Exception as e:                                 # noqa: BLE001
            print(f"  ❌ {name}\n      {type(e).__name__}: {str(e)[:200]}")
            fail.append(name)
        else:
            print(f"  ✅ {name}")
    print("=" * 60)
    print(f"{len(CASES) - len(fail)}/{len(CASES)} 通过")
    for n in fail:
        print(f"  ❌ {n}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main_())
