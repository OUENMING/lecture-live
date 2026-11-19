// lib/live/wake.ts —— 上课期间的「别睡屏」+ 后台标签页记录（W14）。
//
// 对外：holdWakeLock() → { active, release() }；watchHidden(cb) → 取消订阅函数。
// 为什么持有 screen wake lock：桌面版靠进程级活动断言（power.py）挡空闲睡眠；网页没有那个口子，
// 只有 Screen Wake Lock（挡屏幕睡眠，挡不住合盖，这与桌面版同一句话）。
// 为什么静默降级：Safari 老版本 / 非安全上下文 / 用户拒绝都不该把课上不成；拿不到就当没这功能。
// 为什么不用 setInterval：页签被隐藏后浏览器会把定时器降频，计时全靠 visibilitychange +
// 一次 setTimeout，量出来的是墙钟跨越，不是「我醒了多少次」。

export interface WakeLockHandle {
	/** 当前是否真的持着（false = 不支持 / 被拒 / 已释放） */
	readonly active: boolean;
	release(): void;
}

export interface HiddenSpan {
	hiddenAt: number;
	shownAt: number;
}

/** 只用到这两个成员；浏览器真身与测试替身都满足 */
type WakeLockLike = { released?: boolean; release(): Promise<void> };
type WakeLockApi = { request(type: 'screen'): Promise<WakeLockLike> };
type VisDoc = {
	visibilityState?: string;
	hidden?: boolean;
	addEventListener?(type: 'visibilitychange', cb: () => void): void;
	removeEventListener?(type: 'visibilitychange', cb: () => void): void;
};

export interface WakeDeps {
	/** 取 navigator.wakeLock；默认全局。null = 浏览器没有这个 API */
	wakeLock?: WakeLockApi | null;
	/** 取 document；默认全局。null = 没有文档（node 测试） */
	doc?: VisDoc | null;
	/** 墙钟，测试可换 */
	now?: () => number;
}

function wakeApiOf(deps: WakeDeps): WakeLockApi | null {
	if (deps.wakeLock !== undefined) return deps.wakeLock;
	if (typeof navigator === 'undefined') return null;
	return (navigator as unknown as { wakeLock?: WakeLockApi }).wakeLock ?? null;
}

function docOf(deps: WakeDeps): VisDoc | null {
	if (deps.doc !== undefined) return deps.doc;
	if (typeof document === 'undefined') return null;
	return document as unknown as VisDoc;
}

function isHidden(doc: VisDoc | null): boolean {
	if (!doc) return false;
	return doc.visibilityState === 'hidden' || doc.hidden === true;
}

/** 上课期间持有屏幕唤醒锁；回到可见时自动重取（浏览器隐藏时会自己释放）。 */
export function holdWakeLock(deps: WakeDeps = {}): WakeLockHandle {
	const api = wakeApiOf(deps);
	const doc = docOf(deps);
	let lock: WakeLockLike | null = null;
	let stopped = false;
	let pending = false;

	async function acquire(): Promise<void> {
		// 被浏览器自己释放过的锁（隐藏标签页时）不能算「还持着」
		if (lock?.released) lock = null;
		if (!api || stopped || lock || pending) return;
		pending = true;
		try {
			const l = await api.request('screen');
			if (stopped) {
				void l.release().catch(() => {});
				return;
			}
			lock = l;
		} catch {
			lock = null; // 不支持 / 被拒：静默降级
		} finally {
			pending = false;
		}
	}

	const onVisible = () => {
		if (stopped || isHidden(doc)) return;
		if (lock && !lock.released) return;
		void acquire();
	};
	doc?.addEventListener?.('visibilitychange', onVisible);
	void acquire();

	return {
		get active() {
			return !!lock && !lock.released;
		},
		release() {
			if (stopped) return;
			stopped = true;
			doc?.removeEventListener?.('visibilitychange', onVisible);
			const l = lock;
			lock = null;
			void l?.release().catch(() => {});
		},
	};
}

/** 隐藏超过 `thresholdMs` 才记一次；回到可见时结账。不用 setInterval / setTimeout。 */
export function watchHidden(
	cb: (span: HiddenSpan) => void,
	opts: WakeDeps & { thresholdMs?: number } = {}
): () => void {
	const doc = docOf(opts);
	const now = opts.now ?? (() => Date.now());
	const threshold = opts.thresholdMs ?? 5000;
	let hiddenAt: number | null = null;
	let stopped = false;

	const onChange = () => {
		if (stopped) return;
		if (isHidden(doc)) {
			if (hiddenAt === null) hiddenAt = now();
			return;
		}
		if (hiddenAt === null) return;
		const started = hiddenAt;
		hiddenAt = null;
		const shownAt = now();
		if (shownAt - started >= threshold) cb({ hiddenAt: started, shownAt });
	};

	doc?.addEventListener?.('visibilitychange', onChange);

	return () => {
		if (stopped) return;
		stopped = true;
		hiddenAt = null;
		doc?.removeEventListener?.('visibilitychange', onChange);
	};
}
