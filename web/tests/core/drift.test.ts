// 核心拷贝的防漂移闸门：桌面仓库在本机时，`sync-core.py --check` 必须退出 0。
//
// 为什么值得一条测试：`static/core-py/` 是从桌面版拷来的，桌面改了不会通知这里，
// 走样了只在线上表现为字幕文本悄悄不同。
// 判据是脚本的退出码，不是文本 —— 整文件拷贝和切片（stream_core.py）都归它管：
// 切片现在由同一个脚本按名字从桌面 `stream_asr.py` 重新生成后逐字节比。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';

const webRoot = path.resolve(import.meta.dirname, '../..');
const script = path.join(webRoot, 'scripts/sync-core.py');
// 默认与脚本一致：桌面仓库 = worktree 根旁边的 ../lecture-live。
const desktop = process.env.CL_DESKTOP ?? path.resolve(webRoot, '../../lecture-live');

test('core-py 与桌面版一致（整文件拷贝 + stream_core.py 切片；桌面仓库不在本机则跳过）', (t) => {
	if (!fs.existsSync(path.join(desktop, 'stream_asr.py'))) {
		t.skip(`没有桌面仓库 ${desktop}（课堂 / 桌面仓库不进这里）`);
		return;
	}
	const run = spawnSync('python3', ['-I', script, '--check', '--desktop', desktop], {
		cwd: webRoot,
		encoding: 'utf8'
	});
	assert.equal(run.status, 0, `sync-core.py --check 有漂移：\n${run.stdout}${run.stderr}`);
});
