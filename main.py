#!/usr/bin/env python3
"""本地实时课堂双语字幕 ClassLive 主入口。

用法:
  python main.py --source file ~/lecture_0909/rec_socio_crisis.m4a --ui terminal
  python main.py --source mic --ui overlay
  python main.py --source blackhole --ui overlay

线程模型:
  主线程  : 读音频块 -> Segmenter(只做 VAD 判定, 快速) -> 排空结果队列 -> 调 UI
  草稿线程: 对"当前句"做 ASR(latest-wins, 旧的丢弃)
  定稿线程: 句末 ASR + LLM 修正翻译 -> 结果队列
  => 音频循环永不阻塞; 所有 UI 调用都在主线程(悬浮窗要求)
"""
from __future__ import annotations
import argparse, collections, os, queue, re, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from capture import load_source, SR
from vad import Segmenter
from asr import load_asr, load_final_asr, is_degenerate
from translator import load_translator
from cloud_translator import (load_api_key, load_translator as load_cloud_translator,
                              answer_user_content)
from obsidian_writer import ObsidianWriter, DEFAULT_VAULT
from build_notes import (TermNotes, format_gloss, detect_proper_nouns, lookup_term)
from testmode import TestSession

PARTIAL_MAX_S = 10          # 草稿只转写最近 N 秒, 限制单次耗时

# 以这些词收尾(且无句末标点)→ 句子没说完, 不能单独送 LLM 翻译
DANGLING_TAILS = {
    "that", "which", "who", "whom", "whose", "where", "when", "because",
    "and", "or", "but", "so", "if", "as", "than", "to", "with", "in", "of",
    "on", "for", "at", "by", "from", "into", "about", "the", "a", "an", "is",
    "are", "was", "were", "be", "been", "will", "would", "can", "could",
    # 口语连读缩写(ASR 常按连读转写; 漏掉会把 "what we're" 当完整句)
    "we're", "they're", "you're", "i'm", "it's", "he's", "she's", "there's",
    "what's", "we've", "i've", "you've", "they've", "we'll", "i'll", "you'll",
    "won't", "don't", "doesn't", "didn't", "isn't", "aren't", "wasn't",
    "can't", "couldn't", "wouldn't", "shouldn't", "hasn't", "haven't",
}


def is_incomplete(text: str) -> bool:
    """以连词/介词/冠词/助动词收尾且无句末标点 → 判断为未说完。"""
    words = (text or "").strip().split()
    if not words:
        return False
    if text.rstrip().endswith((".", "!", "?", "…")):
        return False
    last = words[-1].lower().strip(".,!?\"'()[]")
    return last in DANGLING_TAILS


def all_settled(finalq, streamq, drafts, busy: bool, carry_text: str) -> bool:
    """收尾判据(停止/播完后的冲刷等待): 所有已收进来的语音都已变成落盘结果。

    ⚠️ 四项**必须一起看**, 只看队列会在两种真实场景丢最后一句(均实测复现):
    ① 半句挂在 carry 里等下一个 utterance 拼接 —— 此时 finalq/streamq 全空,
       但它还没送 LLM;
    ② 最后一句以悬挂词收尾 → 进 carry → 定稿线程退出时 `_force_emit_carry`
       才开始调 LLM —— 首字落地前队列恰好全空, 冲刷循环的"双检查"(间隔
       0.25s)会在云端首字延迟(~1s)内双双通过 → 提前 break, 最后一句丢失。
    所以除队列外还要看 busy(定稿线程正在 ASR/LLM)和 carry(还有半句没送出)。"""
    return (finalq.empty() and streamq.empty() and drafts.empty()
            and not busy and not carry_text)


def split_sentences(text: str, max_words: int = 25) -> list[str]:
    """按句末标点把 ASR 长段切成句子。
    - 过短碎片(<=2词)并入前一句;
    - 无标点 run-on: 优先在逗号/分号处切, 否则才按词数切(避免劈开语法结构)。"""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        if out and len(p.split()) <= 2:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)

    final: list[str] = []
    for s in out:
        words = s.split()
        if len(words) <= max_words:
            final.append(s)
            continue
        idx = 0
        while idx < len(words):
            chunk = words[idx:idx + max_words]
            if idx + max_words < len(words):
                cut = None
                for j in range(len(chunk) - 1, max(1, len(chunk) // 2), -1):
                    if chunk[j].endswith((",", ";", ":")):
                        cut = j + 1
                        break
                if cut:
                    final.append(" ".join(chunk[:cut]))
                    idx += cut
                    continue
            final.append(" ".join(chunk))
            idx += max_words
    return final or [text]


def now() -> str:
    return time.strftime("%H:%M:%S")


def echo(msg: str) -> None:
    print(msg, flush=True)


def _ask_save_notes(n: int) -> bool:
    """结束时问是否存入 Obsidian。
    ⚠️ 这里按 Ctrl+C / 非交互环境一律**默认保存** —— 会话文件已实时落盘,
    这一步只决定要不要复制进 Obsidian 库, 绝不能因为一次误按丢掉整节课。"""
    try:
        ans = input(f"\n📝 本次共记录 {n} 句双语。存入 Obsidian 吗? [Y/n] ").strip().lower()
    except KeyboardInterrupt:
        echo("\n(Ctrl+C → 默认存入 Obsidian, 避免误丢)")
        return True
    except EOFError:
        echo("\n(非交互环境 → 默认存入 Obsidian)")
        return True
    return ans in ("", "y", "yes", "是", "好", "存")


NO_CLOUD_ANSWER = ("⚠ 讲解需要云端引擎(DeepSeek): 本地的 1.7B 模型只会翻译, 没有讲解能力。"
                   "用 --engine auto/cloud 并配好 API key 后可用。")

# 「讲一下」按钮的固定问题(作者锁定: "讲清楚刚讲的这段")。
# 可以是常量 —— 转录底座由 answer_worker 快照后经 answer_user_content 附在同一
# 个 user turn 里, 问题本身不需要携带任何上下文。
ASK_QUESTION = "讲清楚刚讲的这段: 核心是什么、教授为什么现在要讲它。"


class EngineRouter:
    """翻译引擎路由: cloud 优先, 失败自动降级本地(断网兜底)。

    降级**不是永久的**: 一堂 2h 的课 Wi-Fi 抖一次就全程困在本地小模型上
    (实测一次瞬时 Errno 60 就触发过, 且本地回退质量明显更差)。
    降级后每 RETRY_PROBE_S 秒用 1-token 探针试云端, 通了自动切回。
    状态变化通过 notify(msg, warn) 广播(经 streamq 转主线程, 可进 UI)。"""

    RETRY_PROBE_S = 90.0

    def __init__(self, local, cloud, mode: str, notify=None):
        self._local = local
        self._cloud = cloud
        self._mode = mode
        self._notify = notify or (lambda msg, warn=False: None)
        self._use_cloud = bool(cloud) and mode in ("auto", "cloud")
        self._probe_lock = threading.Lock()   # 防降级→恢复→再降级重复起探针线程
        # 降级是一条**状态迁移**, 不是一次赋值。Phase 2 起有第二条并发路径会进来
        # (问答 worker 与常驻翻译 worker 同时在跑), 两个线程可能同时读到 True,
        # 各自广播一次"已降级本地" —— 用户看到两条重复通知, 且两次都去起探针。
        # 加锁把"判断 + 翻转"并成一步。只护这一处, _use_cloud 本身仍是普通 bool。
        self._state_lock = threading.Lock()

    def warmup(self) -> None:
        if self._mode == "local":
            self._local.warmup()
        elif self._mode == "cloud":
            pass                                  # 云端无需预热
        # auto: 不预热本地, 省内存/启动时间; 真降级时再懒加载

    def _fallback(self, e: Exception) -> None:
        with self._state_lock:                    # 见 __init__: 迁移只能发生一次
            if not self._use_cloud:
                return
            self._use_cloud = False
        self._notify(
            f"云端翻译失败({str(e)[:60]}); 已降级本地引擎, "
            f"每 {self.RETRY_PROBE_S:.0f}s 自动重试", warn=True)
        self._start_probe()

    def _start_probe(self) -> None:
        if self._cloud is None:
            return
        if not self._probe_lock.acquire(blocking=False):
            return                                # 已有探针在跑
        def probe():
            try:
                while True:
                    time.sleep(self.RETRY_PROBE_S)
                    if self._use_cloud:           # 别处已恢复(理论不可达, 保险)
                        return
                    if self._cloud.probe():
                        with self._state_lock:
                            self._use_cloud = True
                        self._notify("云端翻译已恢复", warn=False)
                        return
            finally:
                self._probe_lock.release()
        threading.Thread(target=probe, daemon=True,
                         name="cloud-retry-probe").start()

    def fix_and_translate_stream(self, en, context, on_zh=None, on_en=None):
        if self._use_cloud:
            try:
                return self._cloud.fix_and_translate_stream(en, context, on_zh, on_en)
            except Exception as e:                # noqa: BLE001
                self._fallback(e)
        return self._local.fix_and_translate_stream(en, context, on_zh, on_en)

    def fix_stream(self, en, context, on_en=None) -> str:
        """只矫正英文(翻译关闭时)。走同一个模型与同一份上下文, 只是不要中文。"""
        if self._use_cloud:
            try:
                return self._cloud.fix_stream(en, context, on_en)
            except Exception as e:                # noqa: BLE001
                self._fallback(e)
        return self._local.fix_stream(en, context, on_en)

    def translate_draft(self, en, on_zh=None) -> str:
        if self._use_cloud:
            try:
                return self._cloud.translate_draft(en, on_zh)
            except Exception as e:                # noqa: BLE001
                self._fallback(e)
        return self._local.translate_draft(en, on_zh)

    def answer(self, question, transcript, history, on_delta=None) -> str:
        """按需讲解 / 追问。形状照抄 fix_stream: 云端优先, 异常降级。

        ⚠️ 降级目标**不是** `self._local.answer_stream(...)`: `translator.Translator`
        的方法到 `translate_draft` 就结束了, 本地根本没有 answer_stream, 那样写
        在 `--engine local` / 没配 key 时是 AttributeError —— 会把问答线程直接
        打死(而且是在 daemon 线程里, 表现成"点了没反应")。改成**显式守卫**,
        返回一句能直接上屏的说明。
        为什么不补一个本地实现: 1.7B 的翻译模型解释课堂内容只会编, 而且生成期间
        要抢 `translator.py` 那条本地全局锁, 会把正在进行的翻译整段卡住 ——
        宁可不做, 也不能给一个会撒谎的答案。
        """
        if self._use_cloud:
            try:
                return self._cloud.answer_stream(question, transcript, history,
                                                 on_delta)
            except Exception as e:                # noqa: BLE001
                # ⚠️ **刻意不走 `_fallback`**。`_fallback` 会把 `_use_cloud` 翻成
                # False —— 于是**一次纯问答的失败会把整节课的翻译也降级到本地**
                # (独立验证实测: 问答抛错后下一句翻译真的走了本地)。问答失败
                # (例如上下文超限)完全不能说明翻译链路有问题。
                # 而且 `_fallback` 的提示语写死是"云端翻译失败", 在这里是**误导**;
                # 它还会起一个 90s 探针线程去计费的 ping。翻译路径下一句自己会发现
                # 真问题并降级 —— 各管各的。
                return f"⚠ 讲解失败({str(e)[:60]})"
        return NO_CLOUD_ANSWER


class TerminalUI:
    def __init__(self):
        self._zh = ""
        self._en = ""
        self._answer = ""

    def add_draft(self, en: str):
        echo(f"[{now()}] ▸ {en}")

    def draft_zh(self, zh: str):
        echo(f"[{now()}]   ↳ {zh}")

    def terms(self, hits):
        echo(f"[{now()}]   {format_gloss(hits)}")

    def stream_zh(self, delta: str):
        self._zh += delta

    def stream_en(self, delta: str):
        self._en += delta

    def finalize(self, en: str, zh: str):
        echo(f"[{now()}] ✅ {en}\n         🌐 {zh}")
        self._zh = self._en = ""

    def answer_delta(self, delta: str):
        # 只累加不上屏: 与 stream_zh 同规矩 —— 完整回答由 drain() 一次性 echo
        # (逐 delta 打印会和字幕行交错成一片糊)。
        self._answer += delta

    def answer_done(self, question: str, text: str):
        self._answer = ""

    def notice(self, msg: str, warn: bool = False):
        echo(("⚠ " if warn else "✅ ") + msg)

    def pump(self):
        pass


class _Latest:
    """latest-wins 槽位: 草稿只保留最新一份, 旧的自然丢弃。"""

    def __init__(self):
        self._buf = None
        self._ev = threading.Event()
        self._lock = threading.Lock()

    def put(self, buf) -> None:
        with self._lock:
            self._buf = buf
        self._ev.set()

    def take(self, timeout: float):
        if not self._ev.wait(timeout):
            return None
        with self._lock:
            b, self._buf = self._buf, None
            self._ev.clear()
            return b


def _changelog_section(version: str) -> str:
    """取 CHANGELOG.md 里某个版本的正文（不含标题行）。取不到返回空串。"""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")
        text = open(p, encoding="utf-8").read()
    except Exception:                                     # noqa: BLE001
        return ""
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
                  text, re.S | re.M)
    return m.group(1) if m else ""


def _changelog_date(version: str) -> str:
    """版本日期（`## [1.2.0] - 2026-09-24` 里的那串）。取不到返回空串。"""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")
        text = open(p, encoding="utf-8").read()
    except Exception:                                     # noqa: BLE001
        return ""
    # ⚠️ 这里**不能**写成 rf"..."：`\d{4}` 里的 `{4}` 会被当成 f-string 表达式求值，
    # 模式会变成 `(\d4-\d2-\d2)` —— 编译通过、永不匹配、静默返回空串。踩过。
    m = re.search(r"^## \[" + re.escape(version)
                  + r"\][^\n]*?-\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    return m.group(1) if m else ""


def _changelog_summary(version: str, max_lines: int = 6) -> str:
    """更新卡片上显示的那几行。

    写法参考 Keep a Changelog 与各家 "What's New" 的共识（2026-09-24 调研）：
      · **破坏性 / 要先做的**放最前；
      · **说影响，不说实现** —— "笔记写入可能丢数据" 而不是
        "save_to 改为原子写 (.tmp + os.replace)"；
      · **一条一事**，不要把无关改动捆一行；
      · 短、能扫，前几行就要给出重要信息。

    ⚠️ 为什么优先读「### 更新看点」而不是抓 `### Added`/`### Fixed`：
    那些小节是写给**维护者**的（有文件名、符号名、代码片段），卡片是给**用的人**看的。
    两个读者、两份文字。抓出来会出现 `self._model, self._tokenizer = load(...`
    这种屏上没人看得懂的行（实测踩过）。所以由作者在 CHANGELOG 里显式写一小节，
    没有该小节时才退化成抓取（老版本兼容）。
    """
    body = _changelog_section(version)
    if not body:
        return ""

    # ---- 优先：显式的「更新看点」小节 ----
    m = re.search(r"^###\s*更新看点[^\n]*\n(.*?)(?=^### |\Z)", body, re.S | re.M)
    if m:
        out = []
        for line in m.group(1).splitlines():
            s = line.strip()
            if s.startswith(("- ", "* ")):
                out.append(re.sub(r"\*\*|`", "", s[2:]).strip())
            if len(out) >= max_lines:
                break
        if out:
            return "\n".join(out)

    # ---- 兜底：抓每个 ### 小节的第一条（给没有「更新看点」的老版本）----
    out, cur = [], None
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("### ") and "更新看点" not in s:
            cur = s[4:].strip()
        elif s.startswith(("- ", "* ")) and cur:
            b = re.sub(r"\*\*|`", "", s[2:]).strip()
            b = re.split(r"[。；;]", b)[0]
            if len(b) > 58:                               # 掐在标点处，不切字中间
                cut = max(b.rfind("，", 0, 58), b.rfind("、", 0, 58),
                          b.rfind(" ", 0, 58))
                b = (b[:cut] if cut > 20 else b[:57]) + "…"
            out.append(f"{cur} · {b}")
            cur = None                                    # 每节只取第一条
        if len(out) >= max_lines:
            break
    out.sort(key=lambda x: 0 if ("Breaking" in x or "破坏" in x) else 1)
    return "\n".join(out)


def _disp_w(s: str) -> int:
    """终端里的**显示宽度** —— 中文/全角算 2 列。

    ⚠️ 直接用 `len()` 会让框的右边框对不齐: 一个汉字在终端占两列，但只算 1 个字符。
    (2026-09-24 实测发现。)
    """
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _whatsnew_payload() -> tuple[str, str] | None:
    """该不该弹「本次更新」卡片；该的话返回 (版本, 摘要)，否则 None。

    ⚠️ **只判断、不弹** —— 弹的动作交给 UI 层：
      · 悬浮窗模式 → `overlay.show()` 里用**非模态毛玻璃卡片**（见 whatsnew.py）；
      · 终端模式   → 印一个字符框。
    为什么不在这里弹：① 这里是 `run()` 第一行，那时 app 的 activation policy 还是
    `Regular`（实测），NSAlert 会带 Python 的图标 + 在 Dock 里冒出来；② 模态框会
    卡住启动 —— 而这个工具是**上课录课**用的。
    """
    if os.environ.get("CLASSLIVE_NO_UPDATE_CHECK"):
        return None
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        # 勾过「以后不再提示」-> 永不再弹
        if os.path.exists(os.path.join(root, ".update-skip")):
            return None
        ver_file = os.path.join(root, "VERSION")
        cur = (open(ver_file, encoding="utf-8").read().strip()
               if os.path.exists(ver_file) else "?")
        if cur == "?":
            return None
        seen_file = os.path.join(root, ".update-seen")
        seen = (open(seen_file, encoding="utf-8").read().strip()
                if os.path.exists(seen_file) else "")
        if cur == seen:
            return None
        # 第一次跑（seen 为空）不弹 —— 那不是"更新"是全新安装，
        # 对着一条长长的 changelog 弹卡片没有意义。
        payload = ((cur, _changelog_date(cur), _changelog_summary(cur))
                   if seen else None)
        with open(seen_file, "w", encoding="utf-8") as f:   # 记下来，别重复弹
            f.write(cur)
        return payload
    except Exception:                                     # noqa: BLE001
        return None


def _print_whatsnew_box(version: str, date: str, body: str) -> None:
    """终端模式下的退化：印一个字符框。宽度按**显示宽度**算（中文占两列）。

    参数顺序与 `_whatsnew_payload()` 的返回元组一致，调用方可以直接 `*payload`。
    """
    title = f"ClassLive 已更新到 {version}" + (f" · {date}" if date else "")
    lines = [x for x in (body or "").splitlines() if x.strip()] or ["见 CHANGELOG.md"]
    lines.append("")
    lines.append("完整说明见 CHANGELOG.md")
    w = max([_disp_w(title)] + [_disp_w(x) for x in lines]) + 2
    echo("╭─ " + title + " " + "─" * max(0, w - _disp_w(title) - 3) + "╮")
    for x in lines:
        echo("│ " + x + " " * (w - _disp_w(x) - 1) + "│")
    echo("╰" + "─" * (w + 1) + "╯")


def _show_whats_new(version: str, date: str, body: str) -> None:
    """[已弃用] 原来的 NSAlert 弹框。保留仅为兼容，新路径走 whatsnew.py 的非模态卡片。"""
    _print_whatsnew_box(version, date, body)


def _maybe_notice_update() -> None:
    """**一次性**的更新提示: 检测到落后于远程就打印一行, 之后不再打扰。

    ⚠️ 三条自我约束(理由见 README「升级到新版本」):
      1. **默认静默** —— 断网/代理/限流/没有 git 一律什么都不说, 绝不阻塞启动;
      2. **每个版本只提示一次** —— 提示过就写 `.update-notice` 记下当前版本号,
         所以"用户已经看到了但还没升"不会每次上课都被念一遍;
      3. **不自动升级** —— `git pull` 由用户自己敲。启动时改用户的工作区
         是同一条硬规矩("不擅自改环境")的越界。

    为什么可以联网: 只在本地问 `git`(远端 ref 已经在本地仓库里), **不发 HTTP**。
    所以既不碰 GitHub 的未认证限流(60/h), 也不需要用户登录。
    """
    if os.environ.get("CLASSLIVE_NO_UPDATE_CHECK"):
        return
    try:
        # ---- ① 该不该弹「本次更新」卡片（只判断，弹的动作交给 UI 层）----
        payload = _whatsnew_payload()

        # ---- ② 落后远程 -> 一行提示（需要 git）----
        root = os.path.dirname(os.path.abspath(__file__))
        cur = (open(os.path.join(root, "VERSION"), encoding="utf-8").read().strip()
               if os.path.exists(os.path.join(root, "VERSION")) else "?")
        if not os.path.isdir(os.path.join(root, ".git")):
            return payload
        import subprocess
        behind = subprocess.run(["git", "rev-list", "--count", "HEAD..@{u}"],
                                cwd=root, capture_output=True, text=True,
                                timeout=3).stdout.strip()
        if not behind.isdigit() or int(behind) == 0:
            return payload
        stamp = os.path.join(root, ".update-notice")
        if os.path.exists(stamp) and open(stamp, encoding="utf-8").read().strip() == cur:
            return payload                          # 这个版本已经提示过了
        echo(f"↑ 有新版本（本地 {cur}，远程领先 {behind} 个提交）"
             f"—— 升级: cl update（变更清单见 CHANGELOG.md）")
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(cur)
        return payload
    except Exception:                               # noqa: BLE001
        return None                                 # 提示而已, 任何失败都静默


def run(args) -> None:
    # 该不该弹「本次更新」卡片 —— 只判断，不弹。弹的动作交给 UI 层：
    # 悬浮窗模式在 overlay.show() 里用非模态毛玻璃卡片（那时 activation policy
    # 已经是 Accessory，不会带 Python 图标，也不阻塞）；终端模式印字符框。
    whatsnew = _maybe_notice_update()
    # 音源最先打开: 失败(没麦/没 BlackHole/坏文件)在这里就给友好提示退出,
    # 不再走完 ASR/LLM 加载 + 悬浮窗后才崩出一个裸 traceback。
    # ⚠️ 这次只是**试开**: 验证完立刻关。句柄留着不关的话, mic 的 InputStream
    #    已经 start() 了, 下面会再开一个 —— 设备被占 + 句柄泄漏两头不讨好。
    try:
        probe_src = load_source(args.source, args.path, args.speed)
    except Exception as e:                       # noqa: BLE001
        echo(f"⚠ 无法打开音源({args.source}): {e}")
        return
    try:
        probe_src.close()
    except Exception:                            # noqa: BLE001
        pass

    # 两个 ASR 模型都是**必需**的: 草稿/兜底用 Parakeet, 定稿用 Whisper。
    # 缺任何一个都在这里说清楚然后退出 —— 不在课上静默降质。
    try:
        asr = load_asr(args.model_dir)
        asr_final = load_final_asr(args.final_model_dir)
    except Exception as e:                       # noqa: BLE001
        echo(f"⚠ {e}")
        echo("  装好模型再跑：`cl doctor` 会列出缺哪个、该跑哪条命令。")
        return
    local_tr = load_translator(args.llm, args.glossary, args.context,
                               course=args.course)
    cloud_tr = None
    api_key_val = load_api_key(args.api_key)
    if args.engine in ("auto", "cloud"):
        if api_key_val:
            cloud_tr = load_cloud_translator(api_key_val, args.cloud_model,
                                             args.glossary, args.context,
                                             course=args.course)
            echo(f"☁ 引擎: {args.engine} (云端 {args.cloud_model})")
        else:
            echo("⚠ 未找到 DeepSeek API key(--api-key / DEEPSEEK_API_KEY / .deepseek_key); "
                 "回退本地引擎")
    else:
        echo(f"💻 引擎: local ({args.llm})")

    running = threading.Event()
    running.set()
    # ✕ / 菜单栏「退出」请求停机。**必须与 running 分开 —— 这是个真 bug 的修法**:
    # 原来 ✕ 回调直接 running.clear(), 而收尾要先 finalq.put(_QUIT) 交给
    # final_worker 让它收摊; worker 的循环判据正是 running, 提前清掉 = 没人取那个
    # 哨兵 -> finalq 永远非空 -> all_settled() 永远不成立 -> **每次点 ✕ 都卡满
    # 15s deadline**(独立验证实测 15.02s, 而且**完全不提问也一样**)。
    # 现在 ✕ 只置这个标志(主循环据此退出), running 留到收尾真正结束才清。
    stopping = threading.Event()
    flagged = {"on": False}                             # ⭐ 标记当前句
    trans = {"mode": "both"}                            # 🌐 三档: both 双语 / en 只英·校 / raw 纯转录
    translating = {"on": True}                          # 兼容旧读法: raw 之外都算"开"
    writer = ObsidianWriter(args.vault, args.course, mode=args.save_notes,
                            api_key=api_key_val, model=args.cloud_model,
                            glossary_path=args.glossary,
                            polish=args.polish != "off",
                            polish_model=args.polish_model or None)
    notes = TermNotes()                                 # 术语通俗解析(查表)

    # 测试模式: 采一份完整报告 + 留音频。**任何采集失败都不能影响上课** ——
    # TestSession 的所有 note_* 都自带 try/except(见 testmode.py)。
    tester = None
    if args.test_mode:
        tester = TestSession(getattr(writer, "session_path", None),
                             record_audio=not args.no_record_audio)
        echo(f"🧪 测试模式: 报告将写到 {tester.stem}.report.json")
        # ⚠️ 隐私提醒必须放在**启动时** —— 收尾才说就晚了: 那时整节课已经录完,
        #    想改成 --no-record-audio 也来不及。让人在**开始之前**就能决定。
        if tester.record_audio:
            echo("   ⚠️ 会录制**课堂音频**(约 28MB/15 分钟), 收尾打成一个 zip。")
            echo("      音频与逐字转录可能含**其他同学的声音** —— 发出去前请自己确认。")
            echo("      只要指标、不留音频:  Ctrl+C 退出后改用 `cl test --no-record-audio`")
        else:
            echo("   ℹ️ 只采指标, 不录音频（--no-record-audio）。")

    drafts: "queue.Queue[str]" = queue.Queue()          # 草稿文本 -> 主线程
    drafts_zh: "queue.Queue[str]" = queue.Queue()       # 草稿译文 -> 主线程
    streamq: "queue.Queue[tuple]" = queue.Queue()       # ("zh"/"en"/"final"/"terms"/"notice"/"answer"/"answer_done") -> 主线程
    draftq: "queue.Queue" = queue.Queue(maxsize=1)      # 待译草稿(只保留最新)
    properq: "queue.Queue" = queue.Queue()              # 待查专有名词
    answerq: "queue.Queue[str]" = queue.Queue()         # 待讲解的问题(Phase 2)

    def notify(msg: str, warn: bool = False) -> None:
        """引擎状态广播 -> streamq -> 主线程 drain() -> UI(悬浮窗要求主线程)。"""
        streamq.put(("notice", (msg, warn)))

    translator = EngineRouter(local_tr, cloud_tr, args.engine, notify=notify)
    translator.warmup()

    def submit_question(q: str) -> None:
        """悬浮窗输入框回车回调 -> 把问题投进问答线程(Phase 2)。

        ⚠️ 这个函数是在**主线程**被 AppKit 调用的: 只能做立刻返回的事。
        这里只有一次 `queue.put`(无界队列, 不阻塞); 网络与模型全在
        answer_worker 里。echo 是既有行为, 保留它 —— 终端里留一条"我问过什么"。
        """
        echo(f"[{now()}] 🙋 {q}")
        answerq.put(q)

    def ask_about_this() -> None:
        """悬浮窗「讲一下」按钮 -> 用固定问题开一轮(与输入框共用同一条线程)。

        与 submit_question 同规矩: 主线程回调, 只做一次 queue.put —— 不许联网、
        不许 sleep、不许长持 qa_lock(主线程同时还背着音频循环与渲染)。
        """
        echo(f"[{now()}] 🙋 [讲一下] {ASK_QUESTION}")
        answerq.put(ASK_QUESTION)

    ui = TerminalUI() if args.ui == "terminal" else _load_overlay(
        on_quit=stopping.set,
        on_flag=lambda: flagged.__setitem__("on", True),
        on_translate=lambda mode: trans.__setitem__("mode", mode),
        on_submit=submit_question,
        on_ask=ask_about_this,
        # late binding: start_new_topic 定义在下面的问答状态块里, 这里只是把回调
        # 装上(按钮到点名前一定已经定义好了)。
        on_new_topic=lambda: start_new_topic(),
        whatsnew=whatsnew,
    )
    # 终端模式拿不到悬浮窗，退化成字符框（两种模式都要能看到）
    if args.ui == "terminal" and whatsnew:
        _print_whatsnew_box(*whatsnew)

    seen_proper: collections.Counter = collections.Counter()   # 出现次数(≥2 才查)
    queried_proper: set = set()                         # 已发起过查询的(防重复请求)
    finals: list[str] = []                              # 已定稿(修正后), 作 LLM 上下文
    latest = _Latest()
    finalq: "queue.Queue" = queue.Queue()
    _QUIT = object()
    busy = {"on": False}          # 定稿线程正在处理一句(ASR+LLM), 冲刷等待要等它归零

    # ---- 问答线程状态(Phase 2) ----
    # 一节课一条线程: 一个**冻结的转录快照** + 一个只追加的 turns 列表。
    # ⚠️ 转录快照必须是 `list(finals)` 的拷贝: finals 由定稿线程持续 append,
    #    持活引用 = 快照会边问边变, 消息前缀不再稳定(缓存全废)。
    # consumed = 已经附进历史的 finals 条数; 之后的追问只补新增的那一段。
    qa_lock = threading.Lock()
    qa = {"history": [], "consumed": 0, "gen": 0}

    def start_new_topic() -> None:
        """清空问答线程(Phase 3 的「新话题」按钮)。下次提问会重新冻结转录底座 ——
        底座仍是"这节课到此刻为止", 只是不再背着上一个话题的问答历史。"""
        with qa_lock:
            qa["history"].clear()
            qa["consumed"] = 0
            qa["gen"] += 1                     # 在途的那一轮回来时会被丢弃

    # ---- 草稿线程 ----
    # 每定稿一句 +1; 草稿线程据此重置节流(见 partial_worker 里的说明)。
    final_gen = {"n": 0}

    def partial_worker():
        last_tr = 0.0
        last_len = 0
        last_gen = 0
        while running.is_set():
            buf = latest.take(0.5)
            if buf is None or len(buf) == 0:
                continue
            # 定稿是关键路径(决定你多久看到中文), 且它要抢同一把 ASR 锁。
            # 有定稿在排队时让草稿这一轮退开: 草稿迟到 1s 无感, 定稿迟到直接
            # 拖慢首字。定稿线程本来就把草稿译文排在定稿之后(见 final_worker)。
            if not finalq.empty():
                continue
            try:
                t = asr.transcribe(buf[-PARTIAL_MAX_S * SR:])
                if not t:
                    continue
                drafts.put(t)
                # 新句子开始 -> 重置草稿节流。**必须重置**: last_len 是**上一句**
                # 收尾时的词数, 新句若更短(如 20 词 vs 30 词), "新增 ≥6 词"永远
                # 不成立 -> 整句拿不到即时中文("首字中文 ~3s"特性静默失效)。
                if final_gen["n"] != last_gen:
                    last_gen = final_gen["n"]
                    last_tr, last_len = 0.0, 0
                # 草稿译文: 距上次 ≥2.5s 且新增 ≥6 词才送(避免刷爆 LLM)
                now = time.monotonic()
                if trans["mode"] == "both" and now - last_tr >= 2.5 \
                        and len(t.split()) - last_len >= 6:
                    last_tr, last_len = now, len(t.split())
                    try:
                        draftq.put_nowait(t)
                    except queue.Full:               # 只留最新
                        try:
                            draftq.get_nowait(); draftq.put_nowait(t)
                        except queue.Empty:
                            pass
            except Exception as e:                       # noqa: BLE001
                echo(f"⚠ 草稿转写失败: {e}")

    # ---- 定稿线程(ASR + 语义缝合 + 断句 + 逐句流式 LLM) ----
    # 优先级: 定稿 > 草稿译文。carry 挂起超 CARRY_MAX_S 或词数超限 → 强制送出,
    # 绝不让"等句子闭合"把翻译无限期阻塞(否则会出现 20s+ 才吐中文)。
    CARRY_MAX_S = 5.0
    CARRY_MAX_WORDS = 25
    carry = {"text": "", "since": 0.0}

    def _emit(text: str) -> None:
        final_gen["n"] += 1                      # 通知草稿线程: 重置节流
        for sent in split_sentences(text):
            if trans["mode"] == "raw":
                # 纯转录: 一个 LLM 请求都不发。ASR 原样上屏、原样落盘。
                # 这是给"不需要翻译、也不想联 API"的场景 —— 离线、零成本、零首字延迟。
                # ⚠️ 与下面两档的关键差别: 不产出 `en` 流(没有矫正可流式), 直接给
                #    ("final", "", "", sent), 让 en 走 final 那一趟。
                finals.append(sent)
                streamq.put(("final", "", "", sent))
                continue
            if trans["mode"] == "en":
                # 只矫正英文: 中文不出, 但 ASR 错听照修(仍要联模型)。
                # 失败则回退原始 ASR —— 与翻译路径同规矩, 绝不因模型问题丢转录。
                try:
                    fixed = translator.fix_stream(
                        sent, finals,
                        on_en=lambda d: streamq.put(("en", d)))
                except Exception as e:              # noqa: BLE001
                    echo(f"⚠ 英文矫正失败({str(e)[:50]}); 保留原始转录")
                    fixed = ""
                finals.append(fixed or sent)
                streamq.put(("final", fixed, "", sent))
                continue
            # 翻译失败也要保住转录: LLM 挂了不代表这节课没听到东西。
            try:
                res = translator.fix_and_translate_stream(
                    sent, finals,
                    on_zh=lambda d: streamq.put(("zh", d)),
                    on_en=lambda d: streamq.put(("en", d)))
                en, zh = res.en_fixed, res.zh
                finals.append(en)
            except Exception as e:                  # noqa: BLE001
                echo(f"⚠ 翻译失败({str(e)[:50]}); 仅保留转录")
                en, zh = "", ""                     # 无修正版/译文, 内容全在转录里
                finals.append(sent)                 # 上下文仍接得上
            # 第 4 项是**原始 ASR 转录**(未修正), 落盘时要一并记下
            streamq.put(("final", en, zh, sent))
            # 术语/专有名词: 命中术语表就把 (术语, 解析) 交给 UI —— 由 UI 决定
            # 什么时候展开(悬浮窗默认只列术语名, 点击才显示解析)。
            hits = notes.match(sent)
            streamq.put(("terms", hits))
            # 术语表没覆盖的专有名词(人名/机构/地名) -> 第 2 次出现才后台查一次并缓存
            # (一次性的 ASR 误听往往只出现一次, 2 次门槛把它们挡在门外)
            if not hits and api_key_val and trans["mode"] != "raw":
                for pn in detect_proper_nouns(sent, notes.known()):
                    seen_proper[pn] += 1
                    if seen_proper[pn] == 2:
                        properq.put(pn)

    def _force_emit_carry() -> None:
        if not carry["text"]:
            return
        t = carry["text"]
        # ⚠️ 顺序是命门: busy 必须在**清空 carry 之前**置位。
        # 收尾判据 all_settled() 靠"carry 空 + busy 空"两个条件接棒 —— 先清 carry
        # 会在这两条字节码之间让**四项同时为空**, 冲刷循环的"双检查"(间隔 0.25s)
        # 若两次都落进这个窗口就提前 break, 这句永久丢失(实测复现过的丢句 bug)。
        # (2026-09-24 全仓审计发现: 注释一直这么写, 代码却是反的 —— 已按注释修回。)
        busy["on"] = True
        carry["text"], carry["since"] = "", 0.0
        try:
            _emit(t.rstrip() + " …")
        except Exception:                               # noqa: BLE001
            pass
        finally:
            busy["on"] = False

    def final_worker():
        EMPTY = object()
        while running.is_set():
            # 1) 定稿优先
            try:
                buf = finalq.get_nowait()
            except queue.Empty:
                buf = EMPTY
            if buf is _QUIT:
                break
            if buf is EMPTY:
                # 2) 其次: 草稿译文(尽力而为, 过期即丢)
                try:
                    dtext = draftq.get_nowait()
                except queue.Empty:
                    dtext = None
                if dtext and trans["mode"] == "both":
                    try:
                        translator.translate_draft(
                            dtext, lambda d: drafts_zh.put(d))
                    except Exception:                   # noqa: BLE001
                        pass
                    continue
                # 3) 空转: 检查 carry 是否挂起过久
                time.sleep(0.2)
                if carry["text"] and time.monotonic() - carry["since"] >= CARRY_MAX_S:
                    _force_emit_carry()
                continue
            try:
                # ⚠️ 顺序: **先跑定稿模型**, 退化才回头跑草稿模型。
                # 反过来写(先 parakeet 再 whisper)会让 parakeet 那 0.36s 白花 ——
                # 实测它的结果只在 whisper 退化时用得上, 而 64 段里退化 0 次。
                # 先跑慢的那个, 命中就省下整个快的那趟; 退化时多花的 0.36s 无所谓。
                _t_asr = time.monotonic()
                text = asr_final.transcribe(buf)
                _used, _lp = "whisper", asr_final.last_logprob
                if not text or is_degenerate(text):
                    text = asr.transcribe(buf)      # 退化/空 -> 用草稿模型(并兜底)
                    _used, _lp = "parakeet", asr.last_logprob
                if tester is not None:
                    tester.note_segment(buf, text, (time.monotonic() - _t_asr) * 1000,
                                        _used, logprob=_lp,
                                        fell_back=(_used == "parakeet"),
                                        n_sentences=len(split_sentences(text)))
                if not text:
                    continue
                if carry["text"]:                           # 与上句半截拼接
                    text = f"{carry['text']} {text}".strip()
                    carry["text"], carry["since"] = "", 0.0
                # 未说完 且 未超上限 -> 继续等; 否则强制送出
                if is_incomplete(text) and len(text.split()) < CARRY_MAX_WORDS:
                    carry["text"] = text
                    if not carry["since"]:
                        carry["since"] = time.monotonic()
                    continue
                # ⚠️ 顺序同 _force_emit_carry: busy 先置位、再清 carry, 否则这两条
                # 字节码之间四项同时为空 -> 冲刷循环可能提前 break 丢掉这句。
                # (注释旧版写的是"busy 覆盖 ASR + _emit 全程" —— 那与实现不符:
                #  asr.transcribe 在这之前就已执行, busy 并没有覆盖 ASR。而且真把
                #  busy 提到 ASR 之前, 下面几条 `continue` 路径都会忘记清 busy,
                #  反而会让冲刷循环空等到 15s 上限。故只修顺序, 并改正这句注释。)
                busy["on"] = True
                carry["text"], carry["since"] = "", 0.0
                try:
                    _emit(text)
                finally:
                    busy["on"] = False
            except Exception as e:                       # noqa: BLE001
                echo(f"⚠ 定稿失败: {e}")
                busy["on"] = False
        _force_emit_carry()                          # 收尾: 残余半句也翻掉

    seg = Segmenter(
        on_partial=lambda buf: latest.put(buf),
        on_utterance_end=lambda buf: finalq.put(buf),
    )

    # ---- 专有名词查询线程(网络请求, 异步且缓存, 不影响主链路) ----
    def proper_worker():
        while running.is_set():
            try:
                term = properq.get(timeout=0.5)
            except queue.Empty:
                continue
            if term is None:
                break
            if term in queried_proper:
                continue
            if trans["mode"] == "raw":       # 纯转录: 不联模型, 专有名词查询整条跳过
                continue
            queried_proper.add(term)
            try:
                res = lookup_term(term, api_key_val, args.cloud_model)
                if res:
                    notes.add(term, res["note"], res["type"])   # 写 auto 文件, 下次查表命中
                    streamq.put(("terms", [(term, res["note"])]))
            except Exception as e:                                 # noqa: BLE001
                # 单条失败就放过去。线程一旦死掉, 之后**所有**专有名词都静默停摆 ——
                # 而这条链本就是可有可无的(不影响字幕主链路), 不值得为它陪葬。
                echo(f"⚠ 专有名词查询失败({term[:24]}): {str(e)[:60]}")

    # ---- 问答线程(Phase 2) ----
    # 形状照抄 proper_worker: daemon + 只认自己的队列 + 只判 running。
    # ⚠️ **绝不碰 busy / finalq / carry**: 那三个是 all_settled() 收尾闸门的输入
    #    (见 all_settled 的注释), 问答去动它们会让"文件播完"的冲刷判断误判 ——
    #    轻则丢最后一句, 重则提前 break 掉整段收尾。问答是**可以丢的**: 进程退出
    #    就走, 答案没写完就算了, 落盘才是不能丢的那件事。
    def answer_worker():
        while running.is_set():
            try:
                q = answerq.get(timeout=0.5)
            except queue.Empty:
                continue
            if q is None:
                break
            with qa_lock:
                gen = qa["gen"]
                first = not qa["history"]
                # 首次提问 = 冻结整段快照; 追问 = 只补上次提问之后新讲的句子
                # (锁定需求是"整节课转录至今", 不是"第一次提问那一刻的转录")。
                # ⚠️ 只快照**一次**。写成 `list(finals[consumed:])` 再 `len(finals)`
                # 是两次独立读取, final_worker 的 append 可以插在中间 —— 那几句
                # 会被**永久跳过**(consumed 已越过它们), 且只在后续追问里静默少掉,
                # 无从察觉。独立验证用强制线程切换复现: 300000 轮里 3283 轮不一致,
                # 共 265801 句被静默丢弃(默认切换间隔下 4/300000)。
                snap = list(finals)
                block = snap if first else snap[qa["consumed"]:]
                qa["consumed"] = len(snap)
                hist = [dict(t) for t in qa["history"]]
            # 用户 turn 的正文由 answer_user_content 统一生成 —— 下面要把它**逐字**
            # 写回历史, 必须与 answer_stream 内部发给模型的那一份完全一致。
            content = answer_user_content(q, block, not first)

            def _emit(d):
                # ⚠️ 收尾中就不再往 streamq 写。streamq 是 `all_settled()` 的**五个
                # 输入之一**, 答案还在流就会一直等不到收尾(独立验证实测: 0.38s
                # -> 15.2s)。答案在退出时本来就是可以丢的(见上方注释)——
                # **卡住收尾才是真损失**。
                # ⚠️ 判据是 stopping 而不是 running: running 要到收尾之后才清,
                # 用它等于没写守卫(实测自然结束时密集流仍卡 15s)。
                # ⚠️ 再叠 `gen == qa["gen"]`: 按过「新话题」的那一轮**已经在途的增量
                # 也必须停**。否则 ① 线程历史把它丢了(下面的判断), ② 面板却被它重新
                # 接管 —— 用户按「新话题」要的就是"回到字幕", 结果 0.2s 后答案又回到
                # 屏上, 按钮等于没生效。与下面那条"丢弃这一轮"是同一条规则。
                if not stopping.is_set() and gen == qa["gen"]:
                    streamq.put(("answer", d))

            try:
                text = translator.answer(q, block, hist, on_delta=_emit)
            except Exception as e:                # noqa: BLE001
                text = f"⚠ 讲解失败: {str(e)[:80]}"
            with qa_lock:
                if gen == qa["gen"]:              # 期间按过「新话题」-> 丢弃这轮
                    qa["history"].append({"role": "user", "content": content})
                    qa["history"].append({"role": "assistant", "content": text})
            # 同上: 被「新话题」作废的那一轮连收尾包也不发 —— 连终端那份 echo 一起
            # 丢掉, 与"它不进线程历史"保持一致(半途被作废的回答不该留下记录)。
            if not stopping.is_set() and gen == qa["gen"]:
                streamq.put(("answer_done", q, text))

    t1 = threading.Thread(target=partial_worker, daemon=True)
    t2 = threading.Thread(target=final_worker, daemon=True)
    t3 = threading.Thread(target=proper_worker, daemon=True)
    t4 = threading.Thread(target=answer_worker, daemon=True, name="answer-qa")
    t1.start(); t2.start(); t4.start()            # 问答不需要 key 就能起步
    if api_key_val:                               # (没 key 时它只回一句说明)
        t3.start()

    echo(f"▶ 开始: source={args.source}" + (f" path={args.path}" if args.path else ""))
    try:
        src = load_source(args.source, args.path, args.speed)
    except Exception as e:                       # noqa: BLE001
        # 走到这里模型已加载、会话文件已建 —— 绝不能裸崩, 那会把这次课的转录一起丢掉
        echo(f"⚠ 无法打开音源({args.source}): {e}")
        # ⚠️ 早退也要收尾: 会话文件已经建了(带抬头), 直接 return 会跳过 writer.close(),
        # 留下一个只有抬头、没走笔记流程的半成品文件 + 泄漏的文件句柄。
        # (2026-09-24 OCR 发现。)
        try:
            writer.close(ask=False)
        except Exception:                        # noqa: BLE001
            pass
        return

    def drain():
        while True:
            try:
                ui.add_draft(drafts.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                ui.draft_zh(drafts_zh.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                item = streamq.get_nowait()
            except queue.Empty:
                break
            if item[0] == "zh":
                ui.stream_zh(item[1])
            elif item[0] == "en":
                ui.stream_en(item[1])
            elif item[0] == "terms":
                if item[1]:
                    ui.terms(item[1])
            elif item[0] == "notice":
                msg, warn = item[1]
                ui.notice(msg, warn)
            elif item[0] == "answer":               # ("answer", delta) 讲解流式增量
                # ⚠️ 用 getattr 守卫: 悬浮窗的渲染是 Phase 3, Overlay 现在**没有**
                # answer_delta。drain() 没有 try/except, 直接调会 AttributeError
                # 打死主循环(与下面 close() 的守卫同一个理由)。
                fn = getattr(ui, "answer_delta", None)
                if callable(fn):
                    fn(item[1])
            elif item[0] == "answer_done":          # ("answer_done", 问题, 回答)
                fn = getattr(ui, "answer_done", None)
                if callable(fn):
                    fn(item[1], item[2])
                # Phase 2 只要求"可观测/可测": 回答在终端整段打出来(悬浮窗渲染
                # 留给 Phase 3)。与 🙋 那行配对, 终端日志一眼能读完整条问答。
                echo(f"[{now()}] 🤖 {item[2]}")
            elif item[0] == "final":                # ("final", en, zh, asr_raw)
                ui.finalize(item[1] or item[3], item[2])   # 翻译失败时至少显示转录
                writer.append(item[1], item[2], flagged=flagged["on"], raw=item[3])
                flagged["on"] = False

    try:
        while running.is_set() and not stopping.is_set():
            chunk = src.poll()
            if chunk is not None and len(chunk):
                seg.accept(chunk)
                if tester is not None:
                    tester.note_chunk(chunk)
            drain()
            if src.is_done():
                break
            if args.ui == "overlay":
                ui.pump()
            else:
                time.sleep(0.005)
    except KeyboardInterrupt:
        echo("\n⏹ 手动停止")
    finally:
        # 收尾一开始就置停机标志 —— 覆盖**所有**进入收尾的路径(文件播完 / ✕ /
        # Ctrl+C)。它同时是问答的"别再往 streamq 写了"判据: 收尾等的是
        # all_settled(), 而 streamq 是它的五个输入之一, 答案还在流就会一直
        # 等不到(实测密集流把 0.38s 拖到 15.2s)。
        # ⚠️ 判据**不能**用 running: running 要到下面内层 finally 才清, 收尾
        # 期间它一直是 set, 守卫等于没写。
        stopping.set()
        # 先撤悬浮窗: 后面可能等冲刷 + 阻塞问"是否存 Obsidian", 窗口留着不动
        # 就是"点了 ✕ / 按了 Ctrl+C 就卡死"。TerminalUI 没有 close, 故用 getattr。
        # (✕ 按钮已经在自己的回调里关过一次, close() 幂等。)
        close_ui = getattr(ui, "close", None)
        if callable(close_ui):
            close_ui()
        # ⚠️ 必须包住: src.close() 抛异常的话, 下面的 seg.flush() / finalq 排空 /
        # writer.close()(会话落盘 + Obsidian 写入 + 精修)**全部会被跳过** ——
        # 与"落盘才是不能丢的事"这条核心约定直接冲突。
        # 上面 probe 阶段的 probe_src.close() 就是包了 try/except 的(作者已知它会抛)。
        # (2026-09-24 OCR 全量审计发现。)
        try:
            src.close()
        except Exception:                           # noqa: BLE001
            pass
        # ---- 统一冲刷: 文件播完 / ✕ / Ctrl+C 走同一条路 ----
        # 旧代码只在"文件播完"才冲刷, 手动停止会把分段器里在途的整句话
        # (最多 12s 音频)直接丢掉 —— 实测 SIGINT 复现; 而冲刷等待只看队列,
        # carry 半句与强制送出的 LLM 输出都在队列之外, 竞态下文件尾也会丢句
        # (57.5s 切点实测复现)。现在: 冲刷分段器 + 等定稿线程真正空闲。
        # ⚠️ running 在等待期间必须保持 set, 否则定稿线程直接退出不干活。
        # 外层 try/finally 保证用户在等待中再按一次 Ctrl+C 也会走到 writer.close。
        try:
            seg.flush()
            # 收尾诊断: 句尾有没有被切、有没有整段丢掉。一切正常时 report() 为空串,
            # 不产生噪音; 有数就说明这节课的转录值得回头看那几处。
            _diag = seg.report()
            if _diag:
                echo(_diag)
            finalq.put(_QUIT)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                drain()
                if all_settled(finalq, streamq, drafts,
                               busy["on"], carry["text"]):
                    time.sleep(0.25)
                    if all_settled(finalq, streamq, drafts,
                                   busy["on"], carry["text"]):
                        break
                time.sleep(0.02)
            drain()
        finally:
            running.clear()

            def qa_snapshot():
                """「我问过什么」交给笔记(Phase 4)。⚠️ 在 qa_lock 下**拷贝一份**:
                answer_worker 是 daemon 且**从不 join**, 收尾时它可能正卡在
                translator.answer() 里, 回来仍会往 history 追加 —— 传活引用等于让
                writer 边写边看它改。⚠️ 做成函数而不是先取好: close() 里还要等
                用户回答"是否保存"再精修, 早取会漏掉这段时间里回来的那一条问答。
                """
                with qa_lock:
                    return list(qa["history"])

            msg = writer.close(ask=_ask_save_notes, qa=qa_snapshot)
            if msg:
                echo(msg)
            if tester is not None:
                _rep = tester.finish(vad_report=locals().get("_diag", ""),
                                     note_path=str(getattr(writer, "note_path", "") or ""))
                if _rep:
                    echo(f"\n🧪 测试报告: {_rep}")
                if tester.bundle_path:
                    _sz = os.path.getsize(tester.bundle_path)
                    _mb = f"{_sz / 1e6:.1f} MB" if _sz > 1e6 else f"{_sz / 1024:.0f} KB"
                    echo(f"📦 数据包:   {tester.bundle_path}  ({_mb})")
                    if tester.record_audio:
                        echo("   ⚠️ 内含**课堂音频** + 逐字转录(可能有其他同学的声音)"
                             " —— 发出去前自己确认一下。")
                    else:
                        echo("   ℹ️ 只含指标与转录文本, **不含音频**（--no-record-audio）。")
                    echo("   发给作者即可, 不用解压。")
                else:
                    echo("⚠ 数据包生成失败, 但报告已写出(见上面的路径)")


def _load_overlay(on_quit=None, on_flag=None, on_translate=None, on_submit=None,
                  on_ask=None, on_new_topic=None, whatsnew=None):
    try:
        from overlay import Overlay
        o = Overlay(on_quit=on_quit, on_flag=on_flag, on_translate=on_translate,
                    on_submit=on_submit, on_ask=on_ask,
                    on_new_topic=on_new_topic, whatsnew=whatsnew)
        o.show()
        return o
    except Exception as e:       # noqa: BLE001
        echo(f"⚠ 悬浮窗不可用({e}); 回退终端 UI。")
        return TerminalUI()


def main():
    p = argparse.ArgumentParser(description="本地实时课堂双语字幕")
    p.add_argument("--source", choices=["mic", "blackhole", "file"], default="file")
    p.add_argument("--path", help="--source file 时的音频路径")
    p.add_argument("--speed", type=float, default=1.0, help="file 模式回放倍速(测试用)")
    p.add_argument("--ui", choices=["terminal", "overlay"], default="terminal")
    p.add_argument("--model-dir", default=os.path.expanduser("~/models/parakeet-tdt-0.6b-v3-int8"),
                   help="草稿+兜底的 ASR 模型目录(要求够快, 每秒要出一份草稿)")
    p.add_argument("--test-mode", action="store_true",
                   help="测试模式: 采集一份完整指标报告(逐段 ASR 耗时/电平/置信度/"
                        "资源占用), 并在会话文件旁留一份音频, 供以后优化用")
    p.add_argument("--no-record-audio", action="store_true",
                   help="测试模式下不留音频(只要指标; 包会小很多)")
    p.add_argument("--no-bundle", action="store_true",
                   help="测试模式下不打包成可发送的单个 zip")
    p.add_argument("--final-model-dir",
                   default=os.path.expanduser("~/models/sherpa-onnx-whisper-turbo"),
                   help="定稿专用 ASR 模型目录(必需; 用 `cl doctor` 检查)")
    p.add_argument("--llm", default="mlx-community/Qwen3-1.7B-4bit")
    p.add_argument("--glossary", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "glossary.txt"))
    p.add_argument("--context", type=int, default=5,
                   help="送翻译的最近上下文句数(默认 5; 远场听错多, 上下文越长越好修)")
    p.add_argument("--engine", choices=["auto", "cloud", "local"], default="auto",
                   help="翻译引擎: auto(云端优先,失败降级)/cloud/local")
    p.add_argument("--cloud-model", default="deepseek-flash",
                   help="云端模型名")
    p.add_argument("--api-key", help="DeepSeek API key(默认读 DEEPSEEK_API_KEY 或 .deepseek_key)")
    p.add_argument("--course", help="课程代码(如 ECON10101); 不设也能写笔记(课名默认 LECTURE)")
    p.add_argument("--save-notes", choices=["ask", "yes", "no"], default="ask",
                   help="笔记保存策略: ask(默认,结束时问)/ yes(直接存)/ no(不存)")
    p.add_argument("--polish", choices=["auto", "off"], default="auto",
                   help="落笔前二次精修转录(需 API key; 默认 auto); off 直接用直播版")
    p.add_argument("--polish-model", help="精修用的模型(默认同 --cloud-model)")
    p.add_argument("--vault", default=DEFAULT_VAULT, help="Obsidian 库路径")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
