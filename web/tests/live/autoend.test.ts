// web/tests/live/autoend.test.ts —— lib/live/autoend.ts 的离线自测。
//
// 假墙钟手动 advance，tick 全显式喂（模块自己不看时间、不开定时器）。
// 钉的判据：前 120 s 免疫；安静 600 s 正好触发、599 s 不触发；有文字打断并回 ok；
// cancel 后重新从 0 计；后台 300 s 一条；开课零文字 900 s 一条；只 end 一次；
// quota-low 不覆盖 countdown。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createAutoEnd, type AutoEndReason, type AutoEndState } from '../../src/lib/live/autoend.ts';

const T0 = 1_700_000_000_000;

function setup(opts: Parameters<typeof createAutoEnd>[0] = {}) {
	let nowMs = T0;
	const ae = createAutoEnd({ now: () => nowMs, ...opts });
	const at = (s: number) => {
		nowMs = T0 + s * 1000;
	};
	return { ae, at };
}

function quietSig(text = 0, hidden = false, quotaLeftS: number | null = null) {
	return { text, hidden, quotaLeftS };
}

function reasonOf(s: AutoEndState): AutoEndReason | null {
	return s.kind === 'countdown' || s.kind === 'end' ? s.reason : null;
}

test('前 120 s 免疫：什么都触发不了', () => {
	const { ae, at } = setup();
	at(0);
	assert.deepEqual(ae.tick(quietSig(0)), { kind: 'ok' });
	at(119);
	assert.deepEqual(ae.tick(quietSig(0, true, 10)), { kind: 'ok' });
	at(120);
	// 免疫刚过、阈值还没到（安静只看「出过字之后」
	assert.deepEqual(ae.tick(quietSig(1)), { kind: 'ok' });
});

test('安静 599 s 不动，正好 600 s 进入倒计时', () => {
	const { ae, at } = setup();
	at(0);
	ae.tick(quietSig(5));
	at(599);
	assert.deepEqual(ae.tick(quietSig(5)), { kind: 'ok' });
	at(600);
	assert.deepEqual(ae.tick(quietSig(5)), { kind: 'countdown', reason: 'quiet', leftS: 60 });
});

test('倒计时期间不动 → 到点 end，且只给一次', () => {
	const { ae, at } = setup();
	at(0);
	ae.tick(quietSig(5));
	at(600);
	ae.tick(quietSig(5));
	at(630);
	assert.deepEqual(ae.tick(quietSig(5)), { kind: 'countdown', reason: 'quiet', leftS: 30 });
	at(660);
	assert.deepEqual(ae.tick(quietSig(5)), { kind: 'end', reason: 'quiet' });
	// 之后喂什么都还是同一个 end
	at(900);
	assert.deepEqual(ae.tick(quietSig(5, true)), { kind: 'end', reason: 'quiet' });
	assert.deepEqual(ae.state, { kind: 'end', reason: 'quiet' });
});

test('倒计时中出现新文字 → 立刻回 ok 并清倒计时', () => {
	const { ae, at } = setup();
	at(0);
	ae.tick(quietSig(5));
	at(600);
	assert.deepEqual(ae.tick(quietSig(5)), { kind: 'countdown', reason: 'quiet', leftS: 60 });
	at(620);
	assert.deepEqual(ae.tick(quietSig(6)), { kind: 'ok' });
	// 从这次文字重新计 600 s
	at(1219);
	assert.deepEqual(ae.tick(quietSig(6)), { kind: 'ok' });
	at(1220);
	assert.deepEqual(ae.tick(quietSig(6)), { kind: 'countdown', reason: 'quiet', leftS: 60 });
});

test('cancel 后重新从 0 计安静时长', () => {
	const { ae, at } = setup();
	at(0);
	ae.tick(quietSig(5));
	at(600);
	ae.tick(quietSig(5));
	at(610);
	ae.cancel();
	assert.deepEqual(ae.state, { kind: 'ok' });
	at(1209);
	assert.deepEqual(ae.tick(quietSig(5)), { kind: 'ok' });
	at(1210);
	assert.deepEqual(ae.tick(quietSig(5)), { kind: 'countdown', reason: 'quiet', leftS: 60 });
});

test('标签页在后台且安静 300 s → hidden 一条；回前台就清', () => {
	const { ae, at } = setup();
	at(0);
	ae.tick(quietSig(5, false));
	at(200);
	ae.tick(quietSig(5, true)); // 从这里开始算后台
	at(499);
	assert.deepEqual(ae.tick(quietSig(5, true)), { kind: 'ok' });
	at(500);
	assert.deepEqual(ae.tick(quietSig(5, true)), {
		kind: 'countdown',
		reason: 'hidden',
		leftS: 60
	});
	at(520);
	ae.tick(quietSig(5, false));
	assert.deepEqual(ae.state, { kind: 'ok' });
});

test('后台但有新文字：不按 hidden 计', () => {
	const { ae, at } = setup();
	at(0);
	ae.tick(quietSig(5, true));
	at(299);
	assert.deepEqual(ae.tick(quietSig(6, true)), { kind: 'ok' });
	at(598);
	assert.deepEqual(ae.tick(quietSig(6, true)), { kind: 'ok' });
	at(599);
	assert.deepEqual(ae.tick(quietSig(6, true)), { kind: 'countdown', reason: 'hidden', leftS: 60 });
});

test('开课一句都没出过：900 s 后是 no-speech，不是 quiet', () => {
	const { ae, at } = setup();
	at(0);
	ae.tick(quietSig(0));
	at(890);
	assert.deepEqual(ae.tick(quietSig(0)), { kind: 'ok' });
	at(900);
	const s = ae.tick(quietSig(0));
	assert.equal(reasonOf(s), 'no-speech');
	assert.deepEqual(s, { kind: 'countdown', reason: 'no-speech', leftS: 60 });
	at(960);
	assert.deepEqual(ae.tick(quietSig(0)), { kind: 'end', reason: 'no-speech' });
});

test('quota-low 只提示、不倒计时；但不覆盖已经开始的 countdown', () => {
	const { ae, at } = setup();
	at(0);
	assert.deepEqual(ae.tick(quietSig(1, false, 300)), { kind: 'ok' }); // 免疫期内
	at(200);
	assert.deepEqual(ae.tick(quietSig(1, false, 300)), { kind: 'quota-low', leftS: 300 });
	at(599);
	assert.deepEqual(ae.tick(quietSig(1, false, 300)), { kind: 'quota-low', leftS: 300 });
	at(600);
	assert.deepEqual(ae.tick(quietSig(1, false, 30)), {
		kind: 'countdown',
		reason: 'quiet',
		leftS: 60
	});
	at(610);
	assert.equal(ae.tick(quietSig(1, false, 300)).kind, 'countdown');
});

test('自定义阈值也按同一个口径算', () => {
	const { ae, at } = setup({ quietWarnS: 10, countdownS: 5 });
	at(0);
	ae.tick(quietSig(2));
	at(130);
	assert.deepEqual(ae.tick(quietSig(2)), { kind: 'countdown', reason: 'quiet', leftS: 5 });
	at(132);
	assert.deepEqual(ae.tick(quietSig(2)), { kind: 'countdown', reason: 'quiet', leftS: 3 });
	at(135);
	assert.deepEqual(ae.tick(quietSig(2)), { kind: 'end', reason: 'quiet' });
});
