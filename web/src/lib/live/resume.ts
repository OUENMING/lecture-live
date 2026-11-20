// lib/live/resume.ts —— 上课中刷新 / 关掉浏览器再开：接回同一节课，而不是开一节新的。
// 对外：readActive / writeActive / clearActive（本机「正在上的课」记录）、pickResume（该不该接回）、
//       holdClassLock / tryTakeClassLock（Web Locks：同一节课只许一个标签页接手）。
//
// 为什么要有：以前每次进上课页都生成新的本机会话号 → 服务端开新一节、旧的一节永远停在 live，
// 管理面板上出现「两节课在上」，一节课的字幕被切成两段。
// 判据：记录里有同课名的会话、最后心跳在窗口内、且没被别的标签页占着 → 接回；否则开新的。
// 全部可注入存储 / 时钟 / 锁，Node 下直接测（tests/live/resume.test.ts）。

export const ACTIVE_KEY = 'cl.active';
/** 最后一次心跳到现在不超过这么久才接回（刷新是几秒；合盖 / 重开浏览器放宽到 15 分钟） */
export const RESUME_WINDOW_MS = 15 * 60_000;
/** 心跳间隔：每 10 s 写一次 `beat`（后台标签页被浏览器降频也远小于窗口） */
export const BEAT_MS = 10_000;

export interface Active {
	session_id: string;
	/** trim 后的课名；空串 = 没命名 */
	course: string;
	/** 最后一次心跳（epoch ms） */
	beat: number;
}

export interface KV {
	getItem(k: string): string | null;
	setItem(k: string, v: string): void;
	removeItem(k: string): void;
}

const SID_RE = /^web-\d{8}-\d{6}-[0-9a-f]{6}$/;

export function readActive(store: KV | null): Active | null {
	if (!store) return null;
	try {
		const raw = store.getItem(ACTIVE_KEY);
		if (!raw) return null;
		const v = JSON.parse(raw) as Partial<Active>;
		if (typeof v.session_id !== 'string' || !SID_RE.test(v.session_id)) return null;
		if (typeof v.course !== 'string' || !Number.isFinite(v.beat)) return null;
		return { session_id: v.session_id, course: v.course, beat: v.beat as number };
	} catch {
		return null;
	}
}

export function writeActive(store: KV | null, a: Active): void {
	try {
		store?.setItem(ACTIVE_KEY, JSON.stringify(a));
	} catch {
		/* 存不住就退回「刷新开新课」，不影响上课 */
	}
}

/** 只清自己的记录：别的标签页已经开了新一节，不能把人家的记录删了 */
export function clearActive(store: KV | null, session_id: string): void {
	try {
		if (readActive(store)?.session_id === session_id) store?.removeItem(ACTIVE_KEY);
	} catch {
		/* 同上 */
	}
}

/** 该接回哪一节：记录存在、同课名、心跳新鲜 → 它的会话号；否则 null */
export function pickResume(store: KV | null, course: string, now: number): string | null {
	const a = readActive(store);
	if (!a) return null;
	if (a.course !== course.trim()) return null;
	const age = now - a.beat;
	if (age < 0 || age > RESUME_WINDOW_MS) return null;
	return a.session_id;
}

// ---- Web Locks：防「两个标签页同时接手同一节课」（会重复推 seq、重复开麦、双份计费）----

export interface LockApi {
	request(
		name: string,
		opts: { ifAvailable: boolean },
		cb: (lock: unknown) => Promise<unknown> | unknown
	): Promise<unknown>;
}

const lockName = (sid: string) => `cl-class-${sid}`;

/**
 * 占住这节课，一直占到页面消失（刷新 / 关页浏览器自动放锁）或调 `release()`。
 * 拿到 → { release }；别人占着 → null；浏览器不支持 Web Locks → 当作拿到（退回只靠心跳判据）。
 */
export async function holdClassLock(locks: LockApi | null, sid: string): Promise<{ release(): void } | null> {
	if (!locks) return { release() {} };
	let release: () => void = () => {};
	const held = new Promise<void>((r) => (release = r));
	const got = await new Promise<boolean>((resolve) => {
		locks
			.request(lockName(sid), { ifAvailable: true }, (lock) => {
				if (!lock) {
					resolve(false);
					return;
				}
				resolve(true);
				return held; // 不 resolve = 一直占着
			})
			.catch(() => resolve(true)); // 锁 API 自己出错：别挡上课
	});
	return got ? { release } : null;
}

/**
 * 刷新的瞬间旧页面的锁可能还没放：短等几次再判「被别的标签页占着」。
 * 返回拿到的锁；一直占着 → null（调用方开新课）。
 */
export async function tryTakeClassLock(
	locks: LockApi | null,
	sid: string,
	wait: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
	tries = 6,
	gapMs = 300
): Promise<{ release(): void } | null> {
	for (let i = 0; i < tries; i++) {
		const h = await holdClassLock(locks, sid);
		if (h) return h;
		if (i < tries - 1) await wait(gapMs);
	}
	return null;
}
