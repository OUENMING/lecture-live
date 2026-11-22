/* web/tests/audio-asr/soniox.test.mjs —— AsrClient（lib/asr/soniox.ts）的离线自测
 *
 * 跑：node tests/audio-asr/soniox.test.mjs   （或 npm run test:audio-asr）
 *
 * WebSocket 是本地假类（lib.mjs 的 makeSocketFarm），**不连网、不要 key、不花钱**。
 * 验 S2 已经验过的五条 + 两条边界：
 *   1. 握手：key 走子协议（两元素），连上第一条消息是 config，字段与顺序 = stream_asr._config()；
 *   2. 断线重连：先补发断线期间的帧（顺序不乱、不重），然后才续新帧；
 *   3. 旧连接迟到的消息一律丢（代号比对）；
 *   4. 退避序列 30→60→120 ms（按注入值量实际间隔）；
 *   5. keepalive：无音频时按 keepaliveMs 发 {"type":"keepalive"}；
 *   6. 缓冲溢出丢最旧的；7. 退避用完 → failed；close() 后不再重连。
 */
import { pathToFileURL } from 'node:url';
import { makeReport, makeSocketFarm, until, sleep } from './lib.mjs';
import { createSonioxClient, DEFAULT_BACKOFF_MS } from '../../src/lib/asr/soniox.ts';

const WS_URL = 'wss://example.test/rt';
const CONFIG_ORDER = [
	'model',
	'audio_format',
	'sample_rate',
	'num_channels',
	'language_hints',
	'enable_endpoint_detection',
	'enable_speaker_diarization',
];

/** 4 字节的假帧，内容就是标记字符，便于看顺序 */
function frame(tag) {
	const b = new ArrayBuffer(4);
	new Uint8Array(b).set([tag.charCodeAt(0), 0, 0, 0]);
	return b;
}
const tagOf = (b) => String.fromCharCode(new Uint8Array(b)[0]);

/** 造一个带假 socket、每次连接都换 key 的客户端 */
function makeClient(farm, over = {}) {
	const keys = [];
	const client = createSonioxClient({
		getKey: async () => {
			const k = `tk${keys.length + 1}`;
			keys.push(k);
			return k;
		},
		url: WS_URL,
		createSocket: (url, protocols) => new farm.FakeSocket(url, protocols),
		backoffMs: [10, 20, 40],
		keepaliveMs: 15000,
		keepaliveTickMs: 500,
		...over,
	});
	return { client, keys };
}

export async function run(report = makeReport()) {
	report.check(
		'默认退避 500/1000/2000/4000/4000/4000',
		DEFAULT_BACKOFF_MS.join(',') === '500,1000,2000,4000,4000,4000',
		DEFAULT_BACKOFF_MS.join(',')
	);

	/* ── 1+2+3：握手 → 断线缓冲 → 补发顺序 → 迟到消息 ── */
	{
		const farm = makeSocketFarm();
		const { client, keys } = makeClient(farm);
		const got = [];
		client.onMessage((raw) => got.push(raw));
		const s0 = await until(() => farm.created[0], 500);

		report.check('初次连接状态 connecting', client.state === 'connecting', client.state);
		report.check(
			'key 走子协议（两元素 + 每次连接一个）',
			s0.protocols.length === 2 && s0.protocols[0] === 'soniox-api-key' && s0.protocols[1] === 'tk1',
			JSON.stringify(s0.protocols)
		);
		report.check('连上之前不发任何东西', s0.sent.length === 0, `${s0.sent.length} 条`);

		s0.doOpen();
		report.check('open 后状态 open', client.state === 'open', client.state);

		const cfgRaw = s0.sent[0];
		let cfg = null;
		try {
			cfg = JSON.parse(cfgRaw);
		} catch {
			cfg = null;
		}
		report.check(
			'第一条是 config，字段与顺序同 stream_asr._config()',
			cfg && Object.keys(cfg).join(',') === CONFIG_ORDER.join(','),
			cfg ? Object.keys(cfg).join(',') : String(cfgRaw).slice(0, 80)
		);
		report.check(
			'config 值：pcm_s16le / 16000 / 1 / en / 端点检测',
			cfg &&
				cfg.audio_format === 'pcm_s16le' &&
				cfg.sample_rate === 16000 &&
				cfg.num_channels === 1 &&
				Array.isArray(cfg.language_hints) &&
				cfg.language_hints[0] === 'en' &&
				cfg.enable_endpoint_detection === true &&
				cfg.enable_speaker_diarization === true,
			cfg ? `${cfg.model} ${cfg.audio_format} ${cfg.sample_rate}` : '—'
		);

		// 在线时直发
		client.send(frame('a'));
		report.check('在线帧直发', s0.sent.length === 2 && tagOf(s0.sent[1]) === 'a', `${s0.sent.length} 条`);

		// 掉线 → 收发都走缓冲
		s0.doClose(1006, 'net down');
		report.check('掉线后状态 reconnecting', client.state === 'reconnecting', client.state);
		for (const t of ['b', 'c', 'd', 'e', 'f']) client.send(frame(t));

		const s1 = await until(() => farm.created[1], 1000);
		report.check('第二次连接换了新 key（single_use）', keys.length === 2 && s1.protocols[1] === 'tk2', keys.join(','));
		s1.doOpen();
		await until(() => client.state === 'open', 500);

		const resent = s1.sent.slice(1).map(tagOf);
		report.check(
			'重连先发 config，再按顺序补发 5 帧',
			JSON.parse(s1.sent[0]).audio_format === 'pcm_s16le' && resent.join('') === 'bcdef',
			`${s1.sent.length} 条 / ${resent.join('')}`
		);
		report.check('补发不重（无重复帧）', new Set(resent).size === resent.length);

		client.send(frame('g'));
		report.check('补发完才续新帧', s1.sent.slice(1).map(tagOf).join('') === 'bcdefg', s1.sent.slice(1).map(tagOf).join(''));

		// 迟到消息：旧连接的 handler 仍在，但代号不认
		s1.doMessage('fresh');
		s0.doMessage('late');
		report.check('新连接的消息照收', got.includes('fresh'), got.join('|'));
		report.check('旧连接迟到的消息被丢', !got.includes('late'), got.join('|') || '(空)');

		client.close();
		report.check('close() 后状态 closed', client.state === 'closed', client.state);
		await sleep(40);
		report.check('close() 后不再重连', farm.created.length === 2, `${farm.created.length} 条连接`);
	}

	/* ── 4：退避序列 ── */
	{
		const farm = makeSocketFarm();
		const { client } = makeClient(farm, { backoffMs: [30, 60, 120] });
		const gaps = [];
		let prevFailAt = 0;
		// 前三条连接都在握手前被掐；第四条正常开
		for (let i = 0; i < 4; i++) {
			const s = await until(() => farm.created[i], 2000);
			if (i > 0) gaps.push(s.createdAt - prevFailAt);
			if (i < 3) {
				prevFailAt = Date.now();
				s.doClose(1006, 'handshake fail');
			} else {
				s.doOpen();
			}
		}
		await until(() => client.state === 'open', 500);
		const ok =
			gaps.length === 3 &&
			gaps[0] >= 27 &&
			gaps[0] <= 400 &&
			gaps[1] >= 57 &&
			gaps[1] <= 500 &&
			gaps[2] >= 117 &&
			gaps[2] <= 800 &&
			gaps[0] < gaps[1] &&
			gaps[1] < gaps[2];
		report.check('退避序列 30→60→120 ms（实测间隔递增且不短于设定）', ok, `${gaps.join(' / ')} ms`);
		report.check('第 4 次连接成功 → 状态 open', client.state === 'open', client.state);
		client.close();
	}

	/* ── 5：keepalive ── */
	{
		const farm = makeSocketFarm();
		const { client } = makeClient(farm, { keepaliveMs: 30, keepaliveTickMs: 5 });
		const s0 = await until(() => farm.created[0], 500);
		s0.doOpen();
		await sleep(140);
		const ka = s0.sent.slice(1).filter((d) => typeof d === 'string' && d.includes('keepalive'));
		let parsed = null;
		try {
			parsed = JSON.parse(ka[0]);
		} catch {
			parsed = null;
		}
		report.check(
			'30 s→30 ms 无音频时按节拍发 keepalive（≥2 条）',
			ka.length >= 2 && parsed && parsed.type === 'keepalive',
			`${ka.length} 条 / ${ka[0]}`
		);
		report.check('keepalive 不夹带音频', s0.sent.every((d) => typeof d === 'string'), `${s0.sent.length} 条全为文本`);

		// 有音频在流时不该发（两帧之间 < keepaliveMs）
		const before = s0.sent.length;
		client.send(frame('a'));
		await sleep(10);
		const extra = s0.sent.slice(before).filter((d) => typeof d === 'string');
		report.check('刚发过音频就不发 keepalive', extra.length === 0, `${extra.length} 条`);
		client.close();
	}

	/* ── 6：缓冲溢出丢最旧的 ── */
	{
		const farm = makeSocketFarm();
		const { client } = makeClient(farm, { ringCap: 3, backoffMs: [5] });
		const s0 = await until(() => farm.created[0], 500);
		s0.doOpen();
		s0.doClose(1006, 'down');
		for (const t of ['1', '2', '3', '4', '5']) client.send(frame(t));
		const s1 = await until(() => farm.created[1], 1000);
		s1.doOpen();
		await until(() => client.state === 'open', 500);
		report.check(
			'ringCap=3：溢出丢最旧的，补发 3-4-5',
			s1.sent.slice(1).map(tagOf).join('') === '345',
			s1.sent.slice(1).map(tagOf).join('') || '(空)'
		);
		client.close();
	}

	/* ── 7：退避用完 → failed ── */
	{
		const farm = makeSocketFarm();
		const { client } = makeClient(farm, { backoffMs: [2, 2] });
		for (let i = 0; i < 3; i++) {
			const s = await until(() => farm.created[i], 1000);
			s.doClose(1006, 'always fail');
		}
		await until(() => client.state === 'failed', 1000);
		report.check('退避用完 → failed（不再重连）', client.state === 'failed', client.state);
		client.send(frame('x')); // 不该炸
		await sleep(30);
		report.check('failed 之后不新建连接', farm.created.length === 3, `${farm.created.length} 条连接`);
		client.close();
	}

	/* ── 8：握手超时 ── */
	{
		const farm = makeSocketFarm();
		const { client } = makeClient(farm, { backoffMs: [2], handshakeTimeoutMs: 20 });
		const s0 = await until(() => farm.created[0], 500);
		await sleep(60); // 不 open → 20 ms 后超时，关掉重来
		report.check('握手超时会主动关掉那条连接', s0.readyState === 3, `readyState=${s0.readyState}`);
		await until(() => farm.created[1], 500);
		report.check('握手超时后按退避重连', farm.created.length === 2, `${farm.created.length} 条连接`);
		client.close();
	}

	return report.finish();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await run();
