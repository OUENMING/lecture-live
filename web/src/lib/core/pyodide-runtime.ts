// 在 Pyodide 里跑 Python 实时核心（`static/core-py/` 那 9 个文件）。
//
// 装载面全部注入：**浏览器用 CDN + fetch，Node 测试用 node_modules + fs**，核心代码与 Python 源
// 两边完全一样（S1 的穿刺就是靠「一份 Python 两处跑」验的；这里把那条判据变成常驻测试
// `tests/core/tape.test.ts`）。所以本文件里没有 fetch、没有 DOM、没有 Worker。
// 唯一的例外是 `importPyodide()`：浏览器里 pyodide.mjs 只能靠动态 import 拿，而「同源优先、jsDelivr
// 兜底」这个决定只该有一处，所以它连同 `originBase()` 一起放这儿，core / prompt / prep 三处共用。
import type { Snapshot } from './project';

/**
 * 钉死的 Pyodide 版本。
 * - 浏览器走 CDN：这 13 MB（wasm 9.2 MB + stdlib 2.4 MB）不该进仓库和构建产物，CDN 还能跨站复用缓存。
 * - Node 测试走 devDependency `pyodide@314.0.8`（同版本，离线可跑）。
 * 两边必须同版本，否则「逐字节相同」这条判据就掺进了版本差。
 */
export const PYODIDE_VERSION = '314.0.8';
export const PYODIDE_CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
/**
 * 同源自托管根（`npm run vendor` 落到 `static/vendor/pyodide/<ver>/`）。
 * 为什么：国内到 cdn.jsdelivr.net 时好时坏，识别核心下不来 = 整节课没字幕；自托管那份由我们自己
 * 的 nginx 发（`/_app/`、`/vendor/` 缓存一年），CDN 只当兜底。
 */
export const PYODIDE_VENDOR = `/vendor/pyodide/${PYODIDE_VERSION}/`;

/**
 * 同源根（`https://host`），取不到就是空串。
 * 主线程与 Worker 都有 `location`；Node（测试 / 脚本）没有 —— 那儿的部署面只有 CDN，同源这一档自然跳过。
 */
export function originBase(): string {
	const loc = (globalThis as { location?: { origin?: unknown } }).location;
	const origin = typeof loc?.origin === 'string' ? loc.origin : '';
	return /^https?:\/\/[^/]+$/.test(origin) ? origin : '';
}

/** `pyodide.mjs` 的入口面（只需 loadPyodide；别让整包类型渗进来）。 */
export interface PyodideModule {
	loadPyodide(opts: { indexURL: string; stdout?: (line: string) => void }): Promise<PyodideLike>;
}

/** static 下 Python 源的根。站点的 base 是 '/'（adapter-static，svelte.config.js 未配 paths.base）。 */
export const CORE_PY_ROOT = '/core-py';

/** 只用到 Pyodide 的这几样（照 S1 的用法收窄，别让整包类型渗进来）。 */
export interface PyodideLike {
	readonly version: string;
	FS: {
		mkdirTree(path: string): void;
		writeFile(path: string, data: Uint8Array): void;
	};
	runPython(code: string): unknown;
	globals: { get(name: string): unknown };
}

/** 已 boot 的 Python 核心：一拍进、一版快照出。 */
export interface PythonCore {
	feed(raw: string): Snapshot;
	tick(seq: number): Snapshot;
	/** 下课：还没封口的尾句强制定稿（只封一次，重复调不会出重复句） */
	flush(): Snapshot;
	/** 清空核心（同一份 Pyodide 上连跑多盘磁带用；浏览器换会话是 dispose 掉 Worker） */
	reset(): void;
	pyodideVersion: string;
	pythonVersion: string;
	/** 写进虚拟文件系统的 .py + 数据字节数 */
	stagedBytes: number;
}

/**
 * 拿一份 `pyodide.mjs` 模块与它的 `indexURL`：**同源优先、jsDelivr 兜底**。
 *
 * 两档给的 indexURL 决定 pyodide.asm.mjs / .wasm / python_stdlib.zip 从哪儿取 —— 必须和模块本体同源，
 * 否则「同源省下的那 13 MB」会漂回 CDN。兜底发生时报一行 warn（以后接遥测）。
 */
export async function importPyodide(): Promise<{ mod: PyodideModule; indexURL: string }> {
	const origin = originBase();
	const tiers: { indexURL: string; label: string }[] = [];
	if (origin) tiers.push({ indexURL: `${origin}${PYODIDE_VENDOR}`, label: '同源' });
	tiers.push({ indexURL: PYODIDE_CDN, label: 'jsDelivr' });
	let last: unknown = null;
	for (let i = 0; i < tiers.length; i++) {
		const tier = tiers[i];
		try {
			const mod = (await import(/* @vite-ignore */ `${tier.indexURL}pyodide.mjs`)) as PyodideModule;
			if (i > 0) {
				const why = last instanceof Error ? last.message : String(last);
				console.warn(`[classlive] Pyodide 没从同源拉到（${why}），退到 ${tier.label}`);
			}
			return { mod, indexURL: tier.indexURL };
		} catch (e) {
			last = e;
		}
	}
	throw new Error(`拉不到 Pyodide（同源与 jsDelivr 都失败：${last instanceof Error ? last.message : String(last)}）`);
}

export interface CoreLoad extends PythonCore {
	/** 从调 load() 到 Python 核心可用（ms） */
	ms_boot: number;
}

/** 写给调用方的装载面：只有三样。 */
export interface CoreDeps {
	/** 拿到一个 boot 好的 Pyodide 实例（CDN / node_modules 由调用方决定） */
	load: () => Promise<PyodideLike>;
	/** 读 `static/core-py/` 下的一个文件（路径相对 core-py） */
	read: (rel: string) => Promise<Uint8Array>;
	/** 要 stage 的文件清单（来自 `static/core-py/manifest.json`） */
	manifest: string[];
}

/**
 * 装载：把 Python 源原样写进 Pyodide 虚拟文件系统 → 加 sys.path → import `rt`。
 *
 * 不打包、不编译、不改一个字节 —— 文件树就是 `static/core-py/` 的文件树。
 */
export async function loadCore(deps: CoreDeps): Promise<CoreLoad> {
	const t0 = Date.now();
	const py = await deps.load();
	const staged = await Promise.all(deps.manifest.map((rel) => deps.read(rel)));
	let stagedBytes = 0;
	for (let i = 0; i < deps.manifest.length; i++) {
		const dst = `${CORE_PY_ROOT}/${deps.manifest[i]}`;
		py.FS.mkdirTree(dst.slice(0, dst.lastIndexOf('/')));
		py.FS.writeFile(dst, staged[i]);
		stagedBytes += staged[i].length;
	}
	py.runPython(
		`import sys\nif ${JSON.stringify(CORE_PY_ROOT)} not in sys.path: sys.path.insert(0, ${JSON.stringify(CORE_PY_ROOT)})`
	);
	py.runPython('import rt as _rt\n_rt_feed = _rt.feed\n_rt_tick = _rt.tick\n_rt_flush = _rt.flush\n_rt_reset = _rt.reset');
	const feed = py.globals.get('_rt_feed') as (raw: string) => string;
	const tick = py.globals.get('_rt_tick') as (seq: number) => string;
	const flush = py.globals.get('_rt_flush') as () => string;
	const reset = py.globals.get('_rt_reset') as () => void;
	if (typeof feed !== 'function' || typeof tick !== 'function' || typeof reset !== 'function') {
		throw new Error('core-py: rt.py 没导出 feed / tick / reset');
	}
	if (typeof flush !== 'function') throw new Error('core-py: rt.py 没导出 flush');
	return {
		feed: (raw: string) => JSON.parse(feed(raw)) as Snapshot,
		tick: (seq: number) => JSON.parse(tick(seq)) as Snapshot,
		flush: () => JSON.parse(flush()) as Snapshot,
		reset: () => {
			reset();
		},
		pyodideVersion: py.version,
		pythonVersion: String(py.runPython('import sys; sys.version.split()[0]')),
		stagedBytes,
		ms_boot: Date.now() - t0
	};
}
