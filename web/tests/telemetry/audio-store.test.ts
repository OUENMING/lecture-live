// `lib/telemetry/audio-store.ts` 的判据：用 fake-indexeddb 跑真 IndexedDB 语义（主键 [session,n] / 排序 /
// 跨实例可见 —— 等价于「页面重开还在」），以及打不开时退回内存实现仍可用。
// 段的大小与序号是判据，不校验音频内容（这里一个字节都不生成真音频）。

import assert from 'node:assert/strict';
import { test } from 'node:test';
import 'fake-indexeddb/auto';
import { indexedDB as fakeIdb } from 'fake-indexeddb';
import { createAudioStore } from '../../src/lib/telemetry/audio-store.ts';
import type { AudioChunk } from '../../src/lib/telemetry/audio-store.ts';

const SID = 's_3f9a1c2e7b40';

/** 每个用例前清空（同一个 `cl-audio` 库在同一进程里是共用的） */
async function fresh() {
	const store = await createAudioStore({ indexedDB: fakeIdb });
	for (const r of await store.list()) await store.remove(r.session, r.n);
	return store;
}

const chunk = (session: string, n: number, size: number, at = 1_700_000_000_000): AudioChunk => ({
	session,
	n,
	mime: 'audio/webm;codecs=opus',
	at,
	size,
	blob: new Blob([new Uint8Array(size)])
});

test('落库 / 列出 / 删 / 总量 / 最旧 / 最大序号', async () => {
	const store = await fresh();
	assert.equal(store.persistent, true);
	assert.equal(await store.put(chunk(SID, 2, 30)), true);
	assert.equal(await store.put(chunk(SID, 1, 10)), true);
	await store.put(chunk('s_other', 4, 40));
	assert.deepEqual(
		(await store.list(SID)).map((r) => r.n),
		[1, 2],
		'按 (session, n) 升序，别的会话不混进来'
	);
	assert.equal(await store.total(), 80);
	// at 一样时按 session 名再按 n 挑最旧的
	assert.equal((await store.oldest())?.session, SID);
	assert.equal(await store.maxSeq(SID), 2);
	assert.equal(await store.remove(SID, 1), true);
	assert.deepEqual(
		(await store.list(SID)).map((r) => r.n),
		[2]
	);
});

test('跨实例可见：页面重开（新 store）还能读到上一程留下的段', async () => {
	const first = await fresh();
	await first.put(chunk(SID, 7, 1234));
	const second = await createAudioStore({ indexedDB: fakeIdb });
	const rows = await second.list(SID);
	assert.equal(rows.length, 1);
	assert.equal(rows[0].size, 1234);
	assert.equal(rows[0].mime, 'audio/webm;codecs=opus');
	assert.equal(await rows[0].blob.size, 1234);
});

test('打不开 IndexedDB 就退回内存实现，方法照样不抛', async () => {
	const broken = () => {
		throw new Error('no idb');
	};
	const store = await createAudioStore({ indexedDB: { open: broken } as unknown as IDBFactory });
	assert.equal(store.persistent, false);
	assert.equal(await store.put(chunk(SID, 1, 5)), true);
	assert.deepEqual(
		(await store.list()).map((r) => r.n),
		[1]
	);
	assert.equal(await store.total(), 5);
	assert.equal(await store.maxSeq(SID), 1);
	assert.equal(await store.remove(SID, 1), true);
	assert.equal(await store.oldest(), null);
});
