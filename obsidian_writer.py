"""课堂记录落盘。

设计原则: **先落盘, 再询问** —— 运行中每句定稿就追加写入"会话文件",
所以崩溃 / 误按 Ctrl+C / 答"不保存" 都**不会丢内容**。

  sessions/<日期>_<时间>_<课程>.md   ← 运行中实时写的**原始逐句日志**(crash-safe)
        ↓ 结束时组装 + 询问
  <vault>/Lectures/<日期>_<课程>.md   ← **双层笔记**:
        复习层在上(概览 / 自测 / 术语表 / 重点), 完整逐句转录折叠在下。

⚠️ 双层不是"摘要 + 原文": 复习层只是**入口**, 下面那份转录是**逐字保留、未做任何删改**
的 —— 复习要的是"不遗漏", 摘要会把细节吃掉, 所以转录永远完整地在文件里。

  --save-notes ask   结束时问(默认;答否则保留会话文件)
  --save-notes yes   不问,直接进 Obsidian
  --save-notes no    完全不写(会话文件也不建)
"""
from __future__ import annotations
import json, os, re, time
from pathlib import Path

DEFAULT_VAULT = "~/Obsidian/Vault"
SESSIONS = Path(__file__).with_name("sessions")

# 会话文件里一条定稿块的抬头: "> [!abstract] 14:02:18" / "... ⭐ Exam Focus"
_TS = re.compile(r"^> \[!abstract\] (\d\d:\d\d:\d\d)( ⭐ Exam Focus)?\s*$")
_FIELDS = (("en", "EN"), ("zh", "ZH"), ("asr", "ASR"))

REVIEW_SYS = """你是课堂笔记助手。用户给你一节课的逐句中英对照转录(每行 [时间] 英文)。
只依据转录内容, 输出严格 JSON:
{"overview": "一句话概括这节课讲了什么(不超过 60 字)",
 "qa": [{"q": "复习问题", "a": "答案"}]}
硬性要求:
- 只写转录里**确实讲过**的内容, 不得引入任何外部知识、不得猜测; 拿不准就不写。
- qa 提 3-6 条, 覆盖本课的核心概念 / 结论 / 易错点; 答案简短、直接来自转录。
- overview 和答案里都不要出现换行符或 markdown。"""


class ObsidianWriter:
    def __init__(self, vault: str | None, course: str | None, mode: str = "ask",
                 api_key: str | None = None, model: str = "deepseek-flash"):
        self.mode = mode
        self.enabled = bool(vault and course) and mode != "no"
        self._vault = os.path.expanduser(vault) if vault else None
        self._course = course
        self._date = time.strftime("%Y-%m-%d")
        self._key = api_key                 # 有 key 才生成"概览 + 自测"复习层
        self._model = model
        self._n = 0
        self.session_path: Path | None = None
        self.vault_path: Path | None = None
        if self.enabled:
            SESSIONS.mkdir(exist_ok=True)
            self.session_path = SESSIONS / (
                f"{self._date}_{time.strftime('%H%M%S')}_{course}.md")
            self.session_path.write_text(
                f"# {course} · {self._date} · 实时会话日志\n\n", encoding="utf-8")

    # ---- 运行中: 每句立刻落盘(flush) ----
    def append(self, en: str, zh: str, flagged: bool = False, raw: str = "") -> None:
        """`en` 是 LLM 修正后的英文, `raw` 是**原始 ASR 转录**。

        两个都记: 修正版好读, 原始版是"模型实际听到什么"的凭据 ——
        LLM 偶尔会过度修正(把正确的词改错), 没有原始转录就无从复核。
        """
        if not self.enabled or not self.session_path:
            return
        self._n += 1
        ts = time.strftime("%H:%M:%S")          # 定稿那一刻
        mark = " ⭐ Exam Focus" if flagged else ""
        lines = [f"> [!abstract] {ts}{mark}"]
        if en:
            lines.append(f"> **EN**: {en}")
        if zh:
            lines.append(f"> **ZH**: {zh}")
        if raw:
            lines.append(f"> **ASR**: {raw}")
        with self.session_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")

    @property
    def count(self) -> int:
        return self._n

    # ---- 解析原始日志 -> 条目 ----
    @staticmethod
    def _parse(text: str) -> list[dict]:
        entries: list[dict] = []
        cur = None
        for line in text.splitlines():
            m = _TS.match(line)
            if m:
                if cur:
                    entries.append(cur)
                cur = {"ts": m.group(1), "star": bool(m.group(2)),
                       "en": "", "zh": "", "asr": ""}
                continue
            if cur is None:
                continue
            for key, tag in _FIELDS:
                pre = f"> **{tag}**: "
                if line.startswith(pre):
                    cur[key] = line[len(pre):].strip()
                    break
        if cur:
            entries.append(cur)
        return entries

    # ---- 复习层素材 ----
    def _glossary(self, entries: list[dict]) -> list[tuple[str, str, str]]:
        """本课命中的术语 [(术语, 解析, 首次出现时间)]。纯本地查表, 零网络。"""
        try:
            from build_notes import TermNotes
        except Exception:                                 # noqa: BLE001
            return []
        notes = TermNotes()
        seen, out = set(), []
        for e in entries:
            for t, d in notes.match(e["en"] or e["asr"]):
                if t not in seen:
                    seen.add(t)
                    out.append((t, d, e["ts"]))
        return out

    def _review(self, entries: list[dict]) -> tuple[str | None, list[dict]]:
        """LLM 生成"一句话概览 + 复习自测"。无 key / 失败 -> (None, [])。
        失败是**正常路径**(断网、没配 key 都要能落盘), 所以绝不抛。"""
        if not self._key or not entries:
            return None, []
        transcript = "\n".join(
            f"[{e['ts']}] {e['en'] or e['asr']}"
            for e in entries if (e["en"] or e["asr"]))
        if not transcript:
            return None, []
        try:
            import httpx
            payload = {"model": self._model, "stream": False, "max_tokens": 1200,
                       "temperature": 0.2, "thinking": {"type": "disabled"},
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": REVIEW_SYS},
                                    {"role": "user", "content": transcript}]}
            r = httpx.post("https://api.deepseek.com/v1/chat/completions",
                           headers={"Authorization": f"Bearer {self._key}"},
                           json=payload, timeout=120)
            r.raise_for_status()
            obj = json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception:                                 # noqa: BLE001
            return None, []
        ov = obj.get("overview")
        ov = " ".join(ov.split()) if isinstance(ov, str) and ov.strip() else None
        qa = []
        for x in (obj.get("qa") or []):
            if isinstance(x, dict):
                q = x.get("q"); a = x.get("a")
                if isinstance(q, str) and isinstance(a, str) and q.strip() and a.strip():
                    qa.append({"q": " ".join(q.split()), "a": " ".join(a.split())})
        return ov, qa

    # ---- 组装双层笔记 ----
    def _render_note(self, entries: list[dict], overview: str | None,
                     qa: list[dict]) -> str:
        ts0 = entries[0]["ts"] if entries else "—"
        ts1 = entries[-1]["ts"] if entries else "—"
        stars = [e for e in entries if e["star"]]
        gloss = self._glossary(entries)
        L: list[str] = []
        L += ["---",
              f"date: {self._date}",
              f"course: {self._course}",
              "tags: [lecture, live-transcript, review]",
              "type: lecture-notes",
              "---", "",
              f"# {self._course} · 课堂笔记 {self._date}", "",
              "> [!info] 本课信息",
              f"> 🕐 {ts0} – {ts1} · 🗣 {len(entries)} 句 · "
              f"⭐ {len(stars)} 处重点 · 💡 {len(gloss)} 个术语", ""]

        L += ["## 🎯 一句话概览", ""]
        if overview:
            L += [f"> {overview}",
                  ">",
                  "> <sub>🤖 自动生成 · 依据本课转录</sub>"]
        else:
            L += ["> *（未自动生成 —— 未配 API key 或调用失败；可课后自行补一句）*"]
        L += [""]

        L += ["## ❓ 复习自测", ""]
        if qa:
            L += ["> [!tip] 🤖 自动生成 · 请核对后再用于复习",
                  "> <sub>写成 `问题::答案`，可被 Spaced Repetition 插件识别</sub>", ""]
            L += [f"- {x['q']}::{x['a']}" for x in qa]
        else:
            L += ["> [!tip] 用 `问题::答案` 写自测题（可被 Spaced Repetition 插件识别）", "",
                  "- "]
        L += [""]

        L += ["## 💡 本课术语表", ""]
        if gloss:
            L += [f"- **{t}** — {d}　`{ts}`" for t, d, ts in gloss]
        else:
            L += ["*（本课没有命中术语表）*"]
        L += [""]

        L += ["## ⭐ 我标记的重点", ""]
        if stars:
            for e in stars:
                L += [f"- `{e['ts']}` {e['zh'] or e['en'] or e['asr']}"]
                if e["en"] and e["zh"]:
                    L += [f"  - EN: {e['en']}"]
        else:
            L += ["*（课上没按 ⭐；觉得哪句重要就按一下，会自动归到这里）*"]
        L += [""]

        # 完整转录: 折叠 callout 包全套逐句块。**逐字保留**, 是这份笔记的底座。
        L += [f"## 📜 完整逐句转录（点击展开 · {len(entries)} 句）", "",
              "> [!note]- 逐句双语 + 原始 ASR（未做任何删改）", "> "]
        for e in entries:
            L.append(f"> > [!abstract] {e['ts']}" + (" ⭐" if e["star"] else ""))
            if e["en"]:
                L.append(f"> > **EN**: {e['en']}")
            if e["zh"]:
                L.append(f"> > **ZH**: {e['zh']}")
            if e["asr"]:
                L.append(f"> > **ASR**: {e['asr']}")
            L.append("> ")
        return "\n".join(L) + "\n"

    # ---- 结束: 询问是否进 Obsidian ----
    def close(self, ask=None) -> str:
        if not self.enabled or not self.session_path or self._n == 0:
            return ""
        save = self.mode == "yes"
        if self.mode == "ask":
            save = True if ask is None else bool(ask(self._n))
        if not save:
            return (f"📝 未存入 Obsidian({self._n} 句)。"
                    f"记录仍保留在:\n   {self.session_path}")

        entries = self._parse(self.session_path.read_text(encoding="utf-8"))
        if self._key:
            print("🤖 正在生成复习层(概览 + 自测)…", flush=True)
        overview, qa = self._review(entries)
        note = self._render_note(entries, overview, qa)

        d = Path(self._vault) / "Lectures"
        d.mkdir(parents=True, exist_ok=True)
        self.vault_path = d / f"{self._date}_{self._course}.md"
        self.vault_path.write_text(note, encoding="utf-8")   # 覆盖式(同日同课取最近)
        layer = "概览+自测+术语表+重点" if overview else "术语表+重点(未生成概览)"
        return (f"📝 已存入 Obsidian({self._n} 句, 复习层: {layer}) → {self.vault_path}\n"
                f"   原始逐句日志: {self.session_path}")
