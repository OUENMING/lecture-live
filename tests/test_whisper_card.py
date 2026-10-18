#!/usr/bin/env python3
"""`whisper_card` 的判据 —— **假 HOME + 假废纸篓**，绝不碰真 `~/models`、真 `~/.Trash`、真 `~/.classlive`。

    ClassLive.app/Contents/MacOS/python tests/test_whisper_card.py

覆盖：
  ① 出卡的四条件（逐条否掉 + 全中才出）② 删除的安全判据（只碰注册表那一条；
     符号链接 / 内容不对 / 传错路径一律拒绝；搬不动时不假装问过）③ 计数与「答过就永不再问」。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import models                                                  # noqa: E402
import paths as P                                              # noqa: E402
import whisper_card as W                                       # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def _put_model() -> pathlib.Path:
    """在**假 HOME**（进程 env 已换）的注册表路径上放一份极小的模型目录。

    ⚠️ 只认 `models.WHISPER.path` 展开出来的那一条 —— 全测试不 glob、不手拼路径。
    """
    reg = pathlib.Path(os.path.expanduser(models.WHISPER.path))
    reg.mkdir(parents=True, exist_ok=True)
    (reg / W.MARKER).write_bytes(b"x" * 4096)
    return reg


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl_whisper_card_"))

    # ── ① 出卡条件 ───────────────────────────────────────────────
    print("\n--- ① 出卡条件：四个全中才出（逐条否掉）---")
    home = tmp / "home-a"
    home.mkdir(parents=True)
    state = tmp / "state-a"
    with mock.patch.dict(os.environ, {"HOME": str(home)}):
        reg = _put_model()
        check("前置：注册表路径展开到**假 HOME** 下（不碰真 ~/models）",
              str(reg).startswith(str(home)) and reg.name.endswith("whisper-turbo"),
              str(reg))
        check("前置：默认口径是「云端开」（从没写过配置）",
              W._cloud_on(root=state) is True)
        check("① 模型在、云端开，但一节课都还没上过 -> 不出",
              W.should_offer(root=state) is False)
        for _ in range(W.MIN_CLASSES - 1):
            W.note_cloud_class(root=state)
        check("② 差一节（2 < 3）-> 不出", W.should_offer(root=state) is False)
        W.note_cloud_class(root=state)
        check("⭐⭐ 三节云端定稿的课 + 模型在 + 云端开 -> **出**",
              W.should_offer(root=state) is True)
        check("⭐ 体积按盘上真实算（4096 字节那档）",
              W.dir_size_bytes(reg) == 4096 and W.size_text(4096) == "1 MB",
              f"{W.dir_size_bytes(reg)} / {W.size_text(4096)}")
        check("⭐⭐ 云端转写关掉 -> 不出（关着时它还有用，不能劝人删）",
              W.should_offer(root=state, cloud_on=False) is False)
        check("⭐⭐ 模型目录不在 -> 不出",
              W.should_offer(root=state, present=lambda p: False) is False)
        check("前置：answer('keep') 写得进去", W.answer("keep", root=state) is True)
        check("⭐⭐ 答过「保留」-> 永不再问（哪怕条件还全中）",
              W.should_offer(root=state) is False)
        check("⭐ 而且回答没把计数清掉", W.cloud_classes(root=state) == W.MIN_CLASSES,
              str(W.cloud_classes(root=state)))

    # ── ② 删除的安全判据 ─────────────────────────────────────────
    print("\n--- ② 删除：只碰注册表那一条；判据不过就什么都不做 ---")
    home = tmp / "home-b"
    home.mkdir(parents=True)
    state = tmp / "state-b"
    with mock.patch.dict(os.environ, {"HOME": str(home)}):
        reg = _put_model()
        for _ in range(W.MIN_CLASSES):        # 条件本来就全中 —— 之后才答的「删」
            W.note_cloud_class(root=state)
        check("前置：答之前四条件全中（否则「删完不再问」是空转）",
              W.should_offer(root=state) is True)
        calls: list = []

        def trash_ok(p):
            calls.append(str(p))
            return True, ""

        r = W.remove(W.whisper_path(), trash=trash_ok, root=state)
        check("⭐⭐ 删成：假废纸篓只被调**一次**，收到的就是注册表那一条（realpath）",
              r["ok"] and calls == [os.path.realpath(str(reg))], f"{r} {calls}")
        check("⭐⭐ 成功之后才记「删」-> 不再问",
              W.answered(root=state) == "trash"
              and W.should_offer(root=state, cloud_on=True, present=lambda p: True) is False)

        # 符号链接：注册表位置上是个链接（背后另有目录）-> 拒绝
        shutil.rmtree(reg)
        real = home / "elsewhere" / "whisper-real"
        (real / "x").mkdir(parents=True)
        (real / W.MARKER).write_bytes(b"x")
        reg.symlink_to(real)
        calls.clear()
        r = W.remove(W.whisper_path(), trash=trash_ok, root=state)
        check("⭐⭐ 注册表位置是**符号链接** -> 拒绝，废纸篓一次都没被调",
              (not r["ok"]) and r["refused"] and calls == [], f"{r} {calls}")

        # 内容不对：目录在、但没有标志文件
        reg.unlink()
        reg.mkdir()
        (reg / "README").write_text("x", encoding="utf-8")
        calls.clear()
        r = W.remove(W.whisper_path(), trash=trash_ok, root=state)
        check("⭐⭐ 目录里没有 `turbo-encoder.int8.onnx` -> 拒绝（防「路径对了、内容不是它」）",
              (not r["ok"]) and r["refused"] and W.MARKER in r["error"] and calls == [],
              f"{r} {calls}")

        # 传进来的是别的路径 -> 拒绝（**就算内容看着对**，也只认注册表那一条）
        other = home / "not-the-model"
        other.mkdir()
        (other / W.MARKER).write_bytes(b"x")           # 内容齐全 —— 只有「路径不对」能拦它
        calls.clear()
        r = W.remove(str(other), trash=trash_ok, root=state)
        check("⭐⭐ 传进来的是别的路径（内容还对）-> 拒绝，废纸篓一次都没被调",
              (not r["ok"]) and r["refused"] and calls == [], f"{r} {calls}")

        # 废纸篓搬不动 -> 不成功、也**不**假记「删过」
        shutil.rmtree(reg)
        _put_model()
        state2 = tmp / "state-b2"
        calls.clear()
        r = W.remove(W.whisper_path(),
                     trash=lambda p: (calls.append(str(p)), (False, "权限不足"))[1],
                     root=state2)
        check("⭐ 废纸篓搬不动 -> 报失败、且**不**记「删过」（下次还能再问）",
              (not r["ok"]) and not r["refused"] and calls
              and W.answered(root=state2) == "", f"{r} {calls}")

    # ── ③ 计数与持久化 ───────────────────────────────────────────
    print("\n--- ③ 计数 / 回答的持久化（`~/.classlive/whisper-card.json`）---")
    state = tmp / "state-c"
    check("没写过 -> 0", W.cloud_classes(root=state) == 0)
    check("⭐ note 三次 -> 1 / 2 / 3，读完还是 3",
          [W.note_cloud_class(root=state) for _ in range(3)] == [1, 2, 3]
          and W.cloud_classes(root=state) == 3, str(W.cloud_classes(root=state)))
    check("⭐ 两个回答都记得住",
          W.answer("keep", root=state) is True and W.answered(root=state) == "keep"
          and W.cloud_classes(root=state) == 3)
    check("⚠️ 不认识的回答 -> 直接抛（别把拼错的字符串写下去）",
          _raises(lambda: W.answer("nope", root=state)))
    # 坏文件：读不出来当 0（并出声），下一次写盘换成好的
    p = P.whisper_card(root=state)
    p.write_text("{oops", encoding="utf-8")
    check("⚠️ 状态文件被手改坏 -> 按「没答过 / 0 节」处理（不抛）",
          W.cloud_classes(root=state) == 0 and W.answered(root=state) == "",
          f"{W.cloud_classes(root=state)} / {W.answered(root=state)!r}")
    check("⭐ 下一次写盘把它修好", W.note_cloud_class(root=state) == 1
          and W.cloud_classes(root=state) == 1)

    # ── ④ 删掉之后 doctor/update 不再喊着重下 ────────────────────
    print("\n--- ④ 删掉之后 update 不会再喊「下载模型」 ---")
    import draft_engine
    import doctor
    import update
    whisper_p = os.path.expanduser(models.WHISPER.path)
    check("⚠️ 前提：Whisper 在注册表里是**可选**（required=False）——这就是不该喊的原因",
          models.WHISPER.required is False)
    with mock.patch.object(doctor, "model_present", lambda p: p != whisper_p), \
            mock.patch.object(draft_engine, "want_model", lambda: False):
        keys = [s["key"] for s in update.pending_steps()]
    check("⭐⭐ 只缺可选的 Whisper -> 更新卡片**不列**「下载模型」",
          "models" not in keys, str(keys))
    check("⭐ 想装回来那条路是真的（卡里那句「cl doctor」指向它）",
          "fetch_model.py" in models.WHISPER.cmd and models.WHISPER.cmd.rstrip().endswith("whisper"),
          models.WHISPER.cmd)

    shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{'=' * 60}")
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


def _raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    except Exception:                                     # noqa: BLE001
        return False
    return False


if __name__ == "__main__":
    sys.exit(main())
