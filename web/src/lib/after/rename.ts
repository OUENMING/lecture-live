// lib/after/rename.ts —— 下课后的「给这节课起名」：本机改名 + 术语归位 + 服务端改名（失败排队重试）。
// 对外：renameCourse({ store, api, local_session_id, course }) 与 retryPendingRenames(api)。
// 为什么先改本机再改服务端：课名可离线填，本机 rename 一成功，课后页立刻就是新名字；
// 服务端那步失败只留一条 `cl.rename.<local_session_id>` 排队（广域网断线是常态），不挡用户。
// 术语为什么读快照：开课时课名可能还没定，当时用的术语来自草稿键，快照是唯一「这节真正用过什么」的记录。
// 术语为什么还要推上云：这节用的词刚并进那门课，不推就只活在这台电脑上（pushTerms 自己排队，不挡改名）。

import { clearDraftTerms, mergeTermsInto, rememberCourse, termsText } from '../env';
import { pushTerms, type TermsPutApi } from '../terms/cloud';
import type { LineStore } from '../store';

/** 服务端改名只用到这一个方法（测试可整块替身，不造一个完整 api 对象）。
 *  `terms` 是可选的：给了就在术语归位后把整门课的术语推上云（本机那份已经在上面写好了）。*/
export interface RenameApi extends Partial<TermsPutApi> {
	renameSession(id: string, course: string): Promise<{ session_id: string; course: string }>;
}

export interface RenameParams {
	store: Pick<LineStore, 'renameSession'>;
	api: RenameApi;
	/** 本机会话 id（`connectLive` 给的那个） */
	local_session_id: string;
	course: string;
}

export interface RenameResult {
	ok: true;
	/** 快照里的术语并进那门课，新加了几个 */
	addedTerms: number;
	/** true = 服务端这次没改成，已排队等 `retryPendingRenames` */
	serverQueued: boolean;
}

export const COURSE_MAX = 80;
/** C0 + DEL + C1：课名来自随手输入，别让控制字符进账本 / localStorage 键 */
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/g;

const SESSTERMS = (local: string) => `cl.sessterms.${local}`;
const SRV = (local: string) => `cl.srv.${local}`;
const PENDING = (local: string) => `cl.rename.${local}`;
const PENDING_PREFIX = 'cl.rename.';

function get(key: string): string | null {
	try {
		return localStorage.getItem(key);
	} catch {
		return null;
	}
}
function set(key: string, value: string): void {
	try {
		localStorage.setItem(key, value);
	} catch {
		/* 存不住：这一程照用，刷新后丢 */
	}
}
function del(key: string): void {
	try {
		localStorage.removeItem(key);
	} catch {
		/* 同上 */
	}
}

/** 课名规整：去控制字符、trim；不合法（1–80 字以外）抛错，调用方据此不改任何东西 */
export function normalizeCourseName(raw: string): string {
	const s = String(raw ?? '')
		.replace(CONTROL, '')
		.trim();
	if (s.length < 1 || s.length > COURSE_MAX) throw new Error('课名 1–80 个字');
	return s;
}

/**
 * 给这节课起名。顺序有意如此：先验证 → 本机改名（失败即抛）→ 记住常用课名 → 术语归位 → 服务端改名。
 * 本机这步抛错（比如本机没有这节课）时，术语、常用课名、服务端一律不动。
 */
export async function renameCourse(p: RenameParams): Promise<RenameResult> {
	const course = normalizeCourseName(p.course);

	await p.store.renameSession(p.local_session_id, course);
	rememberCourse(course);

	const snapshot = get(SESSTERMS(p.local_session_id)) ?? '';
	const addedTerms = snapshot.trim() ? mergeTermsInto(course, snapshot) : 0;
	if (snapshot) del(SESSTERMS(p.local_session_id));
	clearDraftTerms();

	// 术语归位后把整门课推上云：与改服务端课名**并发**发，不叠加两次网络等待。
	// pushTerms 自己吞错误并排队，所以这一步失败不影响改名结果。
	const terms = termsText(course);
	const put = p.api.putTerms;
	const termsPush: Promise<void> =
		terms.trim() && put ? pushTerms({ putTerms: put }, course, terms) : Promise.resolve();

	const srv = get(SRV(p.local_session_id));
	if (!srv) {
		set(PENDING(p.local_session_id), course);
		await termsPush;
		return { ok: true, addedTerms, serverQueued: true };
	}
	try {
		await p.api.renameSession(srv, course);
		del(PENDING(p.local_session_id));
		await termsPush;
		return { ok: true, addedTerms, serverQueued: false };
	} catch {
		set(PENDING(p.local_session_id), course);
		await termsPush;
		return { ok: true, addedTerms, serverQueued: true };
	}
}

/**
 * 把排队的改课名逐个补发：每个键只试一次（失败留着下次），成功的删键，返回成功数。
 * 不重试风暴：这里没有退避也没有循环，节奏交给调用方（课后页打开 / 下次同步）。
 */
export async function retryPendingRenames(api: RenameApi): Promise<number> {
	let keys: string[] = [];
	try {
		// 用标准 Storage 接口枚举（`Object.keys(localStorage)` 依赖实现细节，测试里的假存储就没有）
		for (let i = 0; i < localStorage.length; i++) {
			const k = localStorage.key(i);
			if (k && k.startsWith(PENDING_PREFIX)) keys.push(k);
		}
	} catch {
		return 0;
	}

	let done = 0;
	for (const key of keys) {
		const local = key.slice(PENDING_PREFIX.length);
		const course = get(key);
		const srv = get(SRV(local));
		if (!course || !srv) continue; // 没服务端号就补发不了：服务端根本没开成这节课
		try {
			await api.renameSession(srv, course);
			del(key);
			done++;
		} catch {
			/* 留着，下次再说 */
		}
	}
	return done;
}
