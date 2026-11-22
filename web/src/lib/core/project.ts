// Python 实时核心的快照 → ViewState 的投影。**纯函数**：同一串快照必然得到同一串 ViewState。
//
// 快照的形状来自 `static/core-py/rt.py`（Python 侧是真源，字段只增不改名）；
// 这里只做两件事：把封口句固定成 final 行、把正在说的那句挂成 draft 行，并且给每行一个稳定 id。
//
// M2 之前没有译文：zh 一律空、zh_state="none"、terms 空。翻译接上后只改这里，界面不用动。
import type { Row, Status, ViewState } from '../state/viewstate';

/** 一句封口（`StreamCore.Sealed` 的镜像）。时间单位秒，相对本流起点。 */
export interface SealedLine {
	text: string;
	lang: string;
	start: number;
	end: number;
	/** Soniox 原始说话人编号（没开分离 → 空串 / 旧快照没有这个键） */
	speaker?: string;
	/** 界面显示的字母（`speaker_label.SpeakerLabeler`：主讲 / 不够长 → 空串） */
	spk?: string;
}

/** `rt.py` 一拍输出的快照。 */
export interface Snapshot {
	/** 本课已过的音频秒（回放里＝磁带时刻 − 第一条消息的时刻） */
	now: number;
	/** 当前未封口那句的文本（可能是空串） */
	draft: string;
	/** 当前未封口那句的起点（音频秒）；拿不到是 null */
	draft_start: number | null;
	/** 这一拍封出来的句子（0..n 条，按封口顺序） */
	sealed: SealedLine[];
	/** 累计吃进的消息条数 */
	n_msgs: number;
	/** 音频帧节拍序号（0 = 还没 tick 过） */
	seq: number;
	/** 出错时给界面看的提示（U6：失败不许静默） */
	notice: string | null;
}

/** 投影需要记住的全部东西：已封口的行（**不含草稿行**，草稿每拍重建）+ 上一版的界面值。 */
export interface ProjState {
	rev: number;
	rows: Row[];
	course: string;
	elapsed_s: number;
	notice: string | null;
}

export const EMPTY_PROJ: ProjState = {
	rev: 0,
	rows: [],
	course: '',
	elapsed_s: 0,
	notice: null
};

/** 一行新字幕的空白译文位（M2 会填）。每次都新建数组：行可能被界面改。 */
function blankZh(): Pick<Row, 'zh' | 'zh_state' | 'terms'> {
	return { zh: '', zh_state: 'none', terms: [] };
}

/** 秒 → 毫秒取整，避免浮点尾巴进界面。 */
function ms(x: number): number {
	return Math.round(x * 1000) / 1000;
}

function viewOf(s: ProjState, rows: Row[]): ViewState {
	const status: Status = {};
	if (s.notice) status.notice = s.notice;
	return { rev: s.rev, rows, status, course: s.course, elapsed_s: s.elapsed_s };
}

/**
 * 一拍：上一版投影 + 一条快照 → 新投影（rev+1）与它的 ViewState。
 *
 * 行的 id 是 `s<seg>`（句子序号，从 1 起）。草稿行先以 `s<n+1>` 出现，等这句封口时
 * **同一个 id** 变成 final 行 —— 界面靠这个认「刚才那行定稿了」。
 */
export function project(
	prev: ProjState,
	snap: Snapshot,
	extra: { course?: string } = {}
): { state: ProjState; view: ViewState } {
	const finals = prev.rows.slice();
	for (const s of snap.sealed) {
		const seg = finals.length + 1;
		finals.push({
			id: `s${seg}`,
			kind: 'final',
			en: s.text,
			...blankZh(),
			seg,
			t_s: ms(s.start),
			// 只有别人说的才带（主讲不标）：没有这个键 = 不标，旧磁带的投影逐字不变
			...(s.spk ? { spk: s.spk } : {})
		});
	}
	const rows = finals.slice();
	if (snap.draft.trim() !== '') {
		const seg = rows.length + 1;
		rows.push({
			id: `s${seg}`,
			kind: 'draft',
			en: snap.draft,
			...blankZh(),
			seg,
			t_s: ms(snap.draft_start ?? snap.now)
		});
	}
	const state: ProjState = {
		rev: prev.rev + 1,
		rows: finals,
		course: extra.course ?? prev.course,
		elapsed_s: ms(snap.now),
		notice: snap.notice
	};
	return { state, view: viewOf(state, rows) };
}

/**
 * 出错时的一版：行与时钟都不动，只把提示挂上（U6：失败必须出现在界面上）。
 * 核心出错不该让字幕消失，也不该静默。
 */
export function noticeView(prev: ProjState, notice: string): { state: ProjState; view: ViewState } {
	const state: ProjState = { ...prev, rev: prev.rev + 1, notice };
	return { state, view: viewOf(state, prev.rows) };
}
