// `lib/telemetry/buffer.ts` 的判据：track 不抛且 O(1)、超 5000 条丢最旧并带 dropped、单批 ≤60 KB / ≤500 条、
// 失败按 2→4→…→60 s 退避、同一时刻只有 1 个在途、收到 `off` 后停发、hidden 时 keepalive。
// 时钟 / 空闲调度 / 发送端全注入，不碰网络、不用真定时器。

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createTelemetry, MAX_EVENTS } from '../../src/lib/telemetry/buffer.ts';
import { MAX_BATCH_BYTES } from '../../src/lib/telemetry/sender.ts';
import { fakeClock, fakeIdle, fakeSend, settle } from './fakes.ts';

/** 待触发的「退避」定时器（≤60 s 的那些；周期 flush 也是同池的一次性定时器，按值分开看） */
const backoff = (c: ReturnType<typeof fakeClock>): number[] => c.due().filter((ms) => ms <= 60_000);

function setup(
	sendOpts: Parameters<typeof fakeSend>[0] = {},
	over: { maxEvents?: number; threshold?: number; flushMs?: number } = {}
) {
	const clock = fakeClock();
	const idle = fakeIdle();
	const sent = fakeSend(sendOpts);
	const tel = createTelemetry({
		send: sent.send,
		now: () => 1_700_000_000_000,
		mono: () => 42,
		setTimer: clock.setTimer,
		idle: idle.idle,
		flushMs: over.flushMs ?? 30_000,
		...(over.maxEvents ? { maxEvents: over.maxEvents } : {}),
		...(over.threshold ? { threshold: over.threshold } : {}),
		listen: false
	});
	return { tel, clock, idle, sent };
}

test('track 不抛、O(1)：10 万次 < 200 ms，且一次都不在热路径发送', () => {
	// 放大上限：默认 5000 会让「丢最旧」的搬运混进这 10 万次的耗时里，测的就不是热路径了
	const { tel, sent, idle } = setup({}, { maxEvents: 200_000, threshold: 200_000 });
	const t0 = Date.now();
	for (let i = 0; i < 100_000; i++) tel.track('tick');
	const ms = Date.now() - t0;
	assert.equal(tel.size(), 100_000);
	assert.equal(sent.batches.length, 0, '热路径不许发请求');
	assert.ok(idle.pending() <= 1, '空闲回调堆积不超过 1 个');
	assert.ok(ms < 200, `10 万次 track 用了 ${ms} ms`);
	// 公开函数一律不抛：坏 data 也吞掉
	assert.doesNotThrow(() => tel.track('bad', null as unknown as Record<string, unknown>));
	assert.doesNotThrow(() => tel.setSession('s_1'));
	assert.doesNotThrow(() => tel.flush());
	assert.doesNotThrow(() => tel.stop());
});

test('事件形状：t / mono / type / 自增 n，外加调用方字段', async () => {
	const { tel, idle, sent } = setup();
	tel.track('a', { x: 1 });
	tel.track('b');
	await tel.flush('manual');
	await idle.run();
	const events = sent.batches.flatMap((b) => b.events);
	assert.deepEqual(
		events.map((e) => [e.type, e.n, e.t, e.mono]),
		[
			['a', 1, 1_700_000_000_000, 42],
			['b', 2, 1_700_000_000_000, 42]
		]
	);
	assert.equal(events[0].x, 1);
});

test('超 5000 条丢最旧：下次随批带一个 dropped 计数', async () => {
	const { tel, sent } = setup();
	for (let i = 0; i < MAX_EVENTS + 7; i++) tel.track('e', { i });
	assert.equal(tel.size(), MAX_EVENTS);
	await tel.flush('manual');
	const first = sent.batches[0].events;
	assert.equal(first[0].type, 'dropped');
	assert.equal(first[0].count, 7);
	// 丢掉的是最旧的 7 条：这条批从 i=7 开始
	assert.equal(first[1].i, 7);
	assert.ok(first.length <= 500);
});

test('单批 ≤60 KB：大事件会被拆成多批，一条不丢', async () => {
	const { tel, sent } = setup();
	const pad = 'x'.repeat(1800);
	for (let i = 0; i < 100; i++) tel.track('big', { i, pad });
	for (let round = 0; round < 20 && tel.size() > 0; round++) await tel.flush('manual');
	assert.equal(tel.size(), 0);
	for (const b of sent.batches) {
		assert.ok(b.bytes <= 60 * 1024, `一批 ${b.bytes} 字节超 60 KB`);
		assert.ok(b.events.length <= 500);
		assert.ok(b.bytes <= MAX_BATCH_BYTES + 4096);
	}
	assert.equal(sent.totalEvents(), 100);
	assert.ok(sent.batches.length > 1, '100 条 1.8 KB 的事件必须拆批');
});

test('失败退避 2 s → 4 s，成功后清零；退避期间不绕过', async () => {
	// 定时 flush 拉得足够远，免得它的到期混进退避序列（`clock.due()` 是同一个池子）
	const { tel, clock, sent } = setup({ fail: 2 }, { threshold: 10_000, flushMs: 600_000 });
	tel.track('e');
	await tel.flush('manual');
	assert.equal(sent.batches.length, 1);
	assert.deepEqual(backoff(clock), [2000]);
	await clock.advance(2000);
	assert.equal(sent.batches.length, 2);
	assert.deepEqual(backoff(clock), [4000]);
	await clock.advance(4000);
	assert.equal(sent.batches.length, 3);
	assert.deepEqual(backoff(clock), [], '成功后不再排重试');
	assert.equal(tel.size(), 0, '成功那批才从缓冲里删');
});

test('同一时刻只有 1 个请求在途', async () => {
	let open: () => void = () => {};
	const gate = new Promise<void>((r) => {
		open = r;
	});
	const { tel, sent } = setup({ gate: () => gate }, { threshold: 10_000, flushMs: 600_000 });
	tel.track('a');
	const p1 = tel.flush('manual');
	await settle();
	tel.track('b');
	const p2 = tel.flush('manual');
	await settle();
	assert.equal(sent.calls(), 1, '在途时不许再发第二个请求');
	assert.equal(sent.maxInFlight(), 1);
	open();
	await Promise.all([p1, p2]);
	assert.equal(sent.maxInFlight(), 1);
});

test('收到 x-cl-telemetry: off 后本次页面内停发', async () => {
	const { tel, sent, idle } = setup({ off: true });
	tel.track('a');
	await tel.flush('manual');
	assert.equal(sent.batches.length, 1);
	tel.track('b');
	tel.flush('manual');
	await idle.run();
	assert.equal(tel.size(), 0, '停了就不攒了');
	assert.equal(sent.batches.length, 1, '停了就不再发');
});

test('空闲与定时：攒够 200 条排队一次，30 s 到点也 flush', async () => {
	const { tel, idle, sent, clock } = setup();
	for (let i = 0; i < 200; i++) tel.track('e', { i });
	assert.equal(idle.pending(), 1);
	await idle.run();
	assert.equal(sent.batches.length, 1);
	tel.track('later');
	await clock.advance(30_000);
	await idle.run();
	assert.equal(sent.batches.length, 2, '30 s 定时也要把这一条发出去');
});

test('hidden / pagehide 的 flush 带 keepalive，普通 flush 不带', async () => {
	const { tel, sent } = setup();
	tel.track('a');
	await tel.flush('hidden');
	tel.track('b');
	await tel.flush('manual');
	assert.equal(sent.batches[0].keepalive, true);
	assert.equal(sent.batches[1].keepalive, false);
});

test('stop()：把手里的先发出去，之后 track 是空操作', async () => {
	const { tel, sent } = setup();
	tel.track('a');
	tel.stop();
	await settle();
	assert.equal(sent.batches.length, 1);
	tel.track('b');
	assert.equal(tel.size(), 0);
});

test('OCR A：在途时 stop() —— 那批回来后立刻带 keepalive 补发剩下的，不丢', async () => {
	let release!: () => void;
	let gated = true;
	const { tel, sent, idle } = setup({ gate: () => (gated ? new Promise<void>((r) => (release = r)) : Promise.resolve()) }, { threshold: 10_000 });
	tel.track('a');
	void tel.flush('manual');
	await settle();
	tel.track('b');
	tel.stop();
	gated = false;
	release();
	await settle();
	await settle();
	assert.equal(sent.batches.length, 2, '第二批要补发');
	assert.equal(sent.batches[1].keepalive, true);
	assert.deepEqual(sent.batches[1].events.map((e) => e.type), ['b']);
	assert.equal(idle.pending(), 0);
});
