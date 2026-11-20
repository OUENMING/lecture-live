// 联网量「说完 → 中文首字」：回放一盘真磁带（1× 实时），逐句记封口 / 首字耗时。
// 用法：node --import tsx scripts/measure-latency.ts [磁带.jsonl] [--max-seconds 600] [--max-requests 200]
// 只做三件事：按磁带时刻投喂消息与音频帧 → 真的 POST /v1/chat/completions（test key）→ 汇总。
// 为什么 1× 实时：延迟要的是「人说完等多久」，压缩回放会把排队时间算进翻译里（同一句的
// `t_sealed` 与 `t_zh_first` 都是墙钟）。所以这里不追求跑得快，只追求时间线是真的。
// key 只从 ~/.classlive/test-key 读（test1 通行码）；没有就跳过并说明 —— 绝不回落到作者的码。
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { loadPyodide } from 'pyodide';
import type { Core } from '../src/lib/core/index.ts';
import { EMPTY_PROJ, project, type ProjState } from '../src/lib/core/project.ts';
import type { PyodideLike } from '../src/lib/core/pyodide-runtime.ts';
import type { ViewState } from '../src/lib/state/viewstate.ts';
import { latencyOf, type LatencySample } from '../src/lib/translate/latency.ts';
import { pyPromptFromPyodide, type PromptBuilder } from '../src/lib/translate/prompt.ts';
import { Translator } from '../src/lib/translate/translator.ts';
import { streamChat } from '../src/lib/translate/stream.ts';
import { parseTape, type TapeMessage } from '../src/lib/session/tape.ts';

const ROOT = path.resolve(import.meta.dirname, '..');
const CORE_PY = path.join(ROOT, 'static/core-py');
const PYODIDE_DIR = path.dirname(new URL(import.meta.resolve('pyodide/package.json')).pathname);
const API = process.env.CL_API_BASE ?? 'https://asr.bldcam.page';
const KEY_FILE = path.join(os.homedir(), '.classlive/test-key');
// 设备名额按安装号算：用本机那枚（桌面 `final_asr.install_id()` 同一个值），
// 别每次现生成 UUID —— 那样每跑一次就占掉一个网页设备名额，第二次就 403 devices。
const INSTALL_FILE = path.join(os.homedir(), '.classlive/install-id');
const DEFAULT_TAPE = '/Users/owen/.classlive/eval/diar_spike/out7/prod_ctx.jsonl';

function arg(name: string, dflt: number): number {
	const i = process.argv.indexOf(`--${name}`);
	const v = i >= 0 ? Number(process.argv[i + 1]) : NaN;
	return Number.isFinite(v) ? v : dflt;
}

const MAX_SECONDS = arg('max-seconds', 600);
const MAX_REQUESTS = arg('max-requests', 200);
const tapePath = process.argv[2] && !process.argv[2].startsWith('--') ? process.argv[2] : DEFAULT_TAPE;

function readKey(): string | null {
	try {
		const k = fs.readFileSync(KEY_FILE, 'utf8').trim();
		return k || null;
	} catch {
		return null;
	}
}

async function bootPython(): Promise<{ py: PyodideLike; prompt: PromptBuilder; rt: Record<string, (...a: unknown[]) => string> }> {
	const { files } = JSON.parse(fs.readFileSync(path.join(CORE_PY, 'manifest.json'), 'utf8')) as { files: string[] };
	const py = (await loadPyodide({ indexURL: `${PYODIDE_DIR}/`, stdout: () => {} })) as unknown as PyodideLike;
	for (const rel of files) {
		const dst = `/core-py/${rel}`;
		py.FS.mkdirTree(dst.slice(0, dst.lastIndexOf('/')));
		py.FS.writeFile(dst, new Uint8Array(await fs.promises.readFile(path.join(CORE_PY, rel))));
	}
	py.runPython(
		'import sys\n' +
			'if "/core-py" not in sys.path: sys.path.insert(0, "/core-py")\n' +
			'import rt\n' +
			'build_request = rt.build_request\n' +
			'build_draft_request = rt.build_draft_request\n' +
			'draft_begin = rt.draft_begin\n' +
			'draft_accept = rt.draft_accept\n' +
			'draft_final = rt.draft_final\n' +
			'feed = rt.feed\n' +
			'tick = rt.tick\n' +
			'flush = rt.flush\n'
	);
	const g = (n: string): ((...a: unknown[]) => string) => py.globals.get(n) as (...a: unknown[]) => string;
	return {
		py,
		prompt: pyPromptFromPyodide(py),
		rt: { feed: g('feed'), tick: g('tick'), flush: g('flush') }
	};
}

/** 把 Python 核心包成 `Core`（与 `tests/session/local-core.ts` 同一条投影）。 */
function localCore(
	rt: Record<string, (...a: unknown[]) => string>,
	onSnap?: (snap: import('../src/lib/core/project.ts').Snapshot) => void
): { core: Core; views: () => ViewState | null } {
	let proj: ProjState = { ...EMPTY_PROJ };
	let last: ViewState | null = null;
	const cbs = new Set<(vs: ViewState) => void>();
	const emit = (next: { state: ProjState; view: ViewState }): void => {
		proj = next.state;
		last = next.view;
		for (const cb of [...cbs]) cb(next.view);
	};
	const snap = (json: string): void => {
		const parsed = JSON.parse(json) as import('../src/lib/core/project.ts').Snapshot;
		onSnap?.(parsed);
		emit(project(proj, parsed));
	};
	return {
		views: () => last,
		core: {
			ready: () => Promise.resolve(),
			feed: (raw) => snap(rt.feed(raw)),
			tick: (seq) => snap(rt.tick(seq)),
			flush: () => snap(rt.flush()),
			onView: (cb) => {
				cbs.add(cb);
				if (last) cb(last);
			},
			dispose: () => cbs.clear()
		}
	};
}

/**
 * 磁带 → 按 `_wall` 实时投喂：消息到点投，音频帧每 100 ms 投一次（与真链路同一个节拍）。
 *
 * 「说完」取的是**这句的音频结束时刻**（`Sealed.end`，磁带的音频秒 + 回放起点），
 * 不是「草稿最后一次变化」—— 后者只差十几毫秒，会把封口那 1 s 的等待漏掉。
 */
async function replay(
	msgs: TapeMessage[],
	core: Core,
	speechEndOf: (seg: number) => number | null,
	onSeal: (seg: number, en: string, tSpeechEnd: number | null, tSealed: number) => void
): Promise<void> {
	let i = 0;
	let seq = 0;
	const t0 = Date.now();
	const sealed = new Set<number>();
	core.onView((vs) => {
		for (const r of vs.rows) {
			if (r.kind !== 'final' || sealed.has(r.seg)) continue;
			sealed.add(r.seg);
			onSeal(r.seg, r.en, speechEndOf(r.seg), Date.now());
		}
	});
	const stop = Math.min(MAX_SECONDS, msgs.length ? msgs[msgs.length - 1].wall : 0);
	while (Date.now() - t0 < stop * 1000) {
		const now = (Date.now() - t0) / 1000;
		while (i < msgs.length && msgs[i].wall <= now) {
			core.feed(msgs[i].raw);
			i += 1;
		}
		seq += 1;
		core.tick(seq);
		await new Promise<void>((r) => setTimeout(r, 100));
	}
	core.flush();
}

/** 本机安装号（与桌面同一枚）；读不到才退到一次性随机值（那会占掉一个网页设备名额）。 */
function installId(): string {
	try {
		const id = fs.readFileSync(INSTALL_FILE, 'utf8').trim();
		if (/^[0-9a-f]{32}$/.test(id)) return id;
	} catch {
		/* 读不到：下面兜底 */
	}
	return Array.from({ length: 16 }, () => Math.floor(Math.random() * 256).toString(16).padStart(2, '0')).join('');
}

async function main(): Promise<number> {
	const key = readKey();
	if (!key) {
		console.log(`跳过：没有 ${KEY_FILE}（放 test1 通行码）。这一步会花钱，只用测试码。`);
		return 0;
	}
	if (!fs.existsSync(tapePath)) {
		console.log(`跳过：没有磁带 ${tapePath}`);
		return 0;
	}
	const lines = fs.readFileSync(tapePath, 'utf8').split('\n').filter((l) => l.trim());
	const msgs = parseTape(lines).filter((m) => m.wall <= MAX_SECONDS);
	const { py: _py, prompt, rt } = await bootPython();

	const headers = {
		Authorization: `Bearer ${key}`,
		'X-ClassLive-Install': installId(),
		'X-ClassLive-Version': 'web-w10',
		'X-ClassLive-Client': 'web',
		'X-ClassLive-Tz': 'Europe/Dublin'
		// 不带 `X-ClassLive-Call`：桌面 `cloud_translator` 走的是 `llm_client.headers(key)`
		// 不传调用类（= legacy）。流式请求在 Worker 里本来就按 `body.stream` 选上游
		// （handler.js 的 `chatUpstream`：`if (body?.stream) return "deepseek"`），加了也不改路由。
	};
	const samples: LatencySample[] = [];
	const sampleOf = new Map<number, LatencySample>();
	let requests = 0;
	const tAll0 = Date.now();
	let segCount = 0;
	/** 第 n 句的音频结束时刻（墙钟毫秒）；`sealed` 按到达顺序数，与 `Core` 的 seg 同一个序 */
	const audioEnd = new Map<number, number>();
	const tStart = Date.now();
	const { core } = localCore(rt, (snap) => {
		for (const line of snap.sealed) {
			segCount += 1;
			audioEnd.set(segCount, tStart + line.end * 1000);
		}
	});
	const tr = new Translator({
		prompt,
		endpoint: () => ({ url: `${API}/v1/chat/completions`, headers }),
		chat: (opts) => {
			requests += 1;
			return streamChat(opts);
		},
		onFinal: (ev) => {
			const s = sampleOf.get(ev.seg);
			if (!s) return;
			if (ev.zh && s.tZhFirst === null) s.tZhFirst = Date.now();
		},
		onDraft: () => {}
	});
	const seen = new Set<number>();

	await replay(msgs, core, (seg) => audioEnd.get(seg) ?? null, (seg, en, speechEnd, tSealed) => {
		if (seen.has(seg) || requests >= MAX_REQUESTS) return;
		seen.add(seg);
		const s: LatencySample = { tAudioEnd: speechEnd, tSealed, tZhFirst: null };
		samples.push(s);
		sampleOf.set(seg, s);
		void tr.final(seg, en);
	});
	tr.close();
	// 收尾：再等一会儿，让最后几句的译文回来（最多 15 s）
	const until = Date.now() + 15_000;
	while (Date.now() < until && samples.some((s) => s.tZhFirst === null)) {
		await new Promise<void>((r) => setTimeout(r, 200));
	}

	const rep = latencyOf(samples);
	const fmt = (x: number | null): string => (x === null ? '—' : (x / 1000).toFixed(2));
	console.log(`磁带：${tapePath}（前 ${Math.min(MAX_SECONDS, Math.round(msgs.at(-1)?.wall ?? 0))} s，${msgs.length} 条消息）`);
	console.log(`墙钟：${((Date.now() - tAll0) / 1000).toFixed(0)} s · 请求 ${requests} 次 · 出中文 ${rep.n} 句 · 失败 ${rep.failed}`);
	console.log(`说完 → 中文首字：中位 ${fmt(rep.p50)} s · p90 ${fmt(rep.p90)} s · 最大 ${fmt(rep.max)} s`);
	console.log(`拆分（中位）：段尾 → 封口 ${fmt(rep.sealP50)} s · 封口 → 中文首字 ${fmt(rep.zhP50)} s`);
	console.log(`桌面基线：中位 1.55 s · p90 2.0 s`);
	return 0;
}

main().then(
	(c) => process.exit(c),
	(e) => {
		console.error(e);
		process.exit(1);
	}
);
