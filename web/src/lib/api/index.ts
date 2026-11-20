// lib/api —— 到 Worker 的接口层（docs/API-web-v0-20261117.md）。一个函数对应一个接口。
// 对外：下面这些具名导出 + HTTP 错误类型 `ApiError`；界面只认这些，不碰 fetch / localStorage。
// 活的：领 Soniox 临时 key、查通行码、请求头与安装号、网页会话六个（`web-session.ts`）、
// 术语上云三个（`web-terms.ts`）；
// `uploadSlides` 仍是 v0 待确认（§3.6 只收抽取好的文字，接口形状未定）。
// `api` 这个对象只是给界面一个「一个名字对应一个接口」的入口，实现全在各自模块，这里不写逻辑。

export { ApiError, request, apiBase, type ApiErrorKind, type RequestOptions } from './http';
export {
	installId,
	passcode,
	isPasscodeShape,
	headers,
	timeZone,
	PASSCODE_RE,
	CLIENT_NAME
} from './identity';
export { getSonioxKey, createKeySource, checkPasscode, type SonioxKey } from './soniox-key';
export {
	startSession,
	putLines,
	endSession,
	getSession,
	listSessions,
	renameSession,
	deleteAll,
	type EndResult,
	type ListResult,
	type PutResult,
	type SessionSummary,
	type SessionView,
	type StartResult,
	type WebSessionOptions,
	type WireLine
} from './web-session';

export {
	getSummary,
	getNote,
	type ArtifactOptions,
	type LiveSummary,
	type SummaryChapter
} from './web-artifacts';
import { getSummary, getNote } from './web-artifacts';
import type { ArtifactOptions, LiveSummary } from './web-artifacts';

export {
	getTerms,
	putTerms,
	sharedTerms,
	type PutTermsResult,
	type SharedTerm,
	type SharedTermsResult,
	type TermsAll,
	type TermsCourse,
	type WebTermsOptions
} from './web-terms';

import { getTerms, putTerms, sharedTerms } from './web-terms';
import type {
	PutTermsResult,
	SharedTermsResult,
	TermsAll,
	WebTermsOptions
} from './web-terms';
import { startSession, putLines, endSession, getSession, listSessions, renameSession, deleteAll } from './web-session';
import type {
	EndResult,
	ListResult,
	PutResult,
	SessionView,
	StartResult,
	WebSessionOptions,
	WireLine
} from './web-session';

export interface SessionRef {
	session_id: string;
}

export const api = {
	/** POST /v1/web/session（`idemKey` = 本机会话 id，重试不会开出第二节课） */
	startSession: (
		course: string,
		started_at: number,
		idemKey: string,
		opts?: WebSessionOptions
	): Promise<StartResult> => startSession(course, started_at, idemKey, opts),
	/** PUT /v1/web/session/:id/lines → 已确认到的序号 */
	putLines: (id: string, lines: WireLine[], opts?: WebSessionOptions): Promise<PutResult> =>
		putLines(id, lines, opts),
	/** POST /v1/web/session/:id/end → 笔记任务 */
	endSession: (
		id: string,
		info: { ended_at: number; lines_total: number; level?: string },
		opts?: WebSessionOptions
	): Promise<EndResult> => endSession(id, info, opts),
	/** GET /v1/web/session/:id */
	getSession: (id: string, opts?: WebSessionOptions): Promise<SessionView> => getSession(id, opts),
	/** GET /v1/web/sessions */
	listSessions: (opts?: WebSessionOptions & { limit?: number; before?: string }): Promise<ListResult> =>
		listSessions(opts),
	/** PATCH /v1/web/session/:id —— 课后改课名（只动 course） */
	renameSession: (
		id: string,
		course: string,
		opts?: WebSessionOptions
	): Promise<{ session_id: string; course: string }> => renameSession(id, course, opts),
	/** POST /v1/web/slides —— 课件术语，v0 待确认（要浏览器里先抽文字，接口形状未定） */
	uploadSlides: async (_file: File): Promise<{ job_id: string }> => {
		throw new Error('api.uploadSlides: 未实现（API-web-v0 §3.6 待确认）');
	},
	/** DELETE /v1/web/data —— 立即删除这枚通行码名下的全部数据 */
	deleteAll: (opts?: WebSessionOptions): Promise<void> => deleteAll(opts),
	/** GET /v1/web/terms —— 这枚码名下全部课程的个人术语 */
	getTerms: (opts?: WebTermsOptions): Promise<TermsAll> => getTerms(opts),
	/** PUT /v1/web/terms/:course —— 写一门课（空串 = 删）；share 决定进不进共享池 */
	putTerms: (course: string, text: string, share: boolean, opts?: WebTermsOptions): Promise<PutTermsResult> =>
		putTerms(course, text, share, opts),
	/** GET /v1/web/terms/shared?course= —— 同课共享池（≥2 枚码写过的词） */
	sharedTerms: (course: string, opts?: WebTermsOptions): Promise<SharedTermsResult> =>
		sharedTerms(course, opts),
	/** GET /v1/web/session/:id/summary —— 实时总结（没出 = null） */
	getSummary: (id: string, opts?: ArtifactOptions): Promise<LiveSummary | null> => getSummary(id, opts),
	/** GET /v1/web/session/:id/note?fmt= —— 课后笔记原文（没出 = null） */
	getNote: (id: string, fmt?: 'html' | 'md', opts?: ArtifactOptions): Promise<string | null> => getNote(id, fmt, opts)
};
