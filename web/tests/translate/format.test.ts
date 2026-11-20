// 两行格式的解析判据：桌面 `translator._StreamParser` 的容错口径（增量切分后结果必须一致）。
// 判的是「屏幕最终显示什么」，不是中间态；每条用例都同时跑「一次喂完」和「逐字符喂」。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { TwoLineParser, looksLikeEcho } from '../../src/lib/translate/format.ts';

function run(deltas: readonly string[], enRaw = 'asr raw'): { en: string; zh: string } {
	const p = new TwoLineParser();
	for (const d of deltas) p.feed(d);
	return p.result(enRaw);
}

test('标准两行：ZH / EN 各归各位', () => {
	const r = run(['ZH: 期中考试覆盖第一到三章。\n', 'EN: The midterm covers chapters one to three.']);
	assert.equal(r.zh, '期中考试覆盖第一到三章。');
	assert.equal(r.en, 'The midterm covers chapters one to three.');
});

test('逐字符喂与一次喂完结果相同（标记被切开也一样）', () => {
	const text = 'ZH: 期中考试覆盖第一到三章。\nEN: The midterm covers chapters one to three.';
	const once = run([text]);
	const chars = run([...text]);
	assert.deepEqual(chars, once);
});

test('全角冒号也认', () => {
	const r = run(['ZH：期中考试。\nEN：The midterm.']);
	assert.equal(r.zh, '期中考试。');
	assert.equal(r.en, 'The midterm.');
});

test('模型不按格式（没 ZH: 标记）→ 攒够 24 字符后全当中文', () => {
	const r = run(['这是一句没有标记的中文译文，长度足够触发兜底判据。']);
	assert.ok(r.zh.startsWith('这是一句没有标记的中文译文'));
});

test('英文段混入中文 → 回退 ASR 原文', () => {
	const r = run(['ZH: 中文。\nEN: 英文 mixed 中文'], 'the raw asr');
	assert.equal(r.en, 'the raw asr');
});

test('没有 ZH: 标记 → 全文当中文、英文回退原文（与桌面 wait_zh 兜底同）', () => {
	const r = run(['EN: only english, no marker at all.'], 'the raw asr');
	assert.equal(r.en, 'the raw asr');
	assert.equal(r.zh, 'EN: only english, no marker at all.');
});

test('回显判据：术语表 / 空 / 长输入短输出', () => {
	assert.equal(looksLikeEcho('', 'x'), true);
	assert.equal(looksLikeEcho('课程术语：…', 'x'), true);
	assert.equal(looksLikeEcho('短', 'one two three four five six seven eight nine ten'), true);
	assert.equal(looksLikeEcho('这是一句正常的译文', 'hello there'), false);
});

test('增量回调：中文先到、英文后到（首字打点靠它）', () => {
	const zh: string[] = [];
	const en: string[] = [];
	const p = new TwoLineParser((d) => zh.push(d), (d) => en.push(d));
	p.feed('ZH: 期中');
	p.feed('考试。\nEN: The mid');
	p.feed('term.');
	p.result('x');
	// 逐条增量按桌面口径**不裁空白**（`ZH:` 后面的那个空格属于第一个 delta）；整段 result 才裁
	assert.equal(zh.join('').trim(), '期中考试。');
	assert.equal(en.join('').trim(), 'The midterm.');
});
