// tests/telemetry/fakes.ts —— 测试替身：假时钟 / 假流与假 MediaRecorder / 假发送端，不含任何浏览器依赖。
// 用它的原因：遥测与录音的判据全是「什么时候发、发多少、失败等多久、哪段先落库」，真定时器与真 MediaRecorder
// 在 node 里跑不了也测不准；索引库有 fake-indexeddb（仓库已有），内存版走 audio-store 的 indexedDB:null。

import type { TelemetrySend, TelemetryEvent } from '../../src/lib/telemetry/buffer';
import type { AudioChunk, AudioStore } from '../../src/lib/telemetry/audio-store';
import { createAudioStore } from '../../src/lib/telemetry/audio-store';
import type { RecLike } from '../../src/lib/telemetry/recorder';

export const settle = async (rounds = 8): Promise<void> => {
	for (let i = 0; i < rounds; i++) await Promise.resolve();
	await new Promise((r) => setTimeout(r, 0));
	await new Promise((r) => setImmediate(r));
};

/** 假时钟：setTimer 记下来，advance 按时间顺序放行；`at()` 看下一批什么时候到 */
export function fakeClock(start = 1_000_000) {
	let now = start;
	let timers: { at: number; cb: () => void }[] = [];
	return {
		now: () => now,
		setTimer(ms: number, cb: () => void) {
			const t = { at: now + ms, cb };
			timers.push(t);
			return () => {
				timers = timers.filter((x) => x !== t);
			};
		},
		/** 待触发的定时器（毫秒延迟，升序） */
		due: () => timers.map((t) => t.at - now).sort((a, b) => a - b),
		async advance(ms: number) {
			const target = now + ms;
			for (;;) {
				const due = timers.filter((t) => t.at <= target).sort((a, b) => a.at - b.at)[0];
				if (!due) break;
				timers = timers.filter((x) => x !== due);
				now = due.at;
				due.cb();
				await settle();
			}
			now = target;
			await settle();
		}
	};
}

/** 假空闲调度：手动跑，验证「发送只在空闲时」 */
export function fakeIdle() {
	const queued: (() => void)[] = [];
	return {
		idle: (cb: () => void) => {
			queued.push(cb);
		},
		pending: () => queued.length,
		async run() {
			for (;;) {
				const cb = queued.shift();
				if (!cb) break;
				cb();
				await settle();
			}
		}
	};
}

export interface SentBatch {
	events: TelemetryEvent[];
	keepalive: boolean;
	bytes: number;
}

/** 假发送端：记录每批；`fail(n)` 让前 n 次失败，`off` 让第一次回「服务端关了」 */
export function fakeSend(opts: { fail?: number; off?: boolean; gate?: () => Promise<void> } = {}) {
	const batches: SentBatch[] = [];
	let fails = opts.fail ?? 0;
	let inFlight = 0;
	let maxInFlight = 0;
	let calls = 0;
	const send: TelemetrySend = async (events, o) => {
		calls += 1;
		inFlight += 1;
		maxInFlight = Math.max(maxInFlight, inFlight);
		try {
			if (opts.gate) await opts.gate();
			batches.push({ events, keepalive: o.keepalive, bytes: JSON.stringify(events).length });
			if (fails > 0) {
				fails -= 1;
				throw new Error('boom');
			}
			return opts.off ? { off: true } : undefined;
		} finally {
			inFlight -= 1;
		}
	};
	return {
		send,
		batches,
		maxInFlight: () => maxInFlight,
		/** send 被调用了几次（含还在 gate 上没回来的） */
		calls: () => calls,
		totalEvents: () => batches.reduce((s, b) => s + b.events.length, 0),
		types: () => batches.flatMap((b) => b.events.map((e) => e.type))
	};
}

/** 假 MediaRecorder：isTypeSupported 可配，手工 emit / finish，便于钉住「先落库再上传」 */
export class FakeMediaRecorder implements RecLike {
	static supported: string[] | null = ['audio/webm;codecs=opus'];
	static made: FakeMediaRecorder[] = [];

	static isTypeSupported(m: string): boolean {
		return (FakeMediaRecorder.supported ?? []).includes(m);
	}

	mimeType: string;
	ondataavailable: ((e: { data: Blob }) => void) | null = null;
	onstop: (() => void) | null = null;
	onerror: ((e: unknown) => void) | null = null;
	timeslice = 0;
	stopped = false;
	readonly stream: unknown;
	readonly opts: Record<string, unknown>;

	constructor(stream: unknown, opts: Record<string, unknown>) {
		this.stream = stream;
		this.opts = opts;
		this.mimeType = String(opts.mimeType ?? '');
		FakeMediaRecorder.made.push(this);
	}

	start(timeslice?: number): void {
		this.timeslice = timeslice ?? 0;
	}

	stop(): void {
		this.stopped = true;
	}

	/** 假装 timeslice 到了：交出一段 */
	emit(size: number): void {
		this.ondataavailable?.({ data: new Blob([new Uint8Array(size)], { type: this.mimeType }) });
	}

	/** 停下来的回调（`stop()` 之后由浏览器调） */
	finish(): void {
		this.onstop?.();
	}

	static latest(): FakeMediaRecorder {
		const last = FakeMediaRecorder.made.at(-1);
		if (!last) throw new Error('还没造过 MediaRecorder');
		return last;
	}

	static reset(supported: string[] | null = ['audio/webm;codecs=opus']): void {
		FakeMediaRecorder.supported = supported;
		FakeMediaRecorder.made = [];
	}
}

/** 假的 MediaStream：每次 clone 出一套新 track，记录谁被停了（原件的 track 不许被停） */
export function fakeStream() {
	const tracks: { stopped: boolean; stop(): void }[] = [];
	const mkTrack = () => {
		const t = { stopped: false, stop: () => void (t.stopped = true) };
		tracks.push(t);
		return t;
	};
	const mkStream = (): any => {
		const own = [mkTrack()];
		return { kind: 'audio', getTracks: () => own, clone: () => mkStream() };
	};
	const stream = mkStream();
	return {
		stream: stream as unknown as MediaStream,
		/** 原始那条流的 track（上传结束后仍应是 stopped === false） */
		original: tracks[0],
		/** 克隆出来的那些 track（stop() 之后应全被停） */
		clones: () => tracks.slice(1),
		clonesAllStopped: () => tracks.slice(1).every((t) => t.stopped)
	};
}

/** 内存版录音仓库（indexedDB: null）—— 不需要 fake-indexeddb 的那几个用例用它 */
export const memoryAudio = (): Promise<AudioStore> => createAudioStore({ indexedDB: null });

/** 从仓库读回来的段（按 session 升序），只留判据要看的字段 */
export async function dumpStore(store: AudioStore): Promise<Pick<AudioChunk, 'session' | 'n' | 'size'>[]> {
	const rows = await store.list();
	return rows.map((r) => ({ session: r.session, n: r.n, size: r.size }));
}
