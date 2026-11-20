// lib/api/web-terms.ts —— 术语上云的三个接口（契约见 docs/API-web-v0-20261117.md §3.9，W19 / W24）。
// 对外：getTerms / putTerms / sharedTerms；一律经 `lib/api/http.request` + `identity.headers('web')`，
// 不自己 fetch，失败只抛 ApiError（由 `lib/terms/cloud.ts` 决定排队还是忽略）。
// 为什么课名在这里 encodeURIComponent：课名是用户随手输的中文 / 空格，拼 URL 前必须先编码；
// 共享池只认课号（如 ECON10770），非课号服务端回 course_key:null，这里原样透出，不自作判断。
// W24 起共享池分三层（course / subject / field，越远越杂），回包带 tier；**旧回包没有 tier 当 course** ——
// 服务端与网页可以不同步上线，客户端不能因为少一个字段就把共享词全丢掉。

import { request, type FetchLike } from './http';

/** 调用方可以不传网络选项：测试注入假 fetch，生产走默认 */
export interface WebTermsOptions {
	fetch?: FetchLike;
	/** 总墙钟；默认交给 http.ts（10 s），`termsForClass` 会自己压更短 */
	deadlineMs?: number;
}

export interface TermsCourse {
	text: string;
	/** 服务端最后一次写入的时刻（epoch 毫秒），本机按它比新旧 */
	updated_at: number;
}

export interface TermsAll {
	courses: Record<string, TermsCourse>;
}

export interface PutTermsResult {
	course: string;
	updated_at: number;
}

/** 共享词的三层（W24）：course = 同课号；subject = 同前缀的其它课；field = 同大类的其它前缀。
 * 越远越容易把识别往杂词上拉，所以服务端逐层提高门槛、逐层减少条数。 */
export type SharedTier = 'course' | 'subject' | 'field';

const TIERS: readonly SharedTier[] = ['course', 'subject', 'field'];

/** 未知 / 缺失的 tier 一律当最稳的 `course`（旧回包兼容） */
function tierOf(v: unknown): SharedTier {
	return typeof v === 'string' && (TIERS as readonly string[]).includes(v) ? (v as SharedTier) : 'course';
}

export interface SharedTerm {
	term: string;
	/** 写过这个词的码数（服务端按层卡门槛，越远要求越多） */
	users: number;
	/** 落在哪一层；旧回包没有这个字段 → `course` */
	tier: SharedTier;
}

export interface SharedTermsResult {
	/** 课名是课号时是 `ECON10770` 这种；自由文字课名 → null（不进池） */
	course_key: string | null;
	terms: SharedTerm[];
}

const base = (opts: WebTermsOptions) => ({ call: 'web_terms', ...opts });

/** 这枚码名下的全部个人术语（没有就是 `{courses:{}}`） */
export async function getTerms(opts: WebTermsOptions = {}): Promise<TermsAll> {
	const res = await request<Partial<TermsAll>>('/v1/web/terms', undefined, {
		...base(opts),
		method: 'GET'
	});
	const courses = res?.courses;
	return { courses: courses && typeof courses === 'object' ? courses : {} };
}

/** 写一门课（`text` 空串 = 删掉这门课）；`share` 决定要不要进同课共享池 */ 
export async function putTerms(
	course: string,
	text: string,
	share: boolean,
	opts: WebTermsOptions = {}
): Promise<PutTermsResult> {
	const res = await request<Partial<PutTermsResult>>(
		`/v1/web/terms/${encodeURIComponent(course)}`,
		{ text, share },
		{ ...base(opts), method: 'PUT' }
	);
	// 服务端没给 updated_at 就退回本机现在：本机靠它比新旧，0 会让下次 pull 永远认服务端
	const at = typeof res?.updated_at === 'number' ? res.updated_at : Date.now();
	return { course: res?.course ?? course, updated_at: at };
}

/** 共享池：只返回过了门槛的词（course/subject ≥2 码、field ≥3 码）；不是课号就 `{course_key:null, terms:[]}`。
 * 服务端已经按 tier → users 降序排好，这里**不改顺序**（客户端拿它只是补位）。 */
export async function sharedTerms(course: string, opts: WebTermsOptions = {}): Promise<SharedTermsResult> {
	const q = new URLSearchParams({ course });
	const res = await request<Partial<SharedTermsResult>>(`/v1/web/terms/shared?${q.toString()}`, undefined, {
		...base(opts),
		method: 'GET'
	});
	const terms = Array.isArray(res?.terms)
		? res.terms
				.filter(
					(t): t is SharedTerm =>
						!!t && typeof t.term === 'string' && Number.isFinite(t.users)
				)
				.map((t) => ({ term: t.term, users: t.users, tier: tierOf(t.tier) }))
		: [];
	return { course_key: typeof res?.course_key === 'string' ? res.course_key : null, terms };
}
