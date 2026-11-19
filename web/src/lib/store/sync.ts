// 同步队列（接口见 index.ts）：把本机 pending 的句子交给注入的 putLines，成功后 ack。
// 依赖全注入（取句子、放句子、时钟、定时器），因此能拿假时钟把「退避序列 / 断网累积」逐条钉住，不碰网络、不写地址。
// 一次只飞一个批次；批次内容每次现取 pending → 天然不乱序、不重复（服务器再按 (session_id, seq) 兜一层）。
// 攒够 batchLines 立刻发，否则最多等 flushMs；失败 1s→2s→4s…封顶 60s，成功后清零并接着发剩下的。
// 退避期间 push 不会绕过退避（靠重试定时器一次补齐）；只有 flush（下课 / 关页面前）才不计退避直接试。

import type { Line, LineStore } from './index';

export interface SyncState {
	/** 连续失败次数（成功后清零） */
	failures: number;
	/** 下一批最早什么时候发（epoch ms）；0 = 没在等 */
	next_at: number;
	in_flight: boolean;
	/** 服务器已确认到的序号；null = 还没成功过 */
	last_acked: number | null;
	last_error: unknown;
	/** 累计发出的句子数（含重发） */
	sent: number;
}

export interface SyncClock {
	now(): number;
	/** 一次性定时器，返回取消函数 */
	setTimer(ms: number, cb: () => void): () => void;
}

export interface SyncParams {
	/** 攒够多少句立刻发（默认 10） */
	batchLines?: number;
	/** 最多等多久必发（默认 5000 ms） */
	flushMs?: number;
	/** 退避上限（默认 60000 ms） */
	maxBackoffMs?: number;
	/** 首次退避（默认 1000 ms），之后 ×2 */
	baseBackoffMs?: number;
	/** 单批上限（默认 200，对齐 API-web-v0 §2 的 lines_batch） */
	maxBatch?: number;
}

/** 注入的发送端：一批一次，回已确认到的序号 */
export type LineSink = (session_id: string, lines: Line[]) => Promise<{ acked_seq: number }>;

export interface SyncDeps {
	store: Pick<LineStore, 'pending' | 'ack'>;
	putLines: LineSink;
	session_id: string;
	clock?: SyncClock;
	params?: SyncParams;
	/** 状态变化，给界面显示「同步中 / 重试中」 */
	onState?: (state: SyncState) => void;
}

export interface Syncer {
	/** 又有新句子了：攒够就发，否则最多等 flushMs 再发 */
	push(): void;
	/** 不管攒多少都立刻发（下课、关页面前） */
	flush(): Promise<void>;
	state(): SyncState;
	stop(): void;
}

const realClock: SyncClock = {
	now: () => Date.now(),
	setTimer: (ms, cb) => {
		const id = setTimeout(cb, ms);
		return () => clearTimeout(id);
	}
};

export function createSyncer(deps: SyncDeps): Syncer {
	const clock = deps.clock ?? realClock;
	const p = deps.params ?? {};
	const batchLines = p.batchLines ?? 10;
	const flushMs = p.flushMs ?? 5000;
	const maxBackoff = p.maxBackoffMs ?? 60_000;
	const baseBackoff = p.baseBackoffMs ?? 1_000;
	const maxBatch = p.maxBatch ?? 200;

	const st: SyncState = {
		failures: 0,
		next_at: 0,
		in_flight: false,
		last_acked: null,
		last_error: null,
		sent: 0
	};

	let cancelTimer: (() => void) | null = null;
	/** 当前定时器什么时候到期，用来避免越排越晚 */
	let timerAt = 0;
	/** 这轮第一次 push 的时间：flushMs 从它算，不被后续句子推后 */
	let firstPushAt = 0;
	/** 自上次成功以来 push 了几次，只用来判「攒够没」；具体发什么仍看 pending() */
	let pushes = 0;
	/** 在飞时又有新句子：这一批收尾后再发一轮 */
	let dirty = false;
	let stopped = false;

	const emit = () => deps.onState?.({ ...st });

	const clearTimer = () => {
		cancelTimer?.();
		cancelTimer = null;
		timerAt = 0;
	};

	/** 挂一个定时器；已有更早的就不动，已有更晚的换掉 */
	const arm = (at: number) => {
		if (stopped) return;
		if (cancelTimer && timerAt <= at) return;
		clearTimer();
		timerAt = at;
		cancelTimer = clock.setTimer(Math.max(0, at - clock.now()), () => {
			cancelTimer = null;
			timerAt = 0;
			void send();
		});
	};

	const scheduleFailure = () => {
		st.failures += 1;
		const backoff = Math.min(maxBackoff, baseBackoff * 2 ** (st.failures - 1));
		st.next_at = clock.now() + backoff;
		// 退避中不攒「窗口」：到点重试会把 pending 一次补齐
		firstPushAt = 0;
		pushes = 0;
		emit();
		arm(st.next_at);
	};

	/** 发一轮；成功且还有剩就接着发（每次至少前进一句，pending 有限 → 必然收敛） */
	const send = async (): Promise<void> => {
		if (stopped || st.in_flight) return;
		if (st.next_at > clock.now()) {
			arm(st.next_at);
			return;
		}

		let pending: Line[];
		try {
			pending = await deps.store.pending(deps.session_id);
		} catch (e) {
			st.last_error = e;
			scheduleFailure();
			return;
		}
		if (pending.length === 0) {
			pushes = 0;
			firstPushAt = 0;
			emit();
			return;
		}

		const batch = pending.slice(0, maxBatch);
		const prevAcked = st.last_acked ?? 0;
		st.in_flight = true;
		st.sent += batch.length;
		emit();

		let confirmed: number;
		try {
			const res = await deps.putLines(deps.session_id, batch);
			confirmed = res.acked_seq;
			await deps.store.ack(deps.session_id, Math.max(prevAcked, confirmed));
		} catch (e) {
			st.in_flight = false;
			st.last_error = e;
			scheduleFailure();
			return;
		}

		st.in_flight = false;
		st.last_acked = Math.max(prevAcked, confirmed);
		st.last_error = null;
		const acked = st.last_acked;

		const rest = pending.filter((l) => l.seq > acked);
		if (rest.length === 0) {
			st.failures = 0;
			st.next_at = 0;
			pushes = 0;
			firstPushAt = 0;
			emit();
			// 在飞期间又来了句子：那些还没发过
			if (dirty) {
				dirty = false;
				await send();
			}
			return;
		}
		if (confirmed <= prevAcked) {
			// 服务器一句都没确认（没进展）：当失败退避，不让它空转
			scheduleFailure();
			return;
		}

		st.failures = 0;
		st.next_at = 0;
		pushes = rest.length;
		emit();
		await send();
	};

	return {
		push() {
			if (stopped) return;
			if (st.in_flight) {
				dirty = true; // 这一批收尾后会再跑一轮
				return;
			}
			if (st.next_at > clock.now()) return; // 退避中：重试定时器会一次补齐
			pushes += 1;
			if (firstPushAt === 0) firstPushAt = clock.now();
			if (pushes >= batchLines) {
				clearTimer();
				void send();
				return;
			}
			arm(firstPushAt + flushMs);
		},

		async flush() {
			if (stopped) return;
			clearTimer();
			pushes = 0;
			firstPushAt = 0;
			// 下课 / 关页面前不排队等退避：现在就试一把（失败会重新安排退避）
			st.next_at = 0;
			await send();
		},

		state: () => ({ ...st }),

		stop() {
			stopped = true;
			clearTimer();
		}
	};
}
