// `lib/api/web-terms.ts` 的判据（契约 = CONTRACT-terms.md）：地址 / 方法 / 头 / body 形状，
// 以及回包缺字段时怎么兜（updated_at 回本机现在、courses 缺失回空表、shared 过滤坏行）。
// 全部假 fetch，不连 Worker、不花 key。
import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { apiBase } from '../../src/lib/api/http.ts';
import { passcode } from '../../src/lib/api/identity.ts';
import { getTerms, putTerms, sharedTerms } from '../../src/lib/api/web-terms.ts';

const CODE = 'cl-owen-0123456789abcdef';

class FakeStorage {
	readonly map = new Map<string, string>();
	getItem(k: string): string | null {
		return this.map.get(k) ?? null;
	}
	setItem(k: string, v: string): void {
		this.map.set(k, String(v));
	}
}

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

test('getTerms：GET /v1/web/terms，头是 web 那套', async () => {
	const { calls, fetch } = fakeFetch(() =>
		json(200, { courses: { ECON10770: { text: 'a\nb', updated_at: 1700000000000 } } })
	);

	const res = await getTerms({ fetch });

	assert.equal(calls[0].url, `${apiBase()}/v1/web/terms`);
	assert.equal(calls[0].method, 'GET');
	assert.equal(calls[0].body, undefined);
	assert.equal(calls[0].headers.Authorization, `Bearer ${CODE}`);
	assert.equal(calls[0].headers['X-ClassLive-Client'], 'web');
	assert.equal(calls[0].headers['X-ClassLive-Call'], 'web_terms');
	assert.deepEqual(res.courses.ECON10770, { text: 'a\nb', updated_at: 1700000000000 });
});

test('getTerms：没有课程就空表', async () => {
	const { fetch } = fakeFetch(() => json(200, { courses: {} }));
	assert.deepEqual(await getTerms({ fetch }), { courses: {} });
});

test('putTerms：PUT /v1/web/terms/:course，课名 encodeURIComponent，body 带 share', async () => {
	const { calls, fetch } = fakeFetch(() => json(200, { course: '微观 经济', updated_at: 42 }));

	const res = await putTerms('微观 经济', '需求弹性', true, { fetch });

	assert.equal(calls[0].url, `${apiBase()}/v1/web/terms/${encodeURIComponent('微观 经济')}`);
	assert.equal(calls[0].method, 'PUT');
	assert.deepEqual(calls[0].body, { text: '需求弹性', share: true });
	assert.deepEqual(res, { course: '微观 经济', updated_at: 42 });
});

test('putTerms：回包没 updated_at 就用本机现在（0 会让下次 pull 永远认服务端）', async () => {
	const { fetch } = fakeFetch(() => json(200, { course: 'ECON10770' }));
	const before = Date.now();

	const res = await putTerms('ECON10770', 'x', false, { fetch });

	assert.ok(res.updated_at >= before);
});

test('sharedTerms：GET /v1/web/terms/shared?course=，透出 course_key 与 users', async () => {
	const { calls, fetch } = fakeFetch(() =>
		json(200, { course_key: 'ECON10770', terms: [{ term: 'deadweight loss', users: 3 }] })
	);

	const res = await sharedTerms('econ 10770', { fetch });

	assert.equal(calls[0].url, `${apiBase()}/v1/web/terms/shared?course=econ+10770`);
	assert.equal(calls[0].method, 'GET');
	assert.deepEqual(res, {
		course_key: 'ECON10770',
		terms: [{ term: 'deadweight loss', users: 3, tier: 'course' }]
	});
});

test('sharedTerms：不是课号 → course_key null、空数组；坏行被滤掉', async () => {
	const { fetch } = fakeFetch(() =>
		json(200, { course_key: null, terms: [{ term: 'ok', users: 2 }, { term: 3 }, {}] })
	);

	const res = await sharedTerms('微观经济', { fetch });

	assert.equal(res.course_key, null);
	assert.deepEqual(res.terms, [{ term: 'ok', users: 2, tier: 'course' }]);
});

test('sharedTerms：W24 三层 tier 透传；缺失 / 未知 tier 当 course（旧回包兼容）', async () => {
	const { fetch } = fakeFetch(() =>
		json(200, {
			course_key: 'ECON10770',
			terms: [
				{ term: 'externality', users: 4, tier: 'course' },
				{ term: 'elasticity', users: 2, tier: 'subject' },
				{ term: 'discount rate', users: 3, tier: 'field' },
				{ term: 'legacy word', users: 2 },
				{ term: 'weird tier', users: 2, tier: 'galaxy' }
			]
		})
	);

	const res = await sharedTerms('ECON10770', { fetch });

	assert.deepEqual(
		res.terms.map((t) => t.tier),
		['course', 'subject', 'field', 'course', 'course'],
		'旧回包（没 tier）/ 未知 tier 都退回最稳的 course'
	);
	assert.deepEqual(
		res.terms.map((t) => t.term),
		['externality', 'elasticity', 'discount rate', 'legacy word', 'weird tier'],
		'服务端给什么顺序就什么顺序，不重排'
	);
});
