// lib/telemetry/buffer.ts —— 遥测事件缓冲与分批发送：唯一决定「什么时候发 / 发多少 / 失败等多久」的地方。
// 对外：createTelemetry({send?, now?, mono?, setTimer?, idle?, listen?}) → {track, setSession, flush, stop, size}。
// 热路径只有 track：一次 push，不序列化、不读 DOM、不 await；发送只在空闲回调或页面隐藏时发生。
// 失败退避 2→4→…→60 s 封顶，同一时刻只允许一个请求在途；每批 ≤500 条 / ≤56 KB（服务端上限 64 KB / 500）。
// `n` 恒为事件序号（每条自增）；超限丢弃的条数放在 `dropped` 事件的 `count` 里，不占用 `n`。

import { cost, MAX_BATCH_BYTES, MAX_BATCH_EVENTS, wireSend } from './sender';

/** 事件形状：墙钟、单调钟、类型、序号，后面跟调用方给的字段 */
export interface TelemetryEvent {
	t: number;
	mono: number;
	type: string;
	n: number;
	[extra: string]: unknown;
}

export interface TelemetrySendOpts {
	/** 页面隐藏 / 卸载时置 true（fetch keepalive） */
	keepalive: boolean;
}

/** 发送端：成功即 resolve；`{off:true}` 表示服务端关了遥测（本次页面内停发）。抛错 = 失败，等退避。 */
export type TelemetrySend = (
	events: TelemetryEvent[],
	opts: TelemetrySendOpts
) => Promise<{ off?: boolean } | void>;

export interface TelemetryOptions {
	/** 发送端（默认走 `/v1/web/telemetry`） */
	send?: TelemetrySend;
	/** 墙钟（默认 `Date.now`） */
	now?: () => number;
	/** 单调钟（默认 `performance.now()` 取整） */
	mono?: () => number;
	/** 一次性定时器，返回取消函数（默认 setTimeout） */
	setTimer?: (ms: number, cb: () => void) => () => void;
	/** 空闲调度（默认 requestIdleCallback，退 setTimeout 1 s） */
	idle?: (cb: () => void) => void;
	/** 是否挂 pagehide / visibilitychange（默认 true；测试里关掉） */
	listen?: boolean;
	/** 当前会话号（进请求体；随时可 setSession 换） */
	sessionId?: string;
	/** 定时 flush 周期（默认 30 s） */
	flushMs?: number;
	/** 攒够多少条立刻排队 flush（默认 200） */
	threshold?: number;
	/** 缓冲上限（默认 5000）；超了丢最旧的。测试里放大，免得丢最旧的搬运掩盖 track 的开销 */
	maxEvents?: number;
}

export interface Telemetry {
	/** 记一条事件。O(1)，永不抛，不序列化、不 await。 */
	track(type: string, data?: Record<string, unknown>): void;
	/** 这节课的号（没有就传 null）；之后的批次带上它 */
	setSession(id: string | null): void;
	/** 立刻试发一批（`pagehide` / `hidden` / `stop` 会带 keepalive）；永不抛 */
	flush(reason?: string): Promise<void>;
	/** 停定时器与监听；已缓冲的事件还会试发一次 */
	stop(): void;
	/** 缓冲里现有条数（测试 / 自检用） */
	size(): number;
}

export const MAX_EVENTS = 5000;
export const PERIOD_MS = 30_000;
export const DEFAULT_THRESHOLD = 200;
export const BASE_BACKOFF_MS = 2000;
export const MAX_BACKOFF_MS = 60_000;

const defaultTimer = (ms: number, cb: () => void): (() => void) => {
	const h = setTimeout(cb, ms);
	return () => clearTimeout(h);
};

const defaultIdle = (cb: () => void): void => {
	const ric = (globalThis as { requestIdleCallback?: (f: () => void, o?: { timeout: number }) => number })
		.requestIdleCallback;
	if (typeof ric === 'function') ric(cb, { timeout: 2000 });
	else setTimeout(cb, 1000);
};

const defaultMono = (): number =>
	typeof performance !== 'undefined' ? Math.round(performance.now()) : 0;

export function createTelemetry(opts: TelemetryOptions = {}): Telemetry {
	const now = opts.now ?? Date.now;
	const mono = opts.mono ?? defaultMono;
	const setTimer = opts.setTimer ?? defaultTimer;
	const idle = opts.idle ?? defaultIdle;
	const threshold = opts.threshold ?? DEFAULT_THRESHOLD;
	const periodMs = opts.flushMs ?? PERIOD_MS;
	const maxEvents = opts.maxEvents ?? MAX_EVENTS;

	let sessionId: string | null = opts.sessionId ?? null;
	const send = opts.send ?? wireSend(() => sessionId);

	const buffer: TelemetryEvent[] = [];
	let seq = 1;
	let dropped = 0;
	let off = false;
	let stopped = false;
	let sending = false;
	let again = false;
	let againKeepalive = false;
	/** stop() 之后还欠的最后一发（在途那批回来后补发；OCR A：原来 again 没人读，剩下的随页面卸载丢了） */
	let finalPending = false;
	let failures = 0;
	let idleQueued = false;
	let cancelRetry: (() => void) | null = null;
	let cancelPeriodic: (() => void) | null = null;

	/** 组一批：丢弃计数打头，然后按顺序装到条数 / 字节上限 */
	function buildBatch(): TelemetryEvent[] {
		const out: TelemetryEvent[] = [];
		let bytes = 0;
		if (dropped > 0) {
			const marker = { t: now(), mono: mono(), type: 'dropped', n: seq++, count: dropped } as TelemetryEvent;
			out.push(marker);
			bytes += cost(marker);
		}
		for (const ev of buffer) {
			if (out.length >= MAX_BATCH_EVENTS) break;
			const c = cost(ev);
			if (out.length > 0 && bytes + c > MAX_BATCH_BYTES) break;
			out.push(ev);
			bytes += c;
		}
		return out;
	}

	function queueIdle(): void {
		if (idleQueued || off || stopped) return;
		idleQueued = true;
		idle(() => {
			idleQueued = false;
			void pump(false);
		});
	}

	function scheduleRetry(): void {
		if (cancelRetry || off || stopped) return;
		const delay = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** Math.max(0, failures - 1));
		cancelRetry = setTimer(delay, () => {
			cancelRetry = null;
			void pump(false);
		});
	}

	/** 发一批；失败时那批原样留在缓冲里等退避。永不并发（sending 挡住第二次）。 */
	async function pump(keepalive: boolean): Promise<void> {
		if (off || (stopped && !finalPending)) return;
		if (sending) {
			again = true;
			againKeepalive ||= keepalive;
			return;
		}
		const batch = buildBatch();
		if (batch.length === 0) {
			finalPending = false;
			return;
		}
		const isFinal = stopped && finalPending;
		const markerN = batch[0]?.type === 'dropped' ? Number(batch[0].count) || 0 : 0;
		const fromBuffer = batch.length - (markerN > 0 ? 1 : 0);

		sending = true;
		let ok = false;
		try {
			const res = await send(batch, { keepalive });
			ok = true;
			if (res && res.off) off = true;
		} catch {
			/* 失败：不丢，等退避 */
		}
		sending = false;
		const rerun = again;
		const rerunKeepalive = againKeepalive;
		again = false;
		againKeepalive = false;
		if (isFinal) finalPending = false;

		if (ok) {
			failures = 0;
			buffer.splice(0, fromBuffer);
			dropped = Math.max(0, dropped - markerN);
			if (off) {
				buffer.length = 0;
				dropped = 0;
				cancelRetry?.();
				cancelRetry = null;
			}
			if (buffer.length > 0) {
				// 在途时来过 flush / stop（页面要走了）→ 立刻补发、带 keepalive；否则空闲时再发
				if (rerun || finalPending) void pump(rerunKeepalive || finalPending);
				else queueIdle();
			} else finalPending = false;
		} else {
			failures += 1;
			if (finalPending) finalPending = false;   // 页面在走：不再退避重试
			else scheduleRetry();
		}
	}

	function armPeriodic(): void {
		cancelPeriodic = setTimer(periodMs, () => {
			cancelPeriodic = null;
			armPeriodic();
			if (buffer.length > 0) queueIdle();
		});
	}

	function clearTimers(): void {
		cancelPeriodic?.();
		cancelPeriodic = null;
		cancelRetry?.();
		cancelRetry = null;
	}

	/** 页面隐藏 / 卸载：keepalive 最后试一次（body 已在 ≤56 KB 内） */
	const onPagehide = (): void => {
		void tel.flush('pagehide');
	};
	const onVisibility = (): void => {
		if (typeof document !== 'undefined' && document.visibilityState === 'hidden') void tel.flush('hidden');
	};

	function listenOnPage(): () => void {
		if (opts.listen === false) return () => {};
		if (typeof window === 'undefined' || typeof document === 'undefined') return () => {};
		window.addEventListener('pagehide', onPagehide);
		document.addEventListener('visibilitychange', onVisibility);
		return () => {
			window.removeEventListener('pagehide', onPagehide);
			document.removeEventListener('visibilitychange', onVisibility);
		};
	}

	const tel: Telemetry = {
		track(type, data) {
			if (off || stopped) return;
			try {
				const ev = { ...(data ?? {}), t: now(), mono: mono(), type, n: seq++ } as TelemetryEvent;
				buffer.push(ev);
				if (buffer.length > maxEvents) {
					const over = buffer.length - maxEvents;
					buffer.splice(0, over);
					dropped += over;
				}
				if (buffer.length >= threshold) queueIdle();
			} catch {
				/* 公开函数永不抛 */
			}
		},
		setSession(id) {
			sessionId = id || null;
		},
		async flush(reason = 'manual') {
			try {
				const urgent = reason === 'pagehide' || reason === 'hidden' || reason === 'stop';
				await pump(urgent);
			} catch {
				/* 永不抛 */
			}
		},
		stop() {
			if (stopped) return;
			unlisten();
			clearTimers();
			finalPending = true;
			stopped = true;
			void pump(true);
		},
		size() {
			return buffer.length;
		}
	};

	const unlisten = listenOnPage();
	armPeriodic();
	return tel;
}
