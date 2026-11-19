// 草稿送翻判据：TS 版与桌面 draft_policy.should_translate("novel") 逐条对照（CPython 现跑，不存 fixture）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DraftGate, novelWords, shouldTranslateNovel } from '../../src/lib/translate/draft-gate.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const corePy = path.resolve(here, '../../static/core-py');

const cases: [number, number, string, string][] = [
	[1.0, 0, '', 'So today we'],
	[3.0, 0, '', 'So today we'],
	[3.0, 0, '', 'So today we will'],
	[5.0, 2.0, 'So today we will', 'So today we will talk about'],
	[5.6, 2.0, 'So today we will', 'So today we will talk about'],
	[7.5, 2.0, 'So today we will talk', 'So today we will talk, about'],
	[9.0, 6.0, 'Hello.', 'Hello. Hello! hello? — yes'],
	[20, 0, 'a a b', 'A, a. b b c d e'],
	[2.5, 0, '', 'one two three four']
];

test('novel 判据与 CPython 逐条相同', () => {
	const py = `
import json, sys
sys.path.insert(0, ${JSON.stringify(corePy)})
import draft_policy as d
cases = json.loads(sys.stdin.read())
print(json.dumps([[d.should_translate("novel", now=n, last_tr=l, n_words=len(t.split()), last_len=0, text=t, sent_text=s), d.novel_words(s, t)] for n, l, s, t in cases]))
`;
	const want = JSON.parse(execFileSync('python3', ['-I', '-c', py], { input: JSON.stringify(cases) }).toString()) as [boolean, number][];
	const got = cases.map(([n, l, s, t]) => [shouldTranslateNovel(n, l, s, t), novelWords(s, t)]);
	assert.deepEqual(got, want);
});

test('换段清零：新句开头不用等上一句的节流', () => {
	const g = new DraftGate();
	assert.equal(g.admit(1, 'one two three four', 3), true);
	assert.equal(g.admit(1, 'one two three four five', 3.5), false); // 距上次 0.5 s
	assert.equal(g.admit(2, 'alpha beta gamma delta', 3.6), true); // 新段：last_tr 清零
});

test('同一句一直在长：大约每 2.5 s 才送一次', () => {
	const g = new DraftGate();
	const words = 'we are going to look at how the marginal rate of substitution changes along an indifference curve today'.split(' ');
	let sent = 0;
	for (let i = 1; i <= words.length; i++) if (g.admit(1, words.slice(0, i).join(' '), 2.5 + i * 0.35)) sent += 1;
	assert.ok(sent >= 2 && sent <= 4, `sent=${sent}`);
});
