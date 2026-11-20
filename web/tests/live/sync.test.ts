// `lib/live/sync.ts` 的判据：开课不挡上课、断网排队、恢复后按序补齐、刷新续推、409/410/413 各自收场、
// 以及「不出现 >1 次/秒的请求风暴」。时钟与定时器全注入（假时钟手动 advance），网络全是假 api。
import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { ApiError } from '../../src/lib/api/http.ts';
import { attachSync } from '../../src/lib/live/sync.ts';
import type { SyncApi } from '../../src/lib/live/sync.ts';
import { memoryStore } from '../../src/lib/store/memory.ts';
import type { Line } from '../../src/lib/store/index.ts';
import type { SyncClock } from '../../src/lib/store/sync.ts';

const LOCAL = 'web-20261118-120000-ab12cd';
const SRV = 's_3f9a1c2e7b40';

class FakeStorage {
	readonly map = new Map<string, string>();
	getItem(k: string): string | null {
		return this.map.get(k) ?? null;
	}
	setItem(k: string, v: string): void {
		this.map.set(k, String(v));
	}
}

/** 假时钟：到点的定时器按时间顺序、每个都放行一轮 promise */
function fakeClock(start = 1_000_000) {
	let now = start;
	let timers: { at: number; cb: () => void }[] = [];
	const clock: SyncClock = {
		now: () => now,
		setTimer(ms, cb) {
			const t = { at: now + ms, cb };
			timers.push(t);
			return () => {
				timers = timers.filter((x) => x !== t);
			};
		}
	};
	return {
		clock,
		now: () => now,
		pendingAt: () => timers.map((t) => t.at).sort((a, b) => a - b),
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

const settle = async () => {
	for (let i = 0; i < 5; i++) {
		for (let j = 0; j < 20; j++) await Promise.resolve();
		await new Promise((r) => setTimeout(r, 0));
	}
};

const line = (seq: number): Line => ({ session_id: LOCAL, seq, en: `l${seq}`, zh: `第${seq}句`, t_s: seq });

const netErr = () => new ApiError('network', 'fetch', '连不上服务器');
const apiErr = (kind: ApiError['kind'], reason: string, status: number) =>
	new ApiError(kind, reason, reason, status);

interface ApiScript {
	start?: ('ok' | 'fail' | 'gone')[];
	put?: ('ok' | 'offline' | 'ended' | 'gone' | 'boom' | 'big')[];
	end?: ('ok' | 'ended' | 'gone')[];
}

/** 假 api：记下每次调用（含时间），按剧本回包；`online=false` 时所有请求都当断网 */
function fakeApi(script: ApiScript = {}) {
	const starts: { at: number; idem: string }[] = [];
	const puts: { at: number; id: string; seqs: number[] }[] = [];
	const ends: { at: number; id: string; lines_total: number }[] = [];
	let si = 0;
	let pi = 0;
	let ei = 0;
	const api: SyncApi = {
		async startSession(_course, _started, idem) {
			starts.push({ at: 0, idem });
			const mode = script.start?.[si++] ?? 'ok';
			if (mode === 'fail') throw netErr();
			if (mode === 'gone') throw apiErr('gone', 'session_deleted', 410);
			return { session_id: SRV };
		},
		async putLines(id, lines) {
			puts.push({ at: 0, id, seqs: lines.map((l) => l.seq) });
			const mode = script.put?.[pi++] ?? 'ok';
			if (mode === 'offline') throw netErr();
			if (mode === 'ended') throw apiErr('server', 'session_ended', 409);
			if (mode === 'gone') throw apiErr('gone', 'session_deleted', 410);
			if (mode === 'boom') throw apiErr('server', 'http_500', 500);
			if (mode === 'big' && lines.length > 1) throw apiErr('too_large', 'too_large', 413);
			return { confirmed_seq: lines[lines.length - 1].seq };
		},
		async endSession(id, info) {
			ends.push({ at: 0, id, lines_total: info.lines_total });
			const mode = script.end?.[ei++] ?? 'ok';
			if (mode === 'ended') throw apiErr('server', 'session_ended', 409);
			if (mode === 'gone') throw apiErr('gone', 'session_deleted', 410);
			return { job_id: 'j_8a01d3f2c9e5' };
		}
	};
	return { api, starts, puts, ends };
}

beforeEach(() => {
	(globalThis as unknown as { localStorage: FakeStorage }).localStorage = new FakeStorage();
});

test('开课成功 → 攒够 10 句推一批；幂等键 = 本机会话 id', async () => {
	const store = memoryStore({ now: () => 0 });
	const clk = fakeClock();
	const { api, starts, puts } = fakeApi();
	const sync = attachSync({ store, local_session_id: LOCAL, course: 'ECON10740', api, clock: clk.clock });

	for (let s = 1; s <= 10; s++) {
		await store.append(line(s));
		sync.push();
	}
	await settle();

	assert.deepEqual(starts.map((s) => s.idem), [LOCAL]);
	assert.deepEqual(puts.map((p) => p.seqs), [[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]]);
	assert.equal(puts[0].id, SRV);
	assert.equal(sync.state().acked, 10);
	assert.equal(sync.state().phase, 'synced');
	assert.equal(localStorage.getItem(`cl.srv.${LOCAL}`), SRV, '服务端会话号要存下来给刷新用');
	sync.stop();
});

test('开课失败不挡上课：push 立刻返回，之后 1 s→2 s 自己重试，成功就接着推', async () => {
	const store = memoryStore({ now: () => 0 });
	const clk = fakeClock();
	const { api, starts, puts } = fakeApi({ start: ['fail', 'fail', 'ok'] });
	const sync = attachSync({
		store,
		local_session_id: LOCAL,
		course: 'ECON10740',
		api,
		clock: clk.clock,
		params: { batchLines: 2 }
	});
	await settle();
	assert.equal(starts.length, 1, '一挂上就开课');
	assert.equal(sync.state().phase, 'offline');
	assert.match(sync.state().message, /连不上服务器/);

	// 上课途中 push 不许 await、也不许卡住：立刻返回
	for (let s = 1; s <= 2; s++) {
		await store.append(line(s));
		sync.push();
	}
	assert.deepEqual(puts, [], '没会话号就别推');

	await clk.advance(1000);
	assert.equal(starts.length, 2, '1 s 后重试');
	await clk.advance(2000);
	assert.equal(starts.length, 3, '再 2 s 后重试');
	await settle();
	assert.equal(puts.length, 1);
	assert.deepEqual(puts[0].seqs, [1, 2]);
	assert.equal(sync.state().acked, 2);
	sync.stop();
});

test('断网：句子全攒着；恢复后按序补齐，acked 只增不减', async () => {
	const store = memoryStore({ now: () => 0 });
	const clk = fakeClock();
	const { api, puts } = fakeApi({ put: Array(6).fill('offline') });
	const sync = attachSync({
		store,
		local_session_id: LOCAL,
		course: 'ECON10740',
		api,
		clock: clk.clock,
		params: { batchLines: 4, flushMs: 30_000 }
	});

	const seen: number[] = [];
	const sample = () => {
		seen.push(sync.state().acked);
	};
	for (let s = 1; s <= 12; s++) {
		await store.append(line(s));
		sync.push();
		await clk.advance(1000);
		sample();
	}
	assert.equal((await store.pending(LOCAL)).length, 12, '断网期间一句都不许丢');
	assert.equal(sync.state().phase, 'offline');

	await clk.advance(120_000);
	sample();
	assert.deepEqual(await store.pending(LOCAL), []);
	assert.equal(sync.state().acked, 12);
	const last = puts[puts.length - 1];
	assert.deepEqual([...last.seqs].sort((a, b) => a - b), last.seqs, '升序');
	for (let i = 1; i < seen.length; i++) assert.ok(seen[i] >= seen[i - 1], 'acked 不许回退');
	sync.stop();
});

test('刷新续推：localStorage 里有 cl.srv → 不再开课，直接从本机 acked 往后推', async () => {
	localStorage.setItem(`cl.srv.${LOCAL}`, SRV);
	const store = memoryStore({ now: () => 0 });
	for (let s = 1; s <= 3; s++) await store.append(line(s));
	await store.ack(LOCAL, 2); // 上一程已确认到 2
	const clk = fakeClock();
	const { api, starts, puts } = fakeApi();
	const sync = attachSync({
		store,
		local_session_id: LOCAL,
		course: 'ECON10740',
		api,
		clock: clk.clock,
		params: { batchLines: 1 }
	});

	sync.push();
	await settle();
	assert.deepEqual(starts, [], '有会话号就不该再开课');
	assert.deepEqual(puts.map((p) => p.seqs), [[3]], '只补 2 之后那句');
	assert.equal(sync.state().acked, 3);
	sync.stop();
});

test('409 session_ended 当完成：不再推，也不再退避', async () => {
	const store = memoryStore({ now: () => 0 });
	for (let s = 1; s <= 2; s++) await store.append(line(s));
	const clk = fakeClock();
	const { api, puts } = fakeApi({ put: ['ended', 'ok'] });
	const sync = attachSync({
		store,
		local_session_id: LOCAL,
		course: 'ECON10740',
		api,
		clock: clk.clock,
		params: { batchLines: 1 }
	});

	sync.push();
	await settle();
	assert.equal(sync.state().phase, 'synced');
	assert.match(sync.state().message, /下课/);
	const before = puts.length;
	await clk.advance(120_000);
	assert.equal(puts.length, before, '结束了就不该再发');
	assert.deepEqual(clk.pendingAt(), [], '也不该留重试定时器');
	sync.stop();
});

test('410 session_deleted：停止同步，进入 error', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line(1));
	const clk = fakeClock();
	const { api, puts } = fakeApi({ put: ['gone', 'ok'] });
	const sync = attachSync({
		store,
		local_session_id: LOCAL,
		course: 'ECON10740',
		api,
		clock: clk.clock,
		params: { batchLines: 1 }
	});

	sync.push();
	await settle();
	assert.equal(sync.state().phase, 'error');
	assert.match(sync.state().message, /删除/);
	const before = puts.length;
	await clk.advance(120_000);
	assert.equal(puts.length, before);
	sync.stop();
});

test('5xx 退避 1→2→4…封顶 60 s，不死循环、成功后恢复', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line(1));
	const clk = fakeClock();
	const { api, puts } = fakeApi({ put: ['boom', 'boom', 'boom', 'boom', 'boom', 'boom', 'ok'] });
	const sync = attachSync({
		store,
		local_session_id: LOCAL,
		course: 'ECON10740',
		api,
		clock: clk.clock,
		params: { batchLines: 1 }
	});

	sync.push();
	await settle();
	const waits: number[] = [];
	for (let i = 0; i < 6; i++) {
		const at = clk.pendingAt()[0];
		assert.ok(at !== undefined, `第 ${i + 1} 次失败后应挂着重试定时器`);
		waits.push(at - clk.now());
		await clk.advance(at - clk.now());
	}
	assert.deepEqual(waits, [1000, 2000, 4000, 8000, 16_000, 32_000]);
	assert.equal(puts.length, 7, '6 次失败 + 1 次成功');
	assert.equal(sync.state().acked, 1);
	assert.equal(sync.state().phase, 'synced');
	sync.stop();
});

test('413 单批太大：就地拆半再发，不靠退避撞同一批', async () => {
	const store = memoryStore({ now: () => 0 });
	const clk = fakeClock();
	const { api, puts } = fakeApi({ put: ['big', 'ok', 'ok'] });
	const sync = attachSync({
		store,
		local_session_id: LOCAL,
		course: 'ECON10740',
		api,
		clock: clk.clock,
		params: { batchLines: 4 }
	});
	for (let s = 1; s <= 4; s++) {
		await store.append(line(s));
		sync.push();
	}
	await settle();
	assert.deepEqual(puts.map((p) => p.seqs), [[1, 2, 3, 4], [1, 2], [3, 4]]);
	assert.deepEqual(await store.pending(LOCAL), []);
	assert.equal(sync.state().acked, 4);
	sync.stop();
});

test('不出现 >1 次/秒的请求风暴：断网 2 分钟，相邻请求间隔 ≥ 1 s', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line(1));
	const clk = fakeClock();
	const { api, puts } = fakeApi({ put: Array(50).fill('offline') });
	const sync = attachSync({
		store,
		local_session_id: LOCAL,
		course: 'ECON10740',
		api,
		clock: clk.clock,
		params: { batchLines: 1 }
	});

	const stamps: number[] = [];
	sync.push();
	await settle();
	stamps.push(clk.now());
	for (let i = 0; i < 120; i++) {
		await clk.advance(1000);
		sync.push(); // 上课途中每句都 push：退避中的 push 不许绕过退避
		if (puts.length > stamps.length) stamps.push(clk.now());
	}
	assert.ok(stamps.length <= 10, `2 分钟最多这么多次，实际 ${stamps.length}`);
	for (let i = 1; i < stamps.length; i++) {
		assert.ok(stamps[i] - stamps[i - 1] >= 1000, `第 ${i} 次请求只隔了 ${stamps[i] - stamps[i - 1]} ms`);
	}
	sync.stop();
});

test('下课：先 flush 补齐再 endSession（lines_total 是本节句数），可重复调用只发一次', async () => {
	const store = memoryStore({ now: () => 0 });
	for (let s = 1; s <= 5; s++) await store.append(line(s));
	const clk = fakeClock();
	const { api, puts, ends } = fakeApi();
	const sync = attachSync({
		store,
		local_session_id: LOCAL,
		course: 'ECON10740',
		api,
		clock: clk.clock,
		params: { batchLines: 50, flushMs: 30_000 }
	});

	sync.push();
	await settle();
	assert.deepEqual(puts, [], '没攒够也没到点，先不发');
	await sync.end();
	assert.deepEqual(puts.map((p) => p.seqs), [[1, 2, 3, 4, 5]], '下课必须先把句子推完');
	assert.deepEqual(ends.map((e) => [e.id, e.lines_total]), [[SRV, 5]]);
	assert.equal(sync.state().phase, 'synced');

	await sync.end();
	assert.equal(ends.length, 1, '重复 end 不再发');
	sync.stop();
});

test('下课遇 409 session_ended 也算完成；遇 410 报「已删除」', async () => {
	for (const [mode, phase, say] of [
		['ended', 'synced', /下课/],
		['gone', 'error', /删除/]
	] as const) {
		const store = memoryStore({ now: () => 0 });
		await store.append(line(1));
		const clk = fakeClock();
		const { api } = fakeApi({ end: [mode as 'ended' | 'gone'] });
		const sync = attachSync({
			store,
			local_session_id: LOCAL,
			course: 'ECON10740',
			api,
			clock: clk.clock,
			params: { batchLines: 1 }
		});
		sync.push();
		await settle();
		await sync.end();
		assert.equal(sync.state().phase, phase, mode);
		assert.match(sync.state().message, say, mode);
		sync.stop();
	}
});
