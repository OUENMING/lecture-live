// 磁带源 —— 一盘录好的 Soniox 原始消息（`~/.classlive/eval/diar_spike/<课>/prod_ctx.jsonl`，
// 每条带 `_wall` = 到达秒数）回放成假的 AudioSource + 假的 AsrClient，
// 让 LiveSession 不连网、不用麦克风也能端到端跑一遍真链路（真 Python 核心）。
//
// 对外：`tapeReplay({ messages })` → `{ audio, asr, done, nMessages, duration }`。
// 帧与消息走**同一条**虚拟时间轴（按磁带时间排序），所以「帧推时钟 / 消息推进识别」的先后
// 与真链路一致，不是两个定时器赛跑；也不用 setInterval。
//
// `outage` 模拟断线：这段时间里 `asr.state === 'reconnecting'`、消息排队，恢复后补发
// （可加 `resend` 条重发，模拟服务端补回 final token）。课堂数据只在测试里从本机路径读。
import type { AsrClient, AsrConn } from '../asr';
import type { AudioFrame, AudioSource } from '../audio';

export interface TapeMessage {
	/** 磁带里的到达秒数 */
	wall: number;
	/** 原始 Soniox 消息（原样，含 `_wall`） */
	raw: string;
}

export interface TapeOptions {
	messages: readonly string[];
	/** 假麦克风的电平；默认恒 0.05（当有声）。给 `() => 0` 就是「一直没有声音」（E1） */
	level?: number | ((tapeS: number) => number);
	/** 每帧推进的磁带秒数；默认 0.1 = 真链路的 100 ms 帧 */
	frameS?: number;
	/** 带尾之后还继续走多少秒的帧（真会话里下课之前帧一直在走）；默认 0 */
	trailS?: number;
	/** 断线窗口（磁带秒）：期间连接 reconnecting、消息排队，`to` 之后补发 */
	outage?: { from: number; to: number; resend?: number };
	/** 每跑多少步让出一次事件循环；默认 200（测试要能中途 `end()`） */
	yieldEvery?: number;
}

export interface TapeReplay {
	audio: AudioSource;
	asr: AsrClient;
	/** 消息条数 / 磁带总时长（秒），给测试报数用 */
	readonly nMessages: number;
	readonly duration: number;
	/** 跑到带尾或被 `stop()` 打断时 resolve */
	done: Promise<void>;
}

/** 从 `_wall` 读到达秒数；缺了就沿用上一条（磁带里的坏行不该让回放卡住）。 */
export function parseTape(messages: readonly string[]): TapeMessage[] {
	const out: TapeMessage[] = [];
	let wall = 0;
	for (const raw of messages) {
		try {
			const w = (JSON.parse(raw) as { _wall?: unknown })._wall;
			if (typeof w === 'number' && Number.isFinite(w)) wall = w;
		} catch {
			/* 坏行照收：核心自己会给提示（U6） */
		}
		out.push({ wall, raw });
	}
	return out;
}

/**
 * 造一盘磁带。
 *
 * 用法（测试里）：
 * ```ts
 * const tape = tapeReplay({ messages });
 * const s = new LiveSession({ audio: tape.audio, asr: tape.asr, core, getKey: async () => 'tape' });
 * await s.start(); await tape.done; await s.end();
 * ```
 */
export function tapeReplay(opts: TapeOptions): TapeReplay {
	const msgs = parseTape(opts.messages);
	const frameS = opts.frameS ?? 0.1;
	const yieldEvery = opts.yieldEvery ?? 200;
	const levelOf: (tapeS: number) => number =
		typeof opts.level === 'function' ? opts.level : () => (typeof opts.level === 'number' ? opts.level : 0.05);
	const duration = msgs.length ? msgs[msgs.length - 1].wall : 0;

	let state: AsrConn = 'idle';
	let onMessage: ((raw: string) => void) | null = null;
	let onFrame: ((f: AudioFrame) => void) | null = null;
	let level = 0;
	let seq = 0;

	const { promise: done, resolve: resolveDone } = Promise.withResolvers<void>();

	// 断线期间排队；恢复时按序补发（真链路里是环形缓冲补音频 + 服务端重发 final token）
	const held: string[] = [];
	let stopped = false;

	const asr: AsrClient = {
		send: () => {
			/* 磁带里没有真音频：消息由时间轴推，不靠 send */
		},
		onMessage: (cb) => {
			onMessage = cb;
		},
		get state() {
			return state;
		},
		close: () => {
			state = 'closed';
			stopped = true;
			held.length = 0;
			resolveDone();
		}
	};

	const audio: AudioSource = {
		start: async (cb) => {
			if (state !== 'idle') throw new Error('磁带只能起一次');
			onFrame = cb;
			state = 'open';
			void pump();
		},
		stop: () => {
			stopped = true;
			level = 0;
			onFrame = null;
			resolveDone();
		},
		get level() {
			return level;
		}
	};

	async function pump(): Promise<void> {
		let i = 0; // 下一条消息
		let t = 0; // 虚拟磁带时刻
		let steps = 0;
		const out = opts.outage;

		while (!stopped && (i < msgs.length || t <= duration + (opts.trailS ?? 0))) {
			// 断线窗口：连接状态切一次，期间的消息排队
			const inOutage = out !== undefined && t >= out.from && t < out.to;
			if (inOutage && state === 'open') state = 'reconnecting';
			if (out && t >= out.to && state === 'reconnecting') {
				state = 'open';
				for (const raw of held.splice(0)) onMessage?.(raw);
				// 服务端重发：模拟重连后补回最后几条 final token（核心必须自己去重）
				for (let k = Math.max(0, i - (out.resend ?? 0)); k < i; k++) onMessage?.(msgs[k].raw);
			}

			// 该推进的下一条消息 / 下一帧，取时间靠前的那个
			const nextMsg = i < msgs.length ? msgs[i].wall : Infinity;
			if (nextMsg <= t) {
				if (inOutage) held.push(msgs[i].raw);
				else onMessage?.(msgs[i].raw);
				i += 1;
			} else {
				t += frameS;
				seq += 1;
				level = levelOf(t);
				onFrame?.({ pcm: new ArrayBuffer(0), seq });
			}

			steps += 1;
			if (steps % yieldEvery === 0) await new Promise<void>((r) => setTimeout(r, 0));
		}
		resolveDone();
	}

	return {
		audio,
		asr,
		nMessages: msgs.length,
		duration,
		done
	};
}
