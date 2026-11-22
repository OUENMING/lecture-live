"""网页版 Python 核心的防漂移闸门 + 切片生成器（唯一定义点）。

对外接口：`--check` 有漂移非零退出，`--write` 刷成桌面版；可选 `--desktop` / `--core`。
两类东西：`WHOLE_FILES` 是整文件原样拷贝（字节级），`SLICES` 里的几份是从桌面源
**按名字**切出来的（ast 找顶层定义与赋值的起止行，含装饰器与紧贴的 `#:` 注释块）：
`stream_core.py` ← `stream_asr.py`（实时核心）、`extract.py` ← `extract.py`（PPTX/DOCX 抽块）、
`prep.py` ← `prep.py`（候选术语的提示词与判据）。切片清单就是 `SLICES` 那张表。

⚠️ **临时来源（W10，2026-11-17）**：`prompt_core.py` 今天只存在于桌面 `lecture-live-prompt`
分支（合进 `main` 之前），所以 `TEMPORARY_FROM` 让它从 `/Users/owen/lecture-live-prompt` 取，
其余文件仍从 `--desktop` 指的仓库取。该分支合进 `main` 后把表清空即可 —— 那时它自然回落到
`--desktop`。表里只写「文件名 → 别的仓库路径」，别在这里加第二个 `desktop` 参数。
为什么按名字：从前 `classlive-web-spike/s1/tools/extract.py` 按行号切，桌面一动行号就全错；
名字改了就报出来，比静默切错好。import 只保留切片真正用到的（从桌面源自己的 import 原文里挑）。
"""
from __future__ import annotations

import argparse
import ast
import builtins
import difflib
import sys
import typing
from pathlib import Path

#: 与桌面仓库同名同内容、逐字节原样拷贝的文件（相对各自仓库根）。
#: rt.py 是网页版胶水、stream_core.py 是切片，都不在这里。
WHOLE_FILES = (
    "caption_flow.py",
    "draft_policy.py",
    "draft_stable.py",
    "draft_translate.py",
    "fragment_merge.py",
    "linebreak.py",
    "prompt_core.py",
    "speaker_label.py",
    "text_diff.py",
    "vendor/budoux/zh-hans.json",
)
#: 暂时只在别的仓库里才有的整文件拷贝（文件名 → 那个仓库根）。清空即恢复「全从 --desktop 取」。
#: ⚠️ 桌面 `lecture-live-prompt` 的这些文件与 `lecture-live` 逐字节相同（除新增的 prompt_core.py）——
#: 一旦那个分支合进 main，删掉这一行，`--check` 会自然盯着 `--desktop`。
TEMPORARY_FROM = {
    "prompt_core.py": "/Users/owen/lecture-live-prompt",
}
#: 切片之间的空档里只留这类「分区横幅」注释（其余注释是别的定义的文档，别带进来）。
BANNER = ("# ═", "# ─")


#: 一份切片要什么：`web` 与桌面同名时（`extract.py` / `prep.py`），桌面源码里
#: `from extract import KIND_MEANING` 这类内部 import 在浏览器里**自然成立** —— 少一层间接，
#: 也就少一处会漂的地方。`header` 写清「剥掉了什么」，读切片的人不必回头翻桌面源。
class Slice(typing.NamedTuple):
    web: str                     # 网页版文件名（core-py 下）
    desktop: str                 # 桌面版文件名
    names: tuple                 # 取哪些顶层名字（顺序即输出顺序）
    header: str                  # 生成文件的文件头
    no_leading_comment: tuple = ()


STREAM_HEADER = '''"""桌面 `stream_asr.py` 的纯逻辑切片，**由 `web/scripts/sync-core.py` 按名字生成，勿手改**。

从源文件里只剥掉了「包在切片外面」的东西，切片内部的代码一个字没动（`--check` 逐字节钉着）：

- `import numpy as np` —— 只被 `_pcts`（封口延迟量具）用，量具不在切片内。
- `import os` / `import urllib.parse` —— 只被 `endpoint()` / `wanted()` 用（读环境变量，浏览器没有）。
- `import queue` / `import threading` / `import random` —— 收线程、重连退避、keepalive
  都在第二层（传输），切片里没有。
- `import asr_endpoint` / `import final_asr` —— 领 key 与云端定稿模块，纯网络依赖。
- 整个「第二层：传输」：`StreamASR`（线程 + websockets + 计时器）、`_KeyClient`（httpx）、
  `side_probe`（socket）、`metrics` / `no_token_stalled` / `seal_latency_s`（量具，numpy）。
- `wanted()` / `notice_for()` / `endpoint()`：读环境变量 + 拼 URL，浏览器里没有意义。

保留下来的 import 是脚本从桌面源里**按切片实际用到的名字**挑的原文（`json` / `re` / `time` /
`collections` / `typing` 这一类），桌面新增依赖时不用手改这里。
"""'''

#: 这两个名字的 `#:` 注释不带进切片 —— S1 当时就是从定义行开始切的，照旧保留这条锚点，
#: 好处是生成器随时能拿 S1 的 `core/stream_core.py` 逐字节对照（证明它没改字）。
NO_LEADING_COMMENT = ("STABLE_S", "_DOMAINS")

STREAM_SLICE = Slice("stream_core.py", "stream_asr.py", (
    "STABLE_S",
    "CONTEXT_CHAR_BUDGET",
    "TERM_MAX_CHARS",
    "GENERAL_KEYS_MAX",
    "GENERAL_VALUE_MAX",
    "CONTEXT_TEXT_MAX",
    "TERMS_MAX",
    "_CONTEXT_SERIAL_MARGIN",
    "SPLIT_ON_SPEAKER",
    "END_PUNCT",
    "CONTROL",
    "_is_end",
    "Tok",
    "Sealed",
    "_HAN",
    "_sent_lang",
    "_sent_speaker",
    "parse_tokens",
    "StreamCore",
    "_DOMAINS",
    "_domain_of",
    "general_pairs",
    "build_context",
), STREAM_HEADER, no_leading_comment=NO_LEADING_COMMENT)

EXTRACT_HEADER = '''"""桌面 `extract.py` 的 **PPTX / DOCX** 那条路，**由 `web/scripts/sync-core.py` 按名字生成，勿手改**。

只切「纯 `zipfile` + 标准库 XML」那半：`_slide_blocks` / `_paragraphs`（PPTX，含表格与占位符口径）
与 `_docx_blocks` / `_w_para` / `_w_paras`（DOCX）。两种情况的口径都照桌面，一个字没改。

没带进来（`--check` 不覆盖它们的漂移，改桌面这几个名字不会让这里变红）：

- `_pdf_blocks` / `_pdf_paragraphs` / `_ensure_vision` / `_ocr_pdf_page` —— PDFKit + Vision，
  浏览器里没有。网页版的 PDF 走 `web/src/lib/prep/pdf.ts`（pdf.js），产出的块与 `Block` 同形。
- `is_supported` / `expand` / `extract`（磁盘上的路径与进度条，网页版在 TS 里装配）。

所以网页版的 PDF 与 PPTX **不是同一条实现**：PPTX/DOCX 与桌面逐块相同（有 CPython 对照测试），
PDF 只是「形状相同」。`--check` 只能盯住这里切出来的部分，PDF 那条没法用 Python 钉。
"""'''

EXTRACT_SLICE = Slice("extract.py", "extract.py", (
    "BLOCK_KINDS",
    "_PH_TEXT",
    "_PH_EXCLUDED",
    "_NS_A",
    "_NS_P",
    "_NS_W",
    "_SLIDE_RE",
    "_NOTES_RE",
    "KIND_MEANING",
    "_numbered",
    "Block",
    "ExtractStats",
    "ExtractResult",
    "_MAX_XML_BYTES",
    "_pptx_blocks",
    "_docx_blocks",
    "_w_para",
    "_w_paras",
    "_parse",
    "_slide_blocks",
    "_paragraphs",
    "_count_kinds",
), EXTRACT_HEADER)

PREP_HEADER = '''"""桌面 `prep.py` 里**不碰文件系统、不碰锁**的那半，**由 `web/scripts/sync-core.py` 按名字生成，勿手改**。

带进来的是「候选术语」这条判断链本身：提示词 `SYS_EXTRACT` / `_prompt()`（kind 说明从
`extract.KIND_MEANING` 生成）、`_extract_user`（拼 user 段，含重复行清单）、`_chunks`（按页分块）、
`_verify` / `_verify_folded`（术语得在原文里出现）、`_term_stats` / `_rank`（确定性排序）。

没带进来：

- `append_terms` / `remove_terms` / `restore_lines` / `_course_write_lock` / `_atomic_write` ——
  写 `glossary/<课号>.txt` 与锁，浏览器里没有「只追加的文件」。
- `_archive` / `course_glossary_path` / `_domain_prior` / `_save_state` / `_notes_count` ——
  `~/.classlive/` 的路径、`translator.course_title`、`store`（墓碑）。
- `prepare()` / `main()` —— 装配层（CLI 与面板的适配器）。网页版的装配在
  `web/src/lib/prep/` 的 TS 里，进度、失败清单都由那边出。

⚠️ **域名先验（`domain`）网页版拿不到**（它来自桌面术语表首行），所以 `_extract_user(course, g)`
   不带第三个参数调 —— 那一段提示词就不出现，模型按课程名判领域。
"""'''

PREP_SLICE = Slice("prep.py", "prep.py", (
    "MAX_TERM_TOKENS",
    "REPEAT_PAGE_RATIO",
    "MAX_CANDIDATES_PER_CALL",
    "SPREAD_MULT_LO",
    "SPREAD_MULT_HI",
    "TITLE_MULT",
    "CHUNK_CHARS",
    "DEFAULT_MAX_AUTO",
    "DEFAULT_MAX_TOTAL",
    "SYS_EXTRACT",
    "_prompt",
    "_MIN_STEM",
    "_SUFFIX_FULL",
    "_SUFFIX_PL",
    "_fold",
    "_exact_pattern",
    "_stem",
    "_word_pattern",
    "_verify",
    "_verify_folded",
    "Candidate",
    "_repeated_lines",
    "_extract_user",
    "_chunks",
    "_term_stats",
    "_rank",
), PREP_HEADER)

#: 全部切片。加一份就往这里加一行 —— `--check` 与 `--write` 都只认这张表。
SLICES = (STREAM_SLICE, EXTRACT_SLICE, PREP_SLICE)

EXIT_OK, EXIT_DRIFT, EXIT_USAGE = 0, 1, 2


class SliceError(Exception):
    """生成不出来（名字被改名 / 删了，或切完有未定义的名字）。消息直接给人看。"""


class Drift:
    """一处漂移。`where` 是给人看的定位（文件名或「文件 · 名字」），`detail` 是原因。"""

    def __init__(self, where: str, detail: str, diff: str = "") -> None:
        self.where = where
        self.detail = detail
        self.diff = diff

    def __str__(self) -> str:
        head = f"  · {self.where}：{self.detail}"
        return f"{head}\n{self.diff}" if self.diff else head


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _diff(desktop: str, web: str, label: str) -> str:
    lines = difflib.unified_diff(
        desktop.splitlines(True), web.splitlines(True), f"桌面/{label}", f"网页/{label}", n=1
    )
    return "".join("    " + ln for ln in list(lines)[:40])


# ── 切片生成


def _names_of(node: ast.stmt) -> list[str]:
    """一个顶层语句定义的名字（def / class 一个，赋值每个目标名一个）。"""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _segments(src: str) -> dict[str, tuple[int, int, int]]:
    """名字 → (起始行, 结束行, 定义自己的起始行)，均 1-based 且含两端。

    起始行把紧贴在上面的 `#:` 注释块一起带走（从块里第一条 `#:` 起）；装饰器算定义的一部分。
    """
    lines = src.splitlines()
    out: dict[str, tuple[int, int, int]] = {}
    for node in ast.parse(src).body:
        names = _names_of(node)
        if not names:
            continue
        start = node.lineno
        for dec in getattr(node, "decorator_list", []):
            start = min(start, dec.lineno)
        lead = start
        i = start - 1
        while i >= 1 and lines[i - 1].lstrip().startswith("#"):
            i -= 1
        block = list(range(i + 1, start))  # 紧贴的注释行
        for k in block:
            if lines[k - 1].lstrip().startswith("#:"):
                lead = k
                break
        end = node.end_lineno or node.lineno
        for name in names:
            out[name] = (lead, end, start)
    return out


def _gap(lines: list[str], prev_end: int, cur_start: int) -> list[str]:
    """两句之间保留哪些行。

    全空 / 只有分区横幅注释块（`# ═…` / `# ─…` 领头的一整块）→ 原样留，切片读起来还分得清层次；
    夹着别的代码或别的定义的 `#:` 注释 → 只留一个空行（那些注释属于切片外的定义）。
    """
    gap = lines[prev_end:cur_start - 1]
    block: list[str] = []
    for line in gap + [""]:
        if line.strip():
            block.append(line)
            continue
        if block:
            head = block[0].lstrip()
            if not head.startswith(BANNER) or any(b.lstrip().startswith("#:") for b in block):
                return ["\n"]
            block = []
    return gap


def _body(src: str, sl: Slice) -> str:
    """切片正文（不含文件头与 import），按 `sl.names` 的顺序拼起来。"""
    seg = _segments(src)
    missing = [n for n in sl.names if n not in seg]
    if missing:
        raise SliceError(f"桌面 {sl.desktop} 里没有这些顶层名字：{'、'.join(missing)}")
    lines = src.splitlines(keepends=True)
    out: list[str] = []
    prev_end = 0
    for name in sl.names:
        lead, end, start = seg[name]
        cur = start if name in sl.no_leading_comment else lead
        if prev_end:
            out.extend(_gap(lines, prev_end, cur))
        out.extend(lines[cur - 1:end])
        prev_end = end
    return "".join(out)


def _roots(body_src: str) -> set[str]:
    """正文里用到的顶层标识符（`collections.Counter` 取 `collections`）。"""
    return {n.id for n in ast.walk(ast.parse(body_src)) if isinstance(n, ast.Name)}


def _defines(src: str) -> set[str]:
    """正文自己造出来的名字（顶层 / 类体 / 函数参数 / 赋值 / 推导式目标）—— 用来判 import 够不够。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.alias):
            out.add((node.asname or node.name).split(".")[0])
    return out


def _imports(src: str, body: str) -> tuple[str, set[str]]:
    """从桌面源自己的 import 原文里挑出切片用到的那些（保持源文件顺序），连同它们绑定的名字。"""
    used = _roots(body) - _defines(body) - set(dir(builtins))
    kept: list[str] = []
    bound_names: set[str] = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Import):
            bound = {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            bound = {(a.asname or a.name) for a in node.names}
        else:
            continue
        if bound & used:
            segment = ast.get_source_segment(src, node)
            if segment:
                kept.append(segment)
                bound_names |= bound & used
    return "\n".join(kept) + "\n", bound_names


def render_slice(src: str, sl: Slice) -> str:
    """桌面源全文 → 网页版切片全文（纯函数）。

    `--check` 会把「桌面源重新渲染一遍」与磁盘上的那份逐字节比 —— 所以切片的每个字节
    都由桌面决定，改这里只会让漂移检查红，改不出一个不对齐的版本。
    """
    body = _body(src, sl)
    imports, imported = _imports(src, body)
    missing = _roots(body) - _defines(body) - set(dir(builtins)) - imported
    if missing:
        raise SliceError(f"{sl.desktop} 的切片里用到了没带进来的名字：{'、'.join(sorted(missing))}")
    return f"{sl.header}\nfrom __future__ import annotations\n\n{imports}\n{body}"


# ── 比较 / 同步


def source_of(desktop: Path, name: str) -> Path:
    """一个整文件拷贝的来源仓库：`TEMPORARY_FROM` 有就按它，否则就是 `--desktop`。"""
    tmp = TEMPORARY_FROM.get(name)
    return Path(tmp).expanduser() if tmp else desktop


def compare_whole_files(desktop: Path, core: Path) -> list[Drift]:
    """逐字节比原样拷贝（含 `vendor/budoux/zh-hans.json` —— 断句字库，换了会静默改断句）。"""
    out: list[Drift] = []
    for name in WHOLE_FILES:
        a, b = source_of(desktop, name) / name, core / name
        if not a.exists():
            out.append(Drift(name, "桌面仓库里没有这个文件"))
            continue
        if not b.exists():
            out.append(Drift(name, "core-py 里没有这个文件"))
            continue
        src, dst = _text(a), _text(b)
        if src != dst:
            out.append(Drift(name, "整文件拷贝走样", _diff(src, dst, name)))
    return out


def compare_slices(desktop: Path, core: Path) -> list[Drift]:
    """每份切片整份比较：拿桌面源重新生成一遍，与 core-py 里的那份逐字节比。"""
    out: list[Drift] = []
    for sl in SLICES:
        out.extend(compare_slice(desktop, core, sl))
    return out


def compare_slice(desktop: Path, core: Path, sl: Slice) -> list[Drift]:
    """一份切片：重新渲染 → 逐字节比。缺桌面源 / 缺网页版都报出来，不静默当一致。"""
    src = desktop / sl.desktop
    if not src.exists():
        return [Drift(sl.web, f"桌面仓库里没有 {sl.desktop}")]
    try:
        want = render_slice(_text(src), sl)
    except SliceError as exc:
        return [Drift(sl.web, str(exc))]
    have_path = core / sl.web
    if not have_path.exists():
        return [Drift(sl.web, "core-py 里没有这个文件")]
    have = _text(have_path)
    if have == want:
        return []
    return [Drift(sl.web, "与桌面源重新生成的切片不同（--write 可修）", _diff(want, have, sl.web))]


def find_desktop(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    root = Path(__file__).resolve().parent.parent.parent  # web/scripts/ → worktree 根
    return (root.parent / "lecture-live").resolve()


def find_core(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "static" / "core-py"


def refresh(desktop: Path, core: Path) -> None:
    """`--write`：整文件拷贝按字节刷，切片按名字重新生成。

    先在内存里把切片渲染出来再落盘 —— 生成失败就一个字节也不写（半写比不写更难查）。
    """
    # 先把每一份切片都渲染出来：任何一份生成失败就一个字节也不写（半写比不写更难查）。
    texts = [(sl, render_slice(_text(desktop / sl.desktop), sl)) for sl in SLICES]
    for name in WHOLE_FILES:
        src = source_of(desktop, name) / name
        if src.exists():
            (core / name).write_bytes(src.read_bytes())
            print(f"  已同步 {name}")
    for sl, text in texts:
        (core / sl.web).write_text(text, encoding="utf-8")
        print(f"  已重新生成 {sl.web}（{len(sl.names)} 个名字）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="网页版 Python 核心与桌面版的漂移检查 / 同步")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="只检查：有漂移就非零退出")
    mode.add_argument("--write", action="store_true", help="刷成桌面版（整文件拷贝 + 切片）")
    ap.add_argument("--desktop", help="桌面仓库路径（默认 worktree 旁的 ../lecture-live）")
    ap.add_argument("--core", help="core-py 目录（默认 web/static/core-py）")
    args = ap.parse_args(argv)

    desktop, core = find_desktop(args.desktop), find_core(args.core)
    lacking = [sl.desktop for sl in SLICES if not (desktop / sl.desktop).exists()]
    if lacking:
        print(f"桌面仓库不对：{desktop} 下没有 {'、'.join(lacking)}", file=sys.stderr)
        return EXIT_USAGE
    if not core.is_dir():
        print(f"core-py 目录不存在：{core}", file=sys.stderr)
        return EXIT_USAGE

    if args.write:
        print(f"从 {desktop} 同步 → {core}")
        try:
            refresh(desktop, core)
        except SliceError as exc:
            print(f"切片生成失败，没写文件：{exc}", file=sys.stderr)
            return EXIT_USAGE

    drift = compare_whole_files(desktop, core)
    slice_drift = compare_slices(desktop, core)
    bad_slices = {d.where for d in slice_drift}

    print(
        f"整文件拷贝 {len(WHOLE_FILES) - len({d.where for d in drift})}/{len(WHOLE_FILES)} 一致；"
        + "；".join(f"{sl.web}（{len(sl.names)} 个名字）{'一致' if sl.web not in bad_slices else '不一致'}"
                   for sl in SLICES)
        + f"。桌面：{desktop}"
    )
    if drift:
        print("整文件漂移：")
        for d in drift:
            print(d)
    if slice_drift:
        print("切片漂移（由本脚本从桌面源生成，别手改；--write 可刷）：")
        for d in slice_drift:
            print(d)
    if not drift and not slice_drift:
        print("没有漂移。")
        return EXIT_OK
    return EXIT_DRIFT


if __name__ == "__main__":
    raise SystemExit(main())
