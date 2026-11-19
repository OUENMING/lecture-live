/* web/tests/audio-asr/mic.test.mjs —— AudioSource（lib/audio/mic.ts）的离线自测
 *
 * 跑：node tests/audio-asr/mic.test.mjs   （或 npm run test:audio-asr）
 *
 * 没有真麦、没有浏览器：getUserMedia / AudioContext / AudioWorkletNode 全用本地假类注入
 * （`AudioWorkletNode` 只能走全局，像 worklet 自测那样当 AudioWorkletGlobalScope 的替身）。
 * 验：三开关默认值、worklet 模块地址、处理器参数、ready 之后才有帧、seq 自增、电平、stop 收尾。
 */
import { pathToFileURL } from 'node:url';
import { makeReport, until } from './lib.mjs';
import { createMicSource, micConstraints } from '../../src/lib/audio/mic.ts';

class FakeParam {
	constructor() {
		this.value = 0;
	}
}
class FakeGain {
	constructor() {
		this.gain = new FakeParam();
		this.disconnected = false;
	}
	connect(dest) {
		return dest;
	}
	disconnect() {
		this.disconnected = true;
	}
}
class FakeSourceNode {
	constructor(stream) {
		this.stream = stream;
		this.disconnected = false;
	}
	connect(dest) {
		return dest;
	}
	disconnect() {
		this.disconnected = true;
	}
}
class FakeAudioContext {
	constructor() {
		this.sampleRate = 48000;
		this.state = 'suspended';
		this.destination = { id: 'destination' };
		this.modules = [];
		this.gains = [];
		this.workletNodes = [];
		this.closed = false;
		this.audioWorklet = {
			addModule: async (url) => {
				this.modules.push(url);
			},
		};
	}
	async resume() {
		this.state = 'running';
	}
	createGain() {
		const g = new FakeGain();
		this.gains.push(g);
		return g;
	}
	createMediaStreamSource(stream) {
		this.sourceNode = new FakeSourceNode(stream);
		return this.sourceNode;
	}
	async close() {
		this.closed = true;
		this.state = 'closed';
	}
}
class FakeWorkletNode {
	constructor(ctx, name, opts) {
		this.ctx = ctx;
		this.name = name;
		this.opts = opts;
		this.disconnected = false;
		this.port = { onmessage: null };
		ctx.workletNodes.push(this);
	}
	connect(dest) {
		return dest;
	}
	disconnect() {
		this.disconnected = true;
	}
}

function fakeMic() {
	const tracks = [{ stopped: false, stop() { this.stopped = true; } }];
	const stream = { getTracks: () => tracks, id: 'fake-stream' };
	const seen = [];
	return {
		tracks,
		stream,
		seen,
		getUserMedia: async (constraints) => {
			seen.push(constraints);
			return stream;
		},
	};
}

function frameOf(value, samples = 1600) {
	const s = new Int16Array(samples);
	s.fill(value);
	return s.buffer;
}

export async function run(report = makeReport()) {
	globalThis.AudioWorkletNode = FakeWorkletNode;

	const c = micConstraints();
	const audio = c.audio;
	report.check(
		'默认约束 EC=true / NS=false / AGC=false / channelCount=1',
		audio.echoCancellation === true &&
			audio.noiseSuppression === false &&
			audio.autoGainControl === false &&
			audio.channelCount === 1 &&
			c.video === false,
		JSON.stringify(audio)
	);

	const mic = fakeMic();
	const contexts = [];
	const source = createMicSource({
		getUserMedia: mic.getUserMedia,
		createContext: () => {
			const c = new FakeAudioContext();
			contexts.push(c);
			return c;
		},
	});
	report.check('start 之前电平为 0', source.level === 0, String(source.level));
	const received = [];

	const started = source.start((f) => received.push(f));
	await until(() => contexts[0]?.workletNodes.length === 1, 500);
	const ctx = contexts[0];
	const node = ctx.workletNodes[0];

	report.check('装了静态 worklet 模块', ctx.modules[0] === '/worklets/capture-worklet.js', ctx.modules.join(','));
	report.check(
		'处理器参数：classlive-capture / 单声道 / 127 taps / 归一化默认开',
		node.name === 'classlive-capture' &&
			node.opts.channelCount === 1 &&
			node.opts.outputChannelCount.join(',') === '1' &&
			node.opts.processorOptions.taps === 127 &&
			node.opts.processorOptions.normalize === true,
		`${node.name} ${JSON.stringify(node.opts.processorOptions)}`
	);
	report.check('接到 0 增益再进 destination（不外放）', ctx.gains.length === 1 && ctx.gains[0].gain.value === 0);
	report.check('用上 getUserMedia 的流', ctx.sourceNode?.stream === mic.stream);

	// ready 之前不出帧
	report.check('worklet 报到前没有帧', received.length === 0, `${received.length} 帧`);
	node.port.onmessage({ data: { type: 'frame', data: frameOf(1000) } });
	report.check('报到前的帧被忽略（不等 ready）', received.length === 0, `${received.length} 帧`);

	node.port.onmessage({ data: { type: 'ready', inRate: 48000, outRate: 16000, frameSamples: 1600 } });
	await started;

	node.port.onmessage({ data: { type: 'frame', data: frameOf(1000) } });
	report.check('ready 之后出帧', received.length === 1, `${received.length} 帧`);
	report.check('帧 1：seq=0 / 3200 字节 s16le', received[0]?.seq === 0 && received[0]?.pcm.byteLength === 3200);
	report.check('level = s16 RMS（1000/32768 ≈ 0.031）', Math.abs(source.level - 1000 / 32768) < 1e-6, source.level.toFixed(5));

	node.port.onmessage({ data: { type: 'frame', data: frameOf(0) } });
	report.check('seq 自增到 1', received[1]?.seq === 1, String(received[1]?.seq));
	report.check('静音帧电平回到 0', source.level === 0, String(source.level));

	source.stop();
	report.check(
		'stop：停麦 + 关上下文 + 断开节点',
		mic.tracks[0].stopped === true && ctx.closed === true && node.disconnected === true
	);
	report.check('stop 之后电平归 0', source.level === 0, String(source.level));

	const restarted = source.start(() => {});
	await until(() => contexts[1]?.workletNodes.length === 1, 500);
	contexts[1].workletNodes[0].port.onmessage({ data: { type: 'ready' } });
	await restarted;
	source.stop();
	report.check('stop 之后能再 start（不残留）', contexts.length === 2 && contexts[1].closed === true);

	return report.finish();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await run();
