// lib/store/idb.ts 的判据：幂等、pending/ack、会话隔离、deleteAll、purgeOlderThan、内存退回。
// 用 fake-indexeddb 在 Node 里跑真 IndexedDB 语义（事务 / 主键 / 键范围），不用浏览器。
// 每个用例一个独立库名；purgeOlderThan 的「现在几点」由注入的 now 决定，所以同一库可以换时钟重开。
import 'fake-indexeddb/auto';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { indexedDB as fakeIdb } from 'fake-indexeddb';
import { openStore } from '../../src/lib/store/idb.ts';
import type { Line } from '../../src/lib/store/index.ts';

let n = 0;
const newName = () => `classlive-test-${++n}-${Date.now()}`;

const open = (name: string, now?: () => number) =>
	openStore({ name, indexedDB: fakeIdb, ...(now ? { now } : {}) });

const line = (session_id: string, seq: number, en = `l${seq}`, zh = `第${seq}句`): Line => ({
	session_id,
	seq,
	en,
	zh,
	t_s: seq * 2.5
});

const DAY = 86_400_000;

test('真 IndexedDB 能打开（persistent = true）', async () => {
	const store = await open(newName());
	assert.equal(store.persistent, true);
});

test('append 幂等：同一 session_id+seq 写两遍只留一条', async () => {
	const store = await open(newName());
	await store.append(line('s_a', 1));
	await store.append(line('s_a', 1));
	await store.append(line('s_a', 2));
	const rows = await store.linesOf('s_a');
	assert.equal(rows.length, 2);
	assert.deepEqual(
		rows.map((r) => r.seq),
		[1, 2]
	);
});

test('append 先到先得：同 seq 第二次不改内容', async () => {
	const store = await open(newName());
	await store.append(line('s_a', 7, 'first', '第一'));
	await store.append(line('s_a', 7, 'second', '第二'));
	const [row] = await store.linesOf('s_a');
	assert.equal(row.en, 'first');
});

test('pending 只给 acked 之后的句子，按 seq 升序（乱序写入也一样）', async () => {
	const store = await open(newName());
	for (const seq of [5, 2, 9, 1]) await store.append(line('s_a', seq));
	assert.deepEqual(
		(await store.pending('s_a')).map((l) => l.seq),
		[1, 2, 5, 9]
	);
	await store.ack('s_a', 2);
	assert.deepEqual(
		(await store.pending('s_a')).map((l) => l.seq),
		[5, 9]
	);
});

test('ack 只增不减', async () => {
	const store = await open(newName());
	for (const seq of [1, 2, 3]) await store.append(line('s_a', seq));
	await store.ack('s_a', 3);
	await store.ack('s_a', 1);
	assert.deepEqual(await store.pending('s_a'), []);
	await store.ack('s_a', 2);
	const [meta] = await store.listSessions();
	assert.equal(meta.acked_seq, 3);
});

test('不同 session 互相隔离', async () => {
	const store = await open(newName());
	await store.append(line('s_a', 1));
	await store.append(line('s_b', 1));
	await store.ack('s_a', 1);
	assert.deepEqual(await store.pending('s_a'), []);
	assert.deepEqual(
		(await store.pending('s_b')).map((l) => l.seq),
		[1]
	);
	assert.deepEqual(
		(await store.linesOf('s_b')).map((l) => l.en),
		['l1']
	);
});

test('listSessions 记课程 / 起止 / 已确认，最近开的在前；openSession 不覆盖已有课程', async () => {
	let t = 1_700_000_000_000;
	const store = await open(newName(), () => t);
	await store.openSession('s_old', 'ECON10740');
	t += 60_000;
	await store.openSession('s_new', 'SOC10020');
	await store.closeSession('s_old');
	await store.openSession('s_old', '别的课'); // 不许改
	await store.ack('s_old', 4);

	const list = await store.listSessions();
	assert.deepEqual(
		list.map((s) => s.session_id),
		['s_new', 's_old']
	);
	const oldMeta = list.find((s) => s.session_id === 's_old')!;
	assert.equal(oldMeta.course, 'ECON10740');
	assert.equal(oldMeta.acked_seq, 4);
	assert.notEqual(oldMeta.ended_at, null);
	assert.equal(list.find((s) => s.session_id === 's_new')!.ended_at, null);
});

test('append 自动补建会话（没先 openSession 也不丢）', async () => {
	const store = await open(newName());
	await store.append(line('s_x', 1));
	const [meta] = await store.listSessions();
	assert.equal(meta.session_id, 's_x');
	assert.equal(meta.acked_seq, 0);
});

test('deleteAll 清空两边的库', async () => {
	const store = await open(newName());
	await store.append(line('s_a', 1));
	await store.append(line('s_b', 2));
	await store.deleteAll();
	assert.deepEqual(await store.listSessions(), []);
	assert.deepEqual(await store.linesOf('s_a'), []);
	assert.deepEqual(await store.pending('s_b'), []);
});

test('purgeOlderThan 只删逾期的已下课会话，没下课的与 30 天内的都留着', async () => {
	const t0 = 1_700_000_000_000;
	const name = newName();
	const store = await open(name, () => t0);
	await store.openSession('s_gone');
	await store.append(line('s_gone', 1));
	await store.closeSession('s_gone'); // ended_at = t0
	await store.openSession('s_live'); // 没下课 → 永不按天数删
	await store.append(line('s_live', 1));

	// 29 天后：还在窗口内，一条不删
	const at29 = await open(name, () => t0 + 29 * DAY);
	assert.equal(await at29.purgeOlderThan(30), 0);
	assert.equal((await at29.listSessions()).length, 2);

	// 31 天后：只删 s_gone 和它的句子
	const at31 = await open(name, () => t0 + 31 * DAY);
	assert.equal(await at31.purgeOlderThan(30), 1);
	assert.deepEqual(
		(await at31.listSessions()).map((s) => s.session_id),
		['s_live']
	);
	assert.deepEqual(await at31.linesOf('s_gone'), []);
	assert.equal((await at31.linesOf('s_live')).length, 1);
});

test('IndexedDB 打不开 → 退回内存实现，persistent = false，功能照旧', async () => {
	const store = await openStore({ name: newName(), indexedDB: null });
	assert.equal(store.persistent, false);
	await store.append(line('s_a', 2));
	await store.append(line('s_a', 1));
	await store.append(line('s_a', 1)); // 幂等
	assert.deepEqual(
		(await store.pending('s_a')).map((l) => l.seq),
		[1, 2]
	);
	await store.ack('s_a', 1);
	assert.deepEqual(
		(await store.pending('s_a')).map((l) => l.seq),
		[2]
	);
	assert.equal((await store.listSessions()).length, 1);
});

test('open 抛错（隐私模式模拟）→ 退回内存实现，不把异常抛给调用方', async () => {
	const broken = {
		open() {
			throw new DOMException('denied', 'InvalidStateError');
		}
	} as unknown as IDBFactory;
	const store = await openStore({ name: newName(), indexedDB: broken });
	assert.equal(store.persistent, false);
	await store.append(line('s_a', 1));
	assert.equal((await store.pending('s_a')).length, 1);
});

test('open 返回 error 事件（onerror）→ 同样退回内存实现', async () => {
	const broken = {
		open() {
			const r = {
				result: undefined,
				error: new Error('quota / 无痕模式'),
				onsuccess: null as ((e: Event) => void) | null,
				onerror: null as ((e: Event) => void) | null,
				onupgradeneeded: null
			};
			setTimeout(() => r.onerror?.({} as Event), 0);
			return r;
		}
	} as unknown as IDBFactory;
	const store = await openStore({ name: newName(), indexedDB: broken });
	assert.equal(store.persistent, false);
});
