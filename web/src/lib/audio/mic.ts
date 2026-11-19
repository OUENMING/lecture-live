// lib/audio/mic.ts —— AudioSource 的正式实现：getUserMedia → AudioWorklet（capture-worklet.js）→ 100 ms 帧
//
// 逻辑来自 S2 穿刺（~/classlive-web-spike/s2/web/main.js `createGraphPcm` + `handleFrame`）：
//   · 约束三档默认 EC=true / NS=false / AGC=false（R1 结论，S2b 建议 1）；
//   · 采音只走 worklet（S2 结论 2：文件源下 MediaRecorder 不差，但真麦未定 → 先按已验证的那条做）；
//   · 帧已经在 worklet 里切成 1600 样本 / 100 ms（见 static/worklets/capture-worklet.js）。
// 本文件只做接线：拿麦、装 worklet、把帧和电平交出去。
// 上课中途换设备（W14）：`switchDevice(deviceId)` 只换流 + 源节点，AudioContext / worklet / seq /
// onFrame 全部保留 —— 下游 Soniox 与核心时钟挂在 `seq` 上，重连或归零会把封口判乱。
// 当前设备被拔掉（track ended）→ 自动退回系统默认输入并回调 `onDeviceLost`。
//
// 依赖（getUserMedia / AudioContext）由调用方注入或默认取浏览器全局 —— 好处是 node 里能用假上下文测，
// 不用起浏览器。

import type { AudioFrame, AudioSource } from './index';

/** worklet 处理器名，必须与 static/worklets/capture-worklet.js 里的 PROCESSOR_NAME 一致 */
export const CAPTURE_PROCESSOR = 'classlive-capture';
/** worklet 模块的默认地址（静态文件，不走打包） */
export const DEFAULT_WORKLET_URL = '/worklets/capture-worklet.js';

export interface MicOptions {
	/** worklet 模块地址；测试 / 非根部署时可改 */
	workletUrl?: string;
	/** 每帧过 PeakNormalizer（与桌面链路同口径）；默认开 */
	normalize?: boolean;
	/** 指定输入设备（deviceId）；不传 = 系统默认输入（W14：上课中途可用 switchDevice 换） */
	deviceId?: string;
	/** 覆盖 getUserMedia（测试注入假实现） */
	getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
	/** 覆盖 AudioContext 构造（测试注入假实现） */
	createContext?: () => AudioContext;
}

/**
 * S2b 建议 1 的三开关；麦克风约束的唯一出口。
 * 传了 deviceId 就 `exact`（要的就是这一只，别的不要 —— 静默换设备会让字幕突然变小声/变噪声）。
 */
export function micConstraints(deviceId?: string): MediaStreamConstraints {
	const audio: MediaTrackConstraints = {
		echoCancellation: true,
		noiseSuppression: false,
		autoGainControl: false,
		channelCount: 1,
	};
	if (deviceId) audio.deviceId = { exact: deviceId };
	return { audio, video: false };
}

/** s16le 的 RMS → 0..1（给「6 秒没声音提示检查麦克风」用，不做 dB 换算） */
export function frameLevel(pcm: ArrayBuffer): number {
	const s = new Int16Array(pcm);
	if (!s.length) return 0;
	let acc = 0;
	for (let i = 0; i < s.length; i++) acc += (s[i] / 32768) ** 2;
	return Math.min(1, Math.sqrt(acc / s.length));
}

interface WorkletMessage {
	type?: string;
	data?: ArrayBuffer;
}

/**
 * 造一个麦克风源。`start` 之前 `level` 恒为 0；`stop` 之后可以再 `start` 一次。
 * `start` 的 promise 在 worklet 报到（`ready`）后 resolve —— 之后才会真的有帧。
 */
export function createMicSource(opts: MicOptions = {}): AudioSource {
	const workletUrl = opts.workletUrl ?? DEFAULT_WORKLET_URL;
	const normalize = opts.normalize ?? true;
	const gum = opts.getUserMedia ?? ((c: MediaStreamConstraints) => navigator.mediaDevices.getUserMedia(c));
	const newContext =
		opts.createContext ??
		(() => new AudioContext()); /* lint-ignore: 只有浏览器里有 AudioContext */

	let level = 0;
	let seq = 0;
	let onFrame: ((f: AudioFrame) => void) | null = null;
	let ready = false;

	let stream: MediaStream | null = null;
	let ctx: AudioContext | null = null;
	let node: AudioWorkletNode | null = null;
	let sink: GainNode | null = null;
	let source: MediaStreamAudioSourceNode | null = null;
	/** 想要的设备（null = 系统默认）；拔设备回退时置 null 且一直记得 */
	let wanted: string | null = opts.deviceId ?? null;
	/** 每次换流自增：异步回来的旧流靠它对账，别把新流顶掉 */
	let gen = 0;
	let lostCb: (() => void) | null = null;

	function emit(msg: WorkletMessage): void {
		if (msg.type !== 'frame' || !msg.data || !ready) return;
		const pcm = msg.data;
		level = frameLevel(pcm);
		onFrame?.({ pcm, seq: seq++ });
	}

	/** 收一条流：挂 track ended（拔设备 / 用户从系统里关掉） */
	function adopt(s: MediaStream): void {
		for (const t of s.getTracks()) t.addEventListener?.('ended', () => onTrackEnded(s));
	}

	function onTrackEnded(s: MediaStream): void {
		if (s !== stream) return;
		const wasWanted = wanted;
		wanted = null;
		lostCb?.();
		// 退回系统默认输入：帧 / seq / 回调 / worklet 全部不动
		void reroute(null).then((ok) => {
			if (!ok && wasWanted) level = 0;
		});
	}

	/**
	 * 只换「流 + 源节点」：AudioContext / worklet / sink / seq / onFrame 全不动。
	 * 失败时原链路原样保留（先拿新流，再拆旧的）。
	 */
	async function reroute(deviceId: string | null): Promise<boolean> {
		if (!ctx || !node || !sink) return false;
		const my = ++gen;
		let s: MediaStream;
		try {
			s = await gum(micConstraints(deviceId ?? undefined));
		} catch {
			return false;
		}
		// 期间又被换了一次 / 已经 stop：新流多余，退掉
		if (my !== gen || !ctx || !node || !sink) {
			s.getTracks().forEach((t) => t.stop());
			return false;
		}
		const next = ctx.createMediaStreamSource(s);
		next.connect(node);
		const old = source;
		const oldStream = stream;
		source = next;
		stream = s;
		wanted = deviceId;
		adopt(s);
		try {
			old?.disconnect();
		} catch {
			/* 已经断了 */
		}
		oldStream?.getTracks().forEach((t) => t.stop());
		return true;
	}

	async function start(cb: (f: AudioFrame) => void): Promise<void> {
		if (stream) throw new Error('麦克风已在采音；先 stop()');
		onFrame = cb;
		ready = false;

		const s = await gum(micConstraints(wanted ?? undefined));
		stream = s;
		adopt(s);
		try {
			ctx = newContext();
			await ctx.resume();
			await ctx.audioWorklet.addModule(workletUrl);
			node = new AudioWorkletNode(ctx, CAPTURE_PROCESSOR, {
				numberOfInputs: 1,
				numberOfOutputs: 1,
				outputChannelCount: [1],
				channelCount: 1,
				channelCountMode: 'explicit',
				channelInterpretation: 'speakers',
				processorOptions: { normalize, taps: 127 },
			});
			// 输出接增益 0 → 不外放（麦克风模式下不会啸叫），但节点确实在被渲染
			sink = ctx.createGain();
			sink.gain.value = 0;
			source = ctx.createMediaStreamSource(s);
			source.connect(node).connect(sink).connect(ctx.destination);

			const n = node;
			await new Promise<void>((resolve) => {
				// 处理器在构造期就 post 'ready'：这里同步挂 onmessage，赶在消息投递之前
				n.port.onmessage = (ev: MessageEvent) => {
					if (ev.data?.type === 'ready') {
						ready = true;
						resolve();
					} else {
						emit(ev.data as WorkletMessage);
					}
				};
			});
		} catch (e) {
			stop();
			throw e;
		}
	}

	function stop(): void {
		try {
			if (source) source.disconnect();
			if (node) node.disconnect();
			if (sink) sink.disconnect();
		} catch {
			/* 断开已经断开的东西不是错 */
		}
		stream?.getTracks().forEach((t) => t.stop());
		void ctx?.close().catch(() => {});
		gen++;
		stream = null;
		ctx = null;
		node = null;
		sink = null;
		source = null;
		onFrame = null;
		level = 0;
		seq = 0;
		ready = false;
	}

	return {
		start,
		stop,
		get level() {
			return level;
		},
		async switchDevice(deviceId: string): Promise<boolean> {
			return reroute(deviceId || null);
		},
		onDeviceLost(cb: () => void): void {
			lostCb = cb;
		},
	};
}
