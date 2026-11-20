// `lib/terms/cloud.ts` 的判据：拉下来的新旧规则、本机赢就要补推、失败排队、空课名不推、
// 共享开关透传、`termsForClass` 超时给空 shared 且不抛、共享词按大小写/空白去重。
// 全部假 localStorage + 假 api，不连服务器、不花 key。
import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { ApiError } from '../../src/lib/api/http.ts';
import { termsText } from '../../src/lib/env.ts';
import {
	META_KEY,
	PENDING_PREFIX,
	CONTEXT_TERMS_MAX,
	mergeForContext,
	pullTerms,
	pushTerms,
	retryPendingTerms,
	saveTermsSynced,
	setShareEnabled,
	shareEnabled,
	termsForClass,
	type TermsApi
} from '../../src/lib/terms/cloud.ts';

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

interface PutCall {
	course: string;
	text: string;
	share: boolean;
}

/** 假 api：`putFail` 次网络错之后成功；各接口都记下调用 */
function fakeApi(opts: { courses?: Record<string, { text: string; updated_at: number }>; putFail?: number; getFail?: boolean; shared?: { term: string; users: number; tier?: 'course' | 'subject' | 'field' }[]; sharedNever?: boolean } = {}) {
	const puts: PutCall[] = [];
	let left = opts.putFail ?? 0;
	let seq = 1000;
	const api: TermsApi = {
		async getTerms() {
			if (opts.getFail) throw new ApiError('network', 'fetch', '连不上服务器');
			return { courses: { ...(opts.courses ?? {}) } };
		},
		async putTerms(course, text, share) {
			puts.push({ course, text, share });
			if (left > 0) {
				left--;
				throw new ApiError('network', 'fetch', '连不上服务器');
			}
			return { updated_at: ++seq };
		},
		async sharedTerms() {
			if (opts.sharedNever) return new Promise(() => {}); // 永不回：测超时
			return { course_key: 'ECON10770', terms: opts.shared ?? [] };
		}
	};
	return { api, puts };
}

const meta = () => JSON.parse(localStorage.getItem(META_KEY) ?? '{}') as Record<string, number>;

beforeEach(() => {
	(globalThis as unknown as { localStorage: FakeStorage }).localStorage = new FakeStorage();
});

test('pullTerms：服务端更新的赢（落本机 + 记 updated_at）', async () => {
	localStorage.setItem('cl.terms.econ10770', 'old term');
	localStorage.setItem(META_KEY, JSON.stringify({ ECON10770: 100 }));
	const { api } = fakeApi({ courses: { ECON10770: { text: 'new term', updated_at: 200 } } });

	const res = await pullTerms(api);

	assert.deepEqual(res, { updated: 1 });
	assert.equal(termsText('ECON10770'), 'new term');
	assert.equal(meta().ECON10770, 200);
});

test('pullTerms：本机更新的不动本机（服务端那份不覆盖）', async () => {
	localStorage.setItem('cl.terms.econ10770', 'mine');
	localStorage.setItem(META_KEY, JSON.stringify({ ECON10770: 500 }));
	const { api, puts } = fakeApi({ courses: { ECON10770: { text: 'theirs', updated_at: 200 } } });

	const res = await pullTerms(api);

	assert.deepEqual(res, { updated: 0 });
	assert.equal(termsText('ECON10770'), 'mine');
	assert.equal(puts.length, 0, '只是旧，不是「没推上」，不该补推');
});

test('pullTerms：本机有没推上去的改动 → 本机赢，然后补推', async () => {
	localStorage.setItem('cl.terms.econ10770', 'local edit');
	localStorage.setItem(PENDING_PREFIX + 'ECON10770', 'local edit');
	localStorage.setItem(META_KEY, JSON.stringify({ ECON10770: 100 }));
	const { api, puts } = fakeApi({ courses: { ECON10770: { text: 'server copy', updated_at: 900 } } });

	const res = await pullTerms(api);

	assert.deepEqual(res, { updated: 0 }, '本机赢不算「从服务端更新了」');
	assert.equal(termsText('ECON10770'), 'local edit', '本机改动没被覆盖');
	assert.deepEqual(
		puts.map((p) => p.text),
		['local edit']
	);
	assert.equal(localStorage.getItem(PENDING_PREFIX + 'ECON10770'), null, '推成功就清排队');
});

test('pullTerms：拉不到就什么都不动（不抛）', async () => {
	localStorage.setItem('cl.terms.econ10770', 'mine');
	const { api } = fakeApi({ getFail: true });

	assert.deepEqual(await pullTerms(api), { updated: 0 });
	assert.equal(termsText('ECON10770'), 'mine');
});

test('pullTerms：内容一样不重复写，只对齐 updated_at', async () => {
	localStorage.setItem('cl.terms.econ10770', 'same');
	localStorage.setItem(META_KEY, JSON.stringify({ ECON10770: 100 }));
	const { api } = fakeApi({ courses: { ECON10770: { text: 'same', updated_at: 300 } } });

	assert.deepEqual(await pullTerms(api), { updated: 0 });
	assert.equal(meta().ECON10770, 300);
});

test('pushTerms 失败排队、retryPendingTerms 补发成功并清键', async () => {
	const { api, puts } = fakeApi({ putFail: 1 });

	await pushTerms(api, 'ECON10770', 'deadweight loss');

	assert.equal(localStorage.getItem(PENDING_PREFIX + 'ECON10770'), 'deadweight loss');
	assert.equal(termsText('ECON10770'), 'deadweight loss', '本机照样生效');

	assert.equal(await retryPendingTerms(api), 1);
	assert.equal(localStorage.getItem(PENDING_PREFIX + 'ECON10770'), null);
	assert.deepEqual(
		puts.map((p) => p.course),
		['ECON10770', 'ECON10770']
	);
	assert.equal(await retryPendingTerms(api), 0, '没排队就不该再发');
	assert.equal(puts.length, 2, '不重试风暴');
});

test('retryPendingTerms：又失败就留着，本轮算 0（每门课只试一次）', async () => {
	const { api } = fakeApi({ putFail: 1 });
	await pushTerms(api, 'ECON10770', 'x');
	const again = fakeApi({ putFail: 1 });

	assert.equal(await retryPendingTerms(again.api), 0);
	assert.equal(localStorage.getItem(PENDING_PREFIX + 'ECON10770'), 'x');
	assert.equal(again.puts.length, 1);
});

test('空课名不推：草稿只留本机', async () => {
	const { api, puts } = fakeApi();

	await pushTerms(api, '   ', 'draft term');
	await saveTermsSynced(api, '', 'draft term');

	assert.equal(puts.length, 0, '草稿不上云');
	assert.equal(termsText(''), 'draft term', '草稿照样存本机');
});

test('share 开关透传：默认 true，关掉后带 false', async () => {
	assert.equal(shareEnabled(), true, '默认开');

	const a = fakeApi();
	await pushTerms(a.api, 'ECON10770', 'term');
	assert.equal(a.puts[0].share, true);

	setShareEnabled(false);
	assert.equal(shareEnabled(), false);
	const b = fakeApi();
	await pushTerms(b.api, 'ECON10770', 'term');
	assert.equal(b.puts[0].share, false);

	// 补发也走同一个开关
	setShareEnabled(false);
	const c = fakeApi({ putFail: 1 });
	await pushTerms(c.api, 'ECON10770', 'term');
	await retryPendingTerms(c.api);
	assert.deepEqual(
		c.puts.map((p) => p.share),
		[false, false]
	);
});

test('saveTermsSynced：本机立刻生效，成功时记 updated_at', async () => {
	const { api } = fakeApi();

	await saveTermsSynced(api, 'ECON10770', 'a\nb');

	assert.equal(termsText('ECON10770'), 'a\nb');
	assert.ok(meta().ECON10770 > 0);
});

test('termsForClass：个人在前，共享补位，大小写/空白不同算同词', async () => {
	localStorage.setItem('cl.terms.econ10770', 'Price Ceiling');
	const { api } = fakeApi({
		shared: [
			{ term: ' price   ceiling ', users: 5 },
			{ term: 'Deadweight Loss', users: 3 },
			{ term: 'deadweight loss', users: 2 }
		]
	});

	const res = await termsForClass(api, 'ECON10770');

	assert.deepEqual(res.personal, ['Price Ceiling']);
	assert.deepEqual(res.shared, ['Deadweight Loss'], '个人已有的、重复的都不要');
});

test('termsForClass：W24 三层共享词按服务端顺序补位，个人已有的先挡住（近的层自然在前）', async () => {
	localStorage.setItem('cl.terms.econ10770', 'externality');
	const { api } = fakeApi({
		shared: [
			{ term: 'ExternalitY', users: 9, tier: 'course' },
			{ term: 'elasticity', users: 3, tier: 'subject' },
			{ term: 'discount rate', users: 4, tier: 'field' }
		]
	});

	const res = await termsForClass(api, 'ECON10770');

	assert.deepEqual(res.personal, ['externality']);
	assert.deepEqual(res.shared, ['elasticity', 'discount rate'], '服务端顺序不动，只去掉个人已有的');
	assert.equal(mergeForContext(res.personal, res.shared).length, 3);
});

test('termsForClass：共享池超时 → shared 空数组，不抛', async () => {
	localStorage.setItem('cl.terms.econ10770', 'mine');
	const { api } = fakeApi({ sharedNever: true });
	const t0 = Date.now();

	const res = await termsForClass(api, 'ECON10770', { timeoutMs: 60 });

	assert.deepEqual(res, { personal: ['mine'], shared: [] });
	assert.ok(Date.now() - t0 < 1000, '不该等到 http 的 10 s');
});

test('termsForClass：共享池报错 → shared 空数组，不抛', async () => {
	localStorage.setItem('cl.terms.econ10770', 'mine');
	const api: TermsApi = {
		...fakeApi().api,
		async sharedTerms() {
			throw new ApiError('server', 'http_500', '服务器出错了');
		}
	};

	assert.deepEqual(await termsForClass(api, 'ECON10770'), { personal: ['mine'], shared: [] });
});

test('termsForClass：空课名只要个人术语，连共享池都不拉', async () => {
	localStorage.setItem('cl.terms.__next', 'draft');
	let asked = 0;
	const api: TermsApi = {
		...fakeApi().api,
		async sharedTerms() {
			asked++;
			return { course_key: null, terms: [] };
		}
	};

	assert.deepEqual(await termsForClass(api, ''), { personal: ['draft'], shared: [] });
	assert.equal(asked, 0);
});

test('mergeForContext：个人在前，按上限截断', () => {
	const personal = Array.from({ length: 5 }, (_, i) => `p${i}`);
	const shared = Array.from({ length: 5 }, (_, i) => `s${i}`);
	assert.deepEqual(mergeForContext(personal, shared).slice(0, 6), ['p0', 'p1', 'p2', 'p3', 'p4', 's0']);

	const many = Array.from({ length: CONTEXT_TERMS_MAX + 20 }, (_, i) => `t${i}`);
	assert.equal(mergeForContext(many, []).length, CONTEXT_TERMS_MAX);
	assert.equal(mergeForContext([], many).length, CONTEXT_TERMS_MAX);
});

test('OCR B：本机有术语但从没对齐过（meta=0、无 pending）→ 不被远端盖掉，补推', async () => {
	localStorage.setItem('cl.terms.econ10770', 'legacy local');
	const { api, puts } = fakeApi({ courses: { ECON10770: { text: 'theirs', updated_at: 200 } } });
	const res = await pullTerms(api);
	assert.deepEqual(res, { updated: 0 });
	assert.equal(termsText('ECON10770'), 'legacy local');
	assert.equal(puts.length, 1);
	assert.equal(puts[0].text, 'legacy local');
});
