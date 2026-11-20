// `lib/api/identity.ts` 的判据：安装号稳定 / 存储炸了也能用、通行码形状与服务端同一条正则、请求头齐全且格式对。
// 为什么钉形状：这些头是 Worker 认人（设备名额、日额度、时区面板）的唯一依据；
// 形状错了只会得到 400 bad install id 或静默忽略，界面上看不出所以然。
// 服务端那条正则从**源码文本**里读（不 import：server/asr-worker 另有会话在改，
// 它自己的依赖坏了不该拖红这里的判据）。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { beforeEach, test } from 'node:test';
import {
	CLIENT_NAME,
	PASSCODE_RE,
	headers,
	installId,
	isPasscodeShape,
	passcode,
	timeZone
} from '../../src/lib/api/identity.ts';

/** 假 localStorage（Node 没有）；`throwOn` 里的操作故意抛异常，模拟隐私窗口 / 禁用存储 */
class FakeStorage {
	readonly map = new Map<string, string>();
	throwOn = new Set<string>();
	getItem(k: string): string | null {
		if (this.throwOn.has('get')) throw new Error('SecurityError');
		return this.map.get(k) ?? null;
	}
	setItem(k: string, v: string): void {
		if (this.throwOn.has('set')) throw new Error('SecurityError');
		this.map.set(k, String(v));
	}
}

let store: FakeStorage;

/** 服务端那两条形状判据（从源码文本里取，口径不靠手抄） */
const serverTz = /^[A-Za-z_]+(\/[A-Za-z0-9_+-]+){1,2}$/;

const workerSrc = path.resolve(import.meta.dirname, '../../../server/asr-worker/src');

/** 从服务端源码里取一条具名正则字面量（`export const CODE_RE = /…/;`） */
function serverRe(file: string, name: string): RegExp {
	const src = fs.readFileSync(path.join(workerSrc, file), 'utf8');
	const m = src.match(new RegExp(`${name}\\s*=\\s*(/.*?/[a-z]*)`));
	assert.ok(m, `${file} 里找不到 ${name}`);
	const [, body, flags] = m[1].match(/^\/(.*)\/([a-z]*)$/)!;
	return new RegExp(body, flags);
}

beforeEach(() => {
	store = new FakeStorage();
	(globalThis as unknown as { localStorage: FakeStorage }).localStorage = store;
});

test('安装号是 32 位 hex，且同一页里稳定', () => {
	const a = installId();
	assert.equal(a.length, 32);
	assert.match(a, /^[0-9a-f]{32}$/);
	assert.equal(serverRe('handler.js', 'INSTALL_OK').test(a), true); // 服务端 device 计数只认这个形状
	assert.equal(installId(), a);
	assert.equal(store.map.get('cl.install'), a);
});

test('localStorage 抛异常也能用：给 32 位 hex，同一页里稳定', () => {
	store.throwOn = new Set(['get', 'set']);
	const id = installId();
	assert.match(id, /^[0-9a-f]{32}$/);
	assert.equal(installId(), id); // 进程中那一枚
});

test('存储写不进去时（setItem 静默失败）用进程内那一枚，不拿丢掉的号冒充', () => {
	// 写丢了就每调一次生成新的会换号 —— 那会让 Worker 把同一台机器数成新设备、白占名额
	store.setItem = () => {};
	const id = installId();
	assert.match(id, /^[0-9a-f]{32}$/);
	assert.equal(installId(), id);
});

test('存下了就以存储为准（存储里的号变了就跟着变）', () => {
	const first = installId();
	assert.equal(store.map.get('cl.install'), first);
	store.map.set('cl.install', 'a'.repeat(32));
	assert.equal(installId(), 'a'.repeat(32)); // 不攥着旧的那枚
});

test('存储里的号形状不对就重新生成一枚并写回', () => {
	store.map.set('cl.install', 'not-a-hex-id');
	const id = installId();
	assert.match(id, /^[0-9a-f]{32}$/);
	assert.equal(store.map.get('cl.install'), id);
	assert.equal(installId(), id);
});

test('通行码：形状判定与服务端 CODE_RE 是同一条正则', () => {
	const CODE_RE = serverRe('passes-core.js', 'CODE_RE');
	assert.equal(PASSCODE_RE.source, CODE_RE.source);
	assert.equal(PASSCODE_RE.flags, CODE_RE.flags);
	for (const code of ['cl-owen-0123456789abcdef', 'cl-a-0000000000000000', 'cl-name9-ffffffffffffffff']) {
		assert.equal(isPasscodeShape(code), true);
		assert.equal(CODE_RE.test(code), true);
		assert.equal(PASSCODE_RE.test(code), CODE_RE.test(code));
	}
	for (const bad of [
		'cl-Owen-0123456789abcdef', // 名字段只收小写
		'cl-owen-0123456789ABCDEF', // 随机段只收小写十六进制
		'cl-owen-0123456789abcde', // 15 位
		'cl--0123456789abcdef',
		'owen-0123456789abcdef',
		'cl-owen-name-0123456789abcdef',
		'CL-owen-0123456789abcdef',
		''
	]) {
		assert.equal(isPasscodeShape(bad), false);
		assert.equal(CODE_RE.test(bad), false);
	}
});

test('通行码存取沿用老键 cl.pass；存储炸了读出空串、写入不抛', () => {
	store.map.set('cl.pass', 'cl-owen-0123456789abcdef');
	assert.equal(passcode.get(), 'cl-owen-0123456789abcdef');
	passcode.set(' cl-a-0000000000000000 '); // 去掉首尾空白
	assert.equal(store.map.get('cl.pass'), 'cl-a-0000000000000000');
	store.throwOn = new Set(['get', 'set']);
	assert.equal(passcode.get(), '');
	assert.doesNotThrow(() => passcode.set('cl-a-0000000000000000'));
});

test('老版本存的 JSON 串（env.write 写的带引号值）还读得出来', () => {
	store.map.set('cl.pass', JSON.stringify('cl-owen-0123456789abcdef'));
	assert.equal(passcode.get(), 'cl-owen-0123456789abcdef');
});

test('请求头：装号 / 版本 / 客户端 / 时区齐全，格式与服务端判据对得上', () => {
	passcode.set('cl-owen-0123456789abcdef');
	const h = headers('note');
	assert.match(h['X-ClassLive-Install'], /^[0-9a-f]{32}$/);
	assert.equal(h['X-ClassLive-Client'], CLIENT_NAME);
	assert.equal(h['X-ClassLive-Client'], 'web');
	assert.ok(h['X-ClassLive-Version'].length > 0 && h['X-ClassLive-Version'].length <= 16);
	assert.equal(h.Authorization, 'Bearer cl-owen-0123456789abcdef');
	assert.equal(h['X-ClassLive-Call'], 'note');
	// 时区：要么不发，要么是服务端 clientTz 认的 IANA 形状
	const tz = h['X-ClassLive-Tz'];
	if (tz !== undefined) assert.equal(serverTz.test(tz), true);
	assert.equal(timeZone() === '' ? tz === undefined : tz === timeZone(), true);
});

test('没有通行码就不带 Authorization；不传 call 就不带 X-ClassLive-Call', () => {
	const h = headers();
	assert.equal('Authorization' in h, false);
	assert.equal('X-ClassLive-Call' in h, false);
	assert.equal(h['X-ClassLive-Install'], installId());
});
