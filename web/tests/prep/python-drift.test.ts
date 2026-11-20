// 开课准备那两份切片（`extract.py` / `prep.py`）的防漂移闸门。
//
// 为什么单独一条（`tests/core/drift.test.ts` 已经跑过同一个脚本）：那条只认「脚本退出 0」，
// 而这里的判据更严 —— 这两份切片是**网页版唯一的抽块与判据实现**，所以除了 `--check` 全绿，
// 还要钉住「切片里真的带着我们在用的那些名字」（名字被改名时脚本会报，但改名到别的文件后
// `--check` 可能仍然绿，比如整份文件被换成另一份也带这些名字的）。名字清单就是运行时调的那批。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';

const webRoot = path.resolve(import.meta.dirname, '../..');
const script = path.join(webRoot, 'scripts/sync-core.py');
const corePy = path.join(webRoot, 'static/core-py');
const desktop = process.env.CL_DESKTOP ?? path.resolve(webRoot, '../../lecture-live');

/** 运行时靠这些名字干活（`prep_rt.py` 调的就是这批）——缺一个就是线上 ImportError / NameError。 */
const REQUIRED: Record<string, string[]> = {
	'extract.py': ['BLOCK_KINDS', 'KIND_MEANING', 'Block', '_pptx_blocks', '_docx_blocks', '_slide_blocks', '_paragraphs'],
	'prep.py': ['SYS_EXTRACT', '_prompt', '_chunks', '_extract_user', '_verify', '_verify_folded', '_term_stats', '_rank', 'CHUNK_CHARS']
};

test('core-py 里的开课准备切片带着运行时用到的每个名字', () => {
	for (const [file, names] of Object.entries(REQUIRED)) {
		const src = fs.readFileSync(path.join(corePy, file), 'utf8');
		for (const n of names) {
			assert.match(src, new RegExp(`^(def |class )?${n}\\b`, 'm'), `${file} 里没有 ${n}`);
		}
	}
});

test('sync-core.py --check 零漂移（含两份新切片；桌面仓库不在本机则跳过）', (t) => {
	if (!fs.existsSync(path.join(desktop, 'prep.py'))) {
		t.skip(`没有桌面仓库 ${desktop}（课堂 / 桌面仓库不进这里）`);
		return;
	}
	const run = spawnSync('python3', ['-I', script, '--check', '--desktop', desktop], {
		cwd: webRoot,
		encoding: 'utf8'
	});
	assert.equal(run.status, 0, `sync-core.py --check 有漂移：\n${run.stdout}${run.stderr}`);
	assert.match(run.stdout, /prep\.py（\d+ 个名字）一致/);
	assert.match(run.stdout, /extract\.py（\d+ 个名字）一致/);
});
