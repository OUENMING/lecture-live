// 增量 SSE 解析：`data:` 行在任意位置都可能被切开，所以先累积再切，不逐块硬解。
// 对外：`new SseParser()` → `push(chunk)` 返回这一块里已经完整的负载、`end()` 收尾、`done` 。
// 为什么这样：判据是「同一段流按任意位置切开，拼出来的事件序列必须一致」（tests/translate/sse.test.ts）。
// 只认 SSE 里用得到的两种行：`data:` 与空行（事件分隔）；注释 / `event:` / 其他字段一律忽略。
// 半截行与半截事件都留在实例里，下一块接着拼 —— 这就是「切在哪儿都一样」的来源。

const DONE = '[DONE]';

export class SseParser {
	#line = '';
	#pending: string[] = [];
	#done = false;

	/** ：之后的 data 一律丢掉（与桌面 `_stream_chat` 的 break 同效）。 */
	get done(): boolean {
		return this.#done;
	}

	/** 喂一块文本；返回这一块里已经完整的事件负载。 */
	push(chunk: string): string[] {
		if (this.#done || !chunk) return [];
		this.#line += chunk;
		return this.#drain(false);
	}

	/** 收尾：最后一条没有空行结尾的事件也交出来（上游可能不给收尾空行）。 */
	end(): string[] {
		if (this.#done) return [];
		const out = this.#drain(true);
		if (!this.#done && this.#pending.length) this.#flush(out);
		return out;
	}

	#consumeLine(raw: string, out: string[]): void {
		const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw;
		if (line === '') {
			// 空行 = 事件边界：把累积的 data 合起来（多行 data 用换行拼，SSE 规范）
			if (this.#pending.length) this.#flush(out);
			return;
		}
		if (!line.startsWith('data:')) return; // 注释 / event: / id: 不认
		let v = line.slice(5);
		if (v.startsWith(' ')) v = v.slice(1);
		this.#pending.push(v);
	}

	#flush(out: string[]): void {
		const payload = this.#pending.join('\n');
		this.#pending = [];
		if (payload === DONE) {
			this.#done = true;
			this.#line = '';
			return;
		}
		out.push(payload);
	}

	#drain(final: boolean): string[] {
		const out: string[] = [];
		while (!this.#done) {
			const nl = this.#line.indexOf('\n');
			if (nl < 0) break;
			const raw = this.#line.slice(0, nl);
			this.#line = this.#line.slice(nl + 1);
			this.#consumeLine(raw, out);
		}
		if (final && !this.#done && this.#line) {
			const raw = this.#line;
			this.#line = '';
			this.#consumeLine(raw, out);
		}
		return out;
	}
}
