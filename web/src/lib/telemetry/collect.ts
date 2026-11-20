// lib/telemetry/collect.ts —— 被动采集器：给 createTelemetry 喂事件，自己不发送、不抛错、不改行为。
// 对外：installCollectors(tel, opts?) → dispose()。
// 只记「环境是啥 / 出了什么错 / 网络与可见性变了 / 卡了多久」这几类，不读课堂内容、不碰字幕与音频数据。
// 线上一律小写字段、栈截 1000 字（服务端按条限体量）；采集器自身出错只报一次 `collector_error`，不重试、不递归。
// 定时器可注入（测试用假时钟）；默认 60 s 一条 `perf`，longtask 只记 ≥200 ms 的。

import type { Telemetry } from './buffer';

export interface CollectorOptions {
	/** 一次性定时器，返回取消函数（测试注入假时钟） */
	setTimer?: (ms: number, cb: () => void) => () => void;
	/** 站点版本 / 渠道（默认读 `import.meta.env.VITE_CL_VERSION`，没有就省略） */
	version?: string;
	/** `perf` 周期（默认 60 s） */
	perfMs?: number;
}

const LONG_TASK_MIN_MS = 200;
const STACK_MAX = 1000;
const PERF_MS = 60_000;
const UA_MAX = 300;

type Any = Record<string, any>;

const str = (v: unknown, max = 64): string | undefined =>
	typeof v === 'string' && v ? v.slice(0, max) : undefined;
const num = (v: unknown): number | undefined =>
	typeof v === 'number' && Number.isFinite(v) ? Math.round(v * 1000) / 1000 : undefined;
const bool = (v: unknown): boolean | undefined => (typeof v === 'boolean' ? v : undefined);

/** 地区 / 语言 / 屏幕这类每次页面加载都不变的东西，只在开头报一次 */
function envOnce(): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	try {
		const nav = (globalThis as Any).navigator ?? {};
		out.ua = str(nav.userAgent, UA_MAX);
		const ua = nav.userAgentData as Any;
		if (ua) {
			const brands = Array.isArray(ua.brands)
				? ua.brands
						.slice(0, 6)
						.map((b: Any) => `${str(b?.brand, 24) ?? '?'}/${str(b?.version, 12) ?? '?'}`)
				: undefined;
			if (brands) out.brands = brands;
			const platform = str(ua.platform, 32);
			if (platform) out.platform = platform;
			const mobile = bool(ua.mobile);
			if (mobile !== undefined) out.mobile = mobile;
		}
		const languages = Array.isArray(nav.languages) ? nav.languages.slice(0, 5) : null;
		out.lang = languages?.length ? languages : str(nav.language, 16);
		out.tz = str(Intl.DateTimeFormat().resolvedOptions().timeZone, 40);
		const scr = (globalThis as Any).screen;
		if (scr) {
			out.screen = `${num(scr.width) ?? 0}x${num(scr.height) ?? 0}`;
			out.dpr = num((globalThis as Any).devicePixelRatio) ?? 1;
		}
		const win = (globalThis as Any).window;
		if (win) out.win = `${num(win.innerWidth) ?? 0}x${num(win.innerHeight) ?? 0}`;
		out.cores = num(nav.hardwareConcurrency);
		out.mem = num(nav.deviceMemory);
		const conn = nav.connection as Any;
		if (conn) {
			out.net = str(conn.effectiveType, 8);
			out.rtt = num(conn.rtt);
			out.down = num(conn.downlink);
			out.save = bool(conn.saveData);
		}
		out.coi = bool((globalThis as Any).crossOriginIsolated);
		const wa = (globalThis as Any).WebAssembly;
		out.jspi = typeof wa?.Suspending === 'function';
		// 读采样率 / 基准延迟必须有个实例：造一个、只读两个数、立刻关掉（不当音频链路用）。
		const Ctx = (globalThis as Any).AudioContext;
		if (typeof Ctx === 'function') {
			try {
				const ctx = new Ctx();
				out.rate = num(ctx.sampleRate);
				out.lat = num(ctx.baseLatency);
				void ctx.close?.();
			} catch {
				/* 没有用户手势 / 不支持：跳过这两项 */
			}
		}
	} catch {
		/* 环境读不到就少几项，绝不挡开课 */
	}
	return out;
}

/** 栈只留前 1000 字（服务端按条限体量，也够定位） */
const stackOf = (v: unknown): string | undefined => str(v, STACK_MAX);

export function installCollectors(tel: Telemetry, opts: CollectorOptions = {}): () => void {
	const setTimer =
		opts.setTimer ??
		((ms: number, cb: () => void) => {
			const h = setTimeout(cb, ms);
			return () => clearTimeout(h);
		});
	const perfMs = opts.perfMs ?? PERF_MS;
	const win = (globalThis as Any).window;
	const doc = (globalThis as Any).document;
	const nav = (globalThis as Any).navigator;
	const cleanups: (() => void)[] = [];
	let reported = false;

	/** 采集器自己出错：只记一次，之后闭嘴（不重试、不递归上报） */
	const guard = <A extends unknown[]>(fn: (...a: A) => void) =>
		(...a: A): void => {
			try {
				fn(...a);
			} catch {
				if (reported) return;
				reported = true;
				tel.track('collector_error');
			}
		};

	const on = (target: Any, ev: string, fn: (e: any) => void): void => {
		if (!target || typeof target.addEventListener !== 'function') return;
		const safe = guard(fn);
		target.addEventListener(ev, safe);
		cleanups.push(() => target.removeEventListener(ev, safe));
	};

	// —— 一次性环境 ——
	try {
		const env = envOnce();
		const version = opts.version ?? (import.meta as Any).env?.VITE_CL_VERSION;
		if (version) env.ver = str(version, 24);
		tel.track('env', env);
	} catch {
		/* 上面的 envOnce 已经兜了 */
	}

	// —— 报错 ——
	on(win, 'error', (e: any) => {
		const err = e?.error;
		tel.track('error', {
			msg: str(e?.message ?? err?.message, 300),
			at: str(`${str(e?.filename, 160) ?? ''}:${num(e?.lineno) ?? 0}:${num(e?.colno) ?? 0}`, 200),
			stack: stackOf(err?.stack)
		});
	});
	on(win, 'unhandledrejection', (e: any) => {
		const r = e?.reason;
		const isObj = r !== null && typeof r === 'object';
		tel.track('rejection', {
			msg: str(isObj ? (r as Any).message : r, 300) ?? 'unknown',
			name: str(isObj ? (r as Any).name : undefined, 40),
			stack: stackOf(isObj ? (r as Any).stack : undefined)
		});
	});

	// —— 状态变化 ——
	on(doc, 'visibilitychange', () => {
		tel.track('visibility', { state: str(doc?.visibilityState, 16) ?? 'unknown' });
	});
	on(win, 'online', () => tel.track('net_change', { online: true }));
	on(win, 'offline', () => tel.track('net_change', { online: false }));
	on(nav?.connection, 'change', () => {
		const conn = nav?.connection as Any;
		tel.track('net_type', {
			net: str(conn?.effectiveType, 8),
			rtt: num(conn?.rtt),
			down: num(conn?.downlink),
			save: bool(conn?.saveData)
		});
	});
	on(nav?.mediaDevices, 'devicechange', () => tel.track('device_change'));

	// —— 卡顿：≥200 ms 的 longtask 逐条记，同时进本分钟的统计 ——
	let tasks = 0;
	let longest = 0;
	const PO = (globalThis as Any).PerformanceObserver;
	if (typeof PO === 'function') {
		try {
			const po = new PO(
				guard((list: Any) => {
					for (const entry of list.getEntries()) {
						const dur = num(entry?.duration) ?? 0;
						if (dur < LONG_TASK_MIN_MS) continue;
						tasks += 1;
						if (dur > longest) longest = dur;
						tel.track('longtask', { dur });
					}
				})
			);
			po.observe({ type: 'longtask', buffered: false });
			cleanups.push(() => {
				try {
					po.disconnect();
				} catch {
					/* 已经断了 */
				}
			});
		} catch {
			/* 不支持 longtask：跳过 */
		}
	}

	const tick = (): void => {
		try {
			const mem = (globalThis as Any).performance?.memory;
			tel.track('perf', {
				tasks,
				longest,
				focus: bool(doc?.hasFocus?.()),
				used: num(mem?.usedJSHeapSize),
				limit: num(mem?.jsHeapSizeLimit)
			});
		} catch {
			if (!reported) {
				reported = true;
				tel.track('collector_error');
			}
		}
		tasks = 0;
		longest = 0;
		// 每分钟一条：自己续上（不靠 setInterval 一次跑到底）
		if (!disposed) cleanups.push(setTimer(perfMs, tick));
	};
	let disposed = false;
	cleanups.push(setTimer(perfMs, tick));

	return () => {
		disposed = true;
		for (const fn of cleanups.splice(0)) {
			try {
				fn();
			} catch {
				/* 拆卸失败不影响上课 */
			}
		}
	};
}
