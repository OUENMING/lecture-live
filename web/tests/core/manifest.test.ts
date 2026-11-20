// `static/core-py/manifest.json` 与磁盘上的文件必须一一对应。
//
// 为什么值得一条测试：Worker 只能读 manifest（浏览器没有目录列表），
// 少列一个 .py 不会在构建时炸，只会在运行时 `ImportError` —— 那是线上才发现的错。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';

const root = path.resolve(import.meta.dirname, '../../static/core-py');

function walk(dir: string, prefix = ''): string[] {
	const out: string[] = [];
	for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
		const rel = prefix ? `${prefix}/${e.name}` : e.name;
		if (e.isDirectory()) out.push(...walk(path.join(dir, e.name), rel));
		else out.push(rel);
	}
	return out.sort();
}

const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8')) as { files: string[] };

test('manifest 列全了 core-py 下的每个文件（除 manifest 自己）', () => {
	const listed = new Set(manifest.files);
	const onDisk = walk(root).filter((f) => f !== 'manifest.json');
	assert.deepEqual(
		onDisk.filter((f) => !listed.has(f)),
		[],
		'磁盘上有、manifest 没列的文件（Worker 不会 stage，import 会炸）'
	);
	assert.deepEqual(
		manifest.files.filter((f) => !onDisk.includes(f)),
		[],
		'manifest 列了、磁盘上没有的文件（fetch 会 404）'
	);
});

test('S1 的 8 个模块一个不少', () => {
	const mods = [
		'caption_flow.py',
		'draft_policy.py',
		'draft_stable.py',
		'draft_translate.py',
		'fragment_merge.py',
		'linebreak.py',
		'stream_core.py',
		'text_diff.py'
	];
	for (const m of mods) assert.ok(manifest.files.includes(m), `manifest 少了 ${m}`);
	assert.ok(manifest.files.includes('rt.py'), 'manifest 少了网页版胶水 rt.py');
});

// 曾经有一条「与 S1 穿刺目录的拷贝逐字节相同」—— 删了：S1 是当时的快照，桌面每改一个字它就红，
// 而它想防的事（拷贝走样）现在由 drift.test.ts 的 `sync-core.py --check` 盯着，
// 且那条连整文件拷贝和 stream_core.py 切片都盖了。
