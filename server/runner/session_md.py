# session_md —— 网页逐句 → 桌面版 `sessions/*.md`（三方契约格式的**唯一 runner 侧写法）。
# 管什么：`{seq,t,en,zh,src}` 行 + 课元数据 → 一个 `obsidian_writer._parse` 能原样读回的
#   会话文件（抬头 `# 课 · 日期 · 实时会话日志` + 每句 `> [!abstract] HH:MM:SS` + EN/ZH 行）。
# 对外接口：`write_session(lines, *, course, started_at, tz, out_dir) -> Path`。
# 为什么不手拼：格式是三方共享契约（obsidian_writer 写 / `_parse` 读 / `rebuild_note` 认抬头）；
#   但桌面没有可指路径的纯写函数（`append` 绑着实例与时钟），所以这里**逐字段借桌面自己的
#   `_flat` 压行**、逐行照 `append()` 的写法拼块，并用 `_parse` 读回做硬断言（对不上就抛，
#   绝不静默产出一个读不回来的文件）。
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: 抬头正则 —— 与 `main._session_course_date` 认的形状一致（它认不出就拒绝补笔记）。
_COURSE_DATE = re.compile(r"^#\s*(.+?)\s*·\s*(\d{4}-\d{2}-\d{2})\s*·")
#: 文件名里不许出现的字符（Linux 上 `/` 是唯一硬非法，其余是跨设备搬运的隐患）。
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _ow():
    """懒 import obsidian_writer（它顶层 import paths，HOME 得先就位）。"""
    import obsidian_writer
    return obsidian_writer


def safe_course(course: str) -> str:
    """课名 → 文件名安全段：非法字符换 `-`；空了给 `LECTURE`（同桌面占位名）。"""
    name = _BAD_FILENAME.sub("-", str(course or "").strip())
    name = name.strip(" .-") or "LECTURE"
    return name


def _wall(started_at: str, tz: str):
    """开课时刻（ISO/UTC）→ 课所在时区的挂钟。tz 非法/缺失退 UTC（课内秒不受影响）。"""
    s = str(started_at or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s or "1970-01-01T00:00:00+00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo(str(tz or "UTC")))
    except Exception:                            # noqa: BLE001
        return dt.astimezone(timezone.utc)


def write_session(lines: list, *, course: str, started_at: str, tz: str,
                  out_dir: Path) -> Path:
    """逐句 → `out_dir/<日期>_<HHMMSS>_<课名>.md`，返回路径。**写后 `_parse` 读回断言**。

    `t` = 课内秒（从 `started_at` 算挂钟时间戳）；`zh` 空的句不写 ZH 行（同 `append`）。
    """
    ow = _ow()
    flat = ow._flat
    # 网页课名是用户随手起的：「/」进桌面笔记库路径会变子目录，「·」会把抬头正则切错（整条任务失败）。
    # 抬头与文件名用同一个净化后的名字（Claude 11-20）。
    course = safe_course(str(course or "").replace("·", "-"))
    if not (lines or []):
        raise ValueError("没有逐句，拒绝生成空会话文件")
    wall = _wall(started_at, tz)
    date = wall.strftime("%Y-%m-%d")
    fname = f"{date}_{wall.strftime('%H%M%S')}_{safe_course(course)}.md"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / fname
    n = 1
    while path.exists():                        # 同秒重放不覆盖：桌面同名也是另存
        n += 1
        path = out / f"{date}_{wall.strftime('%H%M%S')}_{n}_{safe_course(course)}.md"

    # en/zh 压行后都空的句整句不写：只剩时间戳块 `_parse` 读不回，下面断言会让整份笔记失败
    # （OCR 11-22：一句空句 → 3≠2 → 网页用户整份笔记失败）。写和读回比对用同一份列表。
    want = [ln for ln in sorted(lines or [], key=lambda x: int(x.get("seq") or 0))
            if flat(str(ln.get("en") or "")) or flat(str(ln.get("zh") or ""))]
    if not want:
        raise ValueError("逐句全是空句，拒绝生成空会话文件")
    parts = [f"# {course} · {date} · 实时会话日志", ""]
    for ln in want:
        ts = (wall + timedelta(seconds=int(ln.get("t") or 0))).strftime("%H:%M:%S")
        block = [f"> [!abstract] {ts}"]
        en = flat(str(ln.get("en") or ""))
        zh = flat(str(ln.get("zh") or ""))
        if en:
            block.append(f"> **EN**: {en}")
        if zh:
            block.append(f"> **ZH**: {zh}")
        parts.append("\n".join(block) + "\n")
    text = "\n".join(parts) + "\n" if parts else ""
    m = _COURSE_DATE.match(text)
    if not m or m.group(1).strip() != course or m.group(2) != date:
        raise AssertionError(f"抬头不被 _session_course_date 认：{text[:60]!r}")
    path.write_text(text, encoding="utf-8")

    # ⭐ 读回断言：`_parse` 是读法唯一定义点 —— 数量、时间戳、字段逐条对上才算写成了。
    got = ow.ObsidianWriter._parse(path.read_text(encoding="utf-8"))
    if len(got) != len(want):
        raise AssertionError(f"_parse 读回 {len(got)} 条 ≠ 写入 {len(want)} 条")
    for g, w in zip(got, want):
        ts = (wall + timedelta(seconds=int(w.get("t") or 0))).strftime("%H:%M:%S")
        if g["ts"] != ts or g["en"] != flat(str(w.get("en") or "")) \
                or g["zh"] != flat(str(w.get("zh") or "")):
            raise AssertionError(f"读回不一致: {g!r} ≠ {w!r}")
    return path
