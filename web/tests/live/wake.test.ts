// web/tests/live/wake.test.ts —— holdWakeLock / watchHidden（lib/live/wake.ts）的离线自测。
//
// 假 wakeLock / 假 document（visibilitychange 手动触发）/ 假墙钟。
// 钉的判据：不支持或被拒都静默降级不抛；回到可见时重取一次；
// 隐藏 3 s 不回调、6 s 回调一次（带 hiddenAt / shownAt），不用 setInterval。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { holdWakeLock, watchHidden, type WakeLockHandle } from '../../src/lib/live/wake.ts';

type VisDoc = {
	visibilityState: string;
	hidden: boolean;
	addEventListener(type: 'visibilitychange', cb: () => void): void;
	removeEventListener(type: 'visibilitychange', cb: () => void): void;
	hide(): void;
	show(): void;
	fire(): void;
	listeners: () => number;
};

function fakeDoc(): VisDoc {
	let listeners: Array<() => void> = [];
	const doc: VisDoc = {
		visibilityState: 'visible',
		hidden: false,
		addEventListener(_t, cb) {
			listeners.push(cb);
		},
		removeEventListener(_t, cb) {
			listeners = listeners.filter((f) => f !== cb);
		},
		fire() {
			listeners.slice().forEach((cb) => cb());
		},
		listeners: () => listeners.length,
		hide() {
			doc.visibilityState = 'hidden';
			doc.hidden = true;
			doc.fire();
		},
		show() {
			doc.visibilityState = 'visible';
			doc.hidden = false;
			doc.fire();
		}
	};
	return doc;
}

/** 假 Screen Wake Lock：记录 request 次数与释放情况 */
function fakeWake(opts: { reject?: boolean } = {}) {
	const locks: Array<{ released?: boolean; release(): Promise<void> }> = [];
	let requests = 0;
	return {
		locks,
		requests: () => requests,
		api: {
			request: async (_type: 'screen') => {
				requests += 1;
				if (opts.reject) throw new Error('NotAllowedError');
				const l = {
					released: false,
					async release() {
						l.released = true;
					}
				};
				locks.push(l);
				return l;
			}
		}
	};
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

test('holdWakeLock：支持时持一把 screen 锁，release() 放开并退订', async () => {
	const w = fakeWake();
	const doc = fakeDoc();
	const h: WakeLockHandle = holdWakeLock({ wakeLock: w.api, doc });
	await sleep(0);
	assert.equal(w.requests(), 1);
	assert.equal(h.active, true);

	h.release();
	await sleep(0);
	assert.equal(h.active, false);
	assert.equal(w.locks[0].released, true);
	assert.equal(doc.listeners(), 0, 'release 后不该还挂着 visibilitychange');
});

test('holdWakeLock：回到可见时重取；隐藏时不重取', async () => {
	const w = fakeWake();
	const doc = fakeDoc();
	const h = holdWakeLock({ wakeLock: w.api, doc });
	await sleep(0);
	assert.equal(w.requests(), 1);

	// 浏览器在标签页隐藏时会自己释放锁
	w.locks[0].released = true;
	doc.hide();
	await sleep(0);
	assert.equal(w.requests(), 1, '隐藏期间不该 request');

	doc.show();
	await sleep(0);
	assert.equal(w.requests(), 2, '回到可见应重取一次');
	assert.equal(h.active, true);

	doc.show(); // 还持着的时候再 visible 不重复要
	await sleep(0);
	assert.equal(w.requests(), 2);
	h.release();
});

test('holdWakeLock：不支持 / 被拒都静默降级，不抛', async () => {
	const noApi = holdWakeLock({ wakeLock: null, doc: fakeDoc() });
	await sleep(0);
	assert.equal(noApi.active, false);
	noApi.release();

	const denied = fakeWake({ reject: true });
	const h = holdWakeLock({ wakeLock: denied.api, doc: fakeDoc() });
	await sleep(0);
	assert.equal(h.active, false);
	assert.equal(denied.requests(), 1);
	h.release();

	// 连 document 都没有（node 裸环境）也不能抛
	const bare = holdWakeLock({ wakeLock: null, doc: null });
	assert.equal(bare.active, false);
	bare.release();
});

test('watchHidden：隐藏 3 s 不回调', () => {
	const doc = fakeDoc();
	let t = 1_000;
	let n = 0;
	const off = watchHidden(
		() => n++,
		{ doc, now: () => t }
	);
	doc.hide();
	t += 3_000;
	doc.show();
	assert.equal(n, 0);
	off();
});

test('watchHidden：隐藏 6 s 回调一次，带 hiddenAt / shownAt', () => {
	const doc = fakeDoc();
	let t = 1_000;
	const spans: Array<{ hiddenAt: number; shownAt: number }> = [];
	const off = watchHidden((s) => spans.push(s), { doc, now: () => t });
	doc.hide();
	t += 6_000;
	doc.show();
	assert.deepEqual(spans, [{ hiddenAt: 1_000, shownAt: 7_000 }]);

	// 同一段隐藏重复触发 visibilitychange 也只记一次
	doc.show();
	doc.show();
	assert.equal(spans.length, 1);

	// 下一段再藏 6 s 又是一次
	doc.hide();
	t += 6_000;
	doc.show();
	assert.equal(spans.length, 2);
	off();
});

test('watchHidden：退订后不再回调', () => {
	const doc = fakeDoc();
	let t = 0;
	let n = 0;
	const off = watchHidden(() => n++, { doc, now: () => t });
	doc.hide();
	t += 10_000;
	off();
	assert.equal(doc.listeners(), 0);
	doc.show();
	assert.equal(n, 0);
});
