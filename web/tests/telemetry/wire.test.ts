// `lib/telemetry/sender.ts` 与 `upload.ts` 的判据：默认出口的 URL / 方法 / 请求头 / keepalive / priority。
// 要钉的是「发到哪、带哪个头、什么时候带 keepalive」—— 这几个错了不会有任何报错，只会静默丢数据。
// fetch 是假件，不碰网络；identity 需要的 localStorage 在 node 里没有，走它的兜底即可。

import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { wireSend } from '../../src/lib/telemetry/sender.ts';
import { putChunk } from '../../src/lib/telemetry/upload.ts';
import type { TelemetryEvent } from '../../src/lib/telemetry/buffer.ts';

type Call = { url: string; init: RequestInit & { priority?: string } };
const calls: Call[] = [];
const savedFetch = globalThis.fetch;
let nextHeaders: Record<string, string> = {};

before(() => {
	(globalThis as { fetch: unknown }).fetch = async (url: string, init: RequestInit) => {
		calls.push({ url: String(url), init });
		return new Response(null, { status: 204, headers: nextHeaders });
	};
});

after(() => {
	(globalThis as { fetch: unknown }).fetch = savedFetch;
});

const ev: TelemetryEvent = { t: 1, mono: 2, type: 'x', n: 1 };

test('默认发送端：POST /v1/web/telemetry，带 identity 头与 install6', async () => {
	calls.length = 0;
	nextHeaders = {};
	const send = wireSend(() => 's_3f9a1c2e7b40');
	const r = await send([ev], { keepalive: false });
	assert.equal(r?.off, false);
	const [call] = calls;
	assert.match(call.url, /\/v1\/web\/telemetry$/);
	assert.equal(call.init.method, 'POST');
	assert.equal(call.init.keepalive, undefined, '普通发送不带 keepalive');
	const body = JSON.parse(String(call.init.body)) as { events: TelemetryEvent[]; install6: string; session_id: string };
	assert.deepEqual(body.events, [ev]);
	assert.equal(body.session_id, 's_3f9a1c2e7b40');
	assert.equal(body.install6.length, 6);
	const h = call.init.headers as Record<string, string>;
	assert.equal(h['Content-Type'], 'application/json');
	assert.equal(h['X-ClassLive-Client'], 'web');
	assert.equal(h['X-ClassLive-Call'], 'web_telemetry');
});

test('没有会话号就不带 session_id；keepalive 原样传下去', async () => {
	calls.length = 0;
	const send = wireSend(() => null);
	await send([ev], { keepalive: true });
	const [call] = calls;
	assert.equal('session_id' in JSON.parse(String(call.init.body)), false);
	assert.equal(call.init.keepalive, true);
});

test('响应头 x-cl-telemetry: off → 回报 off', async () => {
	calls.length = 0;
	nextHeaders = { 'x-cl-telemetry': 'off' };
	const send = wireSend(() => 's_1');
	assert.deepEqual(await send([ev], { keepalive: false }), { off: true });
});

test('录音段：PUT /v1/web/audio/:session/:n，低优先级、按 mime 设 Content-Type', async () => {
	calls.length = 0;
	nextHeaders = {};
	await putChunk('s_3f9a1c2e7b40', 7, new Blob([new Uint8Array(4)], { type: 'audio/webm;codecs=opus' }), 'audio/webm;codecs=opus');
	const [call] = calls;
	assert.match(call.url, /\/v1\/web\/audio\/s_3f9a1c2e7b40\/7$/);
	assert.equal(call.init.method, 'PUT');
	assert.equal(call.init.priority, 'low');
	assert.equal((call.init.headers as Record<string, string>)['Content-Type'], 'audio/webm;codecs=opus');
	assert.equal((call.init.headers as Record<string, string>)['X-ClassLive-Call'], 'web_audio');
});

test('录音段上传：不是 2xx 就抛（由调用方决定留着还是重试）', async () => {
	(globalThis as { fetch: unknown }).fetch = async () =>
		new Response('nope', { status: 500, headers: {} });
	try {
		await assert.rejects(() => putChunk('s', 1, new Blob([new Uint8Array(1)]), 'audio/mp4'));
	} finally {
		(globalThis as { fetch: unknown }).fetch = async (url: string, init: RequestInit) => {
			calls.push({ url: String(url), init });
			return new Response(null, { status: 204, headers: nextHeaders });
		};
	}
});
