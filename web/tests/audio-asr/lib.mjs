/* web/tests/audio-asr/lib.mjs —— 三个测试文件共用的小工具：断言计数、等待、假 WebSocket、node:vm 装载 worklet。
 *
 * 不连网、不需要 key、不读 ~/.classlive。课堂语料只在「S2 对照」那一项里按本机路径读，不存在就 skip。
 */
import fs from 'node:fs';
import vm from 'node:vm';

/** 记条数、打印 ✅/❌；`finish()` 决定进程退出码 */
export function makeReport() {
	const items = [];
	return {
		check(name, ok, detail) {
			items.push({ name, ok: !!ok });
			console.log(`${ok ? '✅' : '❌'} ${name}${detail ? ` — ${detail}` : ''}`);
			return !!ok;
		},
		skip(name, why) {
			items.push({ name, ok: true, skipped: true });
			console.log(`⚠️ skip ${name}${why ? ` — ${why}` : ''}`);
		},
		finish() {
			const passed = items.filter((i) => i.ok).length;
			const bad = items.filter((i) => !i.ok);
			console.log(`\n${passed}/${items.length} 通过${items.length ? '' : '（没有断言）'}`);
			if (bad.length) {
				console.log(`失败：${bad.map((b) => b.name).join(' / ')}`);
				process.exitCode = 1;
				return false;
			}
			return items.length > 0;
		},
	};
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 轮询等到条件为真（不写 while(true)，有上限） */
export function until(cond, ms = 1000, step = 2) {
	const t0 = Date.now();
	return new Promise((resolve, reject) => {
		const tick = () => {
			let v;
			try {
				v = cond();
			} catch (e) {
				return reject(e);
			}
			if (v) return resolve(v);
			if (Date.now() - t0 > ms) return reject(new Error(`until 超时（${ms} ms）`));
			setTimeout(tick, step);
		};
		tick();
	});
}

/**
 * 假 WebSocket：记录每次连接、发出去的东西，测试自己决定什么时候 open / 掉线 / 迟到消息。
 * `created`/`sent` 是全测试共享的观察点；`readyState` 与浏览器同义（0 连中 / 1 开 / 3 关）。
 */
export function makeSocketFarm() {
	const created = [];
	class FakeSocket {
		constructor(url, protocols) {
			this.url = url;
			this.protocols = protocols;
			this.readyState = 0;
			this.binaryType = null;
			this.sent = [];
			this.createdAt = Date.now();
			this.closedByTest = false;
			this.onopen = null;
			this.onmessage = null;
			this.onclose = null;
			this.onerror = null;
			created.push(this);
		}
		send(data) {
			if (this.readyState !== 1) throw new Error('假 socket 没开就 send');
			this.sent.push(data);
		}
		close() {
			this.closedByTest = true;
			this.doClose(1000);
		}
		/* ── 测试侧的手柄 ── */
		doOpen() {
			this.readyState = 1;
			this.onopen?.({});
		}
		doMessage(data) {
			this.onmessage?.({ data });
		}
		doClose(code = 1006, reason = '') {
			if (this.readyState === 3) return;
			this.readyState = 3;
			this.onclose?.({ code, reason });
		}
	}
	return { created, FakeSocket };
}

/** 用 node:vm 跑 AudioWorklet 模块（AudioWorkletGlobalScope 的替身），返回它的自测出口 */
export function loadWorklet(file, sampleRate = 48000) {
	const src = fs.readFileSync(file, 'utf8');
	const messages = [];
	let registered = null;
	const sandbox = {
		AudioWorkletProcessor: class {
			constructor() {
				this.port = {
					postMessage: (m) => messages.push(m),
					onmessage: null,
				};
			}
		},
		registerProcessor(name, cls) {
			registered = { name, cls };
		},
		sampleRate,
		currentTime: 0,
		console,
	};
	sandbox.globalThis = sandbox;
	vm.createContext(sandbox);
	vm.runInContext(src, sandbox, { filename: file });
	return {
		// 自家模块挂 __CL_CAPTURE；S2 版挂 __S2（同一个 vm 装载器两用）
		api: sandbox.__CL_CAPTURE ?? sandbox.__S2,
		messages,
		registered,
		/** 造处理器实例（帧消息会进 messages） */
		instantiate(processorOptions = {}) {
			if (!registered) throw new Error('worklet 没有 registerProcessor');
			return new registered.cls({ processorOptions });
		},
	};
}
