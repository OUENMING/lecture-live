// 示例字幕回放：门面循环播放一段真课，上课页在 M1 接上麦克风之前也用它演示。
// 产出的形状与 ViewState 的行一致（定稿行 + 草稿），字幕组件只认这个形状，换成真数据不用改组件。
// 时钟：按事件时刻链式 setTimeout，不用 setInterval 推时钟（PLAN A10）。

type Ev = [number, 'd' | 'dz' | 'f', string, string?];
/** t：这句定稿时离开课多少秒（演示里按回放时间算） */
export type CapRow = { id: number; en: string; zh: string; t: number };

export class DemoPlayer {
	rows = $state.raw<CapRow[]>([]);
	draftEn = $state('');
	draftZh = $state('');
	playing = $state(false);
	ready = $state(false);
	/** 回放走到哪了（录音里的秒数）：门面背后的课件按它翻页 */
	pos = $state(0);

	#evs: Ev[] = [];
	#i = 0;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#t0 = 0;
	#next = 1;
	#keep: number;
	#from: number;
	#to: number;

	/** from / to：循环的区间（秒）；keep：屏上保留几行定稿 */
	constructor(opts: { from?: number; to?: number; keep?: number } = {}) {
		this.#from = opts.from ?? 0;
		this.#to = opts.to ?? Infinity;
		this.#keep = opts.keep ?? 4;
	}

	async load(url: string): Promise<void> {
		const all: Ev[] = await (await fetch(url)).json();
		this.#evs = all.filter((e) => e[0] >= this.#from && e[0] <= this.#to);
		this.ready = this.#evs.length > 0;
	}

	/** 先把开头几行铺上，第一眼就不是空窗 */
	prime(lines: number): void {
		while (this.#i < this.#evs.length && this.rows.length < lines) this.#apply(this.#evs[this.#i++]);
		this.draftEn = this.draftZh = '';
	}

	play(): void {
		if (this.playing || !this.ready) return;
		this.playing = true;
		const at = this.#evs[this.#i]?.[0] ?? this.#from;
		this.#t0 = performance.now() - (at - this.#from) * 1000 + 600;
		this.#schedule();
	}

	pause(): void {
		this.playing = false;
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = null;
	}

	#schedule(): void {
		if (!this.playing) return;
		const ev = this.#evs[this.#i];
		if (!ev) {
			// 循环：停一拍再从头来，旧行留着，像同一节课继续往下讲
			this.#i = 0;
			this.#timer = setTimeout(() => {
				this.#t0 = performance.now();
				this.#schedule();
			}, 1800);
			return;
		}
		const wait = Math.max(0, this.#t0 + (ev[0] - this.#from) * 1000 - performance.now());
		this.#timer = setTimeout(() => {
			const now = (performance.now() - this.#t0) / 1000 + this.#from;
			while (this.#i < this.#evs.length && this.#evs[this.#i][0] <= now + 0.001) {
				this.#apply(this.#evs[this.#i++]);
			}
			this.#schedule();
		}, wait);
	}

	#apply(ev: Ev): void {
		this.pos = ev[0];
		if (ev[1] === 'd') this.draftEn = ev[2];
		else if (ev[1] === 'dz') this.draftZh = ev[2];
		else {
			this.rows = [...this.rows.slice(-(this.#keep - 1)), { id: this.#next++, en: ev[2], zh: ev[3] ?? '', t: ev[0] - this.#from }];
			this.draftEn = '';
			this.draftZh = '';
		}
	}
}

/** 门面、上课页、说明页共用一个示例播放器：从门面过渡进上课页时，字幕接着往下走，不重来。 */
export const demo = new DemoPlayer({ from: 8, to: 620, keep: 80 });
let loading: Promise<void> | null = null;
export function startDemo(lines = 2): Promise<void> {
	loading ??= demo.load('/demo/replay.json').then(() => demo.prime(lines));
	return loading.then(() => demo.play());
}
