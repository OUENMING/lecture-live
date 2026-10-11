#!/usr/bin/env python3
"""CHANGELOG.md 的解析 —— **纯函数、只吃文本**（不读文件、不碰 git）。

为什么单独成一个模块：同一份解析有三个读者 ——
  · `main` 的「本次更新」卡片（读**本地**那份 CHANGELOG）；
  · `update` 的自动更新分级（读**远端** ref 上那份，磁盘上还没有）；
  · 开课面板的「有新版本」确认框（同样读远端那份，要在拉代码**之前**告诉人这一版改了什么）。
原来解析写在 `main.py` 里、直接读本地文件 —— 远端那份就没法复用，面板也不能为了几行正则去 import
整个 `main`（它拖着音频、UI 一大串依赖）。所以这里只收**文本**，谁读文件谁负责。
"""
from __future__ import annotations

import re


def section(text: str, version: str) -> str:
    """某个版本的正文（不含 `## [x]` 标题行）。没有就返回空串。"""
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
                  text or "", re.S | re.M)
    return m.group(1) if m else ""


def date(text: str, version: str) -> str:
    """版本日期（`## [1.2.0] - 2026-09-24` 里的那串）。取不到返回空串。"""
    # ⚠️ 这里**不能**写成 rf"..."：`\d{4}` 里的 `{4}` 会被当成 f-string 表达式求值，
    # 模式会变成 `(\d4-\d2-\d2)` —— 编译通过、永不匹配、静默返回空串。踩过。
    m = re.search(r"^## \[" + re.escape(version)
                  + r"\][^\n]*?-\s*(\d{4}-\d{2}-\d{2})", text or "", re.M)
    return m.group(1) if m else ""


def entries(text: str) -> str:
    """从第一个 `## [` 起的全文（去掉文件开头写给维护者的约定），并按 `reorder` 排好。"""
    m = re.search(r"^## \[", text or "", re.M)
    return reorder(text[m.start():]) if m else (text or "")


def versions(text: str) -> list[str]:
    """一段 CHANGELOG 文本里所有版本号，**新到旧**（文件里的出现顺序）。"""
    return re.findall(r"^## \[([^\]]+)\]", text or "", re.M)


def update_mode(text: str, version: str) -> str:
    """该版本能否**后台自动应用** —— 只看 `### 更新方式` 那节。

    ⚠️ **默认 `manual`**（默认拒绝）：没声明自己安全的，绝不自动应用。

    ⚠️⚠️ **不能用 semver 判**：本仓库 `3.4.0 → 3.5.0` 是 **minor** 版本号、内容却是
    **breaking**（两个 ASR 模型改必装、缺了启动即报错）。作者不按 semver 发版，
    按版本号分级会正好漏掉最危险的那些。
    """
    sec = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
                    text or "", re.S | re.M)
    if not sec:
        return "manual"
    m = re.search(r"^###\s*更新方式[^\n]*\n(.*?)(?=^### |\Z)", sec.group(1), re.S | re.M)
    if not m:
        return "manual"
    return "auto" if re.search(r"^\s*auto\s*$", m.group(1), re.M | re.I) else "manual"


def summary(body: str, max_lines: int = 6) -> str:
    """更新卡片上显示的那几行。`body` = 某个版本的正文（`section()` 的产物）。

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
    if not body:
        return ""

    # ---- 优先：显式的「更新看点」小节 ----
    m = re.search(r"^###\s*更新看点[^\n]*\n(.*?)(?=^### |\Z)", body, re.S | re.M)
    if m:
        out = []
        for line in m.group(1).splitlines():
            raw = line.rstrip()
            s = raw.strip()
            # ⚠️ **容错**：不要求每行都以 `- ` 开头。踩过 —— 作者手写时漏了 `- `，
            # 那几行就被**静默丢掉**，卡片上不出现，而且没有任何提示。
            # 现在除了空行/小标题/引用/代码块，其余都当条目。
            if not s or s.startswith(("#", ">", "|", "```")):
                continue
            # ⚠️⚠️ **缩进的行是上一条的续行，不是新条目。**
            #   卡片按**物理行**取，而看点写得长时必然折行（源码里缩进两格）——
            #   当成新条目的话，**一条看点会裂成两条**，而且合并多版本时后半截会被
            #   冠上**别的版本号**。3.7.0 那版实测就是这个症状：
            #       `3.6.5 · （⚠️ 现在只认 Markdown，PDF / PPTX 的自动转换还在做）`
            #   —— 那其实是 3.7.0 某条的后半截。（2026-09-28 写 3.8.0 时才发现。）
            continued = bool(out) and (len(raw) - len(raw.lstrip()) > 0)
            # ⚠️⚠️ **项目符号后面必须有空白**（`\s+` 不是 `\s*`，2026-09-30 修）。
            #    原来写 `\s*`：**续行**若以 `**粗体**` 开头，第一个 `*` 被当成项目符号
            #    吃掉，剩下的 `*粗体` 里那个孤星**再也没人剥** —— 卡片上就显示成
            #    「重活被 *默默跳过」。续行本身不该有项目符号，所以「要求空白」正是
            #    它和真项目符号的区别。
            s = re.sub(r"^[-*]\s+", "", s)                # 有就吃掉，没有也认
            s = re.sub(r"\*\*|`", "", s).strip()
            if not s:
                continue
            if continued:
                out[-1] = f"{out[-1]} {s}".strip()
            else:
                out.append(s)
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


def reorder(text: str) -> str:
    """把每个版本里的 `### 更新看点` 提到最前、`### 升级须知` 紧随其后。

    为什么在**代码里**做、而不是靠作者手写顺序：手写顺序会漂 —— 有的版本先写
    升级须知、有的先写更新看点，卡片上看到的东西就忽前忽后。这里统一，
    作者写哪样都行。

    每个版本内按 `### ` 切块，按 (`更新看点`=0 / `升级须知`=1 / 其余=2) **稳定排序**，
    所以其余小节仍保持作者写的相对顺序。
    """
    def rank(title: str) -> int:
        if "更新看点" in title:
            return 0
        if "升级须知" in title:
            return 1
        return 2

    out = []
    for part in re.split(r"(?m)^(?=## \[)", text):
        m = re.match(r"(## \[[^\n]*\n)(.*)\Z", part, re.S)
        if not m:                          # 不是版本段（比如空串）→ 原样
            out.append(part)
            continue
        head, body = m.group(1), m.group(2)
        chunks = re.split(r"(?m)^(?=### )", body)
        pre = "" if chunks and chunks[0].startswith("### ") else chunks.pop(0)
        idx = list(enumerate(chunks))
        idx.sort(key=lambda t: (rank(t[1].split("\n", 1)[0]), t[0]))   # 稳定
        out.append(head + pre + "".join(c for _, c in idx))
    return "".join(out)


def between(text: str, seen: str, cur: str, max_lines: int = 7) -> str:
    """该给用户看的内容 —— **`(seen, cur]` 这个区间**，不含比 `cur` 更新的版本。

    ⚠️ **必须跨版本合并** —— 有人会从 3.4.0 直接跳到 3.6.0。只看 `cur` 的话，
    他会**永远看不到 3.5.0 那条破坏性变更**（"两个 ASR 模型改为必装、
    第一次要手动 git pull"）—— 恰恰是跳过版本的人最需要看到的那一条。

    ⚠️ **上界必须是 `cur`，不能"从最新往下数到 seen"**。第一版就是那么写的
    （`vers[:vers.index(seen)]`），结果是：**只要 CHANGELOG 里有比 `cur` 更新的
    版本，它就会被报进来** —— 用户会看到自己**还没装的版本**的说明。
    (2026-09-25 隔离测试发现：`(3.6.4, 3.6.5)` 报出了 `3.7.0` 的看点。)

    多版本时每行前面标版本号；带 ⚠️ 的（破坏性/要先做的）排最前。
    """
    vers = versions(text)                 # 新到旧
    if not vers or cur not in vers:
        return summary(section(text, cur), max_lines)
    lo = vers.index(cur)                  # cur 的位置（越小越新）
    # ⚠️ `seen` 不在库里时**收敛到只报当前版**（`lo + 1`），不能退化成 `len(vers)`——
    # 那会把 cur 及更旧的版本全算成"看点"（2026-09-25 ocr review 发现；
    # 旧实现在这里本来有守卫，本次改动删掉了）。
    hi = vers.index(seen) if seen in vers else lo + 1
    todo = vers[lo:hi]                    # ⚠️ 只取 (seen, cur]；比 cur 新的在 vers[:lo]
    if not todo:
        return ""
    if len(todo) == 1:
        return summary(section(text, todo[0]), max_lines)
    rows = [(v, ln)
            for v in todo
            for ln in summary(section(text, v), max_lines).splitlines()]
    # 稳定排序: 带 ⚠️ / Breaking 的提到最前, 其余保持"新版本在前"
    rows.sort(key=lambda r: 0 if ("⚠" in r[1] or "Breaking" in r[1]) else 1)
    return "\n".join(f"{v} · {ln}" for v, ln in rows[:max_lines])


def prev_version(text: str, cur: str) -> str:
    """CHANGELOG 里 `cur` 的**前一版**（比它旧的那个）。取不到就返回 `cur` 自己。

    用途见 `_whatsnew_payload`：弹窗的起点必须是**稳定的**，不能随
    `.update-seen` 往后跑。
    """
    vers = versions(text)
    if cur in vers:
        i = vers.index(cur)
        if i + 1 < len(vers):
            return vers[i + 1]
    return cur


def ver_key(v: str) -> tuple:
    """版本号排序键。`3.10.0` 必须排在 `3.9.0` 之后 —— 纯字符串比不出来。

    非数字段退化成 0，所以 `3.6.3` vs `3.6.3-rc1` 这类也**不会抛异常**。
    """
    parts = []
    for seg in (v or "").split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)
