// 草稿译文什么时候送：桌面 `draft_policy.should_translate("novel", …)` 的 TS 版 + `main.partial_worker` 的节流状态。
//
// 为什么要它：不挡的话草稿每变一个词就发一次请求 —— 2026-11-18 浏览器实测 2 分钟 38 句发了 373 次（桌面约每句 1–2 次）。
// 为什么是 TS 而不是走 Pyodide：判据只有十几行纯算术，每个草稿变化都要问一次；跨 Worker 往返的成本比判据本身大。
// 逐字对照：常数与分支抄 static/core-py/draft_policy.py（novel 档，2026-10-11 起桌面默认）；
// tests/translate/draft-gate.test.ts 用 CPython 跑同一批输入核对。
// 状态重置抄 main.py:3567：换段（新句）→ last_tr / last_sent 清零，否则新句前几词永远等不到送翻。

/** 距上次送翻至少这么久（秒）—— draft_policy.MIN_GAP_S */
export const MIN_GAP_S = 2.5;
/** 相对上次送出的文本至少这么多个新词 —— NOVEL_MIN_NEW */
export const NOVEL_MIN_NEW = 4;
/** 或者距上次 ≥ 这么久且至少 1 个新词 —— NOVEL_STARVE_S */
export const NOVEL_STARVE_S = 5.0;

/** 词身份同 `draft_policy._tokens`：`w.lower().strip(".,;:?!")`，空串也算一个词（Python 原样） */
export function tokens(text: string): string[] {
	return text
		.split(/\s+/)
		.filter((w) => w !== '') // Python 的 str.split() 不出空串
		.map((w) => w.toLowerCase().replace(/^[.,;:?!]+|[.,;:?!]+$/g, ''));
}

/** `text` 里有多少词不在 `sent` 里（多重集差） */
export function novelWords(sent: string, text: string): number {
	const have = new Map<string, number>();
	for (const w of tokens(sent)) have.set(w, (have.get(w) ?? 0) + 1);
	let n = 0;
	for (const w of tokens(text)) {
		const c = have.get(w) ?? 0;
		if (c > 0) have.set(w, c - 1);
		else n += 1;
	}
	return n;
}

export function shouldTranslateNovel(now: number, lastTr: number, sent: string, text: string): boolean {
	const gap = now - lastTr;
	if (gap < MIN_GAP_S) return false;
	const fresh = novelWords(sent, text);
	return fresh >= NOVEL_MIN_NEW || (gap >= NOVEL_STARVE_S && fresh >= 1);
}

/** 一条会话的草稿节流状态。`now` 用秒（单调时钟）。 */
export class DraftGate {
	#seg = -1;
	#lastTr = 0;
	#sent = '';

	/** 这份草稿要不要送翻；要送就记下（与桌面「判定通过即更新 last_tr / last_sent」同序） */
	admit(seg: number, text: string, now: number): boolean {
		if (seg !== this.#seg) {
			this.#seg = seg;
			this.#lastTr = 0;
			this.#sent = '';
		}
		if (!shouldTranslateNovel(now, this.#lastTr, this.#sent, text)) return false;
		this.#lastTr = now;
		this.#sent = text;
		return true;
	}
}
