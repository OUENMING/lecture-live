// web/tests/audio/devices.test.ts —— listMics / onDevicesChanged（lib/audio/devices.ts）的离线自测。
//
// 假 MediaDevices：enumerateDevices 返回给定清单，devicechange 事件由测试手动触发。
// 钉的判据：只留 audioinput、没授权时 label 为空串照收、拿不到 / 抛错都退成空清单、订阅可取消。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { listMics, onDevicesChanged, type DevicesLike } from '../../src/lib/audio/devices.ts';

function fakeDevices(
	devices: Array<{ kind?: string; deviceId?: string; label?: string }>,
	opts: { throwOnEnumerate?: boolean; noEvents?: boolean } = {}
) {
	const listeners: Array<() => void> = [];
	let removed = 0;
	const md = {
		enumerateDevices: async () => {
			if (opts.throwOnEnumerate) throw new Error('NotAllowedError');
			return devices;
		}
	} as DevicesLike & { fire(): void };
	if (!opts.noEvents) {
		md.addEventListener = (_t: 'devicechange', cb: () => void) => {
			listeners.push(cb);
		};
		md.removeEventListener = (_t: 'devicechange', cb: () => void) => {
			removed += 1;
			const i = listeners.indexOf(cb);
			if (i >= 0) listeners.splice(i, 1);
		};
	}
	return {
		md,
		listeners,
		removed: () => removed,
		fire: () => listeners.slice().forEach((cb) => cb())
	};
}

test('listMics：只留 audioinput，顺序不变，没授权时 label 是空串', async () => {
	const f = fakeDevices([
		{ kind: 'audioinput', deviceId: 'a', label: 'MacBook 麦克风' },
		{ kind: 'audiooutput', deviceId: 'spk', label: '扬声器' },
		{ kind: 'audioinput', deviceId: 'b', label: '' },
		{ kind: 'videoinput', deviceId: 'cam', label: '摄像头' }
	]);
	const mics = await listMics({ mediaDevices: f.md });
	assert.deepEqual(mics, [
		{ deviceId: 'a', label: 'MacBook 麦克风' },
		{ deviceId: 'b', label: '' }
	]);
});

test('listMics：没有 mediaDevices / enumerateDevices 抛错都退成空清单，不抛', async () => {
	assert.deepEqual(await listMics({ mediaDevices: null }), []);
	const boom = fakeDevices([], { throwOnEnumerate: true });
	assert.deepEqual(await listMics({ mediaDevices: boom.md }), []);
	// 不传 deps 时取全局 navigator：node 里没有 mediaDevices，也不能抛
	assert.deepEqual(await listMics(), []);
});

test('listMics：没给 deviceId 的条目保底成空串（界面据此当默认输入）', async () => {
	const f = fakeDevices([{ kind: 'audioinput' }]);
	assert.deepEqual(await listMics({ mediaDevices: f.md }), [{ deviceId: '', label: '' }]);
});

test('onDevicesChanged：devicechange 触发回调，退订后不再触发', () => {
	const f = fakeDevices([]);
	let n = 0;
	const off = onDevicesChanged(() => n++, { mediaDevices: f.md });
	f.fire();
	assert.equal(n, 1);
	off();
	assert.equal(f.removed(), 1);
	f.fire();
	assert.equal(n, 1, '退订后不该再回调');
	off(); // 幂等
	assert.equal(f.removed(), 1);
});

test('onDevicesChanged：没有 addEventListener 的环境退成空函数', () => {
	const f = fakeDevices([], { noEvents: true });
	let n = 0;
	const off = onDevicesChanged(() => n++, { mediaDevices: f.md });
	off();
	assert.equal(n, 0);
	assert.equal(typeof off, 'function');
});
