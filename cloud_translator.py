"""云端翻译(DeepSeek OpenAI 兼容接口, 流式)。

与本地 Translator 同接口: warmup / fix_and_translate_stream / translate_draft / Result,
方便 run() 里用 --engine 切换与自动降级。

注意: 只有 **文本** 出本机(音频仍全本地);key 优先取 --api-key, 其次环境变量
DEEPSEEK_API_KEY, 再次 ~/lecture-live/.deepseek_key 文件。
"""
from __future__ import annotations
import json, os, pathlib

from translator import Result, _StreamParser, _clean_fix, domain_block

# 云端用**英文** system prompt: 实测比中文指令首字快约 15%(0.51s vs 0.60s),
# 译文也更自然; 符合 DeepSeek 官方"提示词保持单一语言"的建议。
SYSTEM_PROMPT_CLOUD = """You are a simultaneous interpreter for university lectures.
The user gives one utterance of English lecture speech (already transcribed by ASR),
the course title, the course terminology, and recent context.

Output exactly two lines and nothing else:
ZH: <fluent, accurate Chinese translation OF THE CORRECTED SENTENCE>
EN: <the same sentence with ASR mishearings fixed>

Rules:
1) Silently fix mishearings FIRST, then translate the fixed reading. If you change a word
   on the EN line, the ZH line must reflect the fix — never translate an uncorrected
   garble literally.
2) Fix only clear ASR mishearings (homophones, missing words, proper nouns, technical
   terms). When a word is close in sound to a course term, it is almost certainly a
   mishearing — use the term. Examples:
   "we're max chocolate properties" -> "we're macroscopic properties";
   "how we measure the stalcy" -> "how we measure the statistics";
   "average on the lacker level" -> "average on the lattice level".
3) Keep wording that is already correct and not close to any term exactly as-is.
   Do NOT rewrite, polish, reorder or summarise it (guard against over-correction).
4) Render technical terms the way Chinese textbooks do.
5) If the input is just a list of terms or syllabus keywords, translate them word by word;
   never output a summary heading like "课程术语" or "翻译". """

DRAFT_SYSTEM_CLOUD = ("You are a real-time subtitle translator. "
                      "Translate the user's English speech into Chinese. "
                      "Output ONLY the Chinese translation, no labels, no explanation.")

# 只矫正、不翻译。同样用英文 prompt(与 SYSTEM_PROMPT_CLOUD 同理由: 首字更快)。
FIX_SYSTEM_CLOUD = """You are an ASR transcript corrector for university lectures.
The user gives one utterance of English lecture speech transcribed by ASR, the course title,
the course terminology, and recent context.

Fix ONLY clear ASR mishearings (homophones, missing words, proper nouns, technical terms).
When a word is close in sound to a course term, it is almost certainly a mishearing — use
the term. Examples:
"we're max chocolate properties" -> "we're macroscopic properties";
"how we measure the stalcy" -> "how we measure the statistics";
"average on the lacker level" -> "average on the lattice level".
Keep wording that is already correct and not close to any term exactly as-is. Do NOT
rewrite, polish, shorten, reorder or summarise it (guard against over-correction).

Output ONLY the corrected English sentence on one line. No label, no quotes, no explanation,
and never any Chinese."""

KEY_FILE = pathlib.Path(__file__).with_name(".deepseek_key")
DEFAULT_BASE = "https://api.deepseek.com/v1"

# 模型偶尔把"术语表/标签"回显成译文(小模型 + 长术语块时的已知幻觉)
_ECHO_MARKERS = ("课程术语", "术语表", "术语解析", "以下翻译", "翻译如下", "校正及翻译")


def _looks_like_echo(zh: str, en: str) -> bool:
    z = (zh or "").strip()
    if not z:
        return True
    if any(m in z for m in _ECHO_MARKERS):
        return True
    # 输入很长却只吐出几个字 -> 大概率被截断/回显
    return len(en.split()) >= 10 and len(z) < 6


def load_api_key(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit.strip()
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env.strip()
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8").strip()
    return None


class CloudTranslator:
    """DeepSeek 流式翻译。失败由调用方捕获后降级本地。"""

    def __init__(self, api_key: str, model: str, base_url: str = DEFAULT_BASE,
                 glossary_terms: list[str] | None = None, max_context: int = 2,
                 timeout: float = 30.0, core: list[str] | None = None,
                 course_terms: list[str] | None = None, domain: str = "",
                 collect_usage: bool = False):
        import httpx
        self._key = api_key
        self._model = model
        self._base = base_url.rstrip("/")
        self._terms = glossary_terms or []
        self._core = core or []
        self._course_terms = course_terms or []
        self._domain = domain
        self._max_ctx = max_context
        self._timeout = timeout
        # 汇总最近一次请求的 usage(含 prompt_cache_hit_tokens / miss)。
        # 开着才给请求加 stream_options, 否则线上请求体一字不变。
        self._collect_usage = collect_usage
        self.last_usage: dict = {}
        # 共享连接池: 一节课上千次请求, 每次重建 TLS 握手白白多 ~100ms 首字延迟。
        # Client 对并发请求线程安全(官方文档保证); 断网时池中陈旧连接会抛错,
        # 由 EngineRouter 降级 -> 探针恢复, 语义与每次新建连接一致。
        self._client = httpx.Client(timeout=timeout)

    def warmup(self) -> None:
        pass                                   # 云端无需预热

    # ---- 工具 ----
    def _headers(self):
        return {"Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json"}

    def _stream_chat(self, messages: list[dict], max_tokens: int):
        """生成增量文本。必须显式关掉思考模式:
        DeepSeek V4 默认输出 reasoning_content, 真正译文才在 content 里,
        不关会让首字延迟翻好几倍且拿不到内容。"""
        payload = {"model": self._model, "messages": messages,
                   "stream": True, "max_tokens": max_tokens, "temperature": 0.2,
                   "thinking": {"type": "disabled"}}
        if self._collect_usage:
            # usage 只在流末尾那个 choices 为空的块里返回, 由下面的循环接住。
            payload["stream_options"] = {"include_usage": True}
        with self._client.stream("POST", f"{self._base}/chat/completions",
                                 headers=self._headers(), json=payload,
                                 timeout=self._timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("usage"):
                    self.last_usage = obj["usage"]
                try:
                    delta = obj["choices"][0].get("delta", {}).get("content")
                except (KeyError, IndexError):
                    continue
                if delta:
                    yield delta

    def probe(self) -> bool:
        """云端可用性探针(降级后的自动恢复用): 1 token, 尽量便宜、尽量短。
        探针失败只当"还没恢复", 不产生副作用; 成功由 EngineRouter 切回云端。"""
        try:
            for _ in self._stream_chat(
                    [{"role": "user", "content": "ping"}], max_tokens=1):
                return True
            return False
        except Exception:                        # noqa: BLE001
            return False

    def _terms_block(self, en: str) -> str:
        from translator import select_terms
        return select_terms(en, self._terms, core=self._core,
                            always=self._course_terms)

    # ---- 与本地 Translator 同接口 ----
    def fix_and_translate_stream(self, en: str, context: list[str],
                                 on_zh=None, on_en=None) -> Result:
        ctx = context[-self._max_ctx:]
        ctx_block = "\n".join(f"- {c}" for c in ctx) if ctx else "(none)"
        user = (f"{domain_block(self._domain)}"
                f"Course terms:\n{self._terms_block(en)}\n\n"
                f"Recent context (already corrected):\n{ctx_block}\n\n"
                f"ASR utterance:\n{en}\n\n"
                f"Now translate the ASR utterance above into Chinese and output exactly:\n"
                f"ZH: <Chinese translation of the CORRECTED sentence>\n"
                f"EN: <same utterance with ASR mishearings fixed>\n"
                f"Do not repeat the course-terms list. No headings."
                )
        msgs = [{"role": "system", "content": SYSTEM_PROMPT_CLOUD},
                {"role": "user", "content": user}]
        parser = _StreamParser(on_zh or (lambda s: None), on_en or (lambda s: None))
        for delta in self._stream_chat(msgs, max_tokens=220):
            parser.feed(delta)
        res = parser.result(en)
        if _looks_like_echo(res.zh, en):
            # 模型把术语表/标签回显了 -> 用极简 prompt 重试一次(该场景下最稳)
            res = self._retry_plain(en)
        return res

    def _noop(self, *_a) -> None:
        pass

    # ---- 只矫正英文(关闭中文翻译时用) ----
    def fix_stream(self, en: str, context: list[str], on_en=None) -> str:
        ctx = context[-self._max_ctx:]
        ctx_block = "\n".join(f"- {c}" for c in ctx) if ctx else "(none)"
        user = (f"{domain_block(self._domain)}"
                f"Course terms:\n{self._terms_block(en)}\n\n"
                f"Recent context (already corrected):\n{ctx_block}\n\n"
                f"ASR utterance:\n{en}\n\n"
                f"Now output that same utterance with ASR mishearings fixed. "
                f"English only — no Chinese, no label.")
        msgs = [{"role": "system", "content": FIX_SYSTEM_CLOUD},
                {"role": "user", "content": user}]
        buf: list[str] = []
        for delta in self._stream_chat(msgs, max_tokens=180):
            buf.append(delta)
            if on_en:
                on_en(delta)
        return _clean_fix("".join(buf), en)

    def _retry_plain(self, en: str) -> Result:
        """回显后的极简重试。

        ⚠️ **刻意不回调 UI**: 第一遍的垃圾(术语表/标签回显)已经推给 UI 并累加在
        屏上了 —— 重试文本若再追加, 屏上就是"垃圾+正文"粘连(实测收到并显示
        ' 课程术语 这个模块的阅读材料在 Brightspace 上。')。重试只用来产出干净的
        Result, 由 finalize 一次性换屏。"""
        msgs = [{"role": "system", "content":
                 "Translate the user's English lecture speech into Chinese, and correct "
                 "obvious ASR mishearings. Output exactly two lines:\n"
                 "ZH: <Chinese>\nEN: <corrected English>"},
                {"role": "user", "content": en}]
        parser = _StreamParser(self._noop, self._noop)
        try:
            for delta in self._stream_chat(msgs, max_tokens=220):
                parser.feed(delta)
        except Exception:                                 # noqa: BLE001
            pass
        return parser.result(en)

    def translate_draft(self, en: str, on_zh=None) -> str:
        msgs = [{"role": "system", "content": DRAFT_SYSTEM_CLOUD},
                {"role": "user", "content": en}]
        buf = []
        for delta in self._stream_chat(msgs, max_tokens=120):
            buf.append(delta)
        text = "".join(buf).strip()
        if on_zh and text:
            on_zh(text)
        return text


def load_translator(api_key: str, model: str, glossary_path: str | None,
                    max_context: int = 2, course: str | None = None):
    from translator import (load_terms, core_terms, course_term_list, course_title)
    return CloudTranslator(api_key, model,
                           glossary_terms=load_terms(glossary_path, course),
                           max_context=max_context, core=core_terms(course),
                           course_terms=course_term_list(glossary_path, course),
                           domain=course_title(glossary_path, course))
