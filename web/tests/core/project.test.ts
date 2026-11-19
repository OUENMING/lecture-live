// `project.ts` 的单测：StreamCore 快照 → ViewState 的投影规则（全纯函数，不用起 Pyodide）。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { EMPTY_PROJ, noticeView, project, type ProjState, type Snapshot } from '../../src/lib/core/project.ts';

function snap(over: Partial<Snapshot> = {}): Snapshot {
	return {
		now: 1,
		draft: '',
		draft_start: null,
		sealed: [],
		n_msgs: 1,
		seq: 0,
		notice: null,
		...over
	};
}

test('空快照：一版合法 ViewState，rev 从 1 起', () => {
	const { state, view } = project(EMPTY_PROJ, snap({ now: 0 }));
	assert.equal(view.rev, 1);
	assert.deepEqual(view.rows, []);
	assert.deepEqual(view.status, {});
	assert.equal(view.course, '');
	assert.equal(view.elapsed_s, 0);
	assert.deepEqual(state.rows, []);
});

test('封口句 → kind=final，译文位是空的（翻译是 M2）', () => {
	const { view } = project(
		EMPTY_PROJ,
		snap({ now: 12.5, sealed: [{ text: 'Hello there.', lang: 'en', start: 9.25, end: 11.5 }] })
	);
	assert.deepEqual(view.rows, [
		{ id: 's1', kind: 'final', en: 'Hello there.', zh: '', zh_state: 'none', terms: [], seg: 1, t_s: 9.25 }
	]);
	assert.equal(view.elapsed_s, 12.5);
	assert.equal(view.rows[0].zh_state, 'none');
});

test('正在说的那句 → kind=draft，起点取 draft_start', () => {
	const { view } = project(EMPTY_PROJ, snap({ now: 7, draft: 'so we continue', draft_start: 6.125 }));
	assert.deepEqual(view.rows, [
		{ id: 's1', kind: 'draft', en: 'so we continue', zh: '', zh_state: 'none', terms: [], seg: 1, t_s: 6.125 }
	]);
});

test('draft_start 拿不到时退回 now（行还是要出来）', () => {
	const { view } = project(EMPTY_PROJ, snap({ now: 3.5, draft: 'mm', draft_start: null }));
	assert.equal(view.rows[0].t_s, 3.5);
});

test('只喂空白 / 空串：不出草稿行（别让界面闪空行）', () => {
	for (const draft of ['', '   ', '\n']) {
		const { view } = project(EMPTY_PROJ, snap({ draft }));
		assert.deepEqual(view.rows, [], `draft=${JSON.stringify(draft)}`);
	}
});

test('草稿转定稿：行 id 不变（界面靠它认「那行定稿了」）', () => {
	let p: ProjState = EMPTY_PROJ;
	// 第 1 句先封口
	let r = project(p, snap({ sealed: [{ text: 'A.', lang: 'en', start: 0, end: 1 }], draft: 'B is' }));
	p = r.state;
	assert.deepEqual(
		r.view.rows.map((x) => [x.id, x.kind]),
		[
			['s1', 'final'],
			['s2', 'draft']
		]
	);
	// 第 2 句封口：还是 s2，只是变成 final
	const draftId = r.view.rows[1].id;
	r = project(p, snap({ now: 3, sealed: [{ text: 'B is done.', lang: 'en', start: 1, end: 2.5 }], draft: '' }));
	assert.deepEqual(
		r.view.rows.map((x) => [x.id, x.kind, x.seg]),
		[
			['s1', 'final', 1],
			['s2', 'final', 2]
		]
	);
	assert.equal(r.view.rows[1].id, draftId);
});

test('投影状态只存定稿行：草稿不进 state（否则下一拍会重影）', () => {
	const first = project(EMPTY_PROJ, snap({ draft: 'under way' }));
	assert.equal(first.view.rows.length, 1);
	assert.deepEqual(first.state.rows, []);
	const second = project(first.state, snap({ draft: 'under way more' }));
	assert.deepEqual(
		second.view.rows.map((x) => [x.kind, x.en]),
		[['draft', 'under way more']]
	);
});

test('一条消息里封两句：两条 final，seg 连续', () => {
	const { view } = project(
		EMPTY_PROJ,
		snap({
			sealed: [
				{ text: 'One.', lang: 'en', start: 0, end: 1 },
				{ text: 'Two.', lang: 'en', start: 1, end: 2 }
			]
		})
	);
	assert.deepEqual(
		view.rows.map((x) => [x.id, x.seg, x.t_s]),
		[
			['s1', 1, 0],
			['s2', 2, 1]
		]
	);
});

test('rev 每拍 +1；课名跟着 extra 粘住', () => {
	let p = EMPTY_PROJ;
	for (let i = 1; i <= 3; i++) {
		const r = project(p, snap({ now: i }), { course: 'ECON10730' });
		assert.equal(r.view.rev, i);
		assert.equal(r.state.rev, i);
		p = r.state;
	}
	assert.equal(p.course, 'ECON10730');
});

test('elapsed_s 只到毫秒，不留浮点尾巴', () => {
	const { view } = project(EMPTY_PROJ, snap({ now: 12.3456789 }));
	assert.equal(view.elapsed_s, 12.346);
});

test('notice 进 status.notice（U6：失败要看得见）', () => {
	const { view } = project(EMPTY_PROJ, snap({ notice: '消息不是 JSON：x' }));
	assert.deepEqual(view.status, { notice: '消息不是 JSON：x' });
});

test('noticeView：行与时钟不动，只挂提示、rev 前进', () => {
	const p = project(EMPTY_PROJ, snap({ now: 5, sealed: [{ text: 'A.', lang: 'en', start: 4, end: 5 }] })).state;
	const { state, view } = noticeView(p, 'worker 挂了');
	assert.equal(view.rev, p.rev + 1);
	assert.deepEqual(view.rows, p.rows);
	assert.equal(view.elapsed_s, p.elapsed_s);
	assert.deepEqual(view.status, { notice: 'worker 挂了' });
	assert.equal(state.notice, 'worker 挂了');
});
