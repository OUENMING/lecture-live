// 把「识别核心」用到的公共文件下到 static/vendor/：Pyodide 与 pdf.js，按源码里钉死的版本号。
//
// 对外：`node scripts/vendor-fetch.mjs [--force]`（`npm run vendor`，`prebuild` 自动跑）。已存在且
//       sha256 对得上的文件跳过；`static/vendor/manifest.json` 是这些字节的账本（校验 + 版本）。
// 为什么自托管：国内到 cdn.jsdelivr.net 时好时坏（pyodide.asm.wasm 有 Intermittent 记录），
//       识别核心下不来 = 整节课没字幕。同源优先、jsDelivr 兜底的判断在 `lib/core/pyodide-runtime.ts`。
// 为什么不进仓库：wasm 9.2 MB + stdlib 2.4 MB + pdf.js worker 1.4 MB，构建时拉。
// 版本号唯一来源是源码（`PYODIDE_VERSION` / `PDFJS_VERSION`），这里只读不算，不许两边各写一份。
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const VENDOR = join(ROOT, 'static', 'vendor');
const MANIFEST = join(VENDOR, 'manifest.json');

/** 从源码读一个 `export const NAME = '...'` 字面量（版本号的唯一来源）。 */
async function versionOf(rel, name) {
	const src = await readFile(join(ROOT, rel), 'utf8');
	const m = src.match(new RegExp(`export const ${name} = '([^']+)'`));
	if (!m) throw new Error(`${rel}: 找不到 ${name}`);
	return m[1];
}

/**
 * 真正会被加载的文件。判据是代码怎么拉的，不是包里有什么：
 * - `loadPyodide({indexURL})`（`lib/core/worker.ts` 等三处）→ pyodide.mjs，它自己再 import
 *   pyodide.asm.mjs 并取 python_stdlib.zip / pyodide-lock.json / pyodide.asm.wasm。
 * - pdf.js 在 `lib/prep/pdf.ts` 只 import `legacy/build/pdf.min.mjs` + `pdf.worker.min.mjs`。
 * - `loadPackage` / `micropip` 全仓库没有一处调用（Python 那 12 个 .py 只用标准库），
 *   所以不带任何 Pyodide 包 —— 加了只是多占带宽。
 * - `pyodide.js` 是老的非 ESM 入口，代码不用；留着是给「将来谁换成 script 标签」的余地，代价 19 KB。
 * - `pyodide.asm.js` 在 v314.0.8 已经不存在（CDN 404），老版本才有；OPTIONAL 里试一下，没有就跳过。
 */
const PYODIDE_FILES = [
	'pyodide.mjs',
	'pyodide.js',
	'pyodide.asm.mjs',
	'pyodide.asm.wasm',
	'python_stdlib.zip',
	'pyodide-lock.json'
];
const PYODIDE_OPTIONAL = ['pyodide.asm.js'];
const PDFJS_FILES = ['legacy/build/pdf.min.mjs', 'legacy/build/pdf.worker.min.mjs'];

const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');

async function readManifest() {
	try {
		return JSON.parse(await readFile(MANIFEST, 'utf8'));
	} catch {
		return {};
	}
}

/** 拉一个 URL，重试 3 次（jsDelivr 偶发 5xx / 断连，别为一次抖动重下整包）。 */
async function grab(url, optional) {
	let last = null;
	for (let i = 1; i <= 3; i++) {
		try {
			const res = await fetch(url, { redirect: 'follow' });
			if (res.status === 404 && optional) return null;
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			return Buffer.from(await res.arrayBuffer());
		} catch (e) {
			last = e;
			if (i < 3) await new Promise((r) => setTimeout(r, 500 * i));
		}
	}
	throw new Error(`${url}: ${last instanceof Error ? last.message : String(last)}`);
}

/**
 * 一份「集合」：base + 文件清单 → 写进真目录 + 记账。
 * 返回这一份的 manifest 段：版本、目录（相对 `static/`）、每个文件的 sha256、跳过了什么。
 */
async function fetchSet({ key, version, base, files, optional = [], force, prev }) {
	const rel = `${key}/${version}`;
	const dir = join(VENDOR, rel);
	const old = prev[key]?.version === version ? (prev[key].files ?? {}) : {};
	const hashes = {};
	const skipped = [];
	let fetched = 0;
	let bytes = 0;
	for (const file of files) {
		const isOpt = optional.includes(file);
		const dst = join(dir, file);
		const url = `${base}${file}`;
		if (!force && old[file]) {
			try {
				const have = await readFile(dst);
				if (sha256(have) === old[file]) {
					hashes[file] = old[file];
					console.log(`  = ${rel}/${file}（已存在）`);
					continue;
				}
			} catch {
				// 文件不在 / 读不了：照常下载
			}
		}
		const buf = await grab(url, isOpt);
		if (!buf) {
			skipped.push(file);
			console.log(`  - ${rel}/${file}（该版本没有，跳过）`);
			continue;
		}
		await mkdir(dirname(dst), { recursive: true });
		await writeFile(dst, buf);
		hashes[file] = sha256(buf);
		fetched += 1;
		bytes += buf.length;
		console.log(`  + ${rel}/${file}  ${(buf.length / 1048576).toFixed(1)} MB  ${hashes[file].slice(0, 12)}`);
	}
	console.log(`  ${rel}: 新下 ${fetched} 个（${(bytes / 1048576).toFixed(1)} MB），共 ${Object.keys(hashes).length} 个`);
	return { version, dir: rel, files: hashes, skipped };
}

const force = process.argv.includes('--force');
const prev = await readManifest();

console.log('vendor: Pyodide + pdf.js → static/vendor/');
const pyVersion = await versionOf('src/lib/core/pyodide-runtime.ts', 'PYODIDE_VERSION');
const pdfVersion = await versionOf('src/lib/prep/pdf.ts', 'PDFJS_VERSION');

const pyodide = await fetchSet({
	key: 'pyodide',
	version: pyVersion,
	base: `https://cdn.jsdelivr.net/pyodide/v${pyVersion}/full/`,
	files: PYODIDE_FILES,
	optional: PYODIDE_OPTIONAL,
	force,
	prev
});
const pdfjs = await fetchSet({
	key: 'pdfjs',
	version: pdfVersion,
	base: `https://cdn.jsdelivr.net/npm/pdfjs-dist@${pdfVersion}/`,
	files: PDFJS_FILES,
	force,
	prev
});

await mkdir(VENDOR, { recursive: true });
await writeFile(
	MANIFEST,
	`${JSON.stringify({ note: 'scripts/vendor-fetch.mjs 生成；sha256 是这些字节的账本，别手改', pyodide, pdfjs }, null, '\t')}\n`
);
console.log(`vendor: manifest → static/vendor/manifest.json`);
