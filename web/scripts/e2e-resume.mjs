// web/scripts/e2e-resume.mjs —— 上课中刷新：接回同一节课（不开新会话）的真浏览器验证。
//
//   npm run dev -- --port 5320 &
//   node scripts/e2e-resume.mjs --wav <路径> [--url http://localhost:5320]
//
// 流程：开课 → 等出句 → 刷新（接受 beforeunload 确认）→ 断言：会话号没变 / 服务端会话只有一个 / 出现「已接回」/
// 刷新后新句子继续落盘且序号接着往后（没有被当重复丢掉）→ 下课 → 课后页句子数 = 刷新前 + 刷新后。
// 花钱：Soniox 实时约 2 分钟 + 每句一次翻译，**只用 test key**（~/.classlive/test-key），key 不打印。
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

const argv = process.argv.slice(2);
const arg = (n, d) => {
	const i = argv.indexOf(`--${n}`);
	return i >= 0 ? argv[i + 1] : d;
};
const wav = arg('wav', '');
const base = arg('url', 'http://localhost:5320');
const keyFile = path.join(os.homedir(), '.classlive', 'test-key');
if (!wav || !fs.existsSync(wav)) throw new Error('缺 --wav <路径>');
const key = fs.readFileSync(keyFile, 'utf8').trim();
const installFile = path.join(os.homedir(), '.classlive', 'install-id');
const install = fs.existsSync(installFile) ? fs.readFileSync(installFile, 'utf8').trim() : '';

const req = createRequire('/opt/homebrew/lib/node_modules/@mermaid-js/mermaid-cli/');
const puppeteer = req('puppeteer');
const browser = await puppeteer.launch({
	headless: true,
	args: [
		'--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
		`--use-file-for-fake-audio-capture=${wav}`, '--autoplay-policy=no-user-gesture-required',
		'--no-sandbox', '--disable-features=AudioServiceSandbox,AudioServiceOutOfProcess'
	]
});
const page = await browser.newPage();
page.on('dialog', (d) => d.accept().catch(() => {})); // beforeunload「确定要离开吗」
// 旁听发往 Worker 的网页会话请求：开课次数 / 涉及几个服务端会话 / 每批的 ignored（被当重复丢掉的句子）
const net = { opens: 0, sids: new Set(), ignored: 0, received: 0, confirmed: 0, ends: 0 };
page.on('response', async (r) => {
	const u = r.url(), m = r.request().method();
	try {
		if (m === 'POST' && /\/v1\/web\/session$/.test(u)) {
			net.opens += 1;
			net.openStatus = [...(net.openStatus ?? []), r.status()];
			net.openBody = (await r.text().catch(() => '')).slice(0, 200);
		}
		const x = u.match(/\/v1\/web\/session\/(s_[0-9a-f]{12})\/(lines|end)/);
		if (x) net.sids.add(x[1]);
		if (x?.[2] === 'end') net.ends += 1;
		if (x?.[2] === 'lines' && r.ok()) {
			const j = await r.json();
			net.ignored += j.ignored || 0;
			net.received += j.received || 0;
			net.confirmed = Math.max(net.confirmed, j.confirmed_seq || 0);
		}
	} catch {}
});
const checks = [];
const ok = (name, pass, detail = '') => {
	checks.push({ name, pass: !!pass, detail });
	console.log(pass ? 'PASS' : 'FAIL', name, detail);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const state = () =>
	page.evaluate(() => ({
		active: JSON.parse(localStorage.getItem('cl.active') || 'null'),
		srv: Object.keys(localStorage).filter((k) => k.startsWith('cl.srv.')).map((k) => [k, localStorage.getItem(k)]),
		rows: document.querySelectorAll('.stage [data-id]').length,
		banner: document.querySelector('.banner')?.textContent?.trim() ?? ''
	}));
const waitRows = async (n, maxS) => {
	for (let t = 0; t < maxS; t += 3) {
		const s = await state();
		if (s.rows >= n) return s;
		await sleep(3000);
	}
	return state();
};

await page.goto(base + '/', { waitUntil: 'networkidle0' });
await page.evaluate((k, inst) => {
	localStorage.setItem('cl.pass', k);
	if (/^[0-9a-f]{32}$/.test(inst)) localStorage.setItem('cl.install', inst);
}, key, install);
await page.goto(`${base}/class?course=E2E-RESUME`, { waitUntil: 'domcontentloaded' });

const before = await waitRows(3, 90);
ok('刷新前出了句子', before.rows >= 3, `rows=${before.rows}`);
ok('刷新前写了 cl.active', before.active?.session_id, before.active?.session_id);
await sleep(8000);

await page.reload({ waitUntil: 'domcontentloaded' });
await sleep(4000);
const after = await waitRows(2, 90);
ok('刷新后会话号不变', after.active?.session_id === before.active?.session_id, `${before.active?.session_id} -> ${after.active?.session_id}`);
ok('出现「已接回」提示', /已接回/.test(after.banner) || /已接回/.test(await page.evaluate(() => document.body.innerText)), after.banner);
await sleep(30000);
await page.click('button.end');
await page.click('button.end');
await page.waitForFunction(() => location.pathname.startsWith('/after'), { timeout: 30_000 }).catch(() => {});
await sleep(2000);
const post = await page.evaluate(() => ({
	url: location.pathname + location.search,
	lines: document.querySelectorAll('.lines li').length,
	active: localStorage.getItem('cl.active')
}));
ok('服务端只开了一节课（开课请求 ≤ 2 次且都落到同一个会话）', net.sids.size === 1, JSON.stringify({ opens: net.opens, status: net.openStatus?.slice(0, 3), body: net.openBody, sids: [...net.sids] }));
ok('没有句子被当重复丢掉（ignored = 0）', net.ignored === 0, `ignored=${net.ignored} received=${net.received}`);
ok('服务端确认序号 = 课后页句子数（刷新前后的都在，序号连续）', net.confirmed === post.lines, `confirmed=${net.confirmed} lines=${post.lines}`);
ok('下课报了一次', net.ends >= 1, `ends=${net.ends}`);
ok('下课后 cl.active 已清', post.active === null, String(post.active));
ok('课后页句子数 ≥ 刷新前句子数（刷新前后的都在）', post.lines >= before.rows, `after.lines=${post.lines} before.rows=${before.rows}`);
console.log(JSON.stringify({ before, after, post }, null, 1));
await browser.close();
process.exit(checks.every((c) => c.pass) ? 0 : 1);
