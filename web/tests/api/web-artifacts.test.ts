// `lib/api/web-artifacts.ts` + `lib/after/note-frame.ts` 的判据：没出 = null 不抛、形状收窄、
// 笔记原文要原样回（不能被当 JSON 吞成 {}）、嵌入版的 KaTeX / 主题 / 桥接脚本都挂上。全部假 fetch，不连网。
import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { getNote, getSummary } from '../../src/lib/api/web-artifacts.ts';
import { ApiError } from '../../src/lib/api/http.ts';
import { passcode } from '../../src/lib/api/identity.ts';
import { embedNote, portableNote, isNoteMessage } from '../../src/lib/after/note-frame.ts';

const SID = 's_3f9a1c2e7b40';
class FakeStorage {
	readonly map = new Map<string, string>();
	getItem(k: string) {
		return this.map.get(k) ?? null;
	}
	setItem(k: string, v: string) {
		this.map.set(k, String(v));
	}
}
beforeEach(() => {
	(globalThis as unknown as { localStorage: FakeStorage }).localStorage = new FakeStorage();
	passcode.set('cl-owen-0123456789abcdef');
});
const res = (status: number, body: string, type = 'application/json') =>
	new Response(body, { status, headers: { 'content-type': type } });

test('总结：404 not_ready → null；GET 到 /summary', async () => {
	const urls: string[] = [];
	const out = await getSummary(SID, {
		fetch: async (u, init) => {
			urls.push(`${init.method} ${u}`);
			return res(404, '{"error":"not_ready"}');
		}
	});
	assert.equal(out, null);
	assert.match(urls[0], new RegExp(`^GET .*/v1/web/session/${SID}/summary$`));
});

test('总结：字段收窄 + 按 idx 排 + 空章丢掉 + closed 缺省当已封', async () => {
	const body = {
		session_id: SID,
		updated_at: '2026-11-20T10:00:00Z',
		final: false,
		chapters: [
			{ idx: 2, title: ' 第二章 ', start_seq: 49, end_seq: 60, closed: false, summary: '首段', points: null },
			{ idx: 1, title: '需求弹性', start_seq: 1, end_seq: 48, summary: '正文', points: ['a', '', 3] },
			{ idx: 3, title: '', summary: '' },
			'坏的'
		]
	};
	const out = await getSummary(SID, { fetch: async () => res(200, JSON.stringify(body)) });
	assert.ok(out);
	assert.deepEqual(out.chapters.map((c) => c.idx), [1, 2]);
	assert.equal(out.chapters[0].closed, true);
	assert.deepEqual(out.chapters[0].points, ['a']);
	assert.equal(out.chapters[1].title, '第二章');
	assert.equal(out.chapters[1].closed, false);
	assert.deepEqual(out.chapters[1].points, []);
});

test('总结：别的错照抛（界面要能说「连不上」）', async () => {
	await assert.rejects(getSummary(SID, { fetch: async () => res(503, '{"error":"upstream"}') }), ApiError);
});

test('笔记：HTML 原文原样回，fmt 进查询串；没出 → null', async () => {
	const html = '<!DOCTYPE html><html><body><h1>x</h1></body></html>';
	let url = '';
	const got = await getNote(SID, 'html', {
		fetch: async (u) => {
			url = u;
			return res(200, html, 'text/html');
		}
	});
	assert.equal(got, html);
	assert.match(url, /\/note\?fmt=html$/);
	assert.equal(await getNote(SID, 'md', { fetch: async () => res(404, '{"error":"not_ready"}') }), null);
});

test('会话号不像服务端的就不发请求', async () => {
	await assert.rejects(getNote('local-1'), /不是服务端的 session_id/);
});

const THEME = { dark: true, paper: '#111', paper2: '#222', ink: '#eee', ink2: '#ccc', ink3: '#aaa', rule: '#333', font: 'sans' };
const SAMPLE =
	'<!DOCTYPE html>\n<html lang="zh-CN" data-theme="light"><head><link rel="stylesheet" href=".classlive-assets/katex/0.16.9/katex.min.css"></head>' +
	'<body><div class="wrap"><h1>t</h1></div><script>var s="</body>"</script></body></html>';

test('嵌入：KaTeX 改钉版本 CDN、data-theme 换成本页的、样式进 head、桥接脚本在最后一个 </body> 前', () => {
	const out = embedNote(SAMPLE, THEME);
	assert.ok(out.includes('https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css'));
	assert.ok(!out.includes('.classlive-assets'));
	assert.match(out, /<html lang="zh-CN" data-theme="dark">/);
	assert.ok(out.indexOf('id="cl-embed"') < out.indexOf('</head>'));
	assert.ok(out.lastIndexOf("cl:'note-h'") > out.indexOf('var s="</body>"'));
	assert.ok(out.trimEnd().endsWith('</body></html>'));
	assert.equal(portableNote(SAMPLE).includes('data-theme="light"'), true); // 下载版不动主题
});

test('父页只认两种消息', () => {
	assert.ok(isNoteMessage({ cl: 'note-h', h: 10 }));
	assert.ok(isNoteMessage({ cl: 'note-y', y: 10 }));
	assert.ok(!isNoteMessage({ cl: 'note-h', h: '10' }));
	assert.ok(!isNoteMessage('x'));
});
