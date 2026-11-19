// memory.ts 的判据：它是「IndexedDB 打不开」时的真兜底，语义必须和 idb.ts 一致。
// 这里只钉门面里独有的几条（时间 / 清理 / 删光）；幂等与 pending 已在 idb.test.ts 的退回用例里比过。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { memoryStore } from '../../src/lib/store/memory.ts';
import type { Line } from '../../src/lib/store/index.ts';

const line = (session_id: string, seq: number): Line => ({
	session_id,
	seq,
	en: `l${seq}`,
	zh: `第${seq}句`,
	t_s: seq
});

const DAY = 86_400_000;

test('默认 persistent = false（界面据此提示关掉标签页就没了）', () => {
	assert.equal(memoryStore().persistent, false);
});

test('purgeOlderThan 与 IndexedDB 版同规矩：只删已下课且逾期的', async () => {
	const t0 = 1_700_000_000_000;
	let now = t0;
	const store = memoryStore({ now: () => now });
	await store.openSession('s_gone', 'ECON10740');
	await store.append(line('s_gone', 1));
	await store.closeSession('s_gone');
	await store.openSession('s_live');
	await store.append(line('s_live', 1));

	now = t0 + 29 * DAY;
	assert.equal(await store.purgeOlderThan(30), 0);
	now = t0 + 31 * DAY;
	assert.equal(await store.purgeOlderThan(30), 1);
	assert.deepEqual(
		(await store.listSessions()).map((s) => s.session_id),
		['s_live']
	);
	assert.deepEqual(await store.linesOf('s_gone'), []);
	assert.equal((await store.linesOf('s_live')).length, 1);
});

test('deleteAll 把句子和会话都清掉，之后能接着写', async () => {
	const store = memoryStore();
	await store.append(line('s_a', 1));
	await store.deleteAll();
	assert.deepEqual(await store.listSessions(), []);
	assert.deepEqual(await store.pending('s_a'), []);
	await store.append(line('s_a', 2));
	assert.deepEqual(
		(await store.pending('s_a')).map((l) => l.seq),
		[2]
	);
});
