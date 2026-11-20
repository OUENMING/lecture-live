// lib/telemetry/recorder.ts —— 课堂录音分段：每段一个 MediaRecorder → 先落 cl-audio → 再排队上传 → 成功才删本地。
// ⚠️ 不用 timeslice 切片：那样只有第 1 段带容器头，后面的段单独打不开、丢一段后面全废；
//    改成每 chunkMs 停一个、立刻开下一个（段间空隙毫秒级），每段都是能单独播放的完整文件。
// 对外：createClassRecorder({stream, sessionId, upload?, chunkMs?}) → {start, stop, setSessionId}；
//       resumePending(...) 把库里残留的段补传（页面重开时用）。
// 用传进来的同一条流（clone 后只停克隆），不调 getUserMedia、不碰 mic.ts 的音频图 —— 上课体验优先。
// 不支持 MediaRecorder / 编码的浏览器整个模块变空操作并记一条 recorder_unsupported，不抛错。
// 上传同一时刻只一个在途、只在 navigator.onLine 时发；本地超 200 MB 丢最旧的段并记一条事件。

import type { Telemetry } from './buffer';
import { createAudioStore } from './audio-store';
import type { AudioChunk, AudioStore } from './audio-store';
import { putChunk } from './upload';

export const DB_BYTES_MAX = 200 * 1024 * 1024;
export const CHUNK_MS = 60_000;
export const STOP_WAIT_MS = 3000;
export const RETRY_MS = 30_000;
export const MIME_TYPES = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'];

/** 只用到 MediaRecorder 的这几个成员；测试塞假件即可 */
export interface RecLike {
	mimeType?: string;
	ondataavailable: ((e: { data: Blob }) => void) | null;
	onstop: (() => void) | null;
	onerror: ((e: unknown) => void) | null;
	start(timeslice?: number): void;
	stop(): void;
}
export type RecCtor = new (stream: MediaStream, opts: Record<string, unknown>) => RecLike;

/** 一段一次的上传端（默认走 `PUT /v1/web/audio/:session/:n`） */
export type AudioUpload = (session: string, n: number, blob: Blob, mime: string) => Promise<void>;

export interface RecorderOptions {
	/** 采音流（会 clone；调用方那条不许被停） */
	stream: MediaStream;
	sessionId: string;
	upload?: AudioUpload;
	chunkMs?: number;
	/** 事件出口（可选；不给就完全不记） */
	tel?: Telemetry;
	/** 覆盖 MediaRecorder（测试注入假件；undefined = 取浏览器全局） */
	mediaRecorder?: RecCtor | null;
	/** 覆盖本地仓库（测试注入内存件） */
	store?: AudioStore;
	setTimer?: (ms: number, cb: () => void) => () => void;
	now?: () => number;
	/** 本地存量上限（默认 200 MB；测试里调小，免得造 200 MB 假数据） */
	maxLocalBytes?: number;
}

export interface ClassRecorder {
	start(): void;
	stop(): Promise<void>;
	setSessionId(id: string): void;
	/** 换麦克风后改录新流：当前段收尾，下一段起用新流的克隆 */
	switchStream(stream: MediaStream): void;
}

const defaultStore = (): Promise<AudioStore> => createAudioStore();
const online = (): boolean => (globalThis as any).navigator?.onLine !== false;

function pickMime(Ctor: RecCtor | null): string {
	if (!Ctor) return '';
	const supported = (Ctor as any).isTypeSupported;
	if (typeof supported !== 'function') return MIME_TYPES[0];
	for (const m of MIME_TYPES) {
		try {
			if (supported.call(Ctor, m)) return m;
		} catch {
			/* 这个编码问不出来，试下一个 */
		}
	}
	return '';
}

export function createClassRecorder(opts: RecorderOptions): ClassRecorder {
	const chunkMs = opts.chunkMs ?? CHUNK_MS;
	const maxLocalBytes = opts.maxLocalBytes ?? DB_BYTES_MAX;
	const now = opts.now ?? Date.now;
	const setTimer =
		opts.setTimer ??
		((ms: number, cb: () => void) => {
			const h = setTimeout(cb, ms);
			return () => clearTimeout(h);
		});
	const say = (type: string, data?: Record<string, unknown>): void => {
		try {
			opts.tel?.track(type, data);
		} catch {
			/* 遥测不影响录音 */
		}
	};

	let sessionId = opts.sessionId;
	let store: AudioStore | null = opts.store ?? null;
	let rec: RecLike | null = null;
	let Ctor: RecCtor | null = null;
	/** 换麦后待用的新流克隆：当前段停下后下一段改用它 */
	let pendingStream: MediaStream | null = null;
	let cancelCut: (() => void) | null = null;
	let cloned: MediaStream | null = null;
	let mime = '';
	let nextN = 1;
	let started = false;
	let stopped = false;
	let drainScheduled = false;
	let cancelRetry: (() => void) | null = null;
	let queue: AudioChunk[] = [];
	const storeReady: Promise<AudioStore> = (async () => {
		const s = store ?? (await defaultStore());
		store = s;
		return s;
	})();
	// 每段的落盘任务串成一条链：保证 (session, n) 顺序，也让 stop() 能等到最后一段进库
	let chain: Promise<void> = Promise.resolve();
	const finishWaiters: (() => void)[] = [];

	const uploader: AudioUpload = opts.upload ?? putChunk;

	/** 一次只飞一个；成功才删本地那段。失败留着等 resumePending / 下一次重试。 */
	async function drain(): Promise<void> {
		if (!store || drainScheduled) return;
		drainScheduled = true;
		try {
			while (queue.length) {
				const chunk = queue[0];
				// 用这一段录制时的会话号（中途换会话不能让旧段跑到新会话名下）
				if (!chunk.session) break;
				if (!online()) {
					scheduleRetry();
					return;
				}
				try {
					await uploader(chunk.session, chunk.n, chunk.blob, chunk.mime);
				} catch (e) {
					say('rec_upload_failed', { n: chunk.n, err: String((e as Error)?.message ?? e).slice(0, 120) });
					scheduleRetry();
					return;
				}
				queue = queue.slice(1);
				try {
					await store.remove(chunk.session, chunk.n);
				} catch {
					/* 删不掉就留着，下次 resumePending 重传一遍（服务端按 n 幂等） */
				}
			}
		} finally {
			drainScheduled = false;
		}
	}

	function scheduleRetry(): void {
		if (cancelRetry || stopped) return;
		cancelRetry = setTimer(RETRY_MS, () => {
			cancelRetry = null;
			if (!stopped) void drain();
		});
	}

	/** 本地超过 200 MB：丢最旧的一段并记账（丢的是本地副本，已传成功的早就不在了） */
	async function enforceQuota(): Promise<void> {
		const s = store;
		if (!s) return;
		for (let guard = 0; guard < 8; guard++) {
			const total = await s.total();
			if (total <= maxLocalBytes) return;
			const old = await s.oldest();
			if (!old) return;
			await s.remove(old.session, old.n);
			queue = queue.filter((c) => !(c.session === old.session && c.n === old.n));
			say('rec_dropped_local', { n: old.n, size: old.size });
		}
	}

	/** 一段到手：先落库，再排上传。任何异常只记账。 */
	function onChunk(blob: Blob): void {
		const n = nextN++;
		const chunk: AudioChunk = {
			session: sessionId,
			n,
			mime: blob.type || mime,
			at: now(),
			size: blob.size,
			blob
		};
		chain = chain
			.then(async () => {
				const s = await storeReady;
				try {
					await s.put(chunk);
				} catch {
					say('rec_chunk_unstored', { n });
				}
				queue = [...queue, chunk];
				void drain();
				await enforceQuota();
			})
			.catch(() => {
				/* 这一段丢了不影响后面的段 */
			});
	}

	return {
		start() {
			if (started || stopped) return;
			started = true;
			Ctor = (opts.mediaRecorder === undefined
				? ((globalThis as any).MediaRecorder as RecCtor | undefined)
				: opts.mediaRecorder) ?? null;
			const picked = pickMime(Ctor);
			if (!Ctor || !picked) {
				say('recorder_unsupported');
				return;
			}
			mime = picked;
			try {
				cloned = opts.stream.clone();
			} catch {
				say('rec_error', { where: 'clone' });
				return;
			}
			if (!startSegment()) return;
			// 新段从库里已有的最大序号接着编（同一会话重开页面不撞主键）
			void storeReady
				.then((s) => s.maxSeq(sessionId))
				.then((max) => {
					if (max + 1 > nextN) nextN = max + 1;
				})
				.catch(() => {});
			const nav = (globalThis as any).navigator;
			if (nav?.addEventListener) nav.addEventListener('online', () => void drain());
			say('rec_start', { chunk: chunkMs, mime });
		},
		async stop() {
			if (stopped) return;
			stopped = true;
			cancelRetry?.();
			cancelRetry = null;
			cancelCut?.();
			cancelCut = null;
			const seg = rec;
			if (!seg) {
				stopTracks();
				return;
			}
			const last = new Promise<void>((resolve) => finishWaiters.push(resolve));
			try {
				seg.stop();
			} catch {
				/* 已经停了 */
			}
			const timeout = new Promise<void>((resolve) => setTimer(STOP_WAIT_MS, resolve));
			await Promise.race([last, timeout]);
			// 最后一段先落库再交回（最多 3 s，卡住就交给 resumePending）
			await Promise.race([chain.catch(() => {}), timeout]);
			stopTracks();
			say('rec_stop', { n: nextN - 1 });
			void drain();
		},
		switchStream(stream: MediaStream) {
			if (!started || stopped || !Ctor) return;
			try {
				pendingStream = stream.clone();
			} catch {
				say('rec_error', { where: 'switch' });
				return;
			}
			cut();
		},
		setSessionId(id: string) {
			sessionId = id;
			void drain();
		}
	};

	/** 开一段：失败只记账（录音丢了不影响上课） */
	function startSegment(): boolean {
		if (!Ctor || !cloned) return false;
		try {
			const seg = new Ctor(cloned, { mimeType: mime, audioBitsPerSecond: 24_000 });
			seg.ondataavailable = (e) => {
				if (e?.data && e.data.size > 0) onChunk(e.data);
			};
			seg.onerror = () => say('rec_error');
			seg.onstop = () => {
				if (rec === seg) rec = null;
				for (const fn of finishWaiters.splice(0)) fn();
				if (stopped) return;
				if (pendingStream) {
					stopTracks();
					cloned = pendingStream;
					pendingStream = null;
				}
				startSegment();
			};
			seg.start();
			rec = seg;
			cancelCut = setTimer(chunkMs, cut);
			return true;
		} catch {
			say('rec_error');
			rec = null;
			stopTracks();
			return false;
		}
	}

	/** 到点（或换麦）：停当前段，onstop 里接着开下一段 */
	function cut(): void {
		cancelCut?.();
		cancelCut = null;
		if (stopped || !rec) return;
		try {
			rec.stop();
		} catch {
			say('rec_error', { where: 'cut' });
		}
	}

	function stopTracks(): void {
		try {
			for (const t of cloned?.getTracks() ?? []) t.stop();
		} catch {
			/* clone 已经没了 */
		}
		cloned = null;
	}
}

export interface ResumeOptions {
	session?: string;
	upload?: AudioUpload;
	tel?: Telemetry;
	store?: AudioStore;
	/** 最多补传几段（默认不限） */
	limit?: number;
}

/**
 * 页面重开时把库里残留的段补传（按 session/n 顺序、一段一个请求、成功才删）。
 * 不给 `session` 就补全部；没有网络就一段都不动。返回成功传出去的段数，永不抛。
 */
export async function resumePending(opts: ResumeOptions = {}): Promise<number> {
	let store: AudioStore | null = opts.store ?? null;
	try {
		store ??= await createAudioStore();
		const rows = await store.list(opts.session);
		const uploader: AudioUpload = opts.upload ?? putChunk;
		let sent = 0;
		for (const row of rows) {
			if (opts.limit !== undefined && sent >= opts.limit) break;
			if (!online()) break;
			try {
				await uploader(row.session, row.n, row.blob, row.mime);
				await store.remove(row.session, row.n);
				sent += 1;
			} catch (e) {
				opts.tel?.track('rec_upload_failed', { n: row.n, err: String((e as Error)?.message ?? e).slice(0, 120) });
				break;
			}
		}
		return sent;
	} catch {
		return 0;
	}
}
