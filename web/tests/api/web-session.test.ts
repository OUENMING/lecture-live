// `lib/api/web-session.ts` 的判据（契约 = docs/API-web-v0-20261117.md §3）：地址 / 方法 / 头 / body 形状，
// 以及 404 / 409 / 410 / 413 各自翻成哪个 kind + 中文。全部假 fetch，不连 Worker（W12 还没部署）。
import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { ApiError, apiBase } from '../../src/lib/api/http.ts';
import { passcode } from '../../src/lib/api/identity.ts';
import {
	deleteAll,
	endSession,
	getSession,
	listSessions,
	putLines,
	startSession
} from '../../src/lib/api/web-session.ts';

const CODE = 'cl-owen-0123456789abcdef';
const SID = 's_3f9a1c2e7b40';

class FakeStorage {
	readonly map = new Map<string, string>();
	getItem(k: string): string | null {
		return this.map.get(k) ?? null;
	}
	setItem(k: string, v: string): void {
		this.map.set(k, String(v));
	}
}

/** 记下每次调用（地址 / 方法 / 头 / body），按剧本回包 */
function fakeFetch(script: (n: number) => Response) {
	const calls: { url: string; method: string; headers: Record<string, string>; body: unknown }[] = [];
	const fetch = async (url: string, init: RequestInit): Promise<Response> => {
		calls.push({
			url,
			method: String(init.method ?? ''),
			headers: (init.headers ?? {}) as Record<string, string>,
			body: init.body === undefined ? undefined : JSON.parse(String(init.body))
		});
		return script(calls.length);
	};
	return { calls, fetch };
}

const json = (status: number, body: unknown): Response =>
	new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

beforeEach(() => {
	(globalThis as unknown as { localStorage: FakeStorage }).localStorage = new FakeStorage();
	passcode.set(CODE);
});

test('开课：POST /v1/web/session，带幂等键，时间转 ISO，头是 web 那套', async () => {
	const { calls, fetch } = fakeFetch(() =>
		json(201, {
			session_id: SID,
			server_time: '2026-11-17T15:05:02Z',
			limits: { max_session_s: 10800, lines_batch: 200, body_bytes: 262144 }
		})
	);
	const res = await startSession('ECON10740', Date.parse('2026-11-17T15:05:00Z'), 'web-loc-1', { fetch });

	assert.equal(calls.length, 1);
	assert.equal(calls[0].url, `${apiBase()}/v1/web/session`);
	assert.equal(calls[0].method, 'POST');
	assert.equal(calls[0].headers['Idempotency-Key'], 'web-loc-1');
	assert.equal(calls[0].headers.Authorization, `Bearer ${CODE}`);
	assert.equal(calls[0].headers['X-ClassLive-Client'], 'web');
	assert.equal(calls[0].headers['X-ClassLive-Call'], 'web_session');
	assert.deepEqual(calls[0].body, { course: 'ECON10740', started_at: '2026-11-17T15:05:00.000Z' });
	assert.equal(res.session_id, SID);
	assert.equal(res.limits?.lines_batch, 200);
});

test('开课回包缺 session_id 就是契约破了：抛错，不装成功', async () => {
	const { fetch } = fakeFetch(() => json(201, { server_time: 'x' }));
	await assert.rejects(() => startSession('ECON10740', 0, 'k', { fetch }), /session_id/);
});

test('pushLines：PUT …/lines，body 只有 lines，读回 confirmed_seq', async () => {
	const { calls, fetch } = fakeFetch(() =>
		json(200, { confirmed_seq: 42, high_water: 42, received: 2, ignored: 0 })
	);
	const res = await putLines(
		SID,
		[
			{ seq: 41, t: 122.4, en: 'so the marginal cost is', zh: '所以边际成本是', state: 'final', src: 'asr' },
			{ seq: 42, t: 128.0, en: 'the derivative', zh: '导数', state: 'draft' }
		],
		{ fetch }
	);
	assert.equal(calls[0].url, `${apiBase()}/v1/web/session/${SID}/lines`);
	assert.equal(calls[0].method, 'PUT');
	assert.equal((calls[0].body as { lines: unknown[] }).lines.length, 2);
	assert.deepEqual((calls[0].body as { lines: { seq: number }[] }).lines.map((l) => l.seq), [41, 42]);
	assert.equal(res.confirmed_seq, 42);
	assert.equal(res.high_water, 42);
});

test('下课：POST …/end，带 ended_at ISO 与 lines_total，level 默认 std', async () => {
	const { calls, fetch } = fakeFetch(() =>
		json(202, { job_id: 'j_8a01d3f2c9e5', status: 'queued', dedupe: false })
	);
	const res = await endSession(SID, { ended_at: Date.parse('2026-11-17T16:58:10Z'), lines_total: 1180 }, { fetch });
	assert.equal(calls[0].url, `${apiBase()}/v1/web/session/${SID}/end`);
	assert.equal(calls[0].method, 'POST');
	assert.deepEqual(calls[0].body, {
		ended_at: '2026-11-17T16:58:10.000Z',
		lines_total: 1180,
		level: 'std'
	});
	assert.equal(res.job_id, 'j_8a01d3f2c9e5');
	assert.equal(res.dedupe, false);
});

test('GET：不带 body、方法对；列表带 query 且 limit 封 50', async () => {
	const { calls, fetch } = fakeFetch((n) =>
		n === 1
			? json(200, { session_id: SID, course: 'ECON10740', state: 'ended', lines_confirmed: 1180, note: null })
			: json(200, { sessions: [], next_before: null })
	);
	await getSession(SID, { fetch });
	assert.equal(calls[0].method, 'GET');
	assert.equal(calls[0].body, undefined);
	assert.equal(calls[0].url, `${apiBase()}/v1/web/session/${SID}`);

	const list = await listSessions({ fetch, limit: 999, before: SID });
	assert.equal(calls[1].method, 'GET');
	assert.equal(calls[1].url, `${apiBase()}/v1/web/sessions?limit=50&before=${SID}`);
	assert.deepEqual(list, { sessions: [], next_before: null });
});

test('deleteAll：DELETE /v1/web/data，2xx 空体当成功', async () => {
	const { calls, fetch } = fakeFetch(() => new Response(null, { status: 204 }));
	await deleteAll({ fetch });
	assert.equal(calls[0].url, `${apiBase()}/v1/web/data`);
	assert.equal(calls[0].method, 'DELETE');
});

test('不是服务端 session_id 的形状：本地校验挡住，一个请求都不发', async () => {
	const { calls, fetch } = fakeFetch(() => json(200, {}));
	await assert.rejects(() => putLines('web-20261117-120000-ab12cd', [], { fetch }), /session_id/);
	assert.equal(calls.length, 0, '不能把本机 id 拼进 URL');
});

test('404 / 410 / 413 各自翻成可读中文（只加条目，不改老口径）', async () => {
	const cases: [number, string, string, string][] = [
		[404, 'not_found', 'not_found', '服务器上没有这节课'],
		[410, 'session_deleted', 'gone', '已删除'],
		[413, 'too_large', 'too_large', '一次发得太多']
	];
	for (const [status, reason, kind, say] of cases) {
		const { fetch } = fakeFetch(() => json(status, { error: reason }));
		const err = await putLines(SID, [{ seq: 1, t: 0, en: 'a', zh: '甲' }], { fetch }).catch((e) => e);
		assert.ok(err instanceof ApiError, reason);
		assert.equal(err.kind, kind, reason);
		assert.equal(err.reason, reason);
		assert.equal(err.status, status);
		assert.match(err.message, new RegExp(say), `${reason} 的中文`);
	}
});

test('409 的两种原因都算 server kind，但 reason 留着给同步层判', async () => {
	for (const reason of ['session_ended', 'seq_too_far']) {
		const { fetch } = fakeFetch(() => json(409, { error: reason }));
		const err = await putLines(SID, [{ seq: 1, t: 0, en: 'a', zh: '甲' }], { fetch }).catch((e) => e);
		assert.equal(err.kind, 'server', reason);
		assert.equal(err.reason, reason);
		assert.match(err.message, /这节课已经下课|编号跳得太远/, reason);
	}
});

test('401 / 429 与老口径一致（没动老条目）', async () => {
	const a = await getSession(SID, { fetch: fakeFetch(() => json(401, { error: 'unauthorized' })).fetch }).catch(
		(e) => e
	);
	assert.equal(a.kind, 'passcode');

	const b = await putLines(SID, [], { fetch: fakeFetch(() => json(429, { error: 'rate_limited' })).fetch }).catch(
		(e) => e
	);
	assert.equal(b.kind, 'quota');
	assert.match(b.message, /等一分钟/);
});
