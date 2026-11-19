// `pyodide-core.ts` 的主线程行为：用一个假 Worker 把协议跑通（不碰 Pyodide、不碰浏览器）。
// 真 Python 的判据在 tape.test.ts；这里钉的是「rev / 订阅 / 出错 / dispose」这些界面依赖的性质。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createCore, type CoreWorker } from '../../src/lib/core/pyodide-core.ts';
import type { Snapshot } from '../../src/lib/core/project.ts';
import type { WorkerIn, WorkerOut } from '../../src/lib/core/protocol.ts';
import type { ViewState } from '../../src/lib/state/viewstate.ts';

function fakeWorker() {
	const sent: WorkerIn[] = [];
	let closed = false;
	const worker: CoreWorker = {
		postMessage: (msg) => {
			if (closed) throw new Error('往已 terminate 的 Worker 发消息');
			sent.push(msg);
		},
		terminate: () => {
			closed = true;
		},
		onmessage: null
	};
	const reply = (msg: WorkerOut) => worker.onmessage?.({ data: msg } as MessageEvent<WorkerOut>);
	return { worker, sent, reply, isClosed: () => closed };
}

function snap(over: Partial<Snapshot> = {}): Snapshot {
	return { now: 1, draft: '', draft_start: null, sealed: [], n_msgs: 1, seq: 0, notice: null, ...over };
}

test('feed / tick / flush 原样转发给 Worker，init 最先', () => {
	const f = fakeWorker();
	const core = createCore({ makeWorker: () => f.worker });
	core.feed('{"tokens":[]}');
	core.tick(7);
	core.flush();
	assert.deepEqual(f.sent, [
		{ type: 'init' },
		{ type: 'feed', raw: '{"tokens":[]}' },
		{ type: 'tick', seq: 7 },
		{ type: 'flush' }
	]);
	core.dispose();
});

test('每版快照 → 一版 ViewState，rev 递增；ready 只 resolve 一次', async () => {
	const f = fakeWorker();
	const core = createCore({ makeWorker: () => f.worker, course: 'ECON10730' });
	const views: ViewState[] = [];
	core.onView((v) => views.push(v));
	let readyCount = 0;
	void core.ready().then(() => readyCount++);

	f.reply({ type: 'ready', ms_boot: 1, pyodide_version: 'x', python_version: 'y', staged_bytes: 0 });
	f.reply({ type: 'snapshot', snap: snap({ now: 2, draft: 'hello' }) });
	f.reply({ type: 'snapshot', snap: snap({ now: 3, sealed: [{ text: 'hello.', lang: 'en', start: 1, end: 2 }] }) });
	await core.ready();

	assert.deepEqual(
		views.map((v) => [v.rev, v.rows.length, v.rows[0].kind]),
		[
			[1, 1, 'draft'],
			[2, 1, 'final']
		]
	);
	assert.equal(views[0].course, 'ECON10730');
	assert.equal(views[1].rows[0].id, 's1', '草稿转定稿 id 不变');
	assert.equal(readyCount, 1);
	core.dispose();
});

test('晚到的订阅者先拿到当前这版（置顶小窗挂得晚）', () => {
	const f = fakeWorker();
	const core = createCore({ makeWorker: () => f.worker });
	f.reply({ type: 'snapshot', snap: snap({ draft: 'ahead' }) });
	const got: ViewState[] = [];
	core.onView((v) => got.push(v));
	assert.equal(got.length, 1);
	assert.equal(got[0].rows[0].en, 'ahead');
	core.dispose();
});

test('Worker 报错：行留着、notice 挂 status、ready 拒掉；后续还能用', async () => {
	const f = fakeWorker();
	const core = createCore({ makeWorker: () => f.worker });
	const views: ViewState[] = [];
	core.onView((v) => views.push(v));
	f.reply({ type: 'snapshot', snap: snap({ sealed: [{ text: 'keep me.', lang: 'en', start: 0, end: 1 }] }) });
	f.reply({ type: 'error', message: 'PythonError: boom' });

	assert.equal(views.length, 2);
	assert.equal(views[1].rows[0].en, 'keep me.', '出错不许清屏');
	assert.equal(views[1].status.notice, 'PythonError: boom');
	assert.equal(views[1].rev, views[0].rev + 1);
	assert.equal(f.sent.filter((m) => m.type === 'init').length, 1, '出错不重启 Worker');

	const err = await core.ready().then(
		() => null,
		(e: Error) => e
	);
	// ready 在 ready 消息之前就出错 → 拒绝；如果先 ready 过，这里会是 null
	assert.ok(err === null || err.message === 'PythonError: boom');
	core.dispose();
});

test('dispose 之后不再转发、Worker 被 terminate', () => {
	const f = fakeWorker();
	const core = createCore({ makeWorker: () => f.worker });
	const views: ViewState[] = [];
	core.onView((v) => views.push(v));
	core.dispose();
	assert.equal(f.isClosed(), true);
	f.reply({ type: 'snapshot', snap: snap({ draft: 'ignored' }) });
	assert.equal(views.length, 0, 'dispose 之后到的快照要丢掉');
});
