// `lib/after/rename.ts` 的判据：本机改名优先、术语快照并入并去重、服务端断网排队、补发只试一次、
// 非法课名一个都不改。全部假 localStorage + memoryStore + 假 api，不连服务器。
import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { ApiError } from '../../src/lib/api/http.ts';
import { renameCourse, retryPendingRenames } from '../../src/lib/after/rename.ts';
import { parseTerms, termsText } from '../../src/lib/env.ts';
import { memoryStore } from '../../src/lib/store/memory.ts';

const LOCAL = 'web-20261119-090000-ab12cd';
const SRV = 's_3f9a1c2e7b40';

class FakeStorage {
	readonly map = new Map<string, string>();
	getItem(k: string): string | null {
		return this.map.get(k) ?? null;
	}
	setItem(k: string, v: string): void {
		this.map.set(k, String(v));
	}
	removeItem(k: string): void {
		this.map.delete(k);
	}
	key(i: number): string | null {
		return [...this.map.keys()][i] ?? null;
	}
	get length(): number {
		return this.map.size;
	}
	*[Symbol.iterator](): IterableIterator<string> {
		yield* this.map.keys();
	}
}

/** 假 api：`fail` 次网络错之后成功；记下每次调用 */
function fakeApi(fail = 0) {
	const calls: { id: string; course: string }[] = [];
	let left = fail;
	return {
		calls,
		api: {
			async renameSession(id: string, course: string) {
				calls.push({ id, course });
				if (left > 0) {
					left--;
					throw new ApiError('network', 'fetch', '连不上服务器');
				}
				return { session_id: id, course };
			}
		}
	};
}

/** 开课时的现场：空课名开课 + 草稿术语 + 快照 + 服务端会话号 */
async function setup(opts: { srv?: boolean; snapshot?: string } = {}) {
	const store = memoryStore({ now: () => 1_000 });
	await store.openSession(LOCAL, '');
	if (opts.snapshot) localStorage.setItem(`cl.sessterms.${LOCAL}`, opts.snapshot);
	if (opts.srv !== false) localStorage.setItem(`cl.srv.${LOCAL}`, SRV);
	return store;
}

beforeEach(() => {
	(globalThis as unknown as { localStorage: FakeStorage }).localStorage = new FakeStorage();
});

test('空课名开课 → 改名后 listSessions 显示新课名，服务端也改了', async () => {
	const store = await setup();
	const { api, calls } = fakeApi();

	const res = await renameCourse({ store, api, local_session_id: LOCAL, course: '  微观经济  ' });

	assert.deepEqual(res, { ok: true, addedTerms: 0, serverQueued: false });
	assert.equal((await store.listSessions())[0].course, '微观经济', 'trim 后再存');
	assert.deepEqual(calls, [{ id: SRV, course: '微观经济' }]);
	assert.equal(localStorage.getItem(`cl.rename.${LOCAL}`), null, '成功就不排队');
});

test('术语快照并进新课名的术语表：去重保序，草稿键被清掉', async () => {
	const store = await setup({ snapshot: 'deadweight loss\n微观经济\nPrice Ceiling' });
	localStorage.setItem('cl.terms.__next', 'deadweight loss\n牛市');
	localStorage.setItem('cl.terms.微观经济', '微观经济\n# 注释\nelasticity');

	const res = await renameCourse({ store, api: fakeApi().api, local_session_id: LOCAL, course: '微观经济' });

	// 快照里的 deadweight loss / 微观经济 / Price Ceiling 里，只有 deadweight loss、Price Ceiling 是新的
	assert.equal(res.addedTerms, 2);
	assert.deepEqual(parseTerms(termsText('微观经济')), [
		'微观经济',
		'elasticity',
		'deadweight loss',
		'Price Ceiling'
	]);
	assert.equal(localStorage.getItem('cl.terms.__next'), null, '草稿术语并完就清掉');
	assert.equal(localStorage.getItem(`cl.sessterms.${LOCAL}`), null, '快照是一次性的');
});

test('断网：本机照改、服务端排队；retry 成功后键被删、不再重复发', async () => {
	const store = await setup();
	const { api, calls } = fakeApi(1);

	const res = await renameCourse({ store, api, local_session_id: LOCAL, course: 'ECON10740' });
	assert.equal(res.serverQueued, true);
	assert.equal(res.ok, true);
	assert.equal((await store.listSessions())[0].course, 'ECON10740', '本机不等网络');
	assert.equal(localStorage.getItem(`cl.rename.${LOCAL}`), 'ECON10740');

	assert.equal(await retryPendingRenames(api), 1);
	assert.equal(localStorage.getItem(`cl.rename.${LOCAL}`), null, '成功就删键');
	assert.deepEqual(
		calls.map((c) => c.course),
		['ECON10740', 'ECON10740'],
		'第二次是补发，服务端收到两条（幂等靠同一节课）'
	);
	assert.equal(await retryPendingRenames(api), 0, '没有排队了就不该再发');
	assert.equal(calls.length, 2, '不重试风暴：每个键一次');
});

test('排队键一直失败：retry 每轮只试一次，键留着等下次', async () => {
	const store = await setup();
	const { api, calls } = fakeApi(1);
	await renameCourse({ store, api, local_session_id: LOCAL, course: 'ECON10740' });

	assert.equal(await retryPendingRenames(fakeApi(1).api), 0, '又断网：这轮不算成功');
	assert.equal(localStorage.getItem(`cl.rename.${LOCAL}`), 'ECON10740');
	assert.equal(calls.length, 1, '只有 renameCourse 那一次真连过');
});

test('没有服务端会话号（开课就没同步上）：只排队，不发请求', async () => {
	const store = await setup({ srv: false });
	const { api, calls } = fakeApi();

	const res = await renameCourse({ store, api, local_session_id: LOCAL, course: 'CS101' });

	assert.deepEqual(res, { ok: true, addedTerms: 0, serverQueued: true });
	assert.equal(calls.length, 0);
	assert.equal(localStorage.getItem(`cl.rename.${LOCAL}`), 'CS101');
});

test('非法课名抛错，本机 / 服务端 / 术语一个都不动', async () => {
	const store = await setup({ snapshot: 'deadweight loss' });
	localStorage.setItem('cl.terms.__next', '牛市');
	const { api, calls } = fakeApi();

	for (const bad of ['', '   ', 'x'.repeat(81), '\t\n']) {
		await assert.rejects(
			() => renameCourse({ store, api, local_session_id: LOCAL, course: bad }),
			/课名 1–80 个字/
		);
	}
	assert.equal((await store.listSessions())[0].course, '', '本机课名没动');
	assert.equal(calls.length, 0, '没发服务端请求');
	assert.equal(localStorage.getItem(`cl.sessterms.${LOCAL}`), 'deadweight loss', '快照还在');
	assert.equal(localStorage.getItem('cl.terms.__next'), '牛市', '草稿术语还在');
	assert.equal(localStorage.getItem(`cl.rename.${LOCAL}`), null, '没排队');

	// 控制字符是「去掉」而不是「让课名变非法」：去完还剩 1–80 字就收
	const ok = await renameCourse({ store, api, local_session_id: LOCAL, course: '微观\u0000经济' });
	assert.equal(ok.ok, true);
	assert.equal((await store.listSessions())[0].course, '微观经济');
});

test('本机没有这节课：抛错，服务端与快照都不动', async () => {
	const store = memoryStore({ now: () => 1_000 });
	localStorage.setItem(`cl.sessterms.${LOCAL}`, 'deadweight loss');
	const { api, calls } = fakeApi();

	await assert.rejects(
		() => renameCourse({ store, api, local_session_id: LOCAL, course: 'ECON10740' }),
		/本机没有这节课/
	);
	assert.equal(calls.length, 0);
	assert.equal(localStorage.getItem(`cl.sessterms.${LOCAL}`), 'deadweight loss');
});

test('没有术语快照：addedTerms = 0，不凭空写术语表', async () => {
	const store = await setup();
	const res = await renameCourse({ store, api: fakeApi().api, local_session_id: LOCAL, course: 'ECON10740' });
	assert.equal(res.addedTerms, 0);
	assert.equal(termsText('ECON10740'), '');
});
