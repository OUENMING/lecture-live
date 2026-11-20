// lib/store/sync.ts 的判据：成功 ack、失败退避序列、断网累积后一次补齐（不乱序不重复）。
// 时钟与定时器全注入：假时钟手动 advance，不靠真实 setTimeout，跑得快也不会抖。
// 后端用假 putLines 记账（收到的每一批都记下来），真源仍是内存版 LineStore。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { memoryStore } from '../../src/lib/store/memory.ts';
import { createSyncer, type SyncClock } from '../../src/lib/store/sync.ts';
import type { Line } from '../../src/lib/store/index.ts';

const line = (session_id: string, seq: number): Line => ({
	session_id,
	seq,
	en: `l${seq}`,
	zh: `第${seq}句`,
	t_s: seq
});

/** 假时钟：所有到点的定时器按时间顺序、可反复 flush */
function fakeClock(start = 1_000_000) {
	let now = start;
	let timers: { at: number; cb: () => void; id: number }[] = [];
	let id = 0;
	const clock: SyncClock = {
		now: () => now,
		setTimer(ms, cb) {
			const t = { at: now + ms, cb, id: ++id };
			timers.push(t);
			return () => {
				timers = timers.filter((x) => x !== t);
			};
		}
	};
	return {
		clock,
		now: () => now,
		/** 停着的定时器到期时间（升序），给断言「下一次什么时候重试」 */
		pendingAt: () => timers.map((t) => t.at).sort((a, b) => a - b),
		async advance(ms: number) {
			const target = now + ms;
			// 每到点一个就放行一轮 promise，让 send() 把该做的做完
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

/** 把微任务与已排的已 resolve promise 跑干净 */
const settle = async () => {
	for (let i = 0; i < 20; i++) await Promise.resolve();
};

/** 假后端：记录每批；按脚本决定第几次失败 */
function fakeSink(script: ('ok' | 'fail')[] = []) {
	const calls: { id: string; seqs: number[] }[] = [];
	let i = 0;
	const sink = async (id: string, lines: Line[]) => {
		calls.push({ id, seqs: lines.map((l) => l.seq) });
		const mine = script[i++] ?? 'ok';
		if (mine === 'fail') throw new Error(`网络断了 #${i}`);
		return { acked_seq: lines[lines.length - 1].seq };
	};
	return { calls, sink };
}

test('攒够 batchLines 立刻发，成功后 ack，pending 清空', async () => {
	const store = memoryStore({ now: () => 0 });
	for (let s = 1; s <= 3; s++) await store.append(line('s_a', s));
	const clk = fakeClock();
	const { calls, sink } = fakeSink();
	const sync = createSyncer({
		store,
		putLines: sink,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 3, flushMs: 5000 }
	});

	for (let s = 1; s <= 3; s++) sync.push();
	await settle();

	assert.deepEqual(calls, [{ id: 's_a', seqs: [1, 2, 3] }]);
	assert.deepEqual(await store.pending('s_a'), []);
	assert.equal(sync.state().last_acked, 3);
	assert.equal(sync.state().failures, 0);
	assert.deepEqual(clk.pendingAt(), [], '发完了不该留定时器');
});

test('攒不够时最多等 flushMs 再发', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line('s_a', 1));
	const clk = fakeClock();
	const { calls, sink } = fakeSink();
	const sync = createSyncer({
		store,
		putLines: sink,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 10, flushMs: 5000 }
	});

	sync.push();
	await settle();
	assert.deepEqual(calls, [], '只有 1 句且没到点，不该发');

	await clk.advance(4999);
	assert.deepEqual(calls, [], '还没到 5000 ms');
	await clk.advance(1);
	assert.deepEqual(calls, [{ id: 's_a', seqs: [1] }]);
});

test('后续句子不会把 flush 窗口一直往后推', async () => {
	const store = memoryStore({ now: () => 0 });
	const clk = fakeClock();
	const { calls, sink } = fakeSink();
	const sync = createSyncer({
		store,
		putLines: sink,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 10, flushMs: 5000 }
	});

	await store.append(line('s_a', 1));
	sync.push();
	await clk.advance(3000);
	await store.append(line('s_a', 2));
	sync.push();
	await settle();
	await clk.advance(2000); // 距第一次 push 满 5000
	assert.deepEqual(calls, [{ id: 's_a', seqs: [1, 2] }]);
});

test('失败退避 1s → 2s → 4s → 8s …，封顶 60s，成功后清零', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line('s_a', 1));
	const clk = fakeClock();
	const { calls, sink } = fakeSink(['fail', 'fail', 'fail', 'fail', 'ok']);
	const sync = createSyncer({
		store,
		putLines: sink,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 1, baseBackoffMs: 1000, maxBackoffMs: 60_000 }
	});

	const t0 = clk.now();
	sync.push();
	await settle();
	assert.equal(calls.length, 1, '第一次立刻试');

	const waits: number[] = [];
	for (const expect of [1000, 2000, 4000, 8000]) {
		const at = clk.pendingAt()[0];
		assert.ok(at !== undefined, `失败后应挂着重试定时器（等 ${expect}）`);
		waits.push(at - clk.now());
		await clk.advance(expect);
	}
	assert.deepEqual(waits, [1000, 2000, 4000, 8000]);
	assert.equal(calls.length, 5, '4 次失败 + 1 次成功');
	assert.ok(clk.now() - t0 >= 15_000);
	assert.equal(sync.state().failures, 0, '成功后 failures 清零');
	assert.equal(sync.state().last_acked, 1);
	assert.deepEqual(await store.pending('s_a'), []);
});

test('退避封顶 60s（不会越等越久到天荒地老）', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line('s_a', 1));
	const clk = fakeClock();
	const { sink } = fakeSink(Array(10).fill('fail'));
	const sync = createSyncer({
		store,
		putLines: sink,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 1, baseBackoffMs: 1000, maxBackoffMs: 60_000 }
	});
	sync.push();
	await settle();
	const waits: number[] = [];
	for (let i = 0; i < 8; i++) {
		const at = clk.pendingAt()[0]!;
		waits.push(at - clk.now());
		await clk.advance(at - clk.now());
	}
	assert.deepEqual(waits, [1000, 2000, 4000, 8000, 16_000, 32_000, 60_000, 60_000]);
	assert.equal(sync.state().failures, 9);
});

test('断网期间的句子全攒着，恢复后一次补齐：不乱序、不重复、不丢', async () => {
	const store = memoryStore({ now: () => 0 });
	const clk = fakeClock();
	// 断网靠 gate 模拟；一旦放行，后端就该成功
	const { calls, sink } = fakeSink();
	let online = false;
	const gated = async (id: string, lines: Line[]) => {
		if (!online) {
			calls.push({ id, seqs: lines.map((l) => l.seq) });
			throw new Error('offline');
		}
		return sink(id, lines);
	};
	const sync = createSyncer({
		store,
		putLines: gated,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 100, flushMs: 5000, baseBackoffMs: 1000 }
	});

	// 断网 30 s，期间来了 1..5 句
	sync.push();
	for (let s = 1; s <= 5; s++) {
		await store.append(line('s_a', s));
		sync.push();
	}
	await clk.advance(30_000);
	assert.equal((await store.pending('s_a')).length, 5, '断网期间一句都不许丢');
	const before = calls.length;

	// 退避中 push 不许绕过退避：网络恢复后仍然等退避到点
	online = true;
	sync.push();
	await settle();
	assert.equal(calls.length, before, '退避没到点，push 不该发');
	await clk.advance(60_000);

	assert.deepEqual(await store.pending('s_a'), []);
	assert.equal(sync.state().last_acked, 5);
	// 最后一次成功的那批必须是 1..5 升序且不重复
	const last = calls[calls.length - 1];
	assert.deepEqual(last.seqs, [1, 2, 3, 4, 5]);
	// 所有发出去的批次，都没有逆序 / 批内重复
	for (const c of calls) {
		const sorted = c.seqs.slice().sort((a, b) => a - b);
		assert.deepEqual(c.seqs, sorted);
		assert.equal(new Set(c.seqs).size, c.seqs.length);
	}
});

test('超过单批上限（200）时分多批发完，且顺序不乱', async () => {
	const store = memoryStore({ now: () => 0 });
	for (let s = 1; s <= 450; s++) await store.append(line('s_a', s));
	const clk = fakeClock();
	const { calls, sink } = fakeSink();
	// 450 句、单批 200：靠「在飞期间又有新句」一批批推完
	const sync = createSyncer({
		store,
		putLines: sink,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 10, maxBatch: 200 }
	});
	await sync.flush();
	await settle();

	assert.deepEqual(
		calls.map((c) => c.seqs.length),
		[200, 200, 50]
	);
	assert.deepEqual(await store.pending('s_a'), []);
	assert.equal(sync.state().last_acked, 450);
});

test('在飞时再 push 不会并发发两批（同 seq 不重复提交）', async () => {
	const store = memoryStore({ now: () => 0 });
	for (let s = 1; s <= 2; s++) await store.append(line('s_a', s));
	const clk = fakeClock();
	const seen: number[][] = [];
	let release: (() => void) | null = null;
	const gate = new Promise<void>((r) => (release = r));
	const sync = createSyncer({
		store,
		putLines: async (_id, lines) => {
			seen.push(lines.map((l) => l.seq));
			await gate;
			return { acked_seq: lines[lines.length - 1].seq };
		},
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 2 }
	});

	sync.push();
	await settle();
	sync.push(); // 在飞
	await settle();
	await store.append(line('s_a', 3));
	sync.push(); // 在飞，这一句只能等这一批收尾
	await settle();
	assert.equal(seen.length, 1, '在飞时不许发第二批');
	release!();
	await settle();
	assert.deepEqual(seen[0], [1, 2]);
	// 在飞期间到达的第 3 句随后补上，且只发一次
	assert.deepEqual(seen[1], [3]);
	assert.equal(seen.length, 2);
	assert.deepEqual(await store.pending('s_a'), []);
});

test('flush 无视攒批上限立刻发（下课 / 关页面前）', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line('s_a', 1));
	const clk = fakeClock();
	const { calls, sink } = fakeSink();
	const sync = createSyncer({
		store,
		putLines: sink,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 100, flushMs: 60_000 }
	});
	sync.push();
	await settle();
	assert.deepEqual(calls, []);
	await sync.flush();
	assert.deepEqual(calls, [{ id: 's_a', seqs: [1] }]);
	assert.deepEqual(await store.pending('s_a'), []);
});

test('同步只碰自己的 session', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line('s_a', 1));
	await store.append(line('s_b', 1));
	const clk = fakeClock();
	const { calls, sink } = fakeSink();
	const sync = createSyncer({
		store,
		putLines: sink,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 1 }
	});
	sync.push();
	await settle();
	assert.deepEqual(calls, [{ id: 's_a', seqs: [1] }]);
	assert.equal((await store.pending('s_b')).length, 1);
});

test('flush 无视退避立刻试（下课 / 关页面前不排队等退避）', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line('s_a', 1));
	const clk = fakeClock();
	const { calls, sink } = fakeSink(['fail', 'ok']);
	const sync = createSyncer({
		store,
		putLines: sink,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 1, baseBackoffMs: 30_000 }
	});
	sync.push();
	await settle();
	assert.equal(calls.length, 1);
	assert.equal(sync.state().failures, 1);
	await sync.flush();
	assert.equal(calls.length, 2, 'flush 不该等 30 s 退避');
	assert.deepEqual(await store.pending('s_a'), []);
});

test('stop 之后不再发', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line('s_a', 1));
	const clk = fakeClock();
	const { calls, sink } = fakeSink();
	const sync = createSyncer({
		store,
		putLines: sink,
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 10, flushMs: 5000 }
	});
	sync.push();
	sync.stop();
	await clk.advance(30_000);
	assert.deepEqual(calls, []);
	assert.deepEqual(clk.pendingAt(), []);
});

test('服务器一句都没确认（acked_seq 没前进）→ 按失败退避，不空转', async () => {
	const store = memoryStore({ now: () => 0 });
	await store.append(line('s_a', 1));
	const clk = fakeClock();
	let n = 0;
	const sync = createSyncer({
		store,
		putLines: async () => {
			n++;
			return { acked_seq: 0 };
		},
		session_id: 's_a',
		clock: clk.clock,
		params: { batchLines: 1, baseBackoffMs: 1000 }
	});
	sync.push();
	await settle();
	assert.equal(n, 1);
	assert.equal(sync.state().failures, 1);
	assert.ok(clk.pendingAt()[0]! - clk.now() === 1000);
});

// 2026-11-20 01:21:23 UTC 事故：下课时 connect.end() 对每一行都 persist → push()，
// push 在 `await store.pending()` 那一拍里看到 in_flight 仍是 false，于是一秒并发 95 个 PUT，
// 打到同一个 Durable Object 撞 10058（Reduce your concurrent request rate）。判据：任何时刻最多一个批次在飞。
test('并发 push（在 pending() 还没回来时连推 100 次）也只有一个批次在飞', async () => {
	const base = memoryStore({ now: () => 0 });
	for (let s = 1; s <= 100; s++) await base.append(line('s_a', s));
	// pending() 慢一拍（IndexedDB 真实形状：跨事件循环才回）
	const store = {
		pending: async (id: string) => {
			await new Promise((r) => setTimeout(r, 1));
			return base.pending(id);
		},
		ack: base.ack.bind(base)
	};
	let inFlight = 0;
	let peak = 0;
	let puts = 0;
	const sync = createSyncer({
		store,
		putLines: async (_id, lines) => {
			puts += 1;
			inFlight += 1;
			peak = Math.max(peak, inFlight);
			await new Promise((r) => setTimeout(r, 2));
			inFlight -= 1;
			return { acked_seq: lines[lines.length - 1].seq };
		},
		session_id: 's_a',
		clock: fakeClock().clock,
		params: { batchLines: 10, flushMs: 30_000, maxBatch: 50 }
	});
	for (let i = 0; i < 100; i++) sync.push();
	await sync.flush();
	for (let i = 0; i < 50 && (inFlight > 0 || (await base.pending('s_a')).length); i++) {
		await new Promise((r) => setTimeout(r, 5));
	}
	assert.equal(peak, 1, `同一时刻在飞的批次应 ≤1，实际峰值 ${peak}`);
	assert.ok(puts <= 3, `100 句按 50 一批应 ≤3 个 PUT，实际 ${puts}`);
	assert.deepEqual(await base.pending('s_a'), []);
});
