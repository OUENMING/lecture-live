// web/tests/audio/switch.test.ts —— createMicSource 的中途换设备 / 拔设备回退（W14）。
//
// 钉的判据（核心时钟挂在帧 seq 上，所以这几条是硬要求）：
//   · 换设备后 AudioContext / worklet / onFrame 都不变，seq 接着往下走，不归零；
//   · 旧 MediaStream 的 track 被 stop，旧源节点被 disconnect；
//   · 当前设备被拔掉（track ended）→ 自动退回默认输入（不带 deviceId 再要一次）+ 回调 onDeviceLost；
//   · 目标设备打不开 → 返回 false，原链路还在出帧。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createMicSource, micConstraints } from '../../src/lib/audio/mic.ts';
import type { AudioFrame } from '../../src/lib/audio/index.ts';
import {
	FakeAudioContext,
	frameOf,
	installWorkletGlobal,
	micFarm,
	until,
	type FakeWorkletNode
} from './fakes.ts';

type Rig = {
	source: ReturnType<typeof createMicSource>;
	ctx: FakeAudioContext;
	node: FakeWorkletNode;
	frames: AudioFrame[];
	farm: ReturnType<typeof micFarm>;
};

/** 起一个已 ready 的麦（worklet 已报到） */
async function rig(opts: { deviceId?: string; gum?: (c: MediaStreamConstraints) => Promise<MediaStream> } = {}): Promise<Rig> {
	installWorkletGlobal();
	const farm = micFarm();
	const contexts: FakeAudioContext[] = [];
	const source = createMicSource({
		deviceId: opts.deviceId,
		getUserMedia: opts.gum ?? farm.getUserMedia,
		createContext: () => {
			const c = new FakeAudioContext();
			contexts.push(c);
			return c;
		}
	});
	const frames: AudioFrame[] = [];
	const started = source.start((f) => frames.push(f));
	await until(() => contexts[0]?.workletNodes.length === 1);
	const ctx = contexts[0];
	const node = ctx.workletNodes[0];
	node.post({ type: 'ready' });
	await started;
	return { source, ctx, node, frames, farm };
}

test('micConstraints(deviceId) 走 exact；不传就不带 deviceId', () => {
	const audio = micConstraints('dev-1').audio as MediaTrackConstraints;
	assert.deepEqual(audio.deviceId, { exact: 'dev-1' });
	assert.equal(audio.echoCancellation, true);
	assert.equal((micConstraints().audio as MediaTrackConstraints).deviceId, undefined);
});

test('构造时传 deviceId：第一次 getUserMedia 就带 exact', async () => {
	const { farm } = await rig({ deviceId: 'dev-1' });
	assert.deepEqual((farm.calls[0].audio as MediaTrackConstraints).deviceId, { exact: 'dev-1' });
});

test('换设备：seq 连续、onFrame 不变、不重建 AudioContext / worklet，旧流被停', async () => {
	const r = await rig();
	r.node.post({ type: 'frame', data: frameOf(1000) });
	r.node.post({ type: 'frame', data: frameOf(1000) });
	assert.deepEqual(r.frames.map((f) => f.seq), [0, 1]);

	const ok = await r.source.switchDevice?.('dev-2');
	assert.equal(ok, true);

	// 新流的约束对了；上下文 / worklet / 回调都没换
	assert.deepEqual((r.farm.calls[1].audio as MediaTrackConstraints).deviceId, { exact: 'dev-2' });
	assert.equal(r.ctx.closed, false, 'AudioContext 不该被关');
	assert.equal(r.ctx.workletNodes.length, 1, 'worklet 节点不该重建');
	assert.equal(r.ctx.modules.length, 1, 'worklet 模块只装一次');
	assert.equal(r.ctx.sources.length, 2, '只多了一个源节点');

	// 旧流停、旧源节点断
	assert.equal(r.farm.streams[0].tracks[0].stopped, true, '旧 track 应被 stop');
	assert.equal(r.ctx.sources[0].disconnected, true, '旧源节点应断开');
	assert.equal(r.ctx.sources[1].disconnected, false);

	// 帧继续出，seq 接着走
	r.node.post({ type: 'frame', data: frameOf(1000) });
	assert.deepEqual(r.frames.map((f) => f.seq), [0, 1, 2], 'seq 不能归零');
});

test('换设备：拿不到目标设备时返回 false，原链路还出声', async () => {
	const boom = async () => {
		throw new Error('NotFoundError');
	};
	let allow = true;
	const r = await rig({
		gum: async (c) => {
			if (allow) return micFarm().getUserMedia(c);
			return boom();
		}
	});
	r.node.post({ type: 'frame', data: frameOf(1000) });
	allow = false;
	assert.equal(await r.source.switchDevice?.('dev-x'), false);
	r.node.post({ type: 'frame', data: frameOf(1000) });
	assert.deepEqual(r.frames.map((f) => f.seq), [0, 1], '换失败不该影响原有帧');
});

test('拔设备（track ended）→ 退回默认输入 + 回调 onDeviceLost 一次', async () => {
	const r = await rig({ deviceId: 'dev-1' });
	let lost = 0;
	r.source.onDeviceLost?.(() => lost++);
	r.node.post({ type: 'frame', data: frameOf(1000) });

	// 浏览器里拔掉设备 = track 发 ended
	r.farm.streams[0].tracks[0].fire('ended');
	await until(() => r.farm.calls.length === 2);

	assert.equal(lost, 1, 'onDeviceLost 应该回调一次');
	const audio = r.farm.calls[1].audio as MediaTrackConstraints;
	assert.equal(audio.deviceId, undefined, '回退应该是不指定设备（系统默认）');

	r.node.post({ type: 'frame', data: frameOf(1000) });
	assert.deepEqual(r.frames.map((f) => f.seq), [0, 1], '回退后 seq 继续');
	assert.equal(r.farm.streams[0].tracks[0].stopped, true);
});

test('stop 之后再 switchDevice 返回 false（不偷开麦克风）', async () => {
	const r = await rig();
	r.source.stop();
	const before = r.farm.calls.length;
	assert.equal(await r.source.switchDevice?.('dev-2'), false);
	assert.equal(r.farm.calls.length, before, 'stop 之后不该再要流');
});

test('换设备途中被 stop：晚到的新流被退掉', async () => {
	installWorkletGlobal();
	const farm = micFarm();
	const contexts: FakeAudioContext[] = [];
	const source = createMicSource({
		getUserMedia: farm.getUserMedia,
		createContext: () => {
			const c = new FakeAudioContext();
			contexts.push(c);
			return c;
		}
	});
	const started = source.start(() => {});
	await until(() => contexts[0]?.workletNodes.length === 1);
	contexts[0].workletNodes[0].post({ type: 'ready' });
	await started;

	const pending = source.switchDevice?.('dev-2');
	source.stop();
	assert.equal(await pending, false);
	assert.equal(farm.streams[1].tracks[0].stopped, true, '迟到的新流要 stop');
});

test('OCR A：权限弹窗期间 stop() → 拿到的流立刻被关，start 正常返回、不重开麦', async () => {
	installWorkletGlobal();
	const farm = micFarm();
	let release!: () => void;
	const gate = new Promise<void>((r) => (release = r));
	const source = createMicSource({
		getUserMedia: async (c) => {
			await gate;
			return farm.getUserMedia(c);
		},
		createContext: () => new FakeAudioContext()
	});
	const started = source.start(() => {});
	source.stop();
	release();
	await started;
	assert.equal(farm.streams[0].tracks[0].stopped, true, '迟到的流要被关');
	assert.equal(source.stream, null, '不该记成正在采音');
});
