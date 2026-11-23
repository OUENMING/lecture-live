// lib/live/class-telemetry.ts —— 一节课的测试数据接线：遥测事件 + 课堂录音，挂在 connectLive 上。
// 对外：startClassTelemetry({session_id, course}) → ClassTelemetry（track / socket 工厂 / attach / end）。
// 全部旁路：每个入口都 try/catch，出错只丢数据、不碰上课；采样器 1 s 一拍只读几个字段（O(1)）。
// 测试用户都已授权采集（2026-11-20 Owen），所以不弹任何提示；服务端可用 WEB_TELEMETRY / WEB_AUDIO_UPLOAD 一键关。

import { createTelemetry, installCollectors, createClassRecorder, resumePending, type Telemetry, type ClassRecorder } from '../telemetry';
import { isNotReady, observeRequests, type RequestOutcome } from '../api';
import type { TraceEvent } from '../translate';
import type { SocketLike } from '../asr/soniox';
import type { AudioSource } from '../audio';
import type { LiveSession } from '../session';
import type { SyncView } from './sync';

export interface ClassTelemetry {
	readonly tel: Telemetry;
	/** 安全记一条（永不抛） */
	track(type: string, data?: Record<string, unknown>): void;
	/** 给 Soniox 客户端的 `createSocket`：真 WebSocket + 旁听 open / close / error / 消息数 */
	createSocket(url: string, protocols: string[]): SocketLike;
	/** 会话与麦克风都建好后挂上：状态采样 + 录音 */
	attach(p: { session: LiveSession; mic: AudioSource; sync: () => SyncView | null }): void;
	/** 翻译请求打点（Translator 的 `onTrace`）：逐条记 + 进下课总账 */
	trace(ev: TraceEvent): void;
	/** 定稿句译完（或失败）：逐句一条，带延迟拆分（对齐桌面 `.live.jsonl`） */
	sentence(p: { seg: number; en_len: number; zh_len: number; failed: boolean; lat: Record<string, number | null> | null }): void;
	/** 下课：收尾录音、记总账、把缓冲发出去。最多等 4 s，绝不卡下课。 */
	end(extra?: Record<string, unknown>): Promise<void>;
}

const SAMPLE_MS = 1000;
const LEVEL_EVERY = 10;
/** 一页最多记这么多条 console（防某个循环刷屏把缓冲挤爆） */
const CONSOLE_MAX = 300;

declare const __CL_VERSION__: string | undefined;
declare const __CL_BUILD__: string | undefined;
const VER = typeof __CL_VERSION__ === 'string' ? __CL_VERSION__ : 'web-dev';
const BUILD = typeof __CL_BUILD__ === 'string' ? __CL_BUILD__ : 'dev';

/** 下课总账的计数器（全是 O(1) 自增；`class_end` 一次带走，桌面 report.json 的网页版） */
function newTally() {
	return {
		final: { n: 0, ok: 0, retry: 0, failed_sent: 0, err: {} as Record<string, number>, first_ms: [] as number[], wait_ms: [] as number[] },
		draft: { n: 0, ok: 0, stale: 0, aborted: 0, err: 0 },
		sent: { n: 0, failed: 0, total_ms: [] as number[] },
		api: { n: 0, fail: 0, pending: 0, by: {} as Record<string, number> },
		console: { error: 0, warn: 0 },
		ws: { opened: 0, closed: 0, errors: 0 },
		hidden_ms: 0,
		offline_ms: 0
	};
}

function pct(xs: number[], q: number): number | null {
	if (!xs.length) return null;
	const s = [...xs].sort((a, b) => a - b);
	return s[Math.min(s.length - 1, Math.floor(q * s.length))];
}

export function startClassTelemetry(p: { session_id: string; course: string }): ClassTelemetry {
	const tel = createTelemetry({ sessionId: p.session_id });
	const safe = (type: string, data?: Record<string, unknown>) => {
		try {
			tel.track(type, data);
		} catch {
			/* 遥测永不影响上课 */
		}
	};
	let disposeCollectors = () => {};
	try {
		disposeCollectors = installCollectors(tel);
	} catch {
		/* 采集器装不上就少一类数据 */
	}
	safe('class_open', { session: p.session_id, course_len: p.course.length, page: location.pathname, ver: VER, build: BUILD });
	const tally = newTally();
	const t0 = Date.now();

	// API 调用结局（每个经 lib/api 的请求一条；遥测自己的上报不走 lib/api，不会递归）
	let unobserve = () => {};
	try {
		unobserve = observeRequests((o: RequestOutcome) => {
			tally.api.n++;
			// 「还没出」（404 not_ready）是正常状态：单独记 pending，不进 fail / by，事件带 expected 供面板略过
			const expected = isNotReady(o);
			if (expected) {
				tally.api.pending++;
			} else if (!o.ok) {
				tally.api.fail++;
				const k = `${o.status ?? 0}:${o.reason ?? o.kind ?? ''}`.slice(0, 40);
				tally.api.by[k] = (tally.api.by[k] ?? 0) + 1;
			}
			safe('api', { ...o, ...(expected ? { expected: true } : {}), path: o.path.slice(0, 120) });
		});
	} catch {
		/* 少一类数据 */
	}

	// console.error / warn 旁听（原样照打；只多记一份，封顶 CONSOLE_MAX）
	const restoreConsole: Array<() => void> = [];
	let consoleN = 0;
	for (const level of ['error', 'warn'] as const) {
		try {
			const orig = console[level];
			console[level] = (...args: unknown[]) => {
				try {
					tally.console[level]++;
					if (consoleN++ < CONSOLE_MAX) {
						const msg = args
							.map((a) => (a instanceof Error ? `${a.name}: ${a.message}` : typeof a === 'string' ? a : safeJson(a)))
							.join(' ')
							.slice(0, 500);
						safe('console', { level, msg });
					}
				} catch {
					/* 旁听坏了也要照打 */
				}
				orig.apply(console, args);
			};
			restoreConsole.push(() => {
				console[level] = orig;
			});
		} catch {
			/* 装不上就不旁听 */
		}
	}

	// 后台 / 断网累计时长（课中切走多久、掉网多久 —— 排查「字幕停了」最常见的两个原因）
	let hiddenAt = document.visibilityState === 'hidden' ? Date.now() : 0;
	let offlineAt = navigator.onLine === false ? Date.now() : 0;
	const onVis = () => {
		if (document.visibilityState === 'hidden') hiddenAt ||= Date.now();
		else if (hiddenAt) {
			tally.hidden_ms += Date.now() - hiddenAt;
			hiddenAt = 0;
		}
	};
	const onOff = () => (offlineAt ||= Date.now());
	const onOn = () => {
		if (offlineAt) tally.offline_ms += Date.now() - offlineAt;
		offlineAt = 0;
	};
	try {
		document.addEventListener('visibilitychange', onVis);
		addEventListener('offline', onOff);
		addEventListener('online', onOn);
	} catch {
		/* ignore */
	}
	// 上节课没传完的录音：开课 30 s 后再补，避开开课那几秒的网络高峰
	setTimeout(() => void resumePending({ tel }).catch(() => {}), 30_000);

	let sockN = 0;
	let ws: WebSocket | null = null;
	let timer: ReturnType<typeof setInterval> | null = null;
	let recorder: ClassRecorder | null = null;
	let ended = false;

	return {
		tel,
		track: safe,
		createSocket(url, protocols) {
			const s = new WebSocket(url, protocols);
			const id = ++sockN;
			const t0 = performance.now();
			let opened = 0;
			let msgs = 0;
			ws = s;
			try {
				safe('ws_new', { id, host: new URL(url).host });
				s.addEventListener('open', () => {
					tally.ws.opened++;
					opened = performance.now();
					safe('ws_open', { id, ms: Math.round(opened - t0) });
				});
				s.addEventListener('message', () => {
					msgs++;
				});
				s.addEventListener('error', () => {
					tally.ws.errors++;
					safe('ws_error', { id });
				});
				s.addEventListener('close', (ev) => {
					tally.ws.closed++;
					safe('ws_close', {
						id,
						code: ev.code,
						reason: String(ev.reason || '').slice(0, 200),
						clean: ev.wasClean,
						alive_ms: opened ? Math.round(performance.now() - opened) : 0,
						msgs
					});
				});
			} catch {
				/* 旁听失败不影响这条连接 */
			}
			return s as unknown as SocketLike;
		},
		attach({ session, mic, sync }) {
			let last = { status: '', step: '', message: '', rows: -1, sync: '', online: true };
			let lv = { sum: 0, max: 0, quiet: 0, n: 0 };
			let lastStream: MediaStream | null = null;
			let tick = 0;
			timer = setInterval(() => {
				try {
					tick++;
					const st = session.status;
					if (st !== last.status || session.step !== last.step) {
						safe('status', { status: st, step: session.step });
						last.status = st;
						last.step = session.step;
					}
					if (session.message && session.message !== last.message) {
						safe('notice', { message: session.message.slice(0, 300) });
						last.message = session.message;
					}
					const l = mic.level;
					lv.sum += l;
					lv.n++;
					if (l > lv.max) lv.max = l;
					if (l < 0.008) lv.quiet++;
					const sv = sync();
					const sk = sv ? `${sv.phase}|${sv.message}` : 'none';
					if (sk !== last.sync) {
						safe('sync', sv ? { phase: sv.phase, acked: sv.acked, message: sv.message.slice(0, 200), server: sv.server_id } : { phase: 'none' });
						last.sync = sk;
					}
					if (tick % LEVEL_EVERY === 0) {
						safe('pulse', {
							rows: session.rows.length,
							level_avg: Math.round((lv.sum / Math.max(1, lv.n)) * 1e4) / 1e4,
							level_max: Math.round(lv.max * 1e4) / 1e4,
							quiet_s: lv.quiet,
							ws_state: ws?.readyState ?? -1,
							ws_buffered: ws?.bufferedAmount ?? 0,
							draft_len: session.draftEn.length
						});
						lv = { sum: 0, max: 0, quiet: 0, n: 0 };
					}
					// 录音：麦克风起来后才有流；换麦后流变了就切
					const stream = mic.stream ?? null;
					if (stream && !recorder && !ended) {
						recorder = createClassRecorder({ stream, sessionId: p.session_id, tel });
						recorder.start();
						lastStream = stream;
					} else if (stream && recorder && stream !== lastStream) {
						recorder.switchStream(stream);
						safe('mic_switched');
						lastStream = stream;
					}
				} catch {
					/* 采样坏一拍不要紧 */
				}
			}, SAMPLE_MS);
		},
		trace(ev) {
			try {
				if (ev.kind === 'final') {
					const f = tally.final;
					f.n++;
					if (ev.attempt && ev.attempt > 1) f.retry++;
					if (ev.outcome === 'ok') {
						f.ok++;
						if (typeof ev.first_ms === 'number') f.first_ms.push(ev.first_ms);
					} else {
						const k = `${ev.outcome}${ev.status ? `:${ev.status}` : ''}`;
						f.err[k] = (f.err[k] ?? 0) + 1;
					}
					if (typeof ev.wait_ms === 'number') f.wait_ms.push(ev.wait_ms);
					safe('tr', { ...ev });
				} else {
					const d = tally.draft;
					d.n++;
					if (ev.outcome === 'ok') d.ok++;
					else if (ev.outcome === 'stale') d.stale++;
					else if (ev.outcome === 'aborted') d.aborted++;
					else d.err++;
					// 草稿量大（约每句 2 次）：成功 / 过期 / 被新草稿顶掉只进总账，失败才逐条记
					if (ev.outcome === 'error' || ev.outcome === 'prompt' || ev.outcome === 'empty') safe('tr', { ...ev });
				}
			} catch {
				/* ignore */
			}
		},
		sentence(s) {
			try {
				tally.sent.n++;
				if (s.failed) tally.sent.failed++;
				if (typeof s.lat?.total_ms === 'number') tally.sent.total_ms.push(s.lat.total_ms);
				safe('zh', { seg: s.seg, failed: s.failed, en_len: s.en_len, zh_len: s.zh_len, ...(s.lat ?? {}) });
			} catch {
				/* ignore */
			}
		},
		async end(extra) {
			if (ended) return;
			ended = true;
			if (timer) clearInterval(timer);
			try {
				onVis();
				onOn();
				if (hiddenAt) tally.hidden_ms += Date.now() - hiddenAt;
				const f = tally.final;
				safe('class_report', {
					dur_s: Math.round((Date.now() - t0) / 1000),
					ver: VER,
					build: BUILD,
					sentences: tally.sent.n,
					sent_failed: tally.sent.failed,
					total_p50: pct(tally.sent.total_ms, 0.5),
					total_p90: pct(tally.sent.total_ms, 0.9),
					final: { n: f.n, ok: f.ok, retry: f.retry, err: f.err, first_p50: pct(f.first_ms, 0.5), first_p90: pct(f.first_ms, 0.9), wait_p90: pct(f.wait_ms, 0.9) },
					draft: tally.draft,
					api: tally.api,
					console: tally.console,
					ws: { ...tally.ws, sockets: sockN },
					hidden_s: Math.round(tally.hidden_ms / 1000),
					offline_s: Math.round(tally.offline_ms / 1000),
					...(extra ?? {})
				});
			} catch {
				/* 总账坏了也要收尾 */
			}
			try {
				unobserve();
				for (const r of restoreConsole) r();
				document.removeEventListener('visibilitychange', onVis);
				removeEventListener('offline', onOff);
				removeEventListener('online', onOn);
			} catch {
				/* ignore */
			}
			const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
			try {
				await Promise.race([recorder?.stop() ?? Promise.resolve(), wait(3500)]);
			} catch {
				/* 录音收尾失败交给下次 resumePending */
			}
			safe('class_end', extra);
			try {
				disposeCollectors();
			} catch {
				/* ignore */
			}
			try {
				await Promise.race([tel.flush('stop'), wait(500)]);
				tel.stop();
			} catch {
				/* ignore */
			}
		}
	};
}

function safeJson(v: unknown): string {
	try {
		return JSON.stringify(v)?.slice(0, 300) ?? String(v);
	} catch {
		return String(v);
	}
}
