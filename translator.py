"""LLM 修正 + 翻译(流式, 中文优先)。

输出格式改为两段(不再用 JSON —— JSON 无法流式解析):
    ZH: <中文译文>
    EN: <修正后的英文>

用 mlx-lm 的 stream_generate 逐 token 出字: 中文一出现就推给 UI(打字机效果),
英文随后补齐。解析用状态机, 不做正则匹配(流式文本上正则不可靠)。

用一把锁保证串行生成(GPU 上顺序执行)。
"""
from __future__ import annotations
import difflib, pathlib, threading


# 常驻核心词(高频概念): 无论句子内容都注入, 约 15 token。
# 注意: 纯按匹配筛选会漏掉"被听错而正需纠正"的术语, 所以核心词必须常驻。
# 课号不写死在这里 —— 由 core_terms(course) 从 .course / 术语表载入。
CORE_TERMS = [
    "Brightspace", "midterm", "final assessment component",
    "essential readings", "tutorial", "quiz",
]
MAX_DYNAMIC_TERMS = 3


SYSTEM_PROMPT = """你是英文课堂字幕校正+翻译助手。给你一句 ASR 转写的英文, 以及最近几句上下文。
请严格按下面两行格式输出, 不要任何多余文字:
ZH: <中文译文>
EN: <修正后的英文>

要求:
1) 中文译文自然、准确, 专业术语用中文习惯表达;
2) 英文只修正明显的语音识别错听(音近词、漏词、专有名词), 例如 nation to→relation to;
   本来就正确的词句原样保留, 不要改写或润色;
3) 输入若是名词列表/大纲词条(如课程名、术语串), 逐词直译即可,
   严禁输出"课程术语""翻译""总结"之类的概括性标题。"""


class Result:
    __slots__ = ("en_fixed", "zh")

    def __init__(self, en_fixed: str, zh: str):
        self.en_fixed = en_fixed
        self.zh = zh


def _load_terms(path: str | None) -> list[str]:
    if not path or not pathlib.Path(path).exists():
        return []
    lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    return [t.strip() for t in lines if t.strip() and not t.strip().startswith("#")]


def course_terms_path(glossary_path: str, course: str) -> pathlib.Path | None:
    """定位分课程术语表 glossary/<课号>.txt。

    ⚠️ 容错: 用户常用短代号(`cl course 10202` 会把 `10202` 写进 .course), 而文件是
    全名 `ECON10202.txt` —— 严格拼路径会**静默落空**(实测只加载到公共术语,
    课程术语一条没进)。故精确路径不存在时, 退而在 glossary/ 里按**后缀**匹配;
    仅当唯一命中时才采用(防 `202` 这类歧义前缀误配)。
    """
    d = pathlib.Path(glossary_path).parent / "glossary"
    exact = d / f"{course}.txt"
    if exact.exists():
        return exact
    if not d.is_dir():
        return None
    hits = [p for p in sorted(d.iterdir())
            if p.suffix == ".txt" and p.stem.endswith(course)]
    return hits[0] if len(hits) == 1 else None


def load_terms(glossary_path: str | None, course: str | None = None) -> list[str]:
    """公共术语 + 分课程术语(glossary/<课号>.txt)。"""
    terms = _load_terms(glossary_path)
    if course and glossary_path:
        p = course_terms_path(glossary_path, course)
        if p is not None:
            terms += _load_terms(str(p))
    # 去重保序
    seen, out = set(), []
    for t in terms:
        k = t.lower()
        if k not in seen:
            seen.add(k); out.append(t)
    return out


def core_terms(course: str | None = None) -> list[str]:
    """常驻核心词: 通用核心 + 当前课号(课号发音极近, 必须常驻防听混)。"""
    core = list(CORE_TERMS)
    if course and course not in core:
        core.append(course)
    return core


def select_terms(sentence: str, terms: list[str],
                 core: list[str] | None = None, max_dyn: int = MAX_DYNAMIC_TERMS) -> str:
    """RAG-lite: 核心词常驻 + 动态召回最相关的几个术语。
    收益: 注入从 ~187 token 降到 ~40 token, 缩短 prefill、降低显存压力。
    匹配: 字面包含优先, 否则与句中词的模糊相似度(difflib); 音近错听(如 nation→relation)
    相似度也较高, 能被召回。

    性能: 先用 `real_quick_ratio` 的上界 `2·min(len)/len和` 做**纯算术**预筛, 免掉长度
    悬殊的比对(真实课堂语料实测 2.06→1.35ms/句)。**不做记忆化** —— 试过 `lru_cache`,
    唯一 (术语, 词) 组合随课时长线性增长(120 句已 5.3 万, 2 小时约 24 万), 缓存永远
    装不下, 命中率随时长漂移; 而整节课本来只花 ~1s, 省下的时间是块里的 0.1%。"""
    core = core if core is not None else CORE_TERMS
    sent_l = sentence.lower()
    words = [w.strip(".,!?\"'()[]").lower() for w in sentence.split()]
    words = [w for w in words if len(w) > 3]

    scored: list[tuple[float, str]] = []
    core_l = {c.lower() for c in core}
    for t in terms:
        tl = t.lower()
        if tl in core_l:
            continue
        if tl in sent_l:
            scored.append((1.0, t))
            continue
        lt = len(tl)
        best = 0.0
        for w in words:
            lw = len(w)
            if 2 * (lt if lt < lw else lw) < 0.6 * (lt + lw):
                continue                          # real_quick_ratio 上界, 免构造
            r = difflib.SequenceMatcher(None, tl, w).ratio()
            if r > best:
                best = r
                if best >= 0.99:
                    break
        if best >= 0.6:
            scored.append((best, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = list(core) + [t for _, t in scored[:max_dyn]]
    return "\n".join(picked) if picked else "(无特定术语)"


DRAFT_SYSTEM = "你是实时字幕翻译器。把用户给的英文口语快速译成中文, 只输出中文译文。"

# 只矫正、不翻译(关闭翻译时用): 同一个模型、同一份上下文, 只是不要中文。
FIX_SYSTEM = """你是英文课堂字幕校正助手。给你一句 ASR 转写的英文, 以及最近几句上下文。
请只修正明显的语音识别错听(音近词、漏词、专有名词、术语), 例如 nation to→relation to。
本来就正确的词句**原样保留**, 不要改写、润色、缩写或调整语序。
只输出修正后的那一句英文, 不要任何前缀、标签、解释或中文。"""

# 模型偶尔给结果加标签(EN: / 修正后: …), 或干脆跑偏去翻译/概括。清一遍。
_FIX_LABELS = ("EN:", "EN：", "FIXED:", "CORRECTED:", "OUTPUT:",
               "英文:", "英文：", "修正后:", "修正后：", "矫正后:")


def _clean_fix(text: str, raw: str) -> str:
    """矫正结果归一; 结果不可用时回退原始 ASR(与翻译路径同规矩: 宁可留原样)。
    判为不可用的三种情况: 空、混入中文(说明它跑偏去翻译了)、明显变长(说明在改写
    而不是矫正 —— 本功能的承诺就是"正确的词原样保留")。"""
    t = " ".join((text or "").split())
    up = t.upper()
    for lab in _FIX_LABELS:
        if up.startswith(lab.upper()):
            t = t[len(lab):].strip()
            break
    t = t.strip().strip('"“”\'`').strip()
    if not t or any("一" <= c <= "鿿" for c in t):
        return raw
    if len(t.split()) > 1.8 * len(raw.split()) + 3:
        return raw
    return t


def _apply_chat_generic(toks, user_content: str, system: str) -> str:
    """套 chat template(带 enable_thinking=False 的兼容回退)。"""
    if getattr(toks, "chat_template", None):
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": user_content}]
        try:
            return toks.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True,
                enable_thinking=False)
        except TypeError:
            return toks.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True)
    return user_content


class _StreamParser:
    """把流式文本按 ZH: / EN: 切段, 逐段回调。
    容错: 标记被 token 切开、模型不按格式输出(无标记)、英文段混入中文。"""

    def __init__(self, on_zh, on_en):
        self.on_zh, self.on_en = on_zh, on_en
        self.state = "wait_zh"
        self._zh: list[str] = []
        self._en: list[str] = []
        self._buf = ""
        self._pre: list[str] = []          # 未出现 ZH: 前的累积(防丢弃)
        self._en_seen = False

    @staticmethod
    def _find_marker(s: str, marker: str) -> int:
        i = s.find(marker)
        j = s.find(marker.replace(":", "："))     # 容忍全角冒号
        if i == -1:
            return j
        if j == -1:
            return i
        return min(i, j)

    def feed(self, delta: str) -> None:
        if not delta:
            return
        self._buf += delta
        while True:
            if self.state == "wait_zh":
                k = self._find_marker(self._buf, "ZH:")
                if k == -1:
                    # 保留尾部(可能含被切开的标记), 其余累积;
                    # 攒够长度仍无标记 -> 认定模型没按格式, 全当中文
                    if len(self._buf) > 3:
                        emit, self._buf = self._buf[:-3], self._buf[-3:]
                        if emit:
                            self._pre.append(emit)
                    if sum(len(x) for x in self._pre) >= 24:
                        text = "".join(self._pre + [self._buf]).lstrip("：: ").strip()
                        self._pre, self._buf = [], ""
                        if text:
                            self._zh.append(text); self.on_zh(text)
                        self.state = "in_zh"
                    return
                self._pre = []                    # 有标记 -> 丢弃前缀噪声
                self._buf = self._buf[k + 3:]
                self.state = "in_zh"
                continue
            if self.state == "in_zh":
                k = self._find_marker(self._buf, "EN:")
                if k == -1:
                    if len(self._buf) > 3:        # 留 3 字符防 "EN:" 被切开
                        emit, self._buf = self._buf[:-3], self._buf[-3:]
                        if emit:
                            self._zh.append(emit); self.on_zh(emit)
                    return
                emit = self._buf[:k].rstrip()
                self._buf = self._buf[k + 3:]
                if emit:
                    self._zh.append(emit); self.on_zh(emit)
                self._en_seen = True
                self.state = "in_en"
                continue
            if self.state == "in_en":
                if self._buf:
                    self._en.append(self._buf); self.on_en(self._buf); self._buf = ""
                return

    @staticmethod
    def _has_cjk(s: str) -> bool:
        return any("一" <= c <= "鿿" for c in s)

    def result(self, en_raw: str) -> Result:
        if self._buf:                              # 收尾: 未消费的尾巴
            if self.state == "in_en":
                self._en.append(self._buf); self.on_en(self._buf)
            elif self.state == "in_zh":
                self._zh.append(self._buf); self.on_zh(self._buf)
            elif self.state == "wait_zh":
                self._pre.append(self._buf)
        zh = "".join(self._zh + self._pre).strip().lstrip("：: ").strip()
        en = "".join(self._en).strip()
        # 英文段混入中文(模型跑偏) -> 用 ASR 原文兜底
        if not en or self._has_cjk(en):
            en = en_raw
        return Result(en_fixed=en, zh=zh or en_raw)


def _configure_mlx() -> None:
    """给 MLX 设**宽松**的缓冲缓存上限, 减少 Metal 缓冲的释放/重建次数。

    Why: 2026-09-10 本机发生过一次 IOGPUFamily 驱动断言内核崩溃
    (panic-full-2026-09-10-004444, 肇事进程即本程序), 特征是**内存记账状态损坏**
    而非 OOM(压缩器仅 11%、swap 正常)。已知 MLX 触发同类驱动 panic 的 issue
    (mlx-lm#883 / mlx#3186) 指向 Metal 缓冲的 load/unload 竞态。
    因此: 缓存上限设**宽松**(缓冲复用而非反复释放), 且**不主动 clear_cache**。
    """
    try:
        import mlx.core as mx
        mx.set_memory_limit(6 * 1024 ** 3)       # 仅作上限保护
        mx.set_cache_limit(2 * 1024 ** 3)        # 宽松: 鼓励复用, 减少释放/重建
    except Exception:                             # noqa: BLE001
        pass


class Translator:
    """懒加载 mlx-lm 模型; 生成用锁串行; chat template 提升指令遵循。"""

    def __init__(self, model: str, glossary_path: str | None, max_context: int = 2,
                 course: str | None = None):
        self._model = None
        self._tokenizer = None
        self._terms = load_terms(glossary_path, course)
        self._core = core_terms(course)
        self._max_ctx = max_context
        self._lock = threading.Lock()
        self._model_name = model

    def _ensure(self) -> None:
        if self._model is None:
            _configure_mlx()
            from mlx_lm import load
            self._model, self._tokenizer = load(self._model_name)

    def warmup(self) -> None:
        self._ensure()

    def _apply_chat(self, user_content: str, system: str = SYSTEM_PROMPT) -> str:
        return _apply_chat_generic(self._tokenizer, user_content, system)

    def _terms_context(self, en: str, context: list[str]) -> str:
        ctx = context[-self._max_ctx:]
        ctx_block = "\n".join(f"- {c}" for c in ctx) if ctx else "(无)"
        terms = select_terms(en, self._terms, core=self._core)
        return (f"课程术语(相关):\n{terms}\n\n"
                f"最近上下文(已校正):\n{ctx_block}\n\n"
                f"待处理这句 ASR:\n{en}")

    def _user_content(self, en: str, context: list[str]) -> str:
        return self._terms_context(en, context)

    # ---- 流式(主用) ----
    def fix_and_translate_stream(self, en: str, context: list[str],
                                 on_zh=None, on_en=None) -> Result:
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        self._ensure()          # auto 模式下云端首次失败才走到这里, 那时模型还没加载
        parser = _StreamParser(on_zh or (lambda s: None), on_en or (lambda s: None))
        prompt = self._apply_chat(self._user_content(en, context))
        with self._lock:
            for resp in stream_generate(
                    self._model, self._tokenizer, prompt=prompt,
                    max_tokens=220, sampler=make_sampler(temp=0.2, top_p=0.9)):
                parser.feed(getattr(resp, "text", "") or "")
            # 刻意**不**调用 mx.clear_cache(): 频繁释放/重建 Metal 缓冲是
            # IOGPUFamily 驱动断言的已知竞态窗口(见 _configure_mlx 注释)。
        return parser.result(en)

    # ---- 只矫正英文(关闭中文翻译时用): 不出译文, 但仍吃上下文修 ASR 错听 ----
    def fix_stream(self, en: str, context: list[str], on_en=None) -> str:
        """返回上下文矫正后的英文。失败由调用方兜底(保留原始 ASR)。"""
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        self._ensure()
        prompt = self._apply_chat(self._terms_context(en, context), FIX_SYSTEM)
        buf: list[str] = []
        with self._lock:
            for resp in stream_generate(
                    self._model, self._tokenizer, prompt=prompt,
                    max_tokens=180, sampler=make_sampler(temp=0.1, top_p=0.9)):
                d = getattr(resp, "text", "") or ""
                if d:
                    buf.append(d)
                    if on_en:
                        on_en(d)
        return _clean_fix("".join(buf), en)

    # ---- 非流式封装(测试/兼容) ----
    def fix_and_translate(self, en: str, context: list[str]) -> Result:
        return self.fix_and_translate_stream(en, context)

    # ---- 草稿轻量翻译(无术语表/无上下文, 只为尽早给一版中文) ----
    def translate_draft(self, en: str, on_zh=None) -> str:
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        self._ensure()
        prompt = _apply_chat_generic(self._tokenizer, en, DRAFT_SYSTEM)
        buf: list[str] = []
        with self._lock:
            for resp in stream_generate(self._model, self._tokenizer, prompt=prompt,
                                        max_tokens=120,
                                        sampler=make_sampler(temp=0.1, top_p=0.9)):
                buf.append(getattr(resp, "text", "") or "")
        text = "".join(buf).strip()
        if on_zh and text:
            on_zh(text)
        return text


def load_translator(model: str, glossary_path: str | None, max_context: int = 2,
                    course: str | None = None):
    return Translator(model, glossary_path, max_context, course=course)
