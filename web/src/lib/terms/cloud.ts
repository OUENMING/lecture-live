// lib/terms/cloud.ts —— 术语上云与同课共享（客户端唯一有网络的一处）。
// 对外：pullTerms / pushTerms / saveTermsSynced / retryPendingTerms / shareEnabled /
// setShareEnabled / termsForClass / mergeForContext。个人术语权威在服务端，本机是缓存 + 离线写入口：
// 每门课比 `cl.termsMeta` 里的 updated_at，本机有没推上去的改动时本机赢再补推（`cl.terms.pending.*`）。
// 共享池只是加分项：拉不到 / 超时就当没有，绝不影响个人术语或上课（`termsForClass` 不抛）。

import type { WebTermsOptions } from '../api';
/** 深一级取 tier 的类型：`../api` 那个 barrel 还没转出它，这一层只借个名字 */
import type { SharedTier } from '../api/web-terms';
import { parseTerms, saveTerms, termsText } from '../env';

/** 写只需要这一个：`lib/after` 的调用方不一定有 getTerms / sharedTerms */
export interface TermsPutApi {
	putTerms(
		course: string,
		text: string,
		share: boolean,
		opts?: WebTermsOptions
	): Promise<{ updated_at: number }>;
}

/** 这一层用到三个接口（测试可整块替身，不必造完整 api 对象） */
export interface TermsApi extends TermsPutApi {
	getTerms(opts?: WebTermsOptions): Promise<{ courses: Record<string, { text: string; updated_at: number }> }>;
	sharedTerms(
		course: string,
		opts?: WebTermsOptions
	): Promise<{ course_key: string | null; terms: { term: string; users: number; tier?: SharedTier }[] }>;
}

/** 本机每门课上一次「与服务端对齐」的时刻（epoch 毫秒） */
export const META_KEY = 'cl.termsMeta';
/** 本机共享开关（默认开） */
export const SHARE_KEY = 'cl.shareTerms';
/** 没推上去的改动：键是课名（原样），值是待推的 text */
export const PENDING_PREFIX = 'cl.terms.pending.';

/** 送进 Soniox 的术语条数上限：官方文档没写 context.terms 的上限，300 条是保守截断，
 * 再多的术语对识别率的边际收益已经很低，却会把开课首包撑大（首包 = 全部术语一遍发完）。*/
export const CONTEXT_TERMS_MAX = 300;

function readRaw(key: string): string | null {
	try {
		return localStorage.getItem(key);
	} catch {
		return null;
	}
}
function writeRaw(key: string, value: string): void {
	try {
		localStorage.setItem(key, value);
	} catch {
		/* 存不住：这一程照用 */
	}
}
function delRaw(key: string): void {
	try {
		localStorage.removeItem(key);
	} catch {
		/* 同上 */
	}
}

/** 本机记录的每门课 updated_at；存对象而不是「一课一键」，省掉键名转义问题 */
function readMeta(): Record<string, number> {
	const raw = readRaw(META_KEY);
	if (!raw) return {};
	try {
		const v = JSON.parse(raw) as unknown;
		if (!v || typeof v !== 'object') return {};
		const out: Record<string, number> = {};
		for (const [k, n] of Object.entries(v as Record<string, unknown>)) {
			if (typeof n === 'number' && Number.isFinite(n)) out[k] = n;
		}
		return out;
	} catch {
		return {};
	}
}
function metaOf(course: string): number {
	return readMeta()[course] ?? 0;
}
function setMeta(course: string, at: number): void {
	const m = readMeta();
	m[course] = at;
	writeRaw(META_KEY, JSON.stringify(m));
}

/** 这门课有没有等着的推送；有就是「本机有没推上去的改动」 */
const pendingOf = (course: string): string | null => readRaw(PENDING_PREFIX + course);
const hasPending = (course: string): boolean => pendingOf(course) !== null;

/** 共享开关：本机，默认开 */
export function shareEnabled(): boolean {
	return readRaw(SHARE_KEY) !== 'false';
}
export function setShareEnabled(v: boolean): void {
	writeRaw(SHARE_KEY, v ? 'true' : 'false');
}

/**
 * 把一门课本机术语推上云；失败就记进 `cl.terms.pending.<course>` 等下次补推。
 * 空课名（草稿）直接返回：草稿不上云。成功时顺手清掉排队键并记下服务端给的 updated_at。
 */
export async function pushTerms(api: TermsPutApi, course: string, text: string): Promise<void> {
	const c = course.trim();
	if (!c) return;
	if (termsText(c) !== text) saveTerms(c, text);
	try {
		const res = await api.putTerms(c, text, shareEnabled());
		delRaw(PENDING_PREFIX + c);
		setMeta(c, typeof res?.updated_at === 'number' ? res.updated_at : Date.now());
	} catch {
		writeRaw(PENDING_PREFIX + c, text);
	}
}

/** 界面上「保存术语」走这里：本机立刻生效（不等网络），推不上去自己排队 */
export async function saveTermsSynced(api: TermsPutApi, course: string, text: string): Promise<void> {
	const c = course.trim();
	if (!c) {
		// 草稿照存本机（`saveTerms` 会落到 `cl.terms.__next`），不上云
		saveTerms('', text);
		return;
	}
	saveTerms(c, text);
	await pushTerms(api, c, text);
}

/** 把排队的术语逐个补发：每门课只试一次，成功的删键，返回成功数 */
export async function retryPendingTerms(api: TermsPutApi): Promise<number> {
	const keys: string[] = [];
	try {
		for (let i = 0; i < localStorage.length; i++) {
			const k = localStorage.key(i);
			if (k && k.startsWith(PENDING_PREFIX)) keys.push(k);
		}
	} catch {
		return 0;
	}

	let done = 0;
	for (const key of keys) {
		const course = key.slice(PENDING_PREFIX.length);
		const text = readRaw(key);
		if (course.trim().length === 0 || text === null) continue;
		try {
			const res = await api.putTerms(course, text, shareEnabled());
			delRaw(key);
			setMeta(course, typeof res?.updated_at === 'number' ? res.updated_at : Date.now());
			done++;
		} catch {
			/* 留着，下次再说 */
		}
	}
	return done;
}

/** 一门课的处理结果：本机赢就要补推，服务端赢就直接落本机 */
type Verdict = 'local' | 'remote' | 'same';

function decide(course: string, remoteAt: number, remoteText: string): Verdict {
	const local = metaOf(course);
	if (hasPending(course)) return 'local';
	// OCR B：本机有内容但从没和服务端对齐过（meta=0，例如老版本留下的）→ 别拿远端盖掉（localStorage 盖了找不回），本机赢、补推
	if (local === 0 && termsText(course).trim() && termsText(course) !== remoteText) return 'local';
	if (remoteAt > local) return 'remote';
	return 'same';
}

/**
 * 拉个人术语并进本机：每门课比 `updated_at`，新的赢；本机有没推上去的改动时本机赢，然后补推。
 * 服务端没有的课不动（本机可能是刚敲的草稿，等它自己推）。
 * 返回本次真正改了本机几门课（有判据用，界面可以显示「已同步 N 门课的术语」）。
 */
export async function pullTerms(api: TermsApi): Promise<{ updated: number }> {
	let remote: Record<string, { text: string; updated_at: number }>;
	try {
		const res = await api.getTerms();
		remote = res?.courses && typeof res.courses === 'object' ? res.courses : {};
	} catch {
		return { updated: 0 }; // 拉不到就当没拉：本机照用，下次再来
	}

	let updated = 0;
	for (const [course, entry] of Object.entries(remote)) {
		const c = course.trim();
		if (!c || !entry || typeof entry.text !== 'string') continue;
		const remoteAt = typeof entry.updated_at === 'number' ? entry.updated_at : 0;
		const verdict = decide(c, remoteAt, entry.text);
		if (verdict === 'local') {
			// 本机赢：把本机这份补推上去（失败它会自己排队）
			await pushTerms(api, c, termsText(c));
			continue;
		}
		if (verdict === 'same') continue;
		if (termsText(c) === entry.text) {
			setMeta(c, remoteAt);
			continue;
		}
		saveTerms(c, entry.text);
		setMeta(c, remoteAt);
		updated++;
	}
	return { updated };
}

/** 比较用的归一：大小写 + 折叠空白；共享池去重就用这个口径 */
const fold = (t: string): string => t.trim().toLowerCase().replace(/\s+/g, ' ');

/**
 * 开课要用的术语：个人（本机）+ 共享池里个人没有的词，个人在前。
 * 共享池拉不到 / 超时就给空数组 —— 绝不抛：这条路上的失败不该挡住上课。
 * W24 起服务端分三层（course / subject / field）并已排好序：这里**不动顺序**，只把个人已有的去掉，
 * 最后交给 `mergeForContext` 按 300 条截断（离得近的层自然排在前面、先占名额）。
 */
export async function termsForClass(
	api: TermsApi,
	course: string,
	opts: { timeoutMs?: number } = {}
): Promise<{ personal: string[]; shared: string[] }> {
	const personal = parseTerms(termsText(course));
	if (!course.trim()) return { personal, shared: [] };

	const timeoutMs = opts.timeoutMs ?? 1500;
	let timer: ReturnType<typeof setTimeout> | undefined;
	const timeout = new Promise<null>((resolve) => {
		timer = setTimeout(() => resolve(null), timeoutMs);
	});
	let res: Awaited<ReturnType<TermsApi['sharedTerms']>> | null = null;
	try {
		res = await Promise.race([
			api.sharedTerms(course, { deadlineMs: timeoutMs }).catch(() => null),
			timeout
		]);
	} catch {
		res = null;
	} finally {
		clearTimeout(timer);
	}
	if (!res || !Array.isArray(res.terms)) return { personal, shared: [] };

	const have = new Set(personal.map(fold));
	const shared: string[] = [];
	for (const t of res.terms) {
		if (!t || typeof t.term !== 'string') continue;
		const k = fold(t.term);
		if (!k || have.has(k)) continue;
		have.add(k);
		shared.push(t.term.trim());
	}
	return { personal, shared };
}

/** 个人在前 + 共享补位，按 Soniox context 的上限截断（`CONTEXT_TERMS_MAX`） */
export function mergeForContext(personal: readonly string[], shared: readonly string[]): string[] {
	return [...personal, ...shared].slice(0, CONTEXT_TERMS_MAX);
}
