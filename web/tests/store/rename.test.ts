// `LineStore.renameSession` 的判据：只改 course（acked_seq / 起止时间不动）、空课名开课合法、
// 本机没有这节课就抛。两套实现（真 IndexedDB 与内存）跑同一组断言，免得又漂开。
import 'fake-indexeddb/auto';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { indexedDB as fakeIdb } from 'fake-indexeddb';
import { openStore } from '../../src/lib/store/idb.ts';
import { memoryStore } from '../../src/lib/store/memory.ts';
import type { LineStore } from '../../src/lib/store/index.ts';

let n = 0;
const open = () =>
	openStore({ name: `classlive-rename-${++n}-${Date.now()}`, indexedDB: fakeIdb, now: () => 1_000 });

const impls: [string, () => Promise<LineStore>][] = [
	['idb', open],
	['memory', async () => memoryStore({ now: () => 1_000 })]
];

for (const [name, make] of impls) {
	test(`${name}：空课名开课合法，改名后 listSessions 显示新课名`, async () => {
		const store = await make();
		await store.openSession('web-x-1', '');
		assert.equal((await store.listSessions())[0].course, '', '开课时可以是空课名');

		await store.renameSession('web-x-1', '微观经济');
		const [meta] = await store.listSessions();
		assert.equal(meta.course, '微观经济');
		assert.equal(meta.session_id, 'web-x-1');
	});

	test(`${name}：改名只动 course，acked_seq 与起止时间不变`, async () => {
		const store = await make();
		await store.openSession('web-x-2', '');
		await store.append({ session_id: 'web-x-2', seq: 1, en: 'a', zh: '甲', t_s: 1 });
		await store.ack('web-x-2', 1);
		await store.closeSession('web-x-2');
		const before = (await store.listSessions())[0];

		await store.renameSession('web-x-2', 'ECON10740');
		const after = (await store.listSessions())[0];
		assert.equal(after.course, 'ECON10740');
		assert.equal(after.acked_seq, before.acked_seq);
		assert.equal(after.started_at, before.started_at);
		assert.equal(after.ended_at, before.ended_at);
	});

	test(`${name}：本机没有这节课 → 抛，不静默造一节`, async () => {
		const store = await make();
		await assert.rejects(() => store.renameSession('web-nope', 'X'), /本机没有这节课/);
		assert.deepEqual(await store.listSessions(), []);
	});
}
