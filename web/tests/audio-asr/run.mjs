/* web/tests/audio-asr/run.mjs —— 一次跑完 M1-a 的离线自测（无浏览器 / 无 key / 不连网）
 *
 * 跑：npm run test:audio-asr   （= node tests/audio-asr/run.mjs）
 * 单项：node tests/audio-asr/worklet.test.mjs / soniox.test.mjs / mic.test.mjs
 * 退出码 0 = 全绿；1 = 有 ❌ 或抛错。
 */
import { makeReport } from './lib.mjs';
import { run as worklet } from './worklet.test.mjs';
import { run as soniox } from './soniox.test.mjs';
import { run as mic } from './mic.test.mjs';

const suites = [
	['worklet', worklet],
	['soniox', soniox],
	['mic', mic],
];

let failed = 0;
for (const [name, run] of suites) {
	console.log(`\n── ${name} ──`);
	try {
		const ok = await run(makeReport());
		if (!ok) failed += 1;
	} catch (e) {
		failed += 1;
		console.error(`❌ ${name} 抛错：${e && e.stack ? e.stack : e}`);
	}
}

console.log(failed ? `\n❌ ${failed} 组没通过` : '\n✅ 全部通过');
process.exitCode = failed ? 1 : 0;
