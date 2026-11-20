// `lib/telemetry/recorder.ts` 的判据：不支持 MediaRecorder → 空操作 + 一条 recorder_unsupported；
// 每段先落库再上传、成功才删、失败留着且 resumePending 能补；stop() 拿到最后一段；
// 同一时刻只 1 个上传在途、离线不发、本地超限丢最旧；只用传进来的流的克隆，停也只停克隆。
// 定时器 / MediaRecorder / 仓库 / 上传全注入，不碰真浏览器。

import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { createClassRecorder, resumePending } from '../../src/lib/telemetry/recorder.ts';
import type { RecorderOptions } from '../../src/lib/telemetry/recorder.ts';
import type { Telemetry } from '../../src/lib/telemetry/buffer.ts';
import type { AudioChunk, AudioStore } from '../../src/lib/telemetry/audio-store.ts';
import { createAudioStore } from '../../src/lib/telemetry/audio-store.ts';
import { dumpStore, fakeClock, fakeStream, FakeMediaRecorder, settle } from './fakes.ts';

const SID = 's_3f9a1c2e7b40';

function fakeTel() {
	const events: { type: string; [k: string]: unknown }[] = [];
	const tel: Telemetry = {
		track(type, data) {
			events.push({ type, ...(data ?? {}) });
		},
		setSession() {},
		async flush() {},
		stop() {},
		size: () => events.length
	};
	return { tel, types: () => events.map((e) => e.type) };
}

beforeEach(() => FakeMediaRecorder.reset());

/** 停录音：`onstop` 一般由浏览器给，这里手动补上，并让 3 s 兜底定时器到期（不必真等） */
async function stopRec(s: { rec: { stop(): Promise<void> }; clock: { advance(ms: number): Promise<void> }; media?: () => { finish(): void } }) {
	const p = s.rec.stop();
	try {
		s.media?.().finish();
	} catch {
		/* 不支持 MediaRecorder 的用例没有实例 */
	}
	await s.clock.advance(3100);
	await p;
}

/** 一份能被测的录音装配：内存仓库 + 假 MediaRecorder + 可失败 / 可挂住的上传 */
async function setup(opts: Partial<RecorderOptions> = {}) {
	const clock = fakeClock();
	const store = await createAudioStore({ indexedDB: null });
	const { stream, original, clonesAllStopped } = fakeStream();
	const uploads: { session: string; n: number; size: number }[] = [];
	const tel = fakeTel();
	const state = { fail: false, online: true };
	let gate: Promise<void> | null = null;
	const rec = createClassRecorder({
		stream,
		sessionId: SID,
		store,
		tel: tel.tel,
		setTimer: clock.setTimer,
		now: () => 1_700_000_000_000,
		maxLocalBytes: 2500,
		mediaRecorder: FakeMediaRecorder as unknown as RecorderOptions['mediaRecorder'],
		upload: async (session, n, blob) => {
			if (gate) await gate;
			if (state.fail) throw new Error('upload failed');
			uploads.push({ session, n, size: blob.size });
		},
		...opts
	});
	return {
		rec,
		clock,
		store,
		uploads,
		tel,
		state,
		original,
		clonesAllStopped,
		media: () => FakeMediaRecorder.latest(),
		gateWith: (p: Promise<void> | null) => {
			gate = p;
		}
	};
}

test('MediaRecorder 不支持 → 空操作，只记一条 recorder_unsupported', async () => {
	FakeMediaRecorder.reset([]);
	const s = await setup();
	s.rec.start();
	assert.deepEqual(s.tel.types(), ['recorder_unsupported']);
	await s.rec.stop();
	assert.deepEqual(await dumpStore(s.store), []);
	assert.deepEqual(s.uploads, []);
	assert.equal(s.original.stopped, false);
	assert.equal(FakeMediaRecorder.made.length, 0);
});

test('mimeType 依次试 webm → ogg → mp4；24 kbps、不用 timeslice（每段一个完整文件）', async () => {
	FakeMediaRecorder.reset(['audio/ogg;codecs=opus', 'audio/mp4']);
	const s = await setup();
	s.rec.start();
	assert.equal(s.media().opts.mimeType, 'audio/ogg;codecs=opus');
	assert.equal(s.media().opts.audioBitsPerSecond, 24_000);
	assert.equal(s.media().timeslice, 0);
	assert.equal(s.tel.types().includes('rec_start'), true);
	await stopRec(s);
});

test('每段先落库再上传；失败留着、成功才删', async () => {
	const s = await setup();
	s.rec.start();
	s.state.fail = true;
	s.media().emit(1000);
	await settle();
	assert.deepEqual(
		(await dumpStore(s.store)).map((r) => r.n),
		[1],
		'失败的那段必须留在库里'
	);
	assert.deepEqual(s.uploads, []);

	s.state.fail = false;
	await s.clock.advance(30_000); // 重试定时器
	await settle();
	assert.deepEqual(s.uploads, [{ session: SID, n: 1, size: 1000 }]);
	assert.deepEqual(await dumpStore(s.store), [], '传成功才删');
	await stopRec(s);
});

test('同一时刻只 1 个上传在途', async () => {
	let open: () => void = () => {};
	const gate = new Promise<void>((r) => {
		open = () => r();
	});
	const s = await setup();
	s.rec.start();
	s.gateWith(gate);
	s.media().emit(100);
	s.media().emit(200);
	s.media().emit(300);
	await settle();
	assert.equal(s.uploads.length, 0, '第一个还挂在 gate 上');
	open();
	await settle();
	s.gateWith(null);
	await settle();
	assert.deepEqual(
		s.uploads.map((u) => u.n),
		[1, 2, 3]
	);
	await stopRec(s);
});

test('离线不发（留着等 resumePending）', async () => {
	const s = await setup();
	s.rec.start();
	const nav = globalThis.navigator as unknown as { onLine: boolean };
	Object.defineProperty(nav, 'onLine', { value: false, configurable: true, writable: true });
	try {
		s.media().emit(500);
		await settle();
		assert.deepEqual(s.uploads, []);
		assert.deepEqual(
			(await dumpStore(s.store)).map((r) => r.n),
			[1]
		);
	} finally {
		Object.defineProperty(nav, 'onLine', { value: true, configurable: true, writable: true });
	}
	await stopRec(s);
});

test('本地超限丢最旧的段并记事件', async () => {
	const s = await setup();
	s.rec.start();
	s.state.fail = true; // 传不出去 → 段会一直堆在本地
	for (let i = 0; i < 4; i++) s.media().emit(1000);
	await settle();
	// maxLocalBytes = 2500：4 段 × 1000 字节 → 只剩最新的 2 段
	assert.deepEqual(
		(await dumpStore(s.store)).map((r) => r.n),
		[3, 4]
	);
	assert.equal(s.tel.types().includes('rec_dropped_local'), true);
	await stopRec(s);
});

test('stop() 拿到最后一段（先落库再返回），并只停克隆出来的 track', async () => {
	const s = await setup();
	s.rec.start();
	s.media().emit(700);
	await settle();
	const stopping = s.rec.stop();
	s.media().emit(900); // 停之前的最后一段
	s.media().finish(); // 浏览器给 onstop
	await stopping;
	await settle();
	assert.deepEqual(
		s.uploads.map((u) => u.size),
		[700, 900],
		'stop 之后最后一段也要传出去'
	);
	assert.deepEqual(await dumpStore(s.store), [], '传成功就删干净');
	assert.equal(s.original.stopped, false, '原件那条流的 track 不许被停');
	assert.equal(s.clonesAllStopped(), true, '克隆出来的 track 要停掉');
});

test('stop() 最多等 3 s：浏览器不回调也不卡住', async () => {
	const s = await setup();
	s.rec.start();
	const stopping = s.rec.stop();
	await s.clock.advance(3000);
	await stopping;
	assert.equal(s.tel.types().includes('rec_stop'), true);
	await s.clock.advance(3000);
});

test('resumePending：页面重开把库里残留的段补传，成功才删', async () => {
	const store: AudioStore = await createAudioStore({ indexedDB: null });
	const mk = (n: number, size: number): AudioChunk => ({
		session: SID,
		n,
		mime: 'audio/webm;codecs=opus',
		at: 1_700_000_000_000 + n,
		size,
		blob: new Blob([new Uint8Array(size)])
	});
	await store.put(mk(1, 10));
	await store.put(mk(2, 20));
	await store.put(mk(3, 30));
	const sent: number[] = [];
	let failAt = 2;
	const sentCount = await resumePending({
		store,
		upload: async (_s, n, blob) => {
			if (n === failAt) throw new Error('nope');
			sent.push(blob.size);
		}
	});
	assert.equal(sentCount, 1, '第 2 段失败就停下（保持顺序，不跳着传）');
	assert.deepEqual(sent, [10]);
	assert.deepEqual(
		(await dumpStore(store)).map((r) => r.n),
		[2, 3]
	);
	// 网络好了再来一次：从剩下最旧的接着补（同样用假上传，绝不碰真网络）
	failAt = -1;
	const rest: number[] = [];
	assert.equal(
		await resumePending({
			store,
			upload: async (_s, _n, blob) => void rest.push(blob.size)
		}),
		2
	);
	assert.deepEqual(rest, [20, 30]);
	assert.deepEqual(await dumpStore(store), []);
});

test('resumePending 永不抛（仓库坏掉 / 没有 IndexedDB）', async () => {
	const broken = {
		persistent: false,
		async put() {
			return true;
		},
		async list() {
			throw new Error('坏了');
		},
		async remove() {
			return true;
		},
		async total() {
			return 0;
		},
		async oldest() {
			return null;
		},
		async maxSeq() {
			return 0;
		}
	};
	assert.equal(await resumePending({ store: broken }), 0);
});

test('setSessionId 之后的新段算到新会话名下（旧段不跟过去）', async () => {
	const s = await setup();
	s.rec.start();
	s.media().emit(100);
	await settle();
	s.rec.setSessionId('s_new');
	s.media().emit(200);
	await settle();
	assert.deepEqual(
		s.uploads.map((u) => [u.session, u.n]),
		[
			[SID, 1],
			['s_new', 2]
		]
	);
	await stopRec(s);
});

test('每 chunkMs 换一个 MediaRecorder：每段都是独立完整文件，序号连续', async () => {
	const s = await setup();
	s.rec.start();
	const first = s.media();
	first.emit(500);
	await s.clock.advance(60_000);
	assert.equal(first.stopped, true, '到点停掉第一段');
	first.finish();
	assert.equal(FakeMediaRecorder.made.length, 2, 'onstop 后立刻开第二段');
	assert.equal(s.media().timeslice, 0);
	s.media().emit(700);
	await settle();
	assert.deepEqual(s.uploads.map((u) => [u.n, u.size]), [[1, 500], [2, 700]]);
	await stopRec(s);
	assert.equal(FakeMediaRecorder.made.length, 2, 'stop 之后不再开新段');
	assert.equal(s.clonesAllStopped(), true);
	assert.equal(s.original.stopped, false);
});

test('switchStream：当前段收尾，下一段用新流的克隆，旧克隆被停', async () => {
	const s = await setup();
	s.rec.start();
	const first = s.media();
	const other = fakeStream();
	s.rec.switchStream(other.stream);
	assert.equal(first.stopped, true);
	first.finish();
	assert.equal(FakeMediaRecorder.made.length, 2);
	assert.notEqual(s.media().stream, first.stream, '第二段换了流');
	assert.equal(s.clonesAllStopped(), true, '旧流的克隆停掉了');
	assert.equal(other.original.stopped, false, '新流原件不许被停');
	await stopRec(s);
});
