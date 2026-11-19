// lib/api/identity.ts —— 「我是谁」：安装号、通行码、发给 Worker 的请求头。纯逻辑，不碰网络/界面。
// 对外：installId() · passcode.get/set · isPasscodeShape(code) · headers(call?) · timeZone()。
// 为什么单独一层：安装号与通行码是 Worker 认人（设备名额、日额度、记账）的唯一依据，
// 而隐私窗口里 localStorage 会抛异常 —— 这些事只在这里收口，别的模块不自己读 storage。
// 形状口径一律抄服务端（passes-core 的 CODE_RE、handler 的 INSTALL_OK / clientTz），测试钉住两边一致。

/** 通行码留在 localStorage 的键（与 `lib/env` 的老键同名，迁过来后老用户不用重输） */
export const PASS_KEY = 'cl.pass';

/** 安装号（一台浏览器一枚 32 位 hex） */
const INSTALL_KEY = 'cl.install';

/** Worker 认的网页版标记（`handler.isWeb`：只认这一个字面量） */
export const CLIENT_NAME = 'web';

/** 打包时由 vite define 注入（`vite.config.ts` 读 package.json version）；测试 / 未注入时退回 `web-dev` */
declare const __CL_VERSION__: string | undefined;

/** 与 `passes-core.js` 的 CODE_RE 同一条正则（测试直接 import 服务端那份比对） */
export const PASSCODE_RE = /^cl-[a-z0-9]{1,16}-[0-9a-f]{16}$/;

/** 与 `handler.js` 的 INSTALL_OK / `passes-core.js` 的 INSTALL_RE 同一条 */
const INSTALL_RE = /^[0-9a-f]{32}$/;

/** 与 `handler.js` 的 `clientTz` 同一条（IANA `A…/B…`；`UTC` 这种单段名服务端不收） */
const TZ_RE = /^[A-Za-z_]+(\/[A-Za-z0-9_+-]+){1,2}$/;

const version = (): string => (typeof __CL_VERSION__ === 'string' ? __CL_VERSION__ : 'web-dev');

function randomHex32(): string {
	const c = globalThis.crypto;
	if (c && typeof c.randomUUID === 'function') return c.randomUUID().replace(/-/g, '');
	// 没有 randomUUID 的浏览器（Chromium 92 之前）也得能开课：服务端只认 32 位 hex。
	const b = new Uint8Array(16);
	if (c && typeof c.getRandomValues === 'function') c.getRandomValues(b);
	else for (let i = 0; i < b.length; i++) b[i] = Math.floor(Math.random() * 256);
	return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
}

let fallbackId = '';

/**
 * 这台浏览器的安装号（32 位 hex，形状与桌面版 `final_asr.install_id()` 一致）。
 * 存储能用就以存储为准；存不住（隐私窗口 / 禁用存储 / 写进去读不回来）就用同一枚进程内随机值，
 * 不挡开课（同 `final_asr.install_id` 的兜底）。
 */
export function installId(): string {
	let kept = '';
	try {
		kept = localStorage.getItem(INSTALL_KEY) || '';
	} catch {
		/* 读不到存储：下面走兜底 */
	}
	if (INSTALL_RE.test(kept)) return kept;
	// 存储里没有可用的号：现生成一枚写进去；读得回来才算数，写不进去才用进程内那一枚
	const fresh = randomHex32();
	try {
		localStorage.setItem(INSTALL_KEY, fresh);
		if (localStorage.getItem(INSTALL_KEY) === fresh) return fresh;
	} catch {
		/* 写不进去：下面兜底 */
	}
	if (fallbackId) return fallbackId;
	fallbackId = fresh;
	return fallbackId;
}

/** 老键由 `lib/env` 的 write() 写成 JSON（`"cl-…"` 带引号）：迁过来后仍要读得出来，老用户不用重输 */
function decodeStored(raw: string): string {
	if (!raw.startsWith('"')) return raw;
	try {
		const v = JSON.parse(raw) as unknown;
		return typeof v === 'string' ? v : '';
	} catch {
		return '';
	}
}

/** 通行码的读写（`lib/env` 的 hasPass / savePass 只是它的别名） */
export const passcode = {
	get(): string {
		try {
			return decodeStored(localStorage.getItem(PASS_KEY) || '');
		} catch {
			return '';
		}
	},
	set(code: string): void {
		try {
			localStorage.setItem(PASS_KEY, code.trim());
		} catch {
			/* 禁用存储：这一程本来就在内存里，不记也能用 */
		}
	}
};

/** 形状对不对（不联网；能不能用要问 `checkPasscode`） */
export const isPasscodeShape = (code: string | null | undefined): boolean =>
	PASSCODE_RE.test(code || '');

/** 本机 IANA 时区；拿不到 / 不是 `A…/B…` 形状就返回空串（不发假的，让服务端按 IP 猜） */
export function timeZone(): string {
	try {
		const tz = (Intl.DateTimeFormat().resolvedOptions().timeZone || '').slice(0, 40);
		return TZ_RE.test(tz) ? tz : '';
	} catch {
		return '';
	}
}

/**
 * 发给 Worker 的请求头（`llm_client.headers()` 的网页版）。`call` 是调用类，进 `X-ClassLive-Call`。
 * 没存通行码就不带 Authorization —— 调用方自己会先拒绝。
 */
export function headers(call?: string): Record<string, string> {
	const h: Record<string, string> = {
		'X-ClassLive-Install': installId(),
		'X-ClassLive-Version': version().slice(0, 16),
		'X-ClassLive-Client': CLIENT_NAME
	};
	const code = passcode.get();
	if (code) h.Authorization = `Bearer ${code}`;
	const tz = timeZone();
	if (tz) h['X-ClassLive-Tz'] = tz;
	if (call) h['X-ClassLive-Call'] = call.slice(0, 20);
	return h;
}
