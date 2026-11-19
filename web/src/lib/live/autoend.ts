// lib/live/autoend.ts —— 网页版「忘关网页」的自动下课判据（W22，纯状态机，不碰界面/网络）。
//
// 对外：createAutoEnd(opts) → { tick(sig), cancel(), state }；sig 只吃「已出文字的单调计数 + 是否后台 + 剩余额度」。
// 为什么这样切：判据要能单测，就必须与时钟、DOM、额度接口全部解耦；界面只在已有的音频帧节拍里喂一次 tick，
// 不新增 setInterval / rAF（页签隐藏时定时器会被降频，本来也不可信）。
// 为什么这么保守：课堂会天然安静（小组作业/做题），所以每一条都先给足 120 s 免疫 + 60 s 倒计时，
// 有文字/用户操作立刻回 ok；额度低只提示不倒计时（额度本来已被服务端截断）。

export type AutoEndReason = 'quiet' | 'hidden' | 'no-speech';

export type AutoEndState =
	| { kind: 'ok' }
	| { kind: 'quota-low'; leftS: number }
	| { kind: 'countdown'; reason: AutoEndReason; leftS: number }
	| { kind: 'end'; reason: AutoEndReason };

export interface AutoEndSignal {
	/** 已出文字的单调计数（rows 数 + 草稿长度之类）：变了就算「有声音」 */
	text: number;
	hidden: boolean;
	/** 剩余额度（秒）；未知就传 null / 不传 */
	quotaLeftS?: number | null;
}

export interface AutoEndOpts {
	/** 墙钟（ms），测试注入 */
	now?: () => number;
	quietWarnS?: number;
	hiddenQuietWarnS?: number;
	noSpeechWarnS?: number;
	countdownS?: number;
	quotaWarnS?: number;
}

export interface AutoEnd {
	tick(sig: AutoEndSignal): AutoEndState;
	cancel(): void;
	readonly state: AutoEndState;
}

/** 开课后这么久内免疫一切判据（含额度提示） */
const GRACE_S = 120;
const OK: AutoEndState = { kind: 'ok' };

export function createAutoEnd(opts: AutoEndOpts = {}): AutoEnd {
	const now = opts.now ?? (() => Date.now());
	const quietWarnS = opts.quietWarnS ?? 600;
	const hiddenQuietWarnS = opts.hiddenQuietWarnS ?? 300;
	const noSpeechWarnS = opts.noSpeechWarnS ?? 900;
	const countdownS = opts.countdownS ?? 60;
	const quotaWarnS = opts.quotaWarnS ?? 600;

	const startedAt = now();
	let lastTextAt = startedAt;
	let cancelAt = startedAt;
	let lastText: number | null = null;
	/** 这节课出过字没有（粘住）：没出过字时 quiet 不该判，交给 no-speech */
	let everText = false;
	let hiddenSince: number | null = null;
	let countdownAt: number | null = null;
	let countdownReason: AutoEndReason | null = null;
	let state: AutoEndState = OK;

	function clearCountdown(): void {
		countdownAt = null;
		countdownReason = null;
	}

	/** 已跨过阈值的判据里最早的那个（同理，先到先算） */
	function firstCrossed(t: number, text: number): AutoEndReason | null {
		const quietBase = Math.max(lastTextAt, cancelAt, startedAt);
		const candidates: Array<{ reason: AutoEndReason; at: number }> = [];
		if (everText && t - quietBase >= quietWarnS * 1000) {
			candidates.push({ reason: 'quiet', at: quietBase + quietWarnS * 1000 });
		}
		if (hiddenSince !== null) {
			// 后台 + 安静：既要在后台，也要这段时间没出新文字
			const hiddenBase = Math.max(hiddenSince, lastTextAt, cancelAt);
			if (t - hiddenBase >= hiddenQuietWarnS * 1000) {
				candidates.push({ reason: 'hidden', at: hiddenBase + hiddenQuietWarnS * 1000 });
			}
		}
		if (!everText) {
			const emptyBase = Math.max(cancelAt, startedAt);
			if (t - emptyBase >= noSpeechWarnS * 1000) {
				candidates.push({ reason: 'no-speech', at: emptyBase + noSpeechWarnS * 1000 });
			}
		}
		if (candidates.length === 0) return null;
		let best = candidates[0];
		for (const c of candidates) if (c.at < best.at) best = c;
		return best.reason;
	}

	return {
		tick(sig: AutoEndSignal): AutoEndState {
			const t = now();

			// 结束只给一次：之后无论喂什么都不再改口
			if (state.kind === 'end') return state;

			if (lastText === null) lastText = sig.text;
			else if (sig.text !== lastText) {
				lastText = sig.text;
				lastTextAt = t;
				clearCountdown();
			}
			if (sig.text > 0) everText = true;

			if (sig.hidden) {
				if (hiddenSince === null) hiddenSince = t;
			} else hiddenSince = null;

			if (t - startedAt < GRACE_S * 1000) {
				clearCountdown();
				state = OK;
				return state;
			}

			// 后台倒计时一旦回到前台就作废（人回来了）；安静/没出字只能靠新文字或 cancel 解除
			if (countdownReason === 'hidden' && hiddenSince === null) clearCountdown();

			if (countdownAt !== null && countdownReason !== null) {
				const leftMs = countdownS * 1000 - (t - countdownAt);
				if (leftMs <= 0) {
					state = { kind: 'end', reason: countdownReason };
					clearCountdown();
					return state;
				}
				state = {
					kind: 'countdown',
					reason: countdownReason,
					leftS: Math.ceil(leftMs / 1000)
				};
				return state;
			}

			const reason = firstCrossed(t, sig.text);
			if (reason) {
				countdownAt = t;
				countdownReason = reason;
				state = { kind: 'countdown', reason, leftS: countdownS };
				return state;
			}

			if (sig.quotaLeftS != null && sig.quotaLeftS < quotaWarnS) {
				state = { kind: 'quota-low', leftS: Math.max(0, Math.floor(sig.quotaLeftS)) };
				return state;
			}

			state = OK;
			return state;
		},

		cancel(): void {
			if (state.kind === 'end') return;
			const t = now();
			cancelAt = t;
			clearCountdown();
			state = OK;
		},

		get state(): AutoEndState {
			return state;
		}
	};
}
