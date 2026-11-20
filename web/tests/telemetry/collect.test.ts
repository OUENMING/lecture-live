// `lib/telemetry/collect.ts` 的判据：一次性 env 齐全、报错 / 可见性 / 网络 / 设备事件都进同一个 tel、
// longtask 只收 ≥200 ms、60 s 一条 perf（含本分钟统计与焦点）、采集器自己出错只记一次 collector_error、
// dispose 之后一个事件都不再进。全局替身在启动时装好（模块按需读 globalThis），时钟注入。

import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { installCollectors } from '../../src/lib/telemetry/collect.ts';
import type { Telemetry, TelemetryEvent } from '../../src/lib/telemetry/buffer.ts';
import { fakeClock } from './fakes.ts';

// —— 浏览器替身：window / document / navigator / PerformanceObserver ——
type Fn = (e: any) => void;
const listeners = new Map<unknown, Map<string, Set<Fn | (() => void)>>>();
function fire(target: unknown, type: string, e: any = {}): void {
	for (const fn of listeners.get(target)?.get(type) ?? []) (fn as Fn)(e);
}
// 注意：addEventListener 是方法，`this` 才是目标（写成普通函数参数收不到 target）
function attach(this: unknown, type: string, fn: Fn): void {
	if (!listeners.has(this)) listeners.set(this, new Map());
	const m = listeners.get(this)!;
	if (!m.has(type)) m.set(type, new Set());
	m.get(type)!.add(fn);
}
function detach(this: unknown, type: string, fn: Fn): void {
	listeners.get(this)?.get(type)?.delete(fn);
}

const win: any = {
	addEventListener: attach,
	removeEventListener: detach,
	innerWidth: 1280,
	innerHeight: 720
};
const doc: any = {
	addEventListener: attach,
	removeEventListener: detach,
	visibilityState: 'visible',
	hasFocus: () => true
};
const nav: any = {
	userAgent: 'UA/1.0 (Macintosh)',
	userAgentData: { brands: [{ brand: 'Chromium', version: '140' }], platform: 'macOS', mobile: false },
	languages: ['zh-CN', 'en'],
	hardwareConcurrency: 8,
	deviceMemory: 8,
	connection: { effectiveType: '4g', rtt: 50, downlink: 10, saveData: false, addEventListener: attach, removeEventListener: detach },
	mediaDevices: { addEventListener: attach, removeEventListener: detach },
	onLine: true
};

let observed: ((list: any) => void) | null = null;
const disconnected: number[] = [];
class FakePO {
	constructor(cb: (list: any) => void) {
		observed = cb;
	}
	observe(opts: any) {
		(this as any).opt = opts;
	}
	disconnect() {
		disconnected.push(1);
	}
}

const saved: Record<string, PropertyDescriptor | undefined> = {};
before(() => {
	const def = (k: string, v: unknown) => {
		saved[k] = Object.getOwnPropertyDescriptor(globalThis, k);
		Object.defineProperty(globalThis, k, { value: v, configurable: true, writable: true });
	};
	def('window', win);
	def('document', doc);
	def('navigator', nav);
	def('screen', { width: 1920, height: 1080 });
	def('devicePixelRatio', 2);
	def('crossOriginIsolated', true);
	def('PerformanceObserver', FakePO);
	Object.defineProperty(globalThis.performance, 'memory', {
		value: { usedJSHeapSize: 12_000_000, jsHeapSizeLimit: 4_000_000_000 },
		configurable: true
	});
});

after(() => {
	for (const [k, d] of Object.entries(saved)) {
		if (d) Object.defineProperty(globalThis, k, d);
		else delete (globalThis as any)[k];
	}
	delete (globalThis as any).performance.memory;
	FakePO.prototype.observe = FakePO.prototype.observe;
});

/** 假 tel：只收事件，不发送 */
function fakeTel() {
	const events: TelemetryEvent[] = [];
	let n = 1;
	const tel: Telemetry = {
		track(type, data) {
			events.push({ t: 1, mono: 0, type, n: n++, ...(data ?? {}) });
		},
		setSession() {},
		async flush() {},
		stop() {},
		size: () => events.length
	};
	return {
		tel,
		events,
		types: () => events.map((e) => e.type),
		of: (type: string) => events.filter((e) => e.type === type)
	};
}

test('一次性 env：UA / brands / 语言 / 时区 / 屏幕 / 核数 / 网络 / JSPI / 音频参数', () => {
	const t = fakeTel();
	const clock = fakeClock();
	const dispose = installCollectors(t.tel, { setTimer: clock.setTimer });
	const env = t.of('env')[0];
	assert.equal(env?.ua.includes('Macintosh'), true);
	assert.deepEqual(env?.brands, ['Chromium/140']);
	assert.equal(env?.platform, 'macOS');
	assert.equal(env?.mobile, false);
	assert.deepEqual(env?.lang, ['zh-CN', 'en']);
	assert.equal(typeof env?.tz, 'string');
	assert.equal(env?.screen, '1920x1080');
	assert.equal(env?.win, '1280x720');
	assert.equal(env?.dpr, 2);
	assert.equal(env?.cores, 8);
	assert.equal(env?.mem, 8);
	assert.equal(env?.net, '4g');
	assert.equal(env?.save, false);
	assert.equal(env?.coi, true);
	assert.equal(env?.jspi, false);
	dispose();
});

test('报错：window.error 与 unhandledrejection 都带 文件:行:列 / 栈前 1000 字', () => {
	const t = fakeTel();
	const clock = fakeClock();
	const dispose = installCollectors(t.tel, { setTimer: clock.setTimer });
	const stack = 'x'.repeat(1500);
	fire(win, 'error', {
		message: 'boom',
		filename: 'https://a/b.js',
		lineno: 12,
		colno: 34,
		error: { message: 'boom', stack }
	});
	fire(win, 'unhandledrejection', { reason: new Error('nope') });
	const [err] = t.of('error');
	assert.equal(err.msg, 'boom');
	assert.equal(err.at, 'https://a/b.js:12:34');
	assert.equal(String(err.stack).length, 1000);
	const [rej] = t.of('rejection');
	assert.equal(rej.msg, 'nope');
	assert.equal(rej.name, 'Error');
	dispose();
});

test('状态变化：可见性 / 在线离线 / 网络类型 / 设备增减', () => {
	const t = fakeTel();
	const clock = fakeClock();
	const dispose = installCollectors(t.tel, { setTimer: clock.setTimer });
	doc.visibilityState = 'hidden';
	fire(doc, 'visibilitychange');
	doc.visibilityState = 'visible';
	fire(win, 'online');
	fire(win, 'offline');
	fire(nav.connection, 'change');
	fire(nav.mediaDevices, 'devicechange');
	assert.deepEqual(t.types(), [
		'env',
		'visibility',
		'net_change',
		'net_change',
		'net_type',
		'device_change'
	]);
	assert.equal(t.of('visibility')[0].state, 'hidden');
	assert.equal(t.of('net_type')[0].net, '4g');
	dispose();
});

test('longtask：只记 ≥200 ms，并进本分钟 perf 的统计', async () => {
	disconnected.length = 0;
	const t = fakeTel();
	const clock = fakeClock();
	const dispose = installCollectors(t.tel, { setTimer: clock.setTimer });
	assert.ok(observed, '应该装上了 PerformanceObserver');
	observed!({ getEntries: () => [{ duration: 120 }, { duration: 250 }, { duration: 400 }] });
	assert.deepEqual(t.of('longtask').map((e) => e.dur), [250, 400]);
	await clock.advance(60_000);
	const [perf] = t.of('perf');
	assert.equal(perf.tasks, 2);
	assert.equal(perf.longest, 400);
	assert.equal(perf.focus, true);
	assert.equal(perf.used, 12_000_000);
	// 下一分钟重新计数
	await clock.advance(60_000);
	assert.equal(t.of('perf')[1].tasks, 0);
	dispose();
	assert.equal(disconnected.length, 1, 'dispose 要断开观察器');
});

test('采集器自己出错只记一次 collector_error（不重试、不递归）', async () => {
	const t = fakeTel();
	const clock = fakeClock();
	const dispose = installCollectors(t.tel, { setTimer: clock.setTimer });
	observed!({
		getEntries: () => {
			throw new Error('list 坏了');
		}
	});
	observed!({
		getEntries: () => {
			throw new Error('还是坏的');
		}
	});
	assert.equal(t.of('collector_error').length, 1);
	dispose();
});

test('dispose 之后一个事件都不再进', () => {
	const t = fakeTel();
	const clock = fakeClock();
	const dispose = installCollectors(t.tel, { setTimer: clock.setTimer });
	const before = t.events.length;
	dispose();
	fire(win, 'error', { message: 'after' });
	fire(doc, 'visibilitychange');
	assert.equal(t.events.length, before);
});
