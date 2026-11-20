// web/tests/audio/fakes.ts —— 采音测试共用假件：MediaStream / track / AudioContext / getUserMedia 农场。
//
// 为什么单独一份：devices.test.ts 与 switch.test.ts 都要「假麦」，各抄一份迟早漂。
// 只用于 node:test（`node --import tsx --test tests/audio/*.test.ts`），不碰浏览器 API。

/** 假 track：`stop()` 之后不会自己发 ended（与浏览器一致），要拔设备就显式 `fire('ended')` */
export function fakeTrack() {
	const listeners: Record<string, Array<() => void>> = {};
	return {
		kind: 'audio',
		stopped: false,
		listeners,
		stop() {
			this.stopped = true;
		},
		addEventListener(type: string, cb: () => void) {
			(listeners[type] ??= []).push(cb);
		},
		fire(type: string) {
			for (const cb of listeners[type] ?? []) cb();
		}
	};
}

export type FakeTrack = ReturnType<typeof fakeTrack>;

/** 假 MediaStream：只有 `getTracks`，但 track 会发 ended */
export function fakeStream(id: string) {
	const tracks = [fakeTrack()];
	return {
		id,
		tracks,
		getTracks: () => tracks
	};
}

export type FakeStream = ReturnType<typeof fakeStream>;

class FakeGain {
	gain = { value: 0 };
	disconnected = false;
	connect(dest: unknown) {
		return dest;
	}
	disconnect() {
		this.disconnected = true;
	}
}

export class FakeSourceNode {
	stream: FakeStream;
	disconnected = false;
	constructor(stream: FakeStream) {
		this.stream = stream;
	}
	connect(dest: unknown) {
		return dest;
	}
	disconnect() {
		this.disconnected = true;
	}
}

export class FakeAudioContext {
	sampleRate = 48000;
	state = 'suspended';
	destination = { id: 'destination' };
	modules: string[] = [];
	gains: FakeGain[] = [];
	workletNodes: FakeWorkletNode[] = [];
	sources: FakeSourceNode[] = [];
	closed = false;
	audioWorklet = {
		addModule: async (url: string) => {
			this.modules.push(url);
		}
	};
	async resume() {
		this.state = 'running';
	}
	createGain() {
		const g = new FakeGain();
		this.gains.push(g);
		return g;
	}
	createMediaStreamSource(stream: FakeStream) {
		const s = new FakeSourceNode(stream);
		this.sources.push(s);
		return s;
	}
	async close() {
		this.closed = true;
		this.state = 'closed';
	}
}

export class FakeWorkletNode {
	ctx: FakeAudioContext;
	name: string;
	opts: { channelCount?: number; outputChannelCount?: number[]; processorOptions: Record<string, unknown> };
	disconnected = false;
	port: { onmessage: ((ev: { data: unknown }) => void) | null } = { onmessage: null };
	constructor(ctx: FakeAudioContext, name: string, opts: FakeWorkletNode['opts']) {
		this.ctx = ctx;
		this.name = name;
		this.opts = opts;
		ctx.workletNodes.push(this);
	}
	connect(dest: unknown) {
		return dest;
	}
	disconnect() {
		this.disconnected = true;
	}
	/** 走一次 worklet 的消息处理器（帧 / ready 都从这里进） */
	post(data: unknown) {
		this.port.onmessage?.({ data });
	}
}

/** mic.ts 用的是全局 `AudioWorkletNode`；这里当替身装进去 */
export function installWorkletGlobal(): void {
	(globalThis as unknown as { AudioWorkletNode: unknown }).AudioWorkletNode = FakeWorkletNode;
}

/** 假 getUserMedia：每调一次造一条新流，记下约束，把流按顺序摆出来 */
export function micFarm() {
	const streams: FakeStream[] = [];
	const calls: MediaStreamConstraints[] = [];
	return {
		streams,
		calls,
		getUserMedia: async (c: MediaStreamConstraints) => {
			calls.push(c);
			const s = fakeStream(`stream-${streams.length}`);
			streams.push(s);
			return s as unknown as MediaStream;
		}
	};
}

/** 造 100 ms 的 s16le 帧 */
export function frameOf(value: number, samples = 1600): ArrayBuffer {
	const s = new Int16Array(samples);
	s.fill(value);
	return s.buffer;
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** 等到条件为真（有上限，不写死循环） */
export async function until(cond: () => unknown, ms = 800, step = 2): Promise<void> {
	const t0 = Date.now();
	while (!cond()) {
		if (Date.now() - t0 > ms) throw new Error(`until 超时（${ms} ms）`);
		await sleep(step);
	}
}
