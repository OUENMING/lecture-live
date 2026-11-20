// 接线判据：下课命名后术语要自动上云（`lib/after/rename.ts` → `lib/terms/cloud.pushTerms`），
// 命名本身不等网络、推送失败只排队。假 localStorage + memoryStore + 假 api，不连服务器。
import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { ApiError } from '../../src/lib/api/http.ts';
import { renameCourse } from '../../src/lib/after/rename.ts';
import { termsText } from '../../src/lib/env.ts';
import { memoryStore } from '../../src/lib/store/memory.ts';
import { PENDING_PREFIX, retryPendingTerms } from '../../src/lib/terms/cloud.ts';

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
}

function fakeApi(fail = 0) {
	const puts: { course: string; text: string; share: boolean }[] = [];
	let left = fail;
	return {
		puts,
		api: {
			async renameSession(id: string, course: string) {
				return { session_id: id, course };
			},
			async putTerms(course: string, text: string, share: boolean) {
				puts.push({ course, text, share });
				if (left > 0) {
					left--;
					throw new ApiError('network', 'fetch', '连不上服务器');
				}
				return { updated_at: 5000 };
			}
		}
	};
}

async function setup() {
	const store = memoryStore({ now: () => 1_000 });
	await store.openSession(LOCAL, '');
	localStorage.setItem(`cl.srv.${LOCAL}`, SRV);
	localStorage.setItem(`cl.sessterms.${LOCAL}`, 'deadweight loss');
	return store;
}

beforeEach(() => {
	(globalThis as unknown as { localStorage: FakeStorage }).localStorage = new FakeStorage();
});

test('命名后把这门课的术语整体推上云（不是只推快照里那几条）', async () => {
	const store = await setup();
	localStorage.setItem('cl.terms.微观经济', 'elasticity');
	const { api, puts } = fakeApi();

	const res = await renameCourse({ store, api, local_session_id: LOCAL, course: '微观经济' });

	assert.deepEqual(res, { ok: true, addedTerms: 1, serverQueued: false });
	assert.deepEqual(puts, [
		{ course: '微观经济', text: 'elasticity\ndeadweight loss', share: true }
	]);
	assert.equal(localStorage.getItem(PENDING_PREFIX + '微观经济'), null);
});

test('推送失败：改名照样成功，排队等补推', async () => {
	const store = await setup();
	const { api } = fakeApi(1);

	const res = await renameCourse({ store, api, local_session_id: LOCAL, course: '微观经济' });

	assert.equal(res.ok, true, '网络问题不挡改名');
	assert.equal(localStorage.getItem(PENDING_PREFIX + '微观经济'), 'deadweight loss');
	assert.equal(await retryPendingTerms(api), 1);
	assert.equal(localStorage.getItem(PENDING_PREFIX + '微观经济'), null);
});

test('没有术语就一个请求都不发', async () => {
	const store = memoryStore({ now: () => 1_000 });
	await store.openSession(LOCAL, '');
	const { api, puts } = fakeApi();

	await renameCourse({ store, api, local_session_id: LOCAL, course: 'CS101' });

	assert.equal(puts.length, 0);
	assert.equal(termsText('CS101'), '');
});
