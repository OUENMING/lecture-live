// 两行输出的增量解析（`ZH: …` / `EN: …`）—— 桌面 `translator._StreamParser` 的逐条搬运。
// 对外：`new TwoLineParser()` → `feed(delta)` / `result(enRaw)`；`looksLikeEcho(zh, en)` 是回显判据。
// 为什么搬一份到 TS：那个类在 `translator.py` 里（带 mlx / 线程，搬不进浏览器），而流式增量
// 必须在主线程按 delta 切分；但格式容错的口径必须与桌面一字不差，所以这里不做「优化」，
// 连 3 字符留尾、24 字符兜底这些数字都照抄。判据见 tests/translate/format.test.ts。

export interface TwoLineResult {
	/** 修正后的英文（英文段混入中文 / 空 → 回退 ASR 原文，与桌面 `result()` 同） */
	en: string;
	/** 中文（空 → 回退原文，与桌面同） */
	zh: string;
}

const hasCjk = (s: string): boolean => {
	for (const c of s) if (c >= '\u4e00' && c <= '\u9fff') return true;
	return false;
};

const EN_END = ['.', '?', '!', '。', '？', '！'];
const stripTail = (s: string): string => s.trimEnd().replace(/["')\u201d\u2019]+$/, '');

/**
 * 修正后的英文是不是被**截断**了（输出额度用完，EN 段写到一半）。是 → 用 ASR 原文。
 *
 * ⭐ 2026-11-20 网页用户课：488 字符的长句，ZH 先写吃掉大半 220 token，EN 写到半句就停，
 * 截断的 EN 覆盖了完整原文 —— 中文完整、英文丢了约 20 s。
 * 判据（任一）：① 原文有句末标点、修正版没有，且修正版词更少；② 原文 ≥ 8 词而修正版不到一半。
 * 桌面 `translator.en_fix_truncated` 同口径。
 */
export function enFixTruncated(fixed: string, raw: string): boolean {
	const fw = fixed.split(/\s+/).filter(Boolean);
	const rw = raw.split(/\s+/).filter(Boolean);
	if (!rw.length) return false;
	if (rw.length >= 8 && fw.length < 0.5 * rw.length) return true;
	const r = stripTail(raw);
	const f = stripTail(fixed);
	const ends = (x: string): boolean => EN_END.some((p) => x.endsWith(p));
	return r !== '' && ends(r) && !ends(f) && fw.length < rw.length;
}

export class TwoLineParser {
	#state: 'wait_zh' | 'in_zh' | 'in_en' = 'wait_zh';
	#zh: string[] = [];
	#en: string[] = [];
	#pre: string[] = [];
	#buf = '';
	#onZh: (delta: string) => void;
	#onEn: (delta: string) => void;

	constructor(onZh: (delta: string) => void = () => {}, onEn: (delta: string) => void = () => {}) {
		this.#onZh = onZh;
		this.#onEn = onEn;
	}

	/** 找标记位置；半角 / 全角冒号都认（桌面 `_find_marker`）。 */
	static #marker(s: string, marker: string): number {
		const i = s.indexOf(marker);
		const j = s.indexOf(marker.replace(':', '：'));
		if (i === -1) return j;
		if (j === -1) return i;
		return Math.min(i, j);
	}

	feed(delta: string): void {
		if (!delta) return;
		this.#buf += delta;
		for (;;) {
			if (this.#state === 'wait_zh') {
				const k = TwoLineParser.#marker(this.#buf, 'ZH:');
				if (k === -1) {
					// 留 3 字符防标记被切开；攒够 24 字符还没标记 → 认定模型没按格式，全当中文
					if (this.#buf.length > 3) {
						const emit = this.#buf.slice(0, -3);
						this.#buf = this.#buf.slice(-3);
						if (emit) this.#pre.push(emit);
					}
					if (this.#pre.join('').length >= 24) {
						const text = (this.#pre.join('') + this.#buf).replace(/^[：: ]+/, '').trim();
						this.#pre = [];
						this.#buf = '';
						if (text) {
							this.#zh.push(text);
							this.#onZh(text);
						}
						this.#state = 'in_zh';
					}
					return;
				}
				this.#pre = []; // 有标记 → 丢弃前缀噪声
				this.#buf = this.#buf.slice(k + 3);
				this.#state = 'in_zh';
				continue;
			}
			if (this.#state === 'in_zh') {
				const k = TwoLineParser.#marker(this.#buf, 'EN:');
				if (k === -1) {
					if (this.#buf.length > 3) {
						const emit = this.#buf.slice(0, -3);
						this.#buf = this.#buf.slice(-3);
						if (emit) {
							this.#zh.push(emit);
							this.#onZh(emit);
						}
					}
					return;
				}
				const emit = this.#buf.slice(0, k).replace(/\s+$/, '');
				this.#buf = this.#buf.slice(k + 3);
				if (emit) {
					this.#zh.push(emit);
					this.#onZh(emit);
				}
				this.#state = 'in_en';
				continue;
			}
			// in_en：剩下的都是英文
			if (this.#buf) {
				this.#en.push(this.#buf);
				this.#onEn(this.#buf);
				this.#buf = '';
			}
			return;
		}
	}

	result(enRaw: string): TwoLineResult {
		if (this.#buf) {
			if (this.#state === 'in_en') {
				this.#en.push(this.#buf);
				this.#onEn(this.#buf);
			} else if (this.#state === 'in_zh') {
				this.#zh.push(this.#buf);
				this.#onZh(this.#buf);
			} else {
				this.#pre.push(this.#buf);
			}
			this.#buf = '';
		}
		const zh = (this.#zh.join('') + this.#pre.join('')).trim().replace(/^[：: ]+/, '').trim();
		let en = this.#en.join('').trim();
		if (!en || hasCjk(en)) en = enRaw;
		else if (enFixTruncated(en, enRaw)) en = enRaw; // 额度用完、EN 写到一半 → 原文完整，用原文
		return { en, zh: zh || enRaw };
	}
}

/** 桌面 `cloud_translator._ECHO_MARKERS`：模型把术语表/标签回显成译文。 */
const ECHO_MARKERS = ['课程术语', '术语表', '术语解析', '以下翻译', '翻译如下', '校正及翻译'];

/** 这句译文的「回显 / 截断」判据（桌面 `_looks_like_echo`，逐字同口径）。 */
export function looksLikeEcho(zh: string, en: string): boolean {
	const z = (zh || '').trim();
	if (!z) return true;
	if (ECHO_MARKERS.some((m) => z.includes(m))) return true;
	return en.split(/\s+/).filter(Boolean).length >= 10 && z.length < 6;
}
