// 开课前就判断浏览器能不能用（E5）：第一版只承诺电脑上的 Chrome / Edge。
// `?ua=safari|firefox|mobile` 只给设计稿预览出错状态用。
// 通行码的读写已搬到 `lib/api/identity.ts`（请求头与安装号要跟它在一处），这里只 re-export ——
// 现有调用方（`routes/+page.svelte`）不用改。

import { passcode } from './api/identity';

export type BrowserVerdict = 'ok' | 'other' | 'mobile';

export function browserVerdict(): BrowserVerdict {
	if (typeof navigator === 'undefined') return 'ok';
	const forced = new URLSearchParams(location.search).get('ua');
	if (forced === 'mobile') return 'mobile';
	if (forced === 'safari' || forced === 'firefox') return 'other';

	const ua = navigator.userAgent;
	const uad = (navigator as Navigator & { userAgentData?: { mobile: boolean; brands: { brand: string }[] } })
		.userAgentData;
	const mobile = uad?.mobile ?? /Android|iPhone|iPad|Mobile/i.test(ua);
	if (mobile) return 'mobile';
	const brands = uad?.brands.map((b) => b.brand).join(' ') ?? ua;
	const chromium = /Google Chrome|Microsoft Edge|Chrome\/|Edg\//.test(brands) && !/OPR\/|Firefox/.test(ua);
	// 置顶小窗靠 Document Picture-in-Picture，只有 Chromium 116+ 有
	const pip = 'documentPictureInPicture' in globalThis;
	return chromium && pip ? 'ok' : 'other';
}

const COURSE_KEY = 'cl.courses';

function read<T>(key: string, fallback: T): T {
	try {
		const v = localStorage.getItem(key);
		return v === null ? fallback : (JSON.parse(v) as T);
	} catch {
		return fallback;
	}
}
function write(key: string, value: unknown): void {
	try {
		localStorage.setItem(key, JSON.stringify(value));
	} catch {
		/* 隐私窗口 / 禁用存储：不记也能用 */
	}
}

export const recentCourses = (): string[] => read<string[]>(COURSE_KEY, []);
export function rememberCourse(c: string): void {
	write(COURSE_KEY, [c, ...recentCourses().filter((x) => x !== c)].slice(0, 6));
}

// 通行码：真正读写住在 `lib/api/identity.ts`（与安装号、请求头同一处，老键名不变）
export const hasPass = (): boolean => passcode.get() !== '';
export const savePass = (p: string): void => passcode.set(p);

// 术语表（A9 最简版）：每门课一份，存本机；一行一个术语，`#` 开头是注释（与桌面 glossary/<课号>.txt 同格式）
// 课名可以为空（2026-11-19 起课名可课后填）：空课名的术语存进草稿键 `cl.terms.__next`，
// 下课命名时用 `mergeTermsInto` 并进那门课、再 `clearDraftTerms()`。
export const DRAFT_TERMS_KEY = 'cl.terms.__next';
const termKey = (course: string) => {
	const c = course.trim().toLowerCase();
	return c ? `cl.terms.${c}` : DRAFT_TERMS_KEY;
};
export function termsText(course: string): string {
	try {
		return localStorage.getItem(termKey(course)) ?? '';
	} catch {
		return '';
	}
}
export function saveTerms(course: string, text: string): void {
	try {
		if (text.trim()) localStorage.setItem(termKey(course), text);
		else localStorage.removeItem(termKey(course));
	} catch {
		/* 不记也能上课，只是没有术语 */
	}
}
/** 把 text 里的术语并进该课已存的术语：去重（不分大小写）保序，返回新加了几个；空课名不写 */
export function mergeTermsInto(course: string, text: string): number {
	if (!course.trim()) return 0;
	const have = parseTerms(termsText(course));
	const seen = new Set(have.map((t) => t.toLowerCase()));
	const added: string[] = [];
	for (const t of parseTerms(text)) {
		const k = t.toLowerCase();
		if (seen.has(k)) continue;
		seen.add(k);
		added.push(t);
	}
	if (added.length) saveTerms(course, [...have, ...added].join('\n'));
	return added.length;
}
export function clearDraftTerms(): void {
	try {
		localStorage.removeItem(DRAFT_TERMS_KEY);
	} catch {
		/* 读不到存储也就没草稿可清 */
	}
}
/** 界面上显示课名的地方都走这里：没命名就说「未命名的课」，不留空白 */
export const courseLabel = (course: string): string => course.trim() || '未命名的课';
/** 解析成术语列表：去注释、去空行、去重、单条 ≤200 字（桌面 TERM_MAX_CHARS）、最多 100 条（TERMS_MAX） */
export function parseTerms(text: string): string[] {
	const seen = new Set<string>();
	const out: string[] = [];
	for (const raw of text.split(/\r?\n/)) {
		const t = raw.trim();
		if (!t || t.startsWith('#') || t.length > 200) continue;
		const k = t.toLowerCase();
		if (seen.has(k)) continue;
		seen.add(k);
		out.push(t);
		if (out.length >= 100) break;
	}
	return out;
}

// 「没听懂」标记：按会话存本机（句子序号列表），课后页高亮、导出时带 ❓
const markKey = (sid: string) => `cl.marks.${sid}`;
export function marksOf(sid: string): number[] {
	const v = read<unknown>(markKey(sid), []);
	return Array.isArray(v) ? v.filter((x): x is number => Number.isInteger(x)) : [];
}
export function saveMarks(sid: string, seqs: number[]): void {
	write(markKey(sid), seqs);
}
/** 「删除这台电脑上的全部记录」：标记跟字幕一起清 */
export function clearLocalMarks(): void {
	try {
		for (const k of Object.keys(localStorage)) if (k.startsWith('cl.marks.')) localStorage.removeItem(k);
	} catch {
		/* 读不到存储也就没东西可清 */
	}
}
