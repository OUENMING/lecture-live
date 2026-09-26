"""课程清单 + 每门课的「准备度」 —— 面板上那张卡显示什么，全从这里算。

## 为什么单开一个模块

课程的**身份**散在两处，谁都不知道对方存在：

| 半边 | 在哪 | 谁写的 |
|---|---|---|
| 术语表 | `glossary/<课号>.txt`（**安装目录**旁） | 手写 + `prep.py` 追加 |
| 课件与 prep 状态 | `~/.classlive/courses/<课号>/`（`paths.py`） | 拖进去的、跑 prep 时落的 |

→ **`list_courses` 必须是这两边的并集。** 只看 `glossary/` 会漏掉「拖了课件还没跑 prep」的课；
只看 `~/.classlive/` 会漏掉「手写了术语表还没拖课件」的课。

这也是本模块存在的理由：这个并集规则、容错规则、准备度口径，各写一遍迟早漂移。

## ⚠️ 三条口径（都是实测出来的，别想当然）

1. **`sessions/` 文件名里的课号是「当时 `.course` 写的那个」，不是规范全名。**
   实测 `sessions/` 里是 `2026-09-10_140200_10730.md`，而术语表叫 `ECON10730.txt`。
   → 匹配**必须容错**（后缀），否则「上次上课」永远查不到。
   ⚠️ 同目录还有 `*_TEST.md` 之类的测试残留 —— 按「已知课程」过滤，别把测试当课程。
2. **`prep-state.json` 里没有「跑过几次」。** 它只有 `{"appended": {词: …}}` 一个墓碑字典。
   → 能报的是**「自动加过多少个词」**，不是运行次数。
   ⚠️ 计划里原来写「prep-state.json 跑过几次」——**那份数据不存在**，不许当成有了。
3. **读不出 ≠ 空。** `prep-state.json` 与术语表的读失败一律降级成「这项未知（None）」，
   绝不降级成 0 —— 0 会显示成「一个词都没有」，而那是**谎报**。

## 界面与实现的分工

本模块**不碰 AppKit**，只回答「有哪些课、每门课准备到什么程度」。
谁画、画多大、什么颜色，是 `entry_panel.py` 的事。
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import paths

#: 术语表里以它开头的行是注释（与 `translator._load_terms` 同一口径）。
_COMMENT = "#"


def glossary_dir(glossary_txt) -> pathlib.Path:
    """`glossary.txt` → 它旁边的 `glossary/`（与 `translator.course_terms_path` 同一算法）。

    ⚠️ 别用 `paths` —— `glossary/` 在**安装目录**旁，不在 `~/.classlive/` 下（见模块头）。
    """
    return pathlib.Path(glossary_txt).parent / "glossary"


# ══════════════════════════════════════════════════════════════════════
# 清单
# ══════════════════════════════════════════════════════════════════════
def list_courses(glossary_txt, *, state_root=None) -> list[str]:
    """两边的**并集**（见模块头）。排序稳定，供界面直接铺。"""
    root = pathlib.Path(state_root) if state_root is not None else paths.STATE_ROOT
    names: set[str] = set()

    d = glossary_dir(glossary_txt)
    if d.is_dir():
        names |= {p.stem for p in d.iterdir() if p.suffix == ".txt"}

    cdir = root / "courses"
    if cdir.is_dir():
        names |= {p.name for p in cdir.iterdir() if p.is_dir()}

    return sorted(n for n in names if n and not n.startswith("."))


def candidates(want: str, known: list[str]) -> list[str]:
    """所有匹配 `want` 的课程。**一条规则的两个视图**（`resolve` 由它派生，别各写一遍）。

    精确命中时只回它自己 —— 否则 `cl course ECON10740` 会把「另外几门含这串的」
    也列出来，而用户明明写全了。
    """
    want = (want or "").strip()
    if not want:
        return []
    if want in known:
        return [want]
    return [k for k in known if want in k]


def resolve(want: str, known: list[str]) -> str | None:
    """把用户输入的**片段**解析成规范课号；**歧义或无命中一律 `None`**。

    ⚠️ **歧义时必须拒绝，不能挑一个。** 原来 `cl` 的写法是
       `find -name "*$ARG*" | head -1` —— 实测 `cl course 107` 会**静默选中
       ECON10770**（因为它是 `head -1`），而那既不是「最接近」也不是用户的意图，
       后果是那节课用错术语表。`resolve` 对这种情况返回 `None`，让调用方去问人。
    ⚠️ 反过来，`cl course 1077` 这类**短号**必须能命中 `ECON10770` —— 用户习惯这么敲，
       而 `translator.course_terms_path` 的 `endswith` 规则对 `1077` 是**失败**的。
       片段匹配把这条补上。

    要区分「歧义」和「没找到」（报错文案不一样），用 `candidates()`。
    """
    c = candidates(want, known)
    return c[0] if len(c) == 1 else None


# ══════════════════════════════════════════════════════════════════════
# 准备度
# ══════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Readiness:
    """一张卡要显示的全部事实。

    **每个计数都允许是 `None` = 「未知」**（读失败），与 `0` = 「确实是零」分开。
    界面照这个区别显示（未知画 `—`，零画 `0`）。
    """
    course: str
    title: str                      # 术语表首行；没有时退回课号
    terms: int | None               # 术语条数
    materials: int | None           # 归档的课件份数
    auto_added: int | None          # prep 自动加过的词数（墓碑条数，**不是运行次数**）
    last_session: str | None        # 最后一次上课日期 `YYYY-MM-DD`；从没上过 → None


def _count_terms(path: pathlib.Path) -> int | None:
    """术语条数（跳过空行与 `#` 注释）。读不出 → None。"""
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return sum(1 for ln in text.splitlines()
               if ln.strip() and not ln.lstrip().startswith(_COMMENT))


def _title(path: pathlib.Path, course: str) -> str:
    """术语表首行（`#` 去掉）当课名；没有则退回课号。

    与 `translator.course_title` 同一口径 —— 那边是给模型当**领域先验**用的，
    这里只是显示，所以不共用同一个函数（它要 `glossary_path, course` 两个参数再自己找路径）。
    """
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s:
                return s.lstrip(_COMMENT).strip() or course
    except (OSError, UnicodeDecodeError):
        pass
    return course


def _count_materials(d: pathlib.Path) -> int | None:
    """归档的课件份数。⚠️ 只数文件，不递归 —— `materials/` 是我们的，结构已知。"""
    if not d.is_dir():
        return 0
    try:
        return sum(1 for p in d.iterdir() if p.is_file() and not p.name.startswith("."))
    except OSError:
        return None


def _count_auto_added(p: pathlib.Path) -> int | None:
    """墓碑条数。**读不出返回 None 而不是 0**（见 `prep._load_state` 的同一条理由）。"""
    if not p.exists():
        return 0
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    got = obj.get("appended") if isinstance(obj, dict) else None
    return len(got) if isinstance(got, dict) else None


def _session_course(stem: str) -> str | None:
    """`2026-09-10_140200_10730` → `10730`。形状不对 → None。

    ⚠️ 课号本身可能含 `_`（用户能写任意 `.course`），所以用 `split("_", 2)`：
       前两段固定是日期和时间，**剩下的整段**才是课号。
    """
    parts = stem.split("_", 2)
    return parts[2] if len(parts) == 3 else None


def last_session(sessions_dir, course: str) -> str | None:
    """这门课最后一次上课的日期。

    ⚠️ **必须容错匹配**（模块头第 1 条）：文件名里可能是短号 `10730`，
       而传进来的 `course` 是 `ECON10730`。
    ⚠️ `*_TEST.md` 之类的残留不在 `known` 里就自然被排除 ——
       所以这里按**课程名匹配**，不是按「所有文件」。
    """
    d = pathlib.Path(sessions_dir)
    if not d.is_dir():
        return None
    best: str | None = None
    try:
        entries = list(d.iterdir())
    except OSError:
        return None
    for p in entries:
        if p.suffix != ".md":
            continue
        c = _session_course(p.stem)
        if c is None:
            continue
        if not (course == c or course.endswith(c)):
            continue
        date = p.stem.split("_", 1)[0]
        if best is None or date > best:
            best = date
    return best


def readiness(glossary_txt, course: str, *, sessions_dir=None,
              state_root=None) -> Readiness:
    """算一张卡要显示的全部东西。**纯读，不写。**"""
    root = pathlib.Path(state_root) if state_root is not None else paths.STATE_ROOT
    g = glossary_dir(glossary_txt) / f"{course}.txt"
    return Readiness(
        course=course,
        title=_title(g, course),
        terms=_count_terms(g),
        # ⚠️ 布局一律走 `paths.*`（它的 `root=` 口子）——**别在这里再拼一遍**
        #    `root/"courses"/<课号>`：那是把布局定义成第二份，加个目录就要改 N 处。
        materials=_count_materials(paths.materials_dir(course, root=root)),
        auto_added=_count_auto_added(paths.prep_state(course, root=root)),
        last_session=last_session(sessions_dir, course) if sessions_dir else None,
    )


# ══════════════════════════════════════════════════════════════════════
# 给 `cl course` 用的入口
#
# ⚠️ **存在的理由：`cl` 不能自己再做一份匹配。** 它原来那套是
#    `find glossary -name "*$ARG*" | head -1` —— 实测 `cl course 107`
#    会静默选中 ECON10770（`head -1` 的产物，既不是「最接近」也不是用户意图），
#    后果是那节课用错术语表。规则只留上面 `candidates/resolve` 一份。
#
# 退出码是 bash 唯一读得动的东西，所以「歧义」与「没命中」用**不同退出码**分开：
#    0 = 唯一命中（stdout 是规范课号）
#    2 = 歧义（stderr 每行一个候选）
#    1 = 没命中
# ══════════════════════════════════════════════════════════════════════
def main(argv=None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="courses.py", description="课程清单与解析（给 cl 用）")
    ap.add_argument("--glossary", default="glossary.txt",
                    help="公共术语表路径；glossary/ 按它的父目录定位")
    ap.add_argument("--state-root", default=None,
                    help="课件与 prep 状态的根（默认 ~/.classlive）。"
                         "⚠️ 给了才能**隔离测试** —— 不给就会去读真实的 ~/.classlive，"
                         "于是测试结果绑在这台机器的当前状态上。")
    ap.add_argument("--list", action="store_true", help="列出全部课程")
    ap.add_argument("--current", default="", help="配合 --list：给当前课打星")
    ap.add_argument("--resolve", default=None, help="把片段解析成规范课号")
    a = ap.parse_args(argv)

    known = list_courses(a.glossary, state_root=a.state_root)

    if a.list:
        for k in known:
            print(f"{'*' if k == a.current else ' '} {k}")
        return 0

    if a.resolve is not None:
        hit = resolve(a.resolve, known)
        if hit:
            print(hit)
            return 0
        cands = candidates(a.resolve, known)
        if cands:
            print("\n".join(cands), file=sys.stderr)
            return 2
        return 1

    ap.print_help()
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
