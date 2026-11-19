// web/scripts/e2e-live.mjs —— 真链路端到端：一份 wav 当麦克风 → 网页上课页 → Soniox → 核心 → 翻译 → 本机落盘 → 课后页。
//
//   npm run dev -- --port 5320 &            # 先起 dev（/api 代理到线上 Worker）
//   node scripts/e2e-live.mjs --wav <路径> [--secs 180] [--url http://localhost:5320]
//
// 花钱：Soniox 实时 secs 秒 + 每句一次翻译，**只用 test key**（~/.classlive/test-key），key 不打印。
// Chrome 的 fake-audio 开关把 wav 当成麦克风（循环播放），走的是和真麦一模一样的 getUserMedia → worklet 路径。
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
const secs = Number(arg('secs', '180'));
const base = arg('url', 'http://localhost:5320');
const out = arg('out', path.join(os.tmpdir(), `cl-e2e-${Date.now()}`));
const keyFile = path.join(os.homedir(), '.classlive', 'test-key');
if (!wav || !fs.existsSync(wav)) throw new Error('缺 --wav <路径>');
if (!fs.existsSync(keyFile)) throw new Error(`没有 ${keyFile}`);
const key = fs.readFileSync(keyFile, 'utf8').trim();
// 固定用本机桌面版的安装号：每开一个新浏览器配置就是一台新设备，test 码的设备名额会被占满（实测 devices）
const installFile = path.join(os.homedir(), '.classlive', 'install-id');
const install = fs.existsSync(installFile) ? fs.readFileSync(installFile, 'utf8').trim() : '';
fs.mkdirSync(out, { recursive: true });

const req = createRequire('/opt/homebrew/lib/node_modules/@mermaid-js/mermaid-cli/');
const puppeteer = req('puppeteer');
const browser = await puppeteer.launch({
	headless: true,
	args: [
		'--use-fake-ui-for-media-stream',
		'--use-fake-device-for-media-stream',
		`--use-file-for-fake-audio-capture=${wav}`,
		'--autoplay-policy=no-user-gesture-required',
		// 不加这两条 macOS 上的音频服务沙箱读不了 wav，假麦克风全是 0（实测 rms=0 → 加了 0.036）
		'--no-sandbox',
		'--disable-features=AudioServiceSandbox,AudioServiceOutOfProcess'
	]
});
const page = await browser.newPage();
const logs = [];
page.on('console', (m) => logs.push(`[${m.type()}] ${m.text()}`));
page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`));
let chatReq = 0;
page.on('request', (r) => {
	if (r.url().includes('/v1/chat/completions')) chatReq += 1;
});

await page.goto(base + '/', { waitUntil: 'networkidle0' });
await page.evaluate(
	(k, inst) => {
		localStorage.setItem('cl.pass', k);
		if (/^[0-9a-f]{32}$/.test(inst)) localStorage.setItem('cl.install', inst);
	},
	key,
	install
);
const passKey = await page.evaluate(() => Object.keys(localStorage));
console.log('localStorage keys:', passKey.join(','));

const t0 = Date.now();
await page.goto(`${base}/class?course=E2E-TEST`, { waitUntil: 'domcontentloaded' });

const snap = () =>
	page.evaluate(() => ({
		status: document.querySelector('.status')?.textContent?.trim() ?? '',
		rows: document.querySelectorAll('.stage [data-id]').length,
		zhRows: [...document.querySelectorAll('.stage [data-id]')].filter((el) => /[一-鿿]/.test(el.textContent ?? '')).length,
		empty: document.querySelector('.empty')?.textContent?.replace(/\s+/g, ' ').trim() ?? '',
		banner: document.querySelector('.banner')?.textContent?.trim() ?? ''
	}));

let firstRowAt = null;
while ((Date.now() - t0) / 1000 < secs) {
	await new Promise((r) => setTimeout(r, 10_000));
	const s = await snap();
	if (s.rows > 0 && firstRowAt === null) firstRowAt = (Date.now() - t0) / 1000;
	console.log(`${Math.round((Date.now() - t0) / 1000)}s`, JSON.stringify(s), `chat=${chatReq}`);
	if (/没开成|出错了/.test(s.status)) break;
}

const final = await snap();
const latency = await page.evaluate(() => globalThis.__clLive?.session.latency() ?? null);
// 下课：点两次
await page.click('button.end');
await page.click('button.end');
await page.waitForFunction(() => location.pathname.startsWith('/after'), { timeout: 30_000 }).catch(() => {});
await new Promise((r) => setTimeout(r, 1500));
const after = await page.evaluate(() => ({
	url: location.pathname + location.search,
	lines: document.querySelectorAll('.lines li').length,
	untranslated: [...document.querySelectorAll('.lines .zh')].filter((el) => el.textContent === '（这句没译出）').length,
	sample: [...document.querySelectorAll('.lines li')].slice(0, 3).map((li) => li.textContent?.replace(/\s+/g, ' ').trim())
}));
await page.screenshot({ path: path.join(out, 'after.png'), fullPage: false });
fs.writeFileSync(path.join(out, 'console.log'), logs.join('\n').replaceAll(key, '<KEY>'));
console.log(JSON.stringify({ firstRowAt, final, latency, after, chatReq, out }, null, 1));
await browser.close();
