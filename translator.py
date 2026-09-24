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


SYSTEM_PROMPT = """你是英文课堂字幕校正+翻译助手。给你一句 ASR 转写的英文、课程领域、本课术语表, 以及最近几句上下文。
请严格按下面两行格式输出, 不要任何多余文字:
ZH: <中文译文>
EN: <修正后的英文>

要求:
1) 中文译文自然、准确, 专业术语用中文习惯表达;
2) **先在心里把听错的词改对, 再翻译改对后的句子**。中文必须译自修正后的读法 —— 你在 EN 行改掉的词,
   ZH 行绝不能还按错词直译(实测: EN 已修成 "the velocity", 中文却是"所以 74 速度…", 就是没做到这条);
3) 英文只修正明显的语音识别错听(音近词、漏词、专有名词、术语)。**优先采纳课程术语表里的词**:
   当某个词音近术语表条目时, 几乎可以断定是听错, 直接改成该术语。例如:
   "we're max chocolate properties" -> "we're macroscopic properties";
   "how we measure the stalcy" -> "how we measure the statistics";
   "average on the lacker level" -> "average on the lattice level";
4) 本来就正确、也不音近任何术语的词句**原样保留**, 不要改写、润色或调整语序 —— 防范过度矫正;
5) 输入若是名词列表/大纲词条(如课程名、术语串), 逐词直译即可,
   严禁输出"课程术语""翻译""总结"之类的概括性标题。"""


def domain_block(title: str) -> str:
    return f"Course: {title}\n" if title else ""


class Result:
    __slots__ = ("en_fixed", "zh")

    def __init__(self, en_fixed: str, zh: str):
        self.en_fixed = en_fixed
        self.zh = zh


def _has_cjk(s: str) -> bool:
    return any("一" <= c <= "鿿" for c in s)


def guard_zh_result(res: Result, en: str) -> Result:
    """本地引擎结果守卫: 译文里一个汉字都没有 = 模型把英文原样吐回来了
    (云端降级后的小模型实测会这样), 不是翻译。

    置空 zh 走"只显示英文"的既有路径 —— 屏上出现两行一样的英文, 用户会以为
    是翻译坏了; 空译文则明确表示"这句没翻出来"。云端路径有自己的回显检测
    (cloud_translator._looks_like_echo + 极简重试), 不走这里。"""
    if res.zh and not _has_cjk(res.zh):
        return Result(res.en_fixed, "")
    return res


def _load_terms(path: str | None) -> list[str]:
    """读一份术语表(每行一条, # 开头为注释)。读不出 -> 空列表并出声。

    ⚠️ 不裸读: 术语表是手写的(常从网页/Word 粘来), 可能不是 UTF-8; 也可能在
    exists() 之后被删或改权限。裸 read_text 会抛 UnicodeDecodeError/OSError,
    而这是启动路径 —— 术语表坏了不该让整节课起不来。"""
    if not path:
        return []
    p = pathlib.Path(path)
    if not p.exists():          # 没自备术语表是**正常**状态(公开仓库不含 glossary.txt)
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"⚠ 术语表读取失败, 本次忽略: {path} ({e})")
        return []
    return [t.strip() for t in text.splitlines()
            if t.strip() and not t.strip().startswith("#")]


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


def course_term_list(glossary_path: str | None, course: str | None) -> list[str]:
    """**只**取分课程术语(不含公共表)。这些词每句全量注入 —— 见 select_terms 的说明。"""
    if not course or not glossary_path:
        return []
    p = course_terms_path(glossary_path, course)
    return _load_terms(str(p)) if p is not None else []


def course_title(glossary_path: str | None, course: str | None) -> str:
    """从分课程术语表里取课程标题, 当**领域先验**用。

    例: "# ECON10101 Data Analysis for Economists(经济数据分析)" ->
        "ECON10101 Data Analysis for Economists(经济数据分析)"。
    ⚠️ 为什么要它: 模型知道"这是热力学课"才修得回听错的领域词(macroscopic / enthalpy),
    光给术语表不够 —— 被听错的词根本匹配不上术语表(见 select_terms)。"""
    if not course or not glossary_path:
        return ""
    p = course_terms_path(glossary_path, course)
    if p is None:
        return ""
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("#"):
                return s.lstrip("#").strip()
            if s:
                return s
    except (OSError, UnicodeDecodeError):
        # ⚠️ UnicodeDecodeError 必须一起接: 术语表是手写的, 可能不是 UTF-8。
        # 它在 __init__ 的启动路径上 —— 漏掉就是"术语表编码不对 -> 整节课起不来"。
        # (`_load_terms` 一直是这么接的, 这里当初漏了。2026-09-24 OCR 发现。)
        return ""
    return ""


def core_terms(course: str | None = None) -> list[str]:
    """常驻核心词: 通用核心 + 当前课号(课号发音极近, 必须常驻防听混)。"""
    core = list(CORE_TERMS)
    if course and course not in core:
        core.append(course)
    return core


def select_terms(sentence: str, terms: list[str],
                 core: list[str] | None = None, max_dyn: int = MAX_DYNAMIC_TERMS,
                 always: list[str] | None = None) -> str:
    """核心词常驻 + **课程术语全量** + 动态召回最相关的几个。

    ⚠️ 为什么课程术语要**全量**注入: 纯按匹配筛选会**死循环** —— 句子被 ASR 听错时
    (macroscopic→"max chocolate")术语匹配不到, 于是不注入, 模型没有领域先验, 永远修不回来。
    实测一堂真实热力学课: 65% 的句子矫正后与原文逐字相同, "max chocolate / apple
    temperature / chocolate bonds" 一路留着。课程术语表通常只有几十条(~100 token),
    全量注入的代价可接受。

    匹配: 字面包含优先, 否则与句中词的模糊相似度(difflib); 音近错听(如 nation→relation)
    相似度也较高, 能被召回。"""
    core = core if core is not None else CORE_TERMS
    always = always or []
    sent_l = sentence.lower()
    words = [w.strip(".,!?\"'()[]").lower() for w in sentence.split()]
    words = [w for w in words if len(w) > 3]

    core_l = {c.lower() for c in core}
    always_l = {a.lower() for a in always}
    scored: list[tuple[float, str]] = []
    for t in terms:
        tl = t.lower()
        if tl in core_l or tl in always_l:
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
    seen, picked = set(), []
    for t in list(core) + list(always) + [t for _, t in scored[:max_dyn]]:
        k = t.lower()
        if k not in seen:
            seen.add(k); picked.append(t)
    return "\n".join(picked) if picked else "(无特定术语)"


DRAFT_SYSTEM = "你是实时字幕翻译器。把用户给的英文口语快速译成中文, 只输出中文译文。"

# 只矫正、不翻译(关闭翻译时用): 同一个模型、同一份上下文, 只是不要中文。
FIX_SYSTEM = """你是英文课堂字幕校正助手。给你一句 ASR 转写的英文、课程领域、本课术语表, 以及最近几句上下文。
请只修正明显的语音识别错听(音近词、漏词、专有名词、术语)。**优先采纳课程术语表里的词**:
当某个词音近术语表条目时, 几乎可以断定是听错, 直接改成该术语。例如:
"we're max chocolate properties" -> "we're macroscopic properties";
"how we measure the stalcy" -> "how we measure the statistics";
"average on the lacker level" -> "average on the lattice level"。
本来就正确、也不音近任何术语的词句**原样保留**, 不要改写、润色、缩写或调整语序(防范过度矫正)。
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
        return _has_cjk(s)

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
        self._course_terms = course_term_list(glossary_path, course)
        self._domain = course_title(glossary_path, course)
        self._core = core_terms(course)
        self._max_ctx = max_context
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()   # 保护 _ensure 的懒加载(见该处说明)
        self._model_name = model

    def _ensure(self) -> None:
        """懒加载(双重检查加锁)。

        ⚠️ 必须自己加锁: 4 个调用点(fix_and_translate_stream / fix_stream /
        translate_draft / warmup)都在 `with self._lock` **之外**调它, 而草稿线程与
        定稿线程会并发进来。原先只做 check-then-act: 两线程可能同时看到
        `_model is None` 而**重复 load()**(显存翻倍); 更糟的是
        `self._model, self._tokenizer = load(...)` 是两条 STORE_ATTR, 另一线程可能
        读到 `_model` 已赋值而 `_tokenizer` 仍是 None -> `_apply_chat` 里
        `apply_chat_template` 抛 AttributeError。
        (2026-09-24 OCR 全量审计发现。)"""
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    _configure_mlx()
                    from mlx_lm import load
                    # ⚠️ **赋值顺序是这里的关键**: 必须先 tokenizer、后 model。
                    # 上面那句 `if self._model is None` 是**无锁**读的 —— 它把
                    # `_model` 当成就绪标志。原来写成 `self._model, self._tokenizer
                    # = load(...)`, 是两条 STORE_ATTR 且 **model 在前**: 另一线程可能
                    # 恰在两条之间读到 `_model` 已非 None, 于是跳过整段、拿着还是 None
                    # 的 `_tokenizer` 去 `apply_chat_template` → AttributeError。
                    # 换成 tokenizer 先落地, 则"看到 _model 有值"就等于"两样都齐了"。
                    _m, _tok = load(self._model_name)
                    self._tokenizer = _tok
                    self._model = _m

    def warmup(self) -> None:
        self._ensure()

    def _apply_chat(self, user_content: str, system: str = SYSTEM_PROMPT) -> str:
        return _apply_chat_generic(self._tokenizer, user_content, system)

    def _terms_context(self, en: str, context: list[str]) -> str:
        ctx = context[-self._max_ctx:] if self._max_ctx > 0 else []
        ctx_block = "\n".join(f"- {c}" for c in ctx) if ctx else "(无)"
        terms = select_terms(en, self._terms, core=self._core,
                             always=self._course_terms)
        return (f"{domain_block(self._domain)}"
                f"课程术语:\n{terms}\n\n"
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
        return guard_zh_result(parser.result(en), en)

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
        if on_zh and text and _has_cjk(text):    # 英文回显不是译文, 不上屏
            on_zh(text)
        return text


def load_translator(model: str, glossary_path: str | None, max_context: int = 2,
                    course: str | None = None):
    return Translator(model, glossary_path, max_context, course=course)
