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
