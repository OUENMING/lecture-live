#!/usr/bin/env python3
"""「旧的本地 Whisper 还能清掉」那张一次性卡 —— 判据、文案、以及**唯一**的删除动作。

云端正稿（Soniox，兜底 Nemotron+Groq，最后 Parakeet）接替之后，
`~/models/sherpa-onnx-whisper-turbo`（`models.WHISPER`，解开后约 989 MB）
**只在云端转写关掉时**才会用到。这张卡问一次：「移到废纸篓」还是「保留」；
两个答案都记下来，问过就不再问。

## 三条安全纪律（动任何东西之前）

1. **身份只从注册表来**（`models.WHISPER.path`）—— 不 glob、不按字符串拼路径。
2. 真删之前再核一遍：realpath 与注册表一致、是目录、**不是符号链接**、
   里面有 `turbo-encoder.int8.onnx`。任何一条不过 → **什么都不做并出声**。
3. 搬走只用 `NSFileManager.trashItemAtURL`（Finder 的「移到废纸篓」，**可还原**），
   不是 `rm` / `shutil.rmtree`，也不自己往 `~/.Trash` 里搬。

## 记在哪、数什么

`~/.classlive/whisper-card.json`：`{"cloud_classes": N, "answered": ""|"trash"|"keep"}`。

`cloud_classes` 由 `main.py` 在**每节课结束**时 +1（那节课至少有一段定稿来自云端：
`FinalResult.source in final_asr.CLOUD_SOURCES`）。刻意**不**复用现成的每课记录 ——
`sessions/` 的格式是三方契约（抬头不许加字段）、测试报告只在测试模式写、
`live.jsonl` 要开环境变量，都不总是存在。这个计数器是**本版本才有的**，
所以「≥3 节」天然就是「都在这个版本上完成的课」。
"""
from __future__ import annotations

import os
import pathlib

import paths

#: 数够几节「云端定稿的课」才问。作者口径：≥3（连续几节课都没再见它上过场）。
MIN_CLASSES = 3
#: 这份模型的**身份文件** —— 删之前必须见到它（防"路径对了但内容不是它"）。
MARKER = "turbo-encoder.int8.onnx"

_TRASH, _KEEP = "trash", "keep"


# ---------------------------------------------------------------- 状态
def _read(root=None) -> dict:
    """读状态文件。**坏文件当空处理，但出声**。

    ⚠️ 与 `store` 那条「读不懂不当空的」差在**这是什么数据**：它是偏好 + 计数器，
    不是用户唯一那份数据（丢了最坏是早问一次），所以这里不倒向"拒绝读"；
    下一次写盘会用 `save_json` 把它换成一份好的。
    """
    import store
    try:
        return store.load_json(paths.whisper_card(root=root), {})
    except store.StoreError as e:
        print(f"⚠ Whisper 卡的状态读不出来（{str(e).splitlines()[0]}）"
              f"—— 这次按没答过处理", flush=True)
        return {}


def _write(obj: dict, root=None) -> bool:
    import store
    try:
        store.save_json(paths.whisper_card(root=root), obj)
        return True
    except OSError as e:
        print(f"⚠ Whisper 卡的状态没存下来（{type(e).__name__}: {e}）—— 下次还会问",
              flush=True)
        return False


def cloud_classes(root=None) -> int:
    """本版本上「定稿用过云端」的已上完的课数。"""
    v = _read(root).get("cloud_classes")
    return int(v) if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def note_cloud_class(root=None) -> int:
    """记一节「定稿用过云端」的课（`main.py` 在下课收尾时调）。返回新计数。"""
    n = cloud_classes(root=root) + 1
    _write({**_read(root), "cloud_classes": n}, root=root)
    return n


def answered(root=None) -> str:
    """用户答过什么：`""`（没答过）/ `"trash"` / `"keep"`。"""
    v = _read(root).get("answered")
    return v if v in (_TRASH, _KEEP) else ""


def answer(choice: str, root=None) -> bool:
    """记下回答（`"trash"` / `"keep"`）。记不上返回 False（调用方要把话说出来）。"""
    if choice not in (_TRASH, _KEEP):
        raise ValueError(f"不认识的回答：{choice!r}")
    return _write({**_read(root), "answered": choice}, root=root)


# ---------------------------------------------------------------- 判据
def whisper_path() -> str:
    """注册表里那份 Whisper 的规范路径 —— **身份的唯一来源**。"""
    import models
    return models.WHISPER.path


def _present(path: str) -> bool:
    import doctor
    return doctor.model_present(path)      # 空目录 / 空文件不算「在」（那份判据只有一份）


def _cloud_on(root=None) -> bool:
    """云端定稿转写开着吗（`CLASSLIVE_CLOUD_ASR=0` 时 `read_config` 自己答「关」）。"""
    try:
        import final_asr
        return bool(final_asr.read_config(root=root)["enabled"])
    except Exception:                                      # noqa: BLE001
        return False                                       # 读不出来 = 不打扰（少问一次）


def should_offer(*, root=None, path=None, present=None, cloud_on=None,
                 min_classes: int = MIN_CLASSES) -> bool:
    """该不该出那张卡。**四个条件全中才出**（判据全可注入，方便逐条钉）：

      ① 问过没？`answered` 非空 → **永不再问**（两个答案都算）；
      ② 这个版本上已上完 ≥ `min_classes` 节「定稿用过云端」的课；
      ③ Whisper 目录**还在**（`doctor.model_present` 口径：非空才算在）；
      ④ 云端定稿转写**开着**（关着的话它还有用，不能劝人删）。
    """
    if answered(root=root):
        return False
    if cloud_classes(root=root) < min_classes:
        return False
    p = path or whisper_path()
    if not (present or _present)(p):
        return False
    if cloud_on is None:
        cloud_on = _cloud_on(root=root)
    return bool(cloud_on)


# ---------------------------------------------------------------- 体积 / 文案
def dir_size_bytes(path) -> int | None:
    """目录里所有真文件的大小和（不跟随符号链接）；读不出来 `None`（**不当 0**）。"""
    p = pathlib.Path(os.path.expanduser(str(path)))
    total = 0
    try:
        for f in p.rglob("*"):
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
    except OSError:
        return None
    return total


def size_text(nbytes) -> str:
    """体积说人话（十进制，与 Finder / `doctor` 报 MB 的口径一致）。"""
    if nbytes is None:
        return ""
    if nbytes >= 1e9:
        return f"{nbytes / 1e9:.1f} GB"
    return f"{max(1, round(nbytes / 1e6))} MB"


def size_text_now() -> str:
    """盘上那份的**真实体积**；读不出来退回注册表写的下载体积（别显示空白）。"""
    n = dir_size_bytes(whisper_path())
    if n is None or n <= 0:
        import models
        return models.WHISPER.size
    return size_text(n)


def card_texts(size_now: str) -> tuple:
    """卡片三行：`(标题, 正文, 小字)`。**纯函数**（文案单独钉得住）。"""
    body = (f"本地 Whisper（{size_now}，以前的定稿模型）只在云端转写关掉时才会用到 ——"
            f"云端转写（默认开）更准。")
    note = ("清掉只是省空间；想要回来：跑 `cl doctor`，按它给的命令重下"
            "（约 538 MB）。")
    return ("旧的本地 Whisper 还能清掉", body, note)


# ---------------------------------------------------------------- 删除
def _trash(path) -> tuple:
    """`NSFileManager.trashItemAtURL` —— Finder 的「移到废纸篓」，**可还原**。

    ⚠️ 不用 `rm` / `shutil.rmtree`（不可恢复）；也不自己往 `~/.Trash` 搬
       （跨卷、重名、现成同名文件那些都是让 Finder 处理的事）。
    ⚠️ PyObjC 把带 `NSError**` 的方法映射成 `(Bool, …)` 元组，但出参个数随
       SDK/metadata 会变（`resultingItemURL` 也是出参）→ 两个形状都兜住。
    """
    from Foundation import NSURL
    from AppKit import NSFileManager
    url = NSURL.fileURLWithPath_(str(path))
    got = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
        url, None, None)
    if isinstance(got, tuple):
        ok = bool(got[0])
        err = got[-1] if len(got) > 1 else None
    else:
        ok, err = bool(got), None
    return ok, ("" if not err else str(err))


def remove(path, *, registry=None, trash=None, root=None) -> dict:
    """把那**一个**目录移进废纸篓。返回 `{"ok", "refused", "error"}`。

    `trash(path)` 可注入 —— 判据用它，**绝不碰真的废纸篓**。默认走 `_trash`。
    ⚠️ 任何一条安全判据不过 → **什么都不做**（`refused=True`）并出声：
       realpath 必须等于注册表那条、必须是目录、**不是符号链接**、里面有 `MARKER`。
    ⚠️ 成功之后才记 `answered="trash"` —— 「删了但没记住」会让下次再问一遍，
       而「没删成却记住」更坏：用户再也没机会同意删它（说好的那句成了假话）。
    """
    want = os.path.realpath(os.path.expanduser(registry or whisper_path()))
    raw = os.path.expanduser(str(path))
    got = os.path.realpath(raw)
    why = ""
    if got != want:
        why = f"路径不是注册表里那一条（注册表：{want}）"
    elif os.path.islink(raw):
        why = "它是符号链接（链接背后那份东西不一定是我们装的）"
    elif not os.path.isdir(got):
        why = "它不是一个目录"
    elif not os.path.isfile(os.path.join(got, MARKER)):
        why = f"里面没有 {MARKER}（不是那份模型？）"
    if why:
        print(f"⚠ Whisper 没删：{why}", flush=True)
        return {"ok": False, "refused": True, "error": why}
    mover = trash or _trash
    try:
        ok, err = mover(got)
    except Exception as e:                                 # noqa: BLE001
        ok, err = False, f"{type(e).__name__}: {e}"
    if not ok:
        msg = str(err or "未知原因")
        print(f"⚠ Whisper 没删成：{msg}", flush=True)
        return {"ok": False, "refused": False, "error": msg}
    answer(_TRASH, root=root)
    return {"ok": True, "refused": False, "error": ""}
