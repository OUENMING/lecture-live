// `lib/prep/llm.ts` —— 抽候选术语的那一次非流式对话（唯一出口，走 `lib/api/http` 的 `request`）。
//
// 对外：`prepLLM({ system, user, fetch?, code?, deadlineMs? })` → 已解析的 `{ candidates: [...] }`。
// 请求体逐字照桌面 `build_notes._chat_json` → `llm_client.chat_json`：非流式、
// `response_format: json_object`、`max_tokens: 4000`、`temperature: 0.2`，形状只有 `[system, user]`
// （Worker 的 `prompt-guard.js` 只放行这一种）。
// 为什么不自己发 fetch：`lib/api/http.request` 已经是「基址 / 总墙钟 / 错误归一 / 请求头」的唯一定义点，
// 这里再写一份 fetch 就等于第二套超时与错误码。
import { ApiError, request, type FetchLike } from '../api/http';

/** Worker 只认这两个模型名（`handler.js` 的 LLM_MODELS）；桌面抽候选词也用 `deepseek-flash`。 */
export const PREP_MODEL = 'deepseek-flash';
/** 桌面 `prep._default_chat` 传的 4000 / 0.2 —— 数字照抄，不在这里另拍。
 *
 * ⚠️ **今天服务端会把它压到 400**：`handler.js` 的 `maxTokensCap(web)` 对 `X-ClassLive-Client: web`
 * 一律封顶 `FAST_LLM_MAX_TOKENS`（400，为实时翻译设的）。所以网页版抽候选词**会被截断**，
 * 出来的 JSON 很可能不完整。要真用起来得让服务端给 `X-ClassLive-Call: prep` 单开一档上限
 * （`handler.js` 不在本次改动范围，已写进交回的计划外问题）。这里照旧发 4000：
 * 截断是服务端的政策问题，客户端不该先自宫到 400。
 */
export const PREP_MAX_TOKENS = 4000;
export const PREP_TEMPERATURE = 0.2;
/** 墙钟：一份课件 3 万字、4000 token 输出，实测远低于这个数；超了就当作失败（不重试整场）。 */
export const PREP_DEADLINE_MS = 120_000;

/** 模型回来的一个候选（字段口径照 `prep.prepare()` 里那段循环：`term` / `confidence` / `why`）。 */
export interface PrepCandidate {
	term: string;
	confidence: number;
	why: string;
}

export interface PrepLLMArgs {
	/** `prep._prompt()` —— 不是 TS 里的字符串 */
	system: string;
	/** `prep._extract_user()` */
	user: string;
	/** 注入的 fetch（测试用假实现；默认浏览器全局） */
	fetch?: FetchLike;
	/** 用哪枚通行码（默认读存下的那枚） */
	code?: string;
	deadlineMs?: number;
}

/**
 * 一次对话 → 候选列表（**已收窄到有效项**：空 `term` / 类型不对的都丢掉）。
 *
 * ⚠️ 失败一律抛 `ApiError`／`Error`：调用方要把「这一块抽不出来」记进 `failed`，
 * 而不是当成「这块没有候选」—— 后者会让一个坏请求看起来像一份干净的课件。
 */
export async function prepLLM(args: PrepLLMArgs): Promise<PrepCandidate[]> {
	const body = {
		model: PREP_MODEL,
		stream: false,
		max_tokens: PREP_MAX_TOKENS,
		temperature: PREP_TEMPERATURE,
		response_format: { type: 'json_object' },
		messages: [
			{ role: 'system', content: args.system },
			{ role: 'user', content: args.user }
		]
	};
	const res = await request<{ choices?: { message?: { content?: unknown } }[] }>(
		'/v1/chat/completions',
		body,
		{
			call: 'prep',
			deadlineMs: args.deadlineMs ?? PREP_DEADLINE_MS,
			fetch: args.fetch,
			code: args.code
		}
	);
	const content = res?.choices?.[0]?.message?.content;
	if (typeof content !== 'string') throw new ApiError('server', 'empty_choices', '模型没回内容');
	let obj: unknown;
	try {
		obj = JSON.parse(content);
	} catch {
		throw new ApiError('server', 'bad_json', '模型回的不是 JSON（抽候选词失败）');
	}
	const list = (obj as { candidates?: unknown })?.candidates;
	if (!Array.isArray(list)) throw new ApiError('server', 'bad_shape', '模型没回 {candidates:[…]}');
	return normalize(list);
}

/** 模型的一项 → 一个候选；类型不对的丢掉（桌面那段循环的同口径）。 */
function normalize(list: readonly unknown[]): PrepCandidate[] {
	const out: PrepCandidate[] = [];
	for (const item of list) {
		if (!item || typeof item !== 'object') continue;
		const raw = item as { term?: unknown; confidence?: unknown; why?: unknown };
		// ⚠️ `String(undefined)` 会得到字面量 `"undefined"`；桌面的写法是 `str(x or "")`，照抄。
		const term = raw.term ? String(raw.term).trim() : '';
		if (!term) continue;
		const c = Number(raw.confidence);
		const confidence = Number.isFinite(c) ? Math.max(0, Math.min(1, c)) : 0;
		out.push({ term, confidence, why: raw.why ? String(raw.why).slice(0, 24) : '' });
	}
	return out;
}
