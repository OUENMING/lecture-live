// lib/asr/soniox.ts —— AsrClient 的正式实现：原生 WebSocket + 临时 key + 断线重连 + keepalive
//
// 逻辑来自 S2 穿刺（~/classlive-web-spike/s2/web/main.js `connect` / `scheduleReconnect` /
// `flushRing` / `startKeepalive`，S2b 自测 26/26）。本文件只搬到 AsrClient 这个形状上：
//   · 浏览器不能设请求头 → 临时 key 走 WebSocket 子协议 `['soniox-api-key', key]`，
//     连上后第一条消息是 config（=`stream_asr._config()` 的字段与顺序，pcm 路线）；
//   · 临时 key 是 single_use，**每次连接前重新取** —— 取 key 的动作由调用方注入（`getKey`），
//     本模块不认任何发 key 的地址；
//   · 断线：指数退避 0.5→1→2→4→4→4 s，最多 6 次；断线期间环形缓冲 300 帧（30 s），
//     重连后**先补发再续**，溢出丢最旧的；一条连接活过 3 s 才算「稳」，才把退避计数清零；
//   · 每 15 s 无音频就发 `{"type":"keepalive"}`（服务端 20 s 没东西会掐流）；
//   · 旧连接迟到的消息一律丢：每次连接一个代号（gen），消息/关闭回调先比代号。
//
// 不解析 token：服务端消息原样交给 core（lib/core）。这里只管连接。

import type { AsrClient, AsrConn } from './index';

/** 第 1..6 次重连的等待（S2：`BACKOFF_MS`） */
export const DEFAULT_BACKOFF_MS: readonly number[] = [500, 1000, 2000, 4000, 4000, 4000];

/** WebSocket 的最小面（浏览器 WebSocket 满足它；测试用本地假类） */
export interface SocketLike {
	readonly readyState: number;
	readonly bufferedAmount?: number;
	binaryType?: string;
	onopen: ((ev: unknown) => void) | null;
	onmessage: ((ev: { data: unknown }) => void) | null;
	onclose: ((ev: { code?: number; reason?: string }) => void) | null;
	onerror: ((ev: unknown) => void) | null;
	send(data: string | ArrayBuffer): void;
	close(): void;
}

export interface SonioxOptions {
	/** 领临时 key（single_use，每次连接前调一次）。地址由调用方决定，本模块不写死 */
	getKey: () => Promise<string>;
	/** Soniox 的 WebSocket 地址（调用方给） */
	url: string;
	/** 模型；默认 `stt-rt-v6`（S2 同口径） */
	model?: string;
	/** 可选上下文，原样进 config.context */
	context?: unknown;
	/** 多久没发音频发一次 keepalive；默认 15 s */
	keepaliveMs?: number;
	/** keepalive 检查节拍；默认 500 ms（S2） */
	keepaliveTickMs?: number;
	/** 重连等待序列；默认 `DEFAULT_BACKOFF_MS` */
	backoffMs?: readonly number[];
	/** 连接活过多久算「稳」并清零退避；默认 3 s */
	stableMs?: number;
	/** 断线缓冲上限（帧）；默认 300 = 30 s */
	ringCap?: number;
	/** 握手超时；默认 10 s（S2） */
	handshakeTimeoutMs?: number;
	/** 造 socket（测试注入）；默认 `new WebSocket(url, protocols)` */
	createSocket?: (url: string, protocols: string[]) => SocketLike;
}

const WS_OPEN = 1;

/** config 的字段与顺序 = `stream_asr._config()`（pcm 路线，`audio_format:auto` 不在其中） */
function buildConfig(opts: SonioxOptions): Record<string, unknown> {
	const cfg: Record<string, unknown> = {
		model: opts.model ?? 'stt-rt-v6',
		audio_format: 'pcm_s16le',
		sample_rate: 16000,
		num_channels: 1,
		language_hints: ['en'],
		enable_endpoint_detection: true,
		// 说话人分离（桌面 `stream_asr.DIARIZE`，3.13.0）：实测不加延迟；换人断句、字母在 rt.py 发
		enable_speaker_diarization: true,
	};
	if (opts.context) cfg.context = opts.context;
	return cfg;
}

function browserSocket(url: string, protocols: string[]): SocketLike {
	return new WebSocket(url, protocols) as unknown as SocketLike;
}

const err = (e: unknown): Error => (e instanceof Error ? e : new Error(String(e)));
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/**
 * 造一个 Soniox 连接。构造即开始连（`state` 由 `connecting` 起）。
 * 所有失败都会自己退避重试；只有 `close()` 或 6 次重连都用完才停（`closed` / `failed`）。
 */
export function createSonioxClient(opts: SonioxOptions): AsrClient {
	const backoff = opts.backoffMs ?? DEFAULT_BACKOFF_MS;
	const keepaliveMs = opts.keepaliveMs ?? 15000;
	const keepaliveTickMs = opts.keepaliveTickMs ?? 500;
	const stableMs = opts.stableMs ?? 3000;
	const ringCap = opts.ringCap ?? 300;
	const handshakeTimeoutMs = opts.handshakeTimeoutMs ?? 10000;
	const createSocket = opts.createSocket ?? browserSocket;
	const configJson = JSON.stringify(buildConfig(opts));

	/** 断线期间的待补发帧（ArrayBuffer，先进先出） */
	const ring: ArrayBuffer[] = [];
	let state: AsrConn = 'idle';
	let socket: SocketLike | null = null;
	/** 连接代号：每条连接 +1；旧代号的消息、关闭事件一律忽略 */
	let gen = 0;
	let attempts = 0;
	let openedAt = 0;
	let lastSendAt = Date.now();
	let reconnecting = false;
	let keepaliveTimer: ReturnType<typeof setInterval> | null = null;
	let cb: ((raw: string) => void) | null = null;

	const setState = (s: AsrConn): void => {
		state = s;
	};
	// 用谓词而不是直接写 `state === …`：TS 会在循环条件里把 state 收窄，await 之后就不认别的值了
	const isOpen = (): boolean => state === 'open';
	const isTerminal = (): boolean => state === 'closed' || state === 'failed';

	function ringPush(pcm: ArrayBuffer): void {
		ring.push(pcm);
		// 溢出丢**最旧的**：新帧更接近当前
		while (ring.length > ringCap) ring.shift();
	}

	/** 重连成功后先把断线期间的帧补发出去（同步发完，不给新帧插队的机会） */
	function flushRing(ws: SocketLike): void {
		let sent = 0;
		while (ring.length && ws.readyState === WS_OPEN) {
			const buf = ring.shift() as ArrayBuffer;
			try {
				ws.send(buf);
			} catch {
				ring.unshift(buf); // 发不出去就退回缓冲，别丢
				break;
			}
			sent += 1;
		}
		if (sent) lastSendAt = Date.now();
	}

	function startKeepalive(): void {
		if (keepaliveTimer) return;
		keepaliveTimer = setInterval(() => {
			if (state !== 'open' || !socket) return;
			if (Date.now() - lastSendAt < keepaliveMs) return;
			try {
				socket.send(JSON.stringify({ type: 'keepalive' }));
				lastSendAt = Date.now();
			} catch {
				/* 下一拍再试；socket 迟早会 close 触发重连 */
			}
		}, keepaliveTickMs);
	}

	function stopKeepalive(): void {
		if (keepaliveTimer) clearInterval(keepaliveTimer);
		keepaliveTimer = null;
	}

	function giveUp(): void {
		ring.length = 0;
		stopKeepalive();
		socket = null;
		setState('failed');
	}

	/** 连一条；失败抛异常。config 与补发都在 onopen 里同步做完。 */
	async function connectOnce(): Promise<void> {
		const my = ++gen;
		setState('connecting');
		const key = await opts.getKey();
		if (my !== gen) throw new Error('连接已被取代');

		const ws = createSocket(opts.url, ['soniox-api-key', key]);
		socket = ws;
		try {
			ws.binaryType = 'arraybuffer';
		} catch {
			/* 某些假实现只读，无所谓 */
		}

		await new Promise<void>((resolve, reject) => {
			let settled = false;
			const timer = setTimeout(() => {
				if (settled) return;
				settled = true;
				try {
					ws.close();
				} catch {
					/* ignore */
				}
				reject(new Error('握手超时'));
			}, handshakeTimeoutMs);
			const finish = (e?: Error): void => {
				if (settled) return;
				settled = true;
				clearTimeout(timer);
				if (e) reject(e);
				else resolve();
			};

			ws.onopen = () => {
				if (my !== gen) return;
				openedAt = Date.now();
				lastSendAt = openedAt;
				setState('open');
				try {
					ws.send(configJson);
				} catch (e) {
					try {
						ws.close();
					} catch {
						/* ignore */
					}
					finish(err(e));
					return;
				}
				flushRing(ws);
				startKeepalive();
				finish();
			};

			ws.onmessage = (ev) => {
				if (my !== gen) return; // 迟到的消息：丢
				if (typeof ev.data !== 'string') return;
				cb?.(ev.data);
			};

			ws.onerror = () => {
				if (my !== gen) return;
				try {
					ws.close();
				} catch {
					/* ignore */
				}
				finish(new Error('WebSocket error'));
			};

			ws.onclose = () => {
				if (my !== gen) return;
				socket = null;
				if (state === 'closed') return;
				const wasOpen = openedAt > 0;
				const aliveMs = wasOpen ? Date.now() - openedAt : 0;
				openedAt = 0;
				if (!wasOpen) {
					finish(new Error('连接在握手前关闭'));
					return;
				}
				// 只有「非用户关闭」才重连；稳过 3 s 才清零退避（连上即被踢别一直 0.5 s）
				setState('reconnecting');
				if (aliveMs >= stableMs) attempts = 0;
				scheduleReconnect();
			};
		});
	}

	function scheduleReconnect(): void {
		if (reconnecting || state === 'closed' || state === 'failed') return;
		reconnecting = true;
		void (async () => {
			try {
				while (!isOpen() && !isTerminal()) {
					if (attempts >= backoff.length) {
						giveUp();
						return;
					}
					const delay = backoff[attempts];
					attempts += 1;
					await sleep(delay);
					if (isTerminal()) return;
					try {
						await connectOnce();
					} catch {
						/* 下一轮 */
					}
				}
			} finally {
				reconnecting = false;
			}
		})();
	}

	function send(pcm: ArrayBuffer): void {
		if (state === 'open' && socket && socket.readyState === WS_OPEN) {
			try {
				socket.send(pcm);
				lastSendAt = Date.now();
				return;
			} catch {
				/* 掉进缓冲 */
			}
		}
		ringPush(pcm);
	}

	function onMessage(fn: (raw: string) => void): void {
		cb = fn;
	}

	function close(): void {
		if (state === 'closed') return;
		stopKeepalive();
		gen += 1; // 之后旧 socket 的一切回调都丢
		const ws = socket;
		socket = null;
		ring.length = 0;
		setState('closed');
		try {
			ws?.close();
		} catch {
			/* ignore */
		}
	}

	const client: AsrClient = {
		send,
		onMessage,
		close,
		get state() {
			return state;
		},
	};

	// 构造即连；首次失败也算一次退避（与 S2 的 `connect(false)` 不同：S2 由页面按钮触发重连）
	void connectOnce().catch(() => {
		if (state === 'closed') return;
		setState('reconnecting');
		scheduleReconnect();
	});

	return client;
}
