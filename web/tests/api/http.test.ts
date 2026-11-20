// `lib/api/http.ts` + `lib/api/soniox-key.ts` 的判据：请求头、总墙钟、各种状态码 → kind + 中文。
// 全部用注入的假 fetch，不连网、不花钱；服务端那几支拒绝原因抄的是 handler.js / keyentry 的原文。
import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { ApiError, apiBase, request } from '../../src/lib/api/http.ts';
import { checkPasscode, createKeySource, getSonioxKey } from '../../src/lib/api/soniox-key.ts';
import { passcode } from '../../src/lib/api/identity.ts';

const CODE = 'cl-owen-0123456789abcdef';

class FakeStorage {
	readonly map = new Map<string, string>();
	throwOn = new Set<string>();
	getItem(k: string): string | null {
		if (this.throwOn.has('get')) throw new Error('SecurityError');
		return this.map.get(k) ?? null;
	}
	setItem(k: string, v: string): void {
		if (this.throwOn.has('set')) throw new Error('SecurityError');
		this.map.set(k, String(v));
	}
}

/** 记下每次调用（地址 / 头 / body），按剧本回包 */
function fakeFetch(script: (n: number) => Response) {
	const calls: { url: string; init: RequestInit; body: unknown }[] = [];
	const fetch = async (url: string, init: RequestInit): Promise<Response> => {
		calls.push({ url, init, body: JSON.parse(String(init.body ?? '{}')) });
		return script(calls.length);
	};
	return { calls, fetch };
}

const json = (status: number, body: unknown): Response =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
const text = (status: number, body: string): Response => new Response(body, { status });

beforeEach(() => {
	(globalThis as unknown as { localStorage: FakeStorage }).localStorage = new FakeStorage();
	passcode.set(CODE);
});

test('领 key：地址 = 基址 + /v1/soniox/key，头齐全且格式对，body 只带 region', async () => {
	const { calls, fetch } = fakeFetch(() =>
		json(200, {
			api_key: 'snx_temp_x',
			ws_url: 'wss://stt-rt.soniox.com/transcribe-websocket',
			region: 'us',
			expires_in_seconds: 60,
			max_session_s: 10800
		})
	);
	const key = await getSonioxKey('EU', { fetch });
	assert.equal(calls.length, 1);
	assert.equal(calls[0].url, `${apiBase()}/v1/soniox/key`);
	assert.equal(calls[0].init.method, 'POST');
	const h = calls[0].init.headers as Record<string, string>;
	assert.equal(h.Authorization, `Bearer ${CODE}`);
	assert.equal(h['X-ClassLive-Client'], 'web');
	assert.match(h['X-ClassLive-Install'], /^[0-9a-f]{32}$/);
	assert.ok(h['X-ClassLive-Version'].length > 0);
	assert.equal(h['Content-Type'], 'application/json');
	assert.deepEqual(calls[0].body, { region: 'eu' }); // 小写化；不传 region 就是 {}
	assert.equal(key.api_key, 'snx_temp_x');
	assert.equal(key.region, 'us');
	assert.equal(key.expires_in_seconds, 60);
});

test('不传 region / purpose 时 body 是空对象（老行为逐字不变）', async () => {
	const { calls, fetch } = fakeFetch(() => json(200, { api_key: 'snx_temp_y' }));
	await getSonioxKey(undefined, { fetch });
	assert.deepEqual(calls[0].body, {});
});

test('createKeySource 每次调用都重新领一把（临时 key 是 single_use）', async () => {
	const { calls, fetch } = fakeFetch((n) => json(200, { api_key: `snx_temp_${n}` }));
	const getKey = createKeySource({ fetch });
	assert.equal(await getKey(), 'snx_temp_1');
	assert.equal(await getKey(), 'snx_temp_2');
	assert.equal(calls.length, 2);
});

test('总墙钟：超过 deadlineMs 就 abort 并抛 network/deadline（不是等每次读）', async () => {
	let aborted = false;
	const fetch = (_url: string, init: RequestInit) =>
		new Promise<Response>((_res, reject) => {
			init.signal?.addEventListener('abort', () => {
				aborted = true;
				reject(new Error('AbortError'));
			});
		});
	const t0 = Date.now();
	const err = await request('/v1/soniox/key', {}, { fetch, deadlineMs: 60 }).catch((e) => e);
	const spent = Date.now() - t0;
	assert.ok(err instanceof ApiError);
	assert.equal(err.kind, 'network');
	assert.equal(err.reason, 'deadline');
	assert.match(err.message, /等太久/);
	assert.equal(aborted, true);
	assert.ok(spent < 2000, `应在 deadline 附近就返回，实际 ${spent} ms`);
});

test('「回了头但不给完」也按总墙钟掐：body 流挂住', async () => {
	const fetch = async (_url: string, init: RequestInit) => {
		const stream = new ReadableStream({
			start(c) {
				init.signal?.addEventListener('abort', () => c.error(new Error('aborted')));
			}
		});
		return new Response(stream, { status: 200 });
	};
	const err = await request('/v1/soniox/key', {}, { fetch, deadlineMs: 60 }).catch((e) => e);
	assert.equal(err.kind, 'network');
	assert.equal(err.reason, 'deadline');
});

test('403 各原因 → kind + 中文（与 keyentry.PASSCODE_REASONS 同口径）', async () => {
	const cases: [string, string, string][] = [
		['unknown', 'passcode', '通行码不对'],
		['revoked', 'passcode', '通行码已停用'],
		['expired', 'passcode', '通行码过期了'],
		['devices', 'devices', '设备数满了']
	];
	for (const [reason, kind, say] of cases) {
		const { fetch } = fakeFetch(() => json(403, { error: reason }));
		const err = await getSonioxKey(undefined, { fetch }).catch((e) => e);
		assert.ok(err instanceof ApiError);
		assert.equal(err.kind, kind, reason);
		assert.equal(err.reason, reason);
		assert.equal(err.status, 403);
		assert.match(err.message, new RegExp(say), `${reason} 的中文`);
	}
});

test('429 → quota；403 无 body → passcode；5xx → server', async () => {
	const q = await getSonioxKey(undefined, {
		fetch: fakeFetch(() => json(429, { error: 'quota_exceeded' })).fetch
	}).catch((e) => e);
	assert.equal(q.kind, 'quota');
	assert.match(q.message, /额度用完/);

	// 429 也可能是限流（Worker 回纯文本 rate limited + retry-after）
	const rl = await getSonioxKey(undefined, {
		fetch: fakeFetch(() => text(429, 'rate limited')).fetch
	}).catch((e) => e);
	assert.equal(rl.kind, 'quota');
	assert.match(rl.message, /等一分钟/);

	const bare = await getSonioxKey(undefined, {
		fetch: fakeFetch(() => text(403, '')).fetch
	}).catch((e) => e);
	assert.equal(bare.kind, 'passcode');

	for (const [status, body, reason] of [
		[503, { error: 'passes_unavailable' }, 'passes_unavailable'],
		[503, { error: 'soniox_not_configured' }, 'soniox_not_configured'],
		[502, { error: 'soniox_upstream' }, 'soniox_upstream'],
		[500, {}, 'http_500']
	] as const) {
		const err = await getSonioxKey(undefined, { fetch: fakeFetch(() => json(status, body)).fetch }).catch(
			(e) => e
		);
		assert.equal(err.kind, 'server', `${status} ${reason}`);
		assert.equal(err.reason, reason);
	}
});

test('网络错误 → network/fetch，中文能给用户看', async () => {
	const fetch = async (): Promise<Response> => {
		throw new TypeError('Failed to fetch');
	};
	const err = await checkPasscode(CODE, { fetch }).catch((e) => e);
	assert.ok(err instanceof ApiError);
	assert.equal(err.kind, 'network');
	assert.equal(err.reason, 'fetch');
	assert.match(err.message, /连不上服务器/);
});

test('400 bad install id 也是 server：不是码的问题，别让用户去换码', async () => {
	const err = await getSonioxKey(undefined, {
		fetch: fakeFetch(() => text(400, 'bad install id')).fetch
	}).catch((e) => e);
	assert.equal(err.kind, 'server');
	assert.equal(err.status, 400);
});

test('200 但回包里没有 api_key → 抛错，不装成功', async () => {
	const err = await getSonioxKey(undefined, {
		fetch: fakeFetch(() => json(200, { ws_url: 'wss://x' })).fetch
	}).catch((e) => e);
	assert.ok(err instanceof Error);
	assert.match(err.message, /没有 api_key/);
});

test('checkPasscode：形状不对根本不发请求（不花一次额度）', async () => {
	const { calls, fetch } = fakeFetch(() => json(200, { api_key: 'snx_temp_z' }));
	const err = await checkPasscode('cl-Owen-nope', { fetch }).catch((e) => e);
	assert.ok(err instanceof ApiError);
	assert.equal(err.kind, 'passcode');
	assert.equal(err.reason, 'shape');
	assert.equal(calls.length, 0);
});

test('checkPasscode：形状对就真查一次（用刚输的那个码，不是存着的）', async () => {
	passcode.set('cl-other-0000000000000000');
	const { calls, fetch } = fakeFetch(() => json(200, { api_key: 'snx_temp_ok' }));
	await checkPasscode(` ${CODE} `, { fetch });
	assert.equal(calls.length, 1);
	assert.equal(calls[0].url, `${apiBase()}/v1/soniox/key`);
	assert.equal((calls[0].init.headers as Record<string, string>).Authorization, `Bearer ${CODE}`);
	assert.deepEqual(calls[0].body, {}); // 不传 region：让 Worker 按国家挑
});

test('没存通行码时不带 Authorization（调用方自己会先拒绝）', async () => {
	(globalThis as unknown as { localStorage: FakeStorage }).localStorage = new FakeStorage();
	const { calls, fetch } = fakeFetch(() => text(401, 'unauthorized'));
	await getSonioxKey(undefined, { fetch }).catch(() => {});
	assert.equal('Authorization' in (calls[0].init.headers as Record<string, string>), false);
});
