// lib/summary/poll.ts —— 定时拉一份服务端产物（实时总结 / 课后笔记状态），给界面一个只读快照。
// 对外：pollEvery({ load, everyMs, onchange }) → { refresh, stop }。
// 为什么自己排定时器而不是 setInterval：上一轮没回来不发下一轮（慢网下不叠请求）；
// 失败按 everyMs → ×2 → … 退避，封顶 maxMs，成功一次就回到 everyMs；标签页回到前台立刻补一轮。
// load 回 `done: true` 就停（课后笔记出完了 / 总结 final 了），不再打服务器。

export interface PollState<T> {
	/** 最近一次成功拿到的值（失败不清掉：断网时界面继续显示旧的） */
	value: T | null;
	/** 至少回来过一次（成功或「还没出」） */
	loaded: boolean;
	/** 最近一次失败的原因；成功后清空 */
	error: string;
	/** 最近一次成功的时刻（epoch ms） */
	at: number;
}

export interface PollParams<T> {
	load: () => Promise<{ value: T | null; done?: boolean }>;
	everyMs: number;
	maxMs?: number;
	onchange: (s: PollState<T>) => void;
}

export function pollEvery<T>(p: PollParams<T>): { refresh: () => void; stop: () => void } {
	const maxMs = p.maxMs ?? Math.max(p.everyMs, 120_000);
	const s: PollState<T> = { value: null, loaded: false, error: '', at: 0 };
	let timer: ReturnType<typeof setTimeout> | undefined;
	let inflight = false;
	let stopped = false;
	let wait = p.everyMs;

	const schedule = (ms: number) => {
		clearTimeout(timer);
		if (!stopped) timer = setTimeout(tick, ms);
	};

	async function tick() {
		if (stopped || inflight) return;
		inflight = true;
		try {
			const r = await p.load();
			if (stopped) return;
			s.value = r.value ?? s.value;
			s.loaded = true;
			s.error = '';
			s.at = Date.now();
			wait = p.everyMs;
			p.onchange({ ...s });
			if (r.done) stopped = true;
		} catch (e) {
			if (stopped) return;
			s.loaded = true;
			s.error = e instanceof Error ? e.message : String(e);
			wait = Math.min(maxMs, wait * 2);
			p.onchange({ ...s });
		} finally {
			inflight = false;
			schedule(wait);
		}
	}

	const onVisible = () => {
		if (typeof document !== 'undefined' && document.visibilityState === 'visible') refresh();
	};
	function refresh() {
		if (stopped) return;
		clearTimeout(timer);
		void tick();
	}

	if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisible);
	void tick();
	return {
		refresh,
		stop() {
			stopped = true;
			clearTimeout(timer);
			if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisible);
		}
	};
}
