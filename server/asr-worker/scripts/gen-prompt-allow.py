#!/usr/bin/env python3
"""生成 / 校验 `src/prompt-allow.js`（网页版系统提示词白名单 + user 段长度上限）。

对外：`python3 scripts/gen-prompt-allow.py [--prompt-core P] [--prep-core P] [--check|--print]`
  （默认可写）把**四条** system prompt 的 SHA-256 与各自类别的最大 user 段长度写进
  `src/prompt-allow.js`；`--check` 只比对、不一致就非零退出（CI / 测试用）；
  `--print` 打印 JSON（测试取提示词原文用）。

四条的来源分两处，取法也不同：
  · 翻译三条（`SYSTEM_PROMPT_CLOUD` / `DRAFT_SYSTEM_CLOUD` / `FIX_SYSTEM_CLOUD`）—— `prompt_core.py`，
    **本机临时路径**（那个分支还没合进桌面 main）。
  · 术语候选一条（`prep._prompt()`）—— 从 `web/static/core-py/prep.py`（`sync-core.py` 从桌面
    `prep.py` 按名字切出来的那份）**import** 出来。
    为什么用切片而不是直接 import 桌面 `prep.py`：桌面那个模块顶层 `import store`、
    `_prompt()` 里还 `from extract import KIND_MEANING` —— 从脚本目录 import 会连带
    `~/.classlive/` 的路径模块，白名单生成不该有这种副作用。切片只含纯常量与纯函数，
    import 它**等于** import 桌面同一份文本（`sync-core.py --check` 逐字节钉着），
    而且网页运行时用的就是这份字节 —— 白名单与服务端看到的是**同一串字符**，没有第三份。

为什么用 import 而不是 ast 取字面量：`prompt_core.py` 只 import 标准库（文件头自己的承诺），
import 拿到的就是运行时那份字符串 —— 三重引号、隐式拼接（`DRAFT_SYSTEM_CLOUD` 是括号拼接）、
`\n` 转义全都不用我们自己解释；ast 得把 JoinedStr / 常量拼接重新实现一遍，等于写第二个解析器。
代价：目标文件里若出现 import 期副作用会跟着跑 —— 它没有（纯常量 + 纯函数），文件头有背书。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "src" / "prompt-allow.js"
DEFAULT_CORE = Path("/Users/owen/lecture-live-prompt/prompt_core.py")
#: `prep.py` 切片（网页版跑的那份）。相对本脚本：scripts/ → asr-worker/ → server/ → 仓库根。
DEFAULT_PREP = HERE.parent.parent.parent / "web" / "static" / "core-py" / "prep.py"
NAMES = ("SYSTEM_PROMPT_CLOUD", "DRAFT_SYSTEM_CLOUD", "FIX_SYSTEM_CLOUD")

# ---- 「术语候选」那条 user 段的上限，按 `prep.py` 自己的真实上限算 ----
# 分块上限 `CHUNK_CHARS`（30 000）**是块内正文的字符数**，不是整条 user 段：`_chunks` 的账是
# 每块 `len(b.text) + 16`，而 `_extract_user` 还会加上抬头（课程名）、重复行清单（最多 20 条 × 90 字）
# 与每块的 `### 文件：` 标题。所以「实际最大」= 分块上限 + 每块 16 字节的记账差 + 抬头余量，
# 再 × 2 做漂移余量（与翻译三条同一个口径：`MAX_USER_CHARS = actual * 2`）。
# 每一项都能从切片里读出来，测试（`test-web-prompt.mjs` P8）就是按这几个常量交叉验证的。
PREP_HEADER_CHARS = 4_000      # 抬头：课程名 80 + 重复行清单 20×90 + 标题，量级上界
PREP_PER_BLOCK = 16            # `_chunks` 每块多记的 16 字符（`len(b.text) + 16`）

# ---- 「实际最大 user 段」的输入（每一项都要有出处，测试钉的就是这组数）----
# 术语数 60：`translator.TERM_LOAD_LIMIT`（分课程术语加载上限，每句全量注入）。
# 术语长 40：真实术语表里最长一行的量级（`glossary/*.txt` 抽查 12–45 字符，留到 40）。
# 上下文 19 句：`cloud_translator._ctx_window` 块对齐布局的窗口长度是 [chunk, 2*chunk)，
#   chunk 取 `DEFAULT_CTX_CHUNK` = 10 → 最坏 19 句。
# 一句 300 字符：`vad.MAX_UTTERANCE_S` = 12 s 硬切 × 25 字/秒（英文明读的上限量级）。
# 课程名 80：``.course`` 里一行课程标题的长度量级；译名 8 条 = `prompt_core.TERM_ZH_MAX`。
TERMS_N, TERM_CHARS = 60, 40
CTX_CHUNK, MAX_CONTEXT = 10, 2
UTT_CHARS, TITLE_CHARS, ZH_N = 300, 80, 8


def load_core(path: Path, modname: str, expect: str = ""):
    """把一份 .py 按文件路径 import 进来。

    `expect` 是用来自证「import 到的就是我以为的那份」的顶层名字 —— 缺了就当场退，
    免得白名单里静默少了/多了东西（少了这条，改错路径会让四条变三条还报 ok）。
    """
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载 {path}")
    # ⚠️ 切片里的 `from extract import KIND_MEANING` 要能 import 到同目录的 extract.py ——
    #    把该目录放进 sys.path 就够了（那份 extract.py 也只有标准库依赖）。
    here = str(path.parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if expect and not hasattr(mod, expect):
        raise SystemExit(f"{path} 里没有 {expect} —— 路径指错了？")
    return mod


class prep_Block:
    """`extract.Block` 的最小替身：`_extract_user` 只用 `.text` / `.kind` / `.page`。"""

    def __init__(self, text, kind, page):
        self.text, self.kind, self.page = text, kind, page


def prep_prompt(prep_mod) -> str:
    """术语候选那条 system 提示词（`prep._prompt()` —— 数字与 kind 说明都是现场插的）。"""
    return prep_mod._prompt()


def prep_max_user_chars(prep_mod) -> int:
    """`prep._extract_user` 在最坏一块上能拼出的最长 user 段。

    用 `prep` 自己的 `_chunks` / `_extract_user` 拼，不在这里重写排版 —— 排版改了这里自动跟着变，
    `--check` 就会把 `prompt-allow.js` 里的数字判红。
    """
    chunk = prep_mod.CHUNK_CHARS
    # 一块 = 一份文件的一串块；每块按 `_chunks` 的记账口径（`len(text) + 16`）算，
    # 用「条数最少、每条最长」的极端形状：一条占满分块上限。
    blocks = [prep_Block("x" * (chunk - PREP_PER_BLOCK), "body", 1)]
    group = [("f.pptx", blocks)]
    return len(prep_mod._extract_user("C" * 80, group))


def max_user_chars(pc) -> int:
    """`prompt_core` 在最大术语数 + 最大上下文下能拼出的最长 user 段（两种布局 + 草稿取最大）。

    用 `prompt_core` 自己的函数拼，不在这里重写排版 —— 排版改了这里自动跟着变，
    `--check` 就会把 `prompt-allow.js` 里的数字判红。
    """
    tail_translate = ("Now translate the ASR utterance above into Chinese and output exactly:\n"
                      "ZH: <Chinese translation of the CORRECTED sentence>\n"
                      "EN: <same utterance with ASR mishearings fixed>\n"
                      "Do not repeat the course-terms list. No headings.")
    tail_fix = ("Now output that same utterance with ASR mishearings fixed. "
                "English only — no Chinese, no label.")
    terms = ["term" + str(i) + "x" * (TERM_CHARS - 4 - len(str(i))) for i in range(TERMS_N)]
    assert all(len(t) == TERM_CHARS for t in terms), "术语长度就是 TERM_CHARS（上限的算式要能对上这条测试）"
    zh_map = {t: "译" * 6 for t in terms[:ZH_N]}
    head = " ".join(terms[:ZH_N])
    en = head + " " + "w" * max(0, UTT_CHARS - len(head) - 1)
    context = ["y" * UTT_CHARS] * (2 * CTX_CHUNK - 1)
    domain = "c" * TITLE_CHARS
    sizes = [
        pc.user_content(domain, terms, core=pc.CORE_TERMS, always=terms, term_zh=zh_map,
                        en=en, context=context, tail=tail_translate,
                        ctx_chunk=CTX_CHUNK, max_context=MAX_CONTEXT),
        pc.user_content(domain, terms, core=pc.CORE_TERMS, always=terms, term_zh=zh_map,
                        en=en, context=context, tail=tail_fix,
                        ctx_chunk=CTX_CHUNK, max_context=MAX_CONTEXT, zh=False),
        pc.draft_user(domain, terms[:40], zh_map, en, context),
    ]
    return max(len(s) for s in sizes)


def numbers(pc, pp) -> dict:
    prompts = {n: getattr(pc, n) for n in NAMES}
    prompts["PREP_SYSTEM"] = prep_prompt(pp)
    prep_actual = prep_max_user_chars(pp)
    return {
        "sha256": {n: hashlib.sha256(v.encode()).hexdigest() for n, v in prompts.items()},
        "max_user_chars": max_user_chars(pc),
        "prep_max_user_chars": prep_actual,
        "prep_bound": prep_mod_bound(pp),
        "prep_chunk": pp.CHUNK_CHARS,
        "prompts": prompts,
    }


def prep_mod_bound(pp) -> int:
    """不算 `_extract_user`、只用切片常量的那个上界（`CHUNK_CHARS` + 记账差 + 抬头）。

    两条路（真拼一次 / 按常量算）都留着：真拼出来的数**必须 ≤** 这个上界，
    不等就说明上面那段说明或常量过期了 —— `--check` 之外没有别的地方会发现。
    """
    return pp.CHUNK_CHARS + PREP_PER_BLOCK + PREP_HEADER_CHARS


def render(pc_path: Path, prep_path: Path, n: dict) -> str:
    rows = "\n".join(f'  "{n["sha256"][name]}",   // {name}'
                     for name in (*NAMES, "PREP_SYSTEM"))
    return f'''// 网页版系统提示词的 SHA-256 白名单 + user 段长度上限 —— **本文件由脚本生成，别手改**。
// 为什么：提示词不是秘密（OWASP LLM07），但「谁能拿通行码当任意模型接口」是真风险 —— 网页只放行
// 这四条 system prompt（Pyodide 跑桌面同一份代码拼出来），改一个字就不认：
//   翻译三条（定稿 / 草稿 / 只矫正）来自 `prompt_core.py`；术语候选一条来自 `prep.py` 的 `_prompt()`。
// 生成：python3 server/asr-worker/scripts/gen-prompt-allow.py；校验走 `--check`（不一致非零退出）。
// 判据：test-web-prompt.mjs。

// 翻译三条的来源：{pc_path}（{pc_path.name} 的 sha256 = {hashlib.sha256(pc_path.read_bytes()).hexdigest()[:12]}…）
// 术语候选的来源：{prep_path}（切片的 sha256 = {hashlib.sha256(prep_path.read_bytes()).hexdigest()[:12]}…）
// 生成时间：{time.strftime("%Y-%m-%d %H:%M:%S")}
// 四条常量各算一次；比对在 prompt-guard.js（Web Crypto，Node 测试同样可用）。

export const PROMPT_SHA256 = new Set([
{rows}
]);
//: 术语候选那条的 user 段自成一档（它喂的是整块课件，跟翻译的逐句 prompt 不是一个量级）。
export const PREP_PROMPT_SHA256 = "{n["sha256"]["PREP_SYSTEM"]}";

// 上限 = 2 × 实际最大值（余量给「术语表又长了两行」这类漂移，不给出「随便塞点别的」）。
// actual 的来源（生成脚本自己算的，改输入要连这里的注释一起改）：
//   术语 {TERMS_N} 条 × 每条约 {TERM_CHARS} 字符（translator.TERM_LOAD_LIMIT）
//   上下文 {2 * CTX_CHUNK - 1} 句 × 每句 {UTT_CHARS} 字符（DEFAULT_CTX_CHUNK=10 的块对齐窗口上界；vad.MAX_UTTERANCE_S=12 s）
//   课程名 {TITLE_CHARS} 字符、译名 {ZH_N} 条（prompt_core.TERM_ZH_MAX）
//   三条布局取最大：块对齐定稿 / 只矫正 / 草稿
export const MAX_USER_SOURCE = {{ actual: {n["max_user_chars"]}, terms: {TERMS_N}, ctx: {2 * CTX_CHUNK - 1}, utt: {UTT_CHARS} }};
export const MAX_USER_CHARS = MAX_USER_SOURCE.actual * 2;

// 术语候选那条的上限，算法同翻译（actual × 2）。actual 真拼一次算的：用 `prep._extract_user`
// 把「一条占满 `CHUNK_CHARS`」的最坏一块拼出来。旁边另给一个**按常量算**的上界（bound）——
// `CHUNK_CHARS` + `_chunks` 每块的 16 字符记账差 + 抬头余量。两者不许倒挂：
// actual > bound 说明上面那几条注释里的常量过期了，`--check` 会直接报出来。
export const PREP_MAX_USER_SOURCE = {{ actual: {n["prep_max_user_chars"]}, bound: {n["prep_bound"]}, chunk: {n["prep_chunk"]}, header: {PREP_HEADER_CHARS} }};
export const PREP_MAX_USER_CHARS = PREP_MAX_USER_SOURCE.actual * 2;
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-core", default=str(DEFAULT_CORE))
    ap.add_argument("--prep-core", default=str(DEFAULT_PREP),
                    help="prep.py 切片（默认仓库里的 web/static/core-py/prep.py）")
    ap.add_argument("--check", action="store_true", help="只比对，不一致非零退出")
    ap.add_argument("--print", dest="emit", action="store_true", help="打印 JSON（测试取提示词原文）")
    a = ap.parse_args()
    pc_path = Path(a.prompt_core).resolve()
    if not pc_path.is_file():
        print(f"找不到 prompt_core.py：{pc_path}", file=sys.stderr)
        return 2
    prep_path = Path(a.prep_core).resolve()
    if not prep_path.is_file():
        print(f"找不到 prep.py 切片：{prep_path}", file=sys.stderr)
        return 2
    n = numbers(load_core(pc_path, "prompt_core_for_allowlist", "SYSTEM_PROMPT_CLOUD"),
                load_core(prep_path, "prep_for_allowlist", "SYS_EXTRACT"))
    if a.emit:
        print(json.dumps({"sha256": n["sha256"], "max_user_chars": n["max_user_chars"],
                          "prep_max_user_chars": n["prep_max_user_chars"],
                          "prompts": n["prompts"]}, ensure_ascii=False))
        return 0
    if a.check:
        if not OUT.is_file():
            print(f"{OUT} 不存在", file=sys.stderr)
            return 1
        text = OUT.read_text(encoding="utf-8")
        got = set(re.findall(r'"([0-9a-f]{64})"', text))
        want = set(n["sha256"].values())
        m = re.search(r"actual:\s*(\d+)", text)
        got_max = int(m.group(1)) if m else -1
        pm = re.search(r"PREP_MAX_USER_SOURCE = \{ actual: (\d+)", text)
        got_prep = int(pm.group(1)) if pm else -1
        bad = []
        if got != want:
            bad.append(f"hash 不一致：多 {sorted(got - want)} / 少 {sorted(want - got)}")
        if got_max != n["max_user_chars"]:
            bad.append(f"actual 不一致：文件 {got_max} / 实测 {n['max_user_chars']}")
        if got_prep != n["prep_max_user_chars"]:
            bad.append(f"prep actual 不一致：文件 {got_prep} / 实测 {n['prep_max_user_chars']}")
        # 两条路必须对得上：真拼出来的 user 段不许超过按常量算的上界（超了说明说明或常量过期）。
        if n["prep_max_user_chars"] > n["prep_bound"]:
            bad.append(f"prep 真拼 {n['prep_max_user_chars']} > 常量上界 {n['prep_bound']}"
                       "（CHUNK_CHARS / PREP_HEADER_CHARS 该改注释了）")
        if bad:
            print("\n".join(bad), file=sys.stderr)
            return 1
        print(f"ok：{len(want)} 条 hash 一致，actual={got_max}，prep actual={got_prep}")
        return 0
    OUT.write_text(render(pc_path, prep_path, n), encoding="utf-8")
    print(f"写入 {OUT}（actual={n['max_user_chars']}，prep actual={n['prep_max_user_chars']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
