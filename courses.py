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

⚠️ **唯一一处例外是 `create()`（2026-09-28 起）**：它真的建文件。放这里是因为
「一门课的身份 = `glossary/<课号>.txt`」这件事只有本模块知道（`glossary_file` /
`list_courses` 都建在它上面）—— 让界面自己去拼那个路径，就等于把这条知识抄第二份。
其余函数一律**只读**。
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


def glossary_file(glossary_txt, course: str) -> pathlib.Path:
    """`glossary/<课号>.txt` —— **容错解析的唯一入口**（与运行时加载的是同一个答案）。

    ⚠️ **别裸 join** `glossary_dir(...) / f"{course}.txt"`。`.course` 里存短代号是
       **真会发生**的（`cl` 那条兜底分支就是把用户原样输入写进去），而术语表是全名。
       裸 join 会指到一个 translator **永远不加载**的文件上，偏偏**两边都报成功** ——
       面板上那张卡于是显示「**0 条术语**」，正是模块头口径 3 要防的谎报。

    ⚠️ 算法**不在这里**，在 `translator.course_terms_path`（运行时用的就是它）。
       这里只做「延迟导入 + 兜底」，**绝不另写一套** —— 两套迟早漂移，而漂移是静默的。
    """
    from translator import course_terms_path
    public = pathlib.Path(glossary_txt)
    found = course_terms_path(str(public), course)
    if found is not None:
        return found
    return public.parent / "glossary" / f"{course}.txt"


# ══════════════════════════════════════════════════════════════════════
# 清单
# ══════════════════════════════════════════════════════════════════════
def list_courses(glossary_txt, *, state_root=None) -> list[str]:
    """两边的**并集**（见模块头）。排序稳定，供界面直接铺。

    ⚠️ **合并之前先把状态侧的目录名对账到规范课号。** 两边记的可能是同一门课的
       两个写法：`.course` 里存短代号时，`~/.classlive/courses/10740/` 与
       `glossary/ECON10740.txt` 说的是同一件事。不对账 → 面板上出**两张同一门课的卡**，
       而短代号那张按错误路径算准备度 → 报「0 条术语」（谎报）。
       对账走的是与运行时**同一套**容错（`glossary_file`），不是第二套算法。
    """
    root = pathlib.Path(state_root) if state_root is not None else paths.STATE_ROOT
    names: set[str] = set()

    d = glossary_dir(glossary_txt)
    if d.is_dir():
        names |= {p.stem for p in d.iterdir() if p.suffix == ".txt"}

    cdir = root / "courses"
    if cdir.is_dir():
        for p in cdir.iterdir():
            if p.is_dir():
                names.add(canonical_course(glossary_txt, p.name))

    return sorted(n for n in names if n and not n.startswith("."))


def canonical_course(glossary_txt, name: str) -> str:
    """状态侧目录名 → 规范课号。**唯一命中才归并**，有歧义就保留原名。

    ⚠️ **宁可多一张卡，也不要把两门课并成一门** —— 并错了是**静默用错术语表**，
       比多一张卡糟得多（同 `resolve` 那条「歧义就拒绝」的取向）。
    """
    g = glossary_file(glossary_txt, name)
    return g.stem if g.exists() and g.stem != name else name


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
# 新增课程（2026-09-28）
#
# 面板上那个「＋ 新增课程」的全部判断都在这里，`entry_panel` 只负责画。
# 分开的理由与上面 `resolve` 同一条：**规则只留一份**，谁调都一样。
# ══════════════════════════════════════════════════════════════════════
#: 课号长度上限。⚠️ 不是为了好看：macOS 单个文件名的上限是 255 **字节**，
#: 而中文课号一个字 3 字节 —— 不设限的话，粘错一整段话会得到一个
#: 「写到一半 ENAMETOOLONG」的半截文件。60 是个宽到不可能挡人的数。
MAX_CODE = 60

#: 不能出现在课号里的字符 → 人话原因。
#: ⚠️ 判据是**两件事**：① 能不能安全地变成一个文件名；② 建完能不能在面板上看见。
#: `:` 在 APFS 上合法，但 Finder 把它**显示成 `/`** → 用户看到的文件名和真名不是一个。
_FORBIDDEN = {"/": "斜杠", "\\": "反斜杠", ":": "冒号（Finder 会把它显示成斜杠）",
              "\x00": "空字符", "\n": "换行", "\t": "制表符"}


def valid_code(want: str) -> str:
    """课号能不能当文件名。返回 `""` = 可以；否则**一句人话**说明为什么不行。

    ⚠️ 纯函数，不碰磁盘 —— 判据全在 `tests/test_courses.py`。
    """
    w = (want or "").strip()
    if not w:
        return "先输一个课号"
    if len(w) > MAX_CODE:
        return f"太长了（{len(w)} 个字）—— 课号一般不超过 {MAX_CODE} 个字"
    for ch, why in _FORBIDDEN.items():
        if ch in w:
            return f"课号里不能有{why}"
    if w.startswith("."):
        # ⚠️ 不是洁癖：`list_courses` 明确跳过 `.` 开头的名字（那是隐藏文件），
        #    所以以点开头建出来的课**面板上永远不出现** —— 用户会以为没建成。
        return "课号不能以点开头 —— 那样建出来是隐藏文件，面板上看不见"
    return ""


def plan_add(want: str, known) -> dict:
    """输入 → **该干什么**。纯函数（界面只负责把 `text` 画出来）。

    返回 `{"action", "course", "hits", "text"}`，`action` 四选一：

    | action | 意思 | 界面该做什么 |
    |---|---|---|
    | `bad` | 这个课号当不了文件名 | 说 `text`，**别建** |
    | `exists` | 已经有了（`course` 是规范课号） | 说 `text`，**别重复建** |
    | `pick` | 片段命中多门（`hits` 是候选） | 说 `text`，让他写全 |
    | `create` | 建它 | 走 `create()` |

    ⚠️ ⭐ **`exists` / `pick` 两档存在的理由与 `resolve` 那条一致**：`resolve` 遇歧义
       返回 `None` 是**拒绝猜**。这里更进一步 —— 它把「你输的其实已经有了」
       也拦下来（`resolve` 会把它解析成那门课，于是调用方以为要新建、实际重名）。
       后果不是"少建一门课"，是**两门课共用一份术语表**：静默用错术语表，
       正是本模块最怕的那类失败。
    """
    w = (want or "").strip()
    why = valid_code(w)
    if why:
        return {"action": "bad", "course": "", "hits": [], "text": why}
    hits = candidates(w, list(known))
    if len(hits) == 1:
        return {"action": "exists", "course": hits[0], "hits": hits,
                "text": f"已经有这门课了：{hits[0]} —— 没重复建"}
    if hits:
        # ⚠️ 候选**只列前 3 门**：这一行在面板底部那条上，宽度有限、换行会被吃掉。
        #    多出来的用计数说清楚 —— 别让一个截断的列表读起来像"就这些"。
        shown = " · ".join(hits[:3])
        more = f" 等 {len(hits)} 门" if len(hits) > 3 else ""
        return {"action": "pick", "course": "", "hits": hits,
                "text": f"{len(hits)} 门课都含「{w}」：{shown}{more} —— 写全一点"}
    return {"action": "create", "course": w, "hits": [], "text": f"建 {w}"}


def create(path, course: str) -> bool:
    """建一门新课的术语表。返回**有没有真的建**。

    ⚠️ **写路径是参数，不由本函数算** —— 同 `prep.append_terms` 那条纪律
       （`term_notes.json` 从 41KB 被写成 4.8KB 那次换来的）：调用方给什么路径就写什么。
       生产里那个路径是 `prep.course_glossary_path(...)` 算的，**与 prep 写词时同一个答案**。

    ⚠️⚠️ **只建不覆盖。** 文件已存在就**一个字都不动**、返回 `False`。
       这不只是防手滑：`plan_add` 判完到真写之间隔着一次界面往返，用户完全可能
       在别处（`cl course`、Finder）已经把课建好了 —— 那时**覆盖就等于删掉他刚写的东西**。

    ⚠️ 首行写 `# <课号>` 是**既有约定**（`prep.append_terms` 建新课时逐字写的就是它）。
       它同时是卡片标题的来源（`_title`）与模型判领域的先验，所以**不猜课名** ——
       猜错比空着更糟（同 `prep.append_terms` 的注释）。
    """
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        # ⚠️ **排他创建**（`"x"`）而不是 `exists()` + `write_text`：后者两步之间留着窗口，
        #    别处（Finder / `cl` / 另一个进程）恰好在这一瞬间建好文件的话，
        #    `write_text` 会**直接截断覆盖** —— 正是上面那段要防的事。
        #    `"x"` 把这个判断交给内核，没有窗口（2026-09-28 审查指出）。
        with p.open("x", encoding="utf-8") as fh:
            fh.write(f"# {course}\n")
    except FileExistsError:
        return False
    return True


# ══════════════════════════════════════════════════════════════════════
# 删除一门课（2026-09-28）
# ══════════════════════════════════════════════════════════════════════
def facts(glossary_txt, course, *, sessions_dir=None, state_root=None) -> dict:
    """确认框要说的那几件事：**这门课到底有什么**。纯读，不写任何东西。

    ⭐ 存在的理由：删除是**少见且破坏性**的动作，而我们的「课」由两个互不相干的
       半边拼成（见模块头那张表）。用户有权在点之前知道**具体会动什么** ——
       `[一手]` Apple 支持文档《Delete or uninstall apps on Mac》的口径正是这个：
       当删除会波及「other data the app might have stored in other locations」时，
       要**提供一个统一的入口把它说清楚**，而不是让用户自己去各处清。

    ⚠️ **`sessions` 只是"报出来"，不是"会被删"** —— 上课记录是历史，见 `delete()`。
    """
    root = pathlib.Path(state_root) if state_root is not None else paths.STATE_ROOT
    g = glossary_file(glossary_txt, course)
    mats = paths.materials_dir(course, root=root)
    # ⚠️ 走 `_count_materials`（本模块那份唯一定义），**不在这里再数一遍**。
    #    原来这里自己数，三处与它不一致：读失败降级成 `0`（口径 3 要求 `None`）、
    #    `.DS_Store` 被算成一份课件、以及"同一件事两份实现"。
    #    而这里是**删除确认框**的数字 —— `0` 会让用户在破坏性操作前
    #    以为"没有课件会被动"（2026-09-28 审查指出）。
    n_mat = _count_materials(mats)
    try:
        n_bytes = g.stat().st_size if g.exists() else 0
    except OSError:
        n_bytes = None          # ⚠️ 同上：读不出是「未知」，不是「0 字节」
    return {"glossary": g, "glossary_bytes": n_bytes,
            "course_dir": paths.course_dir(course, root=root), "materials": n_mat,
            "sessions": len(session_files(sessions_dir, course))}


#: 日志最多留这么多条。⚠️ 它会**一直长**（一学期几百条），而用途只是
#: 「看看准了多少 / 攒 few-shot 例子」—— 旧的几十条价值一样，所以砍尾不砍头。
MAX_CORRECTIONS = 500


def record_batch(pairs, *, root=None, ai=None, at=None) -> int:
    """把一批「文件 → 用户最终认定的课号」记下来。返回追加了几条。

    `pairs` = `[(路径, 课号)]`（`_confirm_batch` 里现成的那个）。
    `ai` = `{路径: 模型当时说的课号}` —— ⭐ **有它才算得出"改对了几条"**。

    ⚠️⚠️ **只记录，不训练、不影响分类。** 它先当**度量**用：
       「上了转录语料之后到底准了多少」—— 没有它，"要不要继续投入"只能靠感觉
       （同 `voice.DEFAULT_THRESHOLD` 那条「零背书」：**没有量就别定数**）。
    ⚠️ **本函数不判断"哪些才算标注"** —— 那是「未分类不排队」那条规矩的事，
       定义在 `entry_panel.group_for_archive`（唯一一处）。调用方传进来的应该是
       **真的会归档的那些**。（这里只挡空课号这一种明显无意义的行。）
    ⚠️ 参数名不叫 `store` —— 那是模块名，会**静默遮蔽**（`voice.save_store` 栽过）。
    """
    import time
    import paths
    import store
    rows = []
    for path, course in pairs:
        if not course:
            continue                              # 「未分类」不是标注
        rows.append({"path": str(path), "course": str(course),
                     "ai": (ai or {}).get(str(path)) or None,
                     "at": at if at is not None else time.time()})
    if not rows:
        return 0
    p = paths.corrections_log(root=root)
    try:
        old = store.load_json(p, default={})
    except store.StoreError:
        # ⚠️ 读不出来时**不覆盖**：那本日志是唯一一份（同 `voice.load_store` 那条）。
        raise
    # ⚠️ **日志是 `{"rows": [...]}`，不是裸 list** —— `store.save_json` 靠
    #    `{_v: 1, **obj}` 盖版本号，那个展开**只对 dict 成立**（第一版塞了 list
    #    进去，当场 TypeError）。附带好处：这本日志也就有了版本约定。
    prev = (old or {}).get("rows") if isinstance(old, dict) else None
    store.save_json(p, {"rows": (list(prev or []) + rows)[-MAX_CORRECTIONS:]})
    return len(rows)


def corrections(*, root=None) -> list:
    """读回那本日志。**读不出来返回空表、不抛**（它丢了不影响上课）。"""
    import paths
    import store
    try:
        got = store.load_json(paths.corrections_log(root=root), default={})
    except store.StoreError:
        return []
    rows = (got or {}).get("rows") if isinstance(got, dict) else None
    return list(rows) if isinstance(rows, list) else []


def delete(glossary_txt, course, *, sessions_dir=None, state_root=None,
           keep_materials: bool = False, trash_fn=None) -> dict:
    """删一门课。⭐ **这是全项目唯一会删东西的入口。**

    | `keep_materials` | 术语表 | 课程目录（课件 + prep 状态）|
    |---|---|---|
    | `False`（**全部删除**）| → 废纸篓 | → 废纸篓 |
    | `True`（**只删课号**）| → 废纸篓 | → **搬进保留区**（`paths.removed_dir`）|

    ⭐ 「只删课号」为什么要**搬**而不是留着：面板上的课是
       `glossary/*.txt` **∪** `~/.classlive/courses/*/` 的并集 ——
       只删术语表的话**卡片不会消失**，只会变成一张「0 条术语」的空卡。
       搬进点开头的保留区，`list_courses` 就看不见它了，而课件还在磁盘上。

    ⚠️⚠️ **`sessions/` 一个字节都不碰。** 上课记录是**历史**，不是课程的一部分 ——
       它记的是"那节课发生过"。后果（**界面上必须说出来**）：删了课再建同名课，
       历史会**自己接回来**（`session_files` 按课号后缀匹配）。
       业界三种解法（墓碑 / 改名换 ID / 唯一约束算进软删记录），**我们都不做**。

    ⚠️ 删路径 / 写路径**全由 `paths.*` 与 `glossary_file` 算**，不在这里手拼。
    ⚠️ **幂等**：东西本来就不在 → 照常返回，不算错误（`trash_fn` 自己也幂等）。

    `trash_fn(path) -> (ok, why)` 是**验收注入点**（同 `prep.prepare` 的 `chat=`）——
       判据**不能真往用户的废纸篓里扔东西**。

    返回 `{"facts", "trashed": [路径…], "kept": 路径|None, "errors": [人话…]}`。
    """
    if trash_fn is None:
        import trash as trash_mod
        trash_fn = trash_mod.to_trash
    f = facts(glossary_txt, course, sessions_dir=sessions_dir, state_root=state_root)
    out = {"facts": f, "trashed": [], "kept": None, "errors": []}

    def _burn(p) -> None:
        ok, why = trash_fn(p)
        if ok:
            out["trashed"].append(str(p))
        else:
            out["errors"].append(f"{pathlib.Path(p).name}：{why}")

    if f["glossary"].exists():
        _burn(f["glossary"])

    if f["course_dir"].is_dir():
        if keep_materials:
            dest = paths.removed_dir(root=state_root) / course
            # ⚠️ 保留区里已有同名就加 `-2` 后缀 —— 与 `prep._archive` **同一套约定**：
            #    **绝不覆盖**（覆盖 = 静默丢掉上一次搬走的那批课件）。
            n = 2
            while dest.exists():
                dest = paths.removed_dir(root=state_root) / f"{course}-{n}"
                n += 1
            try:
                import shutil
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f["course_dir"]), str(dest))
                out["kept"] = str(dest)
            except OSError as e:
                out["errors"].append(f"课件搬不动：{e}")
        else:
            _burn(f["course_dir"])
    return out


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
    ⚠️⚠️ **空课号段（`…_140200_.md`）也当"形状不对"。** 放它过去的话
       `course.endswith("")` **恒为真** → 这个文件被算进**每一门课**：
       `facts()` 的节数虚高、`readiness.last_session` 还会把它当成那门课的最后一次上课。
       （2026-09-28 审查指出；当天真实 `sessions/` 里实测 **0 个**这种文件，
       但录课中断 / 测试残留正是这么长出来的。`find.py` 共用本函数，一并受益。）
    """
    parts = stem.split("_", 2)
    return (parts[2] or None) if len(parts) == 3 else None


#: 「这节课其实属于哪门课」。⚠️ **住在 `sessions/` 里，不住 `~/.classlive/`** ——
#: 它描述的就是那些会话，放在旁边才自洽；而且**临时目录天然隔离**
#: （判据不用额外做任何事）。同 `.lost.jsonl` / `.atoms.jsonl` 的成例：
#: 旁路文件，**绝不改会话抬头**。
ATTRIBUTION_NAME = ".attribution.json"

#: 用户显式声明「这节**不属于任何课**」。
#:
#: ⚠️ 为什么需要它（2026-09-30 实测，不是假想）：**短号会被两门课共用** ——
#:    作者在工程学院时录过一节课朋友的课（热力学），文件名落成
#:    `2026-09-11_140757_10730.md`，而 `course_matches` 的
#:    `course.endswith(c)` 把它算成了 **`ECON10730`**。
#:    后果是活的：那节挖出的 `force` / `pressure` / `mathematically`
#:    进了 `corpus.keywords`，被当候选术语**注入 ECON10730 的翻译 prompt**。
#: ⚠️ 它必须是**一个不可能撞上真课号的串**（`endswith` 判据下，括号开头就够）；
#:    但**判据不能靠这个巧合** —— `course_matches` 里有一条显式守门。
NO_COURSE = "(不属于任何课)"


def attribution_path(sessions_dir) -> pathlib.Path:
    return pathlib.Path(sessions_dir) / ATTRIBUTION_NAME


def attribution_map(sessions_dir) -> dict:
    """`{会话 stem: 课号}` —— 用户手改过的归属。**只读**，读不出当空。

    ⚠️ 录课时没设课号会落成 `LECTURE` / `ECON10xxx` 这种**占位符**，
       而文件名一旦写下就不再改（会话抬头是三方共享契约）。实测作者的
       `sessions/` 里躺着 8 个 `LECTURE` + 1 个 2897 行的 `ECON10xxx`。
    """
    import store
    if sessions_dir is None:
        return {}
    got = store.load_json(attribution_path(sessions_dir), {})
    return got if isinstance(got, dict) else {}


def set_attribution(sessions_dir, stem: str, course) -> dict:
    """把一节课判给某门课；`course=None` = 撤销，回到按文件名认。返回改完的表。

    ⚠️⚠️ **只写这个旁路文件，绝不碰会话 `.md`。**
       会话抬头是**三方共享契约**（`obsidian_writer` 写 / `_parse` 读回 /
       `cl last` grep），改一个后缀 `_parse` 就认不出那一条，
       会把 EN/ZH/ASR **静默盖到上一条头上**（`obsidian_writer` 文件头逐字记着）。
    ⚠️ 撤销是**删键**而不是写空串 —— 空串会让「改过」和「没改过」长得一样。
    ⚠️ `course` 传 **`NO_COURSE`** 是合法用法：那是「这节不属于任何课」，
       **不等于**「没改过」（后者要靠上面的 `None` 撤销）。
    """
    import store
    m = {k: v for k, v in attribution_map(sessions_dir).items() if k != store.K}
    if course:
        m[stem] = str(course)
    else:
        m.pop(stem, None)
    store.save_json(attribution_path(sessions_dir), m)
    return m


def course_matches(c: str, course: str) -> bool:
    """文件名里那个课号 `c` 与课程名 `course` 是不是同一门。**匹配规则只此一处。**

    ⚠️ **必须容错**（模块头第 1 条）：文件名里可能是**短号** `10730`，
       而传进来的 `course` 是 `ECON10730`。
    ⚠️ 抽出来是因为 `session_files` 与 `orphan_files` **都要这一条** ——
       2026-09-29 之前 `orphan_files` 用的是精确集合判定（`c in known`），
       于是短号文件**同时出现在两组列表里**（被算进这门课的 `rows`，
       又被当成"没归课"的孤儿），还会诱使用户把它「判给」错误的课。
       两处规则不一致的教训同 `facts`/`last_session` 那条：**各写一遍迟早分叉**。
    ⚠️ `NO_COURSE`（用户显式排除）在这里**立刻返回 False**，不是靠"碰巧不像"——
       否则哪天课号真叫那个串就会被重新算进来。
    """
    if c == NO_COURSE:
        return False
    return course == c or course.endswith(c)


def orphan_files(sessions_dir, known) -> list:
    """**不属于任何已知课程**的上课记录 —— 录课时没设课号留下的占位符。

    ⚠️ 判据是「**名字不在已知课表里**」，**不是**「等于 `LECTURE`」——
       占位符是任意字符串。实测作者手里有三种：`LECTURE`（8 节）、
       `ECON10xxx`（2 节），还有一份抬头直接写着 `# None`。
    ⚠️ 只在**文件名**上判；用户已经判过课的（`attribution_map`）不算孤儿。
    ⚠️ 与 `session_files` 走**同一条匹配规则**（`course_matches`）——
       不然同一节课会同时出现在两组列表里。
    """
    if sessions_dir is None:
        return []
    d = pathlib.Path(sessions_dir)
    if not d.is_dir():
        return []
    known = list(known or ())
    amap = attribution_map(d)
    try:
        entries = sorted(d.iterdir())
    except OSError:
        # ⚠️ 与 `session_files` 同款兜底：目录存在、`is_dir()` 通过，
        #    并不保证随后 `iterdir()` 成功（权限 / 读盘途中被删）。
        return []
    out = []
    for p in entries:
        if p.suffix != ".md":
            continue
        c = amap.get(p.stem) or _session_course(p.stem)
        if c == NO_COURSE:
            # ⚠️ 用户**显式排除**的**不算孤儿** —— 它出现在「没归课的上课记录」那一组里，
            #    就等于在诱他再点一次「判给本课」（那正是他要避免的）。
            continue
        if c is None or any(course_matches(c, k) for k in known):
            continue
        out.append(p)
    return out


def session_files(sessions_dir, course: str) -> list:
    """这门课在 `sessions/` 下的**上课记录**文件。**匹配规则只此一处。**

    ⚠️ **必须容错匹配**（模块头第 1 条）：文件名里可能是短号 `10730`，
       而传进来的 `course` 是 `ECON10730`。
    ⚠️ `*_TEST.md` 之类的残留不在 `known` 里就自然被排除 ——
       所以这里按**课程名匹配**，不是按「所有文件」。

    ⭐ 抽出来给两个视图共用：`last_session`（要最新那个日期）与
       `facts`（确认框要报「几节」）。**它们是同一条规则的两个视图** ——
       各写一遍迟早一个算 46、一个算 47。

    ⭐⭐ **用户指定优先于文件名**（`attribution_map`）：录课时没设课号会落成
       `LECTURE` / `ECON10xxx` 这种占位符，而文件名**一旦写下就不再改**
       （抬头是三方共享契约）→ 那是**占位符，不是事实**。
    """
    if sessions_dir is None:
        return []
    d = pathlib.Path(sessions_dir)
    if not d.is_dir():
        return []
    try:
        entries = list(d.iterdir())
    except OSError:
        return []
    amap = attribution_map(d)          # ⚠️ 读一次，别在循环里逐文件读盘
    out = []
    for p in entries:
        if p.suffix != ".md":
            continue
        c = amap.get(p.stem) or _session_course(p.stem)
        if c is None:
            continue
        if course_matches(c, course):
            out.append(p)
    return out


def last_session(sessions_dir, course: str) -> str | None:
    """这门课最后一次上课的日期。**规则见 `session_files`，这里只取最大。**"""
    dates = [p.stem.split("_", 1)[0] for p in session_files(sessions_dir, course)]
    return max(dates) if dates else None


def readiness(glossary_txt, course: str, *, sessions_dir=None,
              state_root=None) -> Readiness:
    """算一张卡要显示的全部东西。**纯读，不写。**"""
    root = pathlib.Path(state_root) if state_root is not None else paths.STATE_ROOT
    # ⚠️ 走 `glossary_file()` 而不是裸 join —— 见它的 docstring（短代号会谎报 0 条术语）
    g = glossary_file(glossary_txt, course)
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
