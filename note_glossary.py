"""笔记知识点 → **术语库**（`glossary/<课号>.txt`）：只追加、走 prep 的锁与墓碑（step 3）。

作者的原话（这条是硬要求）：
> 笔记生成后，从笔记知识点里抽术语，只收：在本课转录或课件中真实出现过的、学科术语
> （过滤人名/常识词）。写入 `glossary/<课号>.txt`：**只追加**，必须走 `prep.py` 现有的
> `append_terms`（含锁与去重；作者删过的词不能加回来）。

## 「你删过」的机制怎么复用（查清后照抄，不新造）

`prep.prepare()` 落盘是**两步**（`prep.py` §⑤）：
1. `append_terms(glossary_path, picked)` —— 带**本课写入锁**（`prep._course_write_lock`）
   与去重（含行尾/编码的既有纪律）；
2. `_save_state(state_path, stamped)` —— 把**本次加进去的**写进
   `~/.classlive/courses/<课号>/prep-state.json` 的 `appended`。

那个 state 就是**墓碑**：`prepare()` 里 `if k in have or k in tomb: skip` ——
作者从术语表里删掉一行**不动 state**，于是重跑时它仍然撞墓碑、**不会复活**
（`prep.py:369-375` 有一段专门纠正方案里"删除也要进墓碑"的说法：不必）。

⚠️ 所以这里**必须两步都做**：只调 `append_terms` 而不写 state，等于把作者的删除
   变成"下一节课又回来"。反过来的失败方向（先写 state 再 append 失败）更坏 ——
   词被永久拉黑却从没出现过，所以顺序与 `prepare()` 一致：**glossary 先、state 后**。

⚠️ 本模块**只做抽取与落盘**，不做排序优化：候选来自笔记（= 本课真讲过的），
   本来就比"从 40 份课件里抽"精准得多 —— 那是 `prep` 那条路。
"""
from __future__ import annotations
import dataclasses


@dataclasses.dataclass(frozen=True)
class Outcome:
    """`added` 真写进去了、`skipped` 已经在表里/撞墓碑、`rejected` 被过滤器挡下（带原因）。"""
    added: list
    skipped: list
    rejected: list
    aborted: str = ""            # 非空 = 这次没写成（原因），**不是异常**

    def line(self) -> str:
        """收尾/笔记里那一行（用户看得见的那份）。"""
        if self.aborted:
            return f"💡 术语库没更新（{self.aborted}）"
        if not self.added:
            return f"💡 本课没有新术语进库（{len(self.skipped)} 条已在表里）"
        return f"💡 本课新加入术语库 {len(self.added)} 条：{' · '.join(self.added)}"


def _count(term: str, blob: str) -> int:
    """词面在语料里出现几次。

    ⚠️ 走 `build_notes.compile_term` —— 术语匹配的**唯一定义点**（允许多/复数、
       不允许子串命中）。这里再写一份正则就是"一条纪律两处定义"。
    """
    if not term or not blob:
        return 0
    try:
        from build_notes import compile_term
        return len(compile_term(term).findall(blob))
    except Exception:                                         # noqa: BLE001
        return blob.lower().count(term.lower())


def _blob_of(materials) -> str:
    """课件索引的全文（**唯一出处**：`Materials._blob`）。拿不到就是空串。"""
    return str(getattr(materials, "_blob", "") or "")


def candidates(review: dict, entries: list, *, materials=None, type_of=None) -> tuple:
    """复习层 + 转录 + 课件 → `(可入库的词, 被挡下的 [(词, 原因)])`。**纯函数**。

    ⭐ 作者第六轮第 4 条的**四条闸门**（逐条落地）：
      ① **出现过 ≥2 次**（转录 + 课件合计）：一次多半是顺口一说/噪声；
      ② **不是机构/平台/学位/职位/课型/日常词**（`note_terms.GENERIC` 那张小表）；
      ③ **学科词优先**：课件里出现过的排前面（课件是这门课的"教材"，它写下来的
         才是这门课要考的词）；
      ④ **每节上限 15 条**：入库是"只追加"的，宁缺勿滥（一次收 58 条、其中 34 条
         不是术语，下一节课的实时翻译就把这些噪声全量注入 prompt 了）。

    ⚠️ 与 `note_terms.drop_reason` 的分工：那张表管"是不是术语"（词形/语境），
       这里管"在这门课里成不成立"（出处次数、优先序、配额）。

    `type_of(词) -> "person"|"org"|…`（可选）：运行时查词缓存里记着类型的那些词
    （人名最常见）在这里就能被认出来 —— 否则只能靠"课上没出现过"顺带挡掉，
    而人名恰恰**经常**出现在课上（`Masha asked a good question`）。
    """
    import note_terms as nt
    terms: list = []
    for t in (review.get("terms") or []):
        if t.get("en"):
            terms.append(t["en"])
    for s in (review.get("sections") or []):
        for t in (s.get("terms") or []):
            if t.get("en"):
                terms.append(t["en"])
    transcript = "\n".join((e.get("en") or e.get("asr") or "") for e in entries)
    blob = _blob_of(materials)
    type_of = type_of or (lambda _t: "")
    keep: list = []
    rejected: list = []
    seen: set = set()
    for t in terms:
        t = " ".join(str(t).split())
        k = t.lower()
        if not t or k in seen:
            continue
        seen.add(k)
        why = nt.drop_reason(t, type_=type_of(t), source=nt.SRC_LECTURE)
        if why:
            rejected.append((t, why))
            continue
        n_t, n_m = _count(t, transcript), _count(t, blob)
        if n_t + n_m < nt.MIN_HITS:
            rejected.append((t, f"本课只出现过 {n_t + n_m} 次（要求 ≥{nt.MIN_HITS}）"))
            continue
        keep.append({"term": t, "hits": n_t + n_m, "in_materials": bool(n_m)})
    # ③ 学科词优先：课件里出现过 > 只在转录里出现；再按出现次数；同分保持原顺序
    keep.sort(key=lambda x: (0 if x["in_materials"] else 1, -x["hits"]))
    out = [x["term"] for x in keep[:nt.MAX_TERMS]]
    for x in keep[nt.MAX_TERMS:]:
        rejected.append((x["term"], f"超过每节 {nt.MAX_TERMS} 条上限"))
    return out, rejected


def deleted_terms(course: str, *, glossary_txt, state_path=None, glossary_path=None) -> set:
    """**作者删过**的词（小写集合）：墓碑里有、而表里已经没有。

    ⭐ 这是"删过的不复活"那条规矩的**第三种用法**（前两种：`prep.prepare` 选词、
    `note_glossary.append` 落表）—— 课上候选词池（`live_terms`）也归它管：
    作者从术语表里删掉一个词，就是想让它**整条链路**都别再出现。

    ⚠️ 墓碑**不等于**删过：它是"曾经过过表"。差别就是"表里还有没有这一行" ——
       少了这一步，一个正常还在表里的词会被当成"删过"，候选池白丢一批词。
    """
    import pathlib
    if glossary_path is None:
        try:
            import courses
            glossary_path = courses.glossary_file(glossary_txt, course)
        except Exception:                                     # noqa: BLE001
            return set()
    if state_path is None:
        try:
            import paths
            state_path = paths.prep_state(course)
        except Exception:                                     # noqa: BLE001
            return set()
    import prep
    try:
        tomb = {k.lower() for k in (prep._load_state(pathlib.Path(state_path)) or {})}
    except Exception:                                         # noqa: BLE001
        return set()
    if not tomb:
        return set()
    try:
        cur = {ln.strip().lower() for ln in
               pathlib.Path(glossary_path).read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.strip().startswith("#")}
    except Exception:                                         # noqa: BLE001
        return set()
    return tomb - cur


def append(terms: list, *, course: str, glossary_txt, state_path=None,
           glossary_path=None) -> Outcome:
    """把 `terms` 追加进本课术语表 + 写墓碑。**失败一律走 `Outcome.aborted`，不抛。**

    `glossary_path` 给定时直接用它（判据注入临时路径）；否则按课号定位
    （`courses.glossary_file` —— 与运行时加载的是**同一个答案**，别裸拼路径）。
    """
    if not terms or not course:
        return Outcome(added=[], skipped=[], rejected=[])
    import prep
    import pathlib
    if glossary_path is None:
        try:
            import courses
            glossary_path = courses.glossary_file(glossary_txt, course)
        except Exception as e:                                # noqa: BLE001
            return Outcome([], [], [], aborted=f"定位术语表失败 {type(e).__name__}")
    path = pathlib.Path(glossary_path)
    # state（墓碑）默认在 `~/.classlive/courses/<课号>/prep-state.json`。
    if state_path is None:
        try:
            import paths
            state_path = paths.prep_state(course)
        except Exception:                                     # noqa: BLE001
            state_path = path.with_name(path.stem + ".prep-state.json")
    state_path = pathlib.Path(state_path)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    lock_path = prep.state_lock_path(state_path)
    import instance_lock
    _state0, _ = instance_lock.probe(lock_path)
    if _state0 == "held":
        return Outcome([], [], [], aborted="另一个 prep/面板正在写这门课的术语表")
    lock, _holder = instance_lock.acquire(lock_path)
    if lock is None and _state0 != "unknown":
        return Outcome([], [], [], aborted="拿不到本课术语表写入锁")
    try:
        # ⚠️⚠️ **墓碑先读**（`prep.prepare()` 里那一步的等价物）：state 里记着
        #    "prep/笔记曾经加过哪些词"，作者删掉表里那一行**不动 state** ——
        #    所以撞墓碑的词必须在**进 `append_terms` 之前**挡下，否则就复活了。
        #    （2026-10-17 实测：漏了这一步，`test_墓碑_删过的词不会复活` 当场红。）
        tomb: set = set()
        state = None
        try:
            state = prep._load_state(state_path)          # 读不出 -> None（别覆盖）
            tomb = {k.lower() for k in (state or {})}
        except Exception:                                 # noqa: BLE001
            state, tomb = None, set()
        asked = [t for t in terms if t.strip()]
        fresh = [t for t in asked if t.strip().lower() not in tomb]
        blocked = [t for t in asked if t.strip().lower() in tomb]
        if not fresh:
            return Outcome(added=[], skipped=blocked, rejected=[])
        try:
            ar = prep.append_terms(path, fresh)
        except prep.GlossaryError as e:
            return Outcome([], blocked, [], aborted=str(e)[:80])
        except Exception as e:                            # noqa: BLE001
            # 只读目录 / 磁盘满 / 路径不是文件 …… 一律不进笔记，但**出声**。
            return Outcome([], blocked, [],
                           aborted=f"{type(e).__name__}: {str(e)[:60]}")
        # 墓碑：与 `prepare()` 同序（glossary 先、state 后），只记本次**真加进去**的。
        if ar.added:
            try:
                if state is None or not isinstance(state, dict):
                    state = prep._load_state(state_path)
                if state is None:
                    print(f"⚠ {state_path.name} 读不出 —— 本次**不覆盖**它"
                          f"（删过的词这次可能被重新加回）")
                else:
                    import datetime
                    stamped = dict(state)
                    today = datetime.date.today().isoformat()
                    for t in ar.added:
                        stamped[t] = today
                    prep._save_state(state_path, stamped)
            except Exception as e:                             # noqa: BLE001
                print(f"⚠ 术语库状态没写成（{type(e).__name__}）—— 删过的词可能被加回来")
        return Outcome(added=list(ar.added), skipped=blocked + list(ar.skipped_dup),
                       rejected=[(t, "超过词数上限") for t in ar.skipped_long])
    finally:
        instance_lock.release(lock)
