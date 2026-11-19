// lib/api/soniox-key.ts —— 领 Soniox 临时 key（`/v1/soniox/key`）与「这个码能不能用」（`checkPasscode`）。
// 对外：getSonioxKey(region?, opts?) · createKeySource(opts?) · checkPasscode(code, opts?)。
// 为什么在网页版领 key：主 key 只留在 Worker 的 secret 里；临时 key 是 single_use，
// 所以每次开课 / 重连都得重新领一把（`lib/asr/soniox.ts` 的 `getKey` 注入点就是它）。
// 请求体抄桌面版 `stream_asr.py:700-740`：只有 `{region}`，没有 region 就发空对象。

import { ApiError, request, type RequestOptions } from './http';
import { isPasscodeShape } from './identity';

/** Worker 回的 key（字段名与桌面版 `_KeyClient.request_key` 收到的同一个） */
export interface SonioxKey {
	api_key: string;
	ws_url: string;
	region: string;
	/** 能用来「开新流」的秒数（Worker 侧 60 s）；给界面做提示用 */
	expires_in_seconds: number;
	/** 单次会话上限（秒） */
	max_session_s: number;
}

/** 领一把 key。`region` 只在探测后改道时给（不给 = Worker 按国家 / 大洲挑）。 */
export async function getSonioxKey(
	region?: string,
	opts: RequestOptions & { purpose?: 'probe' } = {}
): Promise<SonioxKey> {
	const body: Record<string, string> = {};
	const r = (region || '').trim().toLowerCase();
	if (r) body.region = r;
	if (opts.purpose) body.purpose = opts.purpose;
	const js = await request<Partial<SonioxKey>>('/v1/soniox/key', body, opts);
	if (!js.api_key) throw new Error('领 key 的回包里没有 api_key');   // 服务端契约破了就是破，不装成功
	return {
		api_key: js.api_key,
		ws_url: js.ws_url ?? '',
		region: js.region ?? '',
		expires_in_seconds: js.expires_in_seconds ?? 0,
		max_session_s: js.max_session_s ?? 0
	};
}

/**
 * `AsrClient` 要的那个 `getKey`（只回 key 本身）。每次连（含重连）调一次。
 * 连之前必须已经有通行码 —— 调用方（界面）负责让用户先输。
 */
export function createKeySource(opts: RequestOptions = {}): () => Promise<string> {
	return async () => (await getSonioxKey(undefined, opts)).api_key;
}

/**
 * 这个码能不能用：先看形状（不联网、不花任何钱），再领一次临时 key 走真正的查码闸。
 * 为什么用领 key：它查的是 `kind="rt"` 的额度与设备名额，且**不进模型 / 不调对话接口**（一分钱不花）。
 * 不写存储 —— 存不存、存哪由调用方决定（`identity.passcode.set`）。
 */
export async function checkPasscode(code: string, opts: RequestOptions = {}): Promise<void> {
	const c = code.trim();
	if (!isPasscodeShape(c)) {
		throw new ApiError(
			'passcode',
			'shape',
			'通行码格式不对 —— 应该是 cl- 开头、像 cl-owen-0123456789abcdef 这样'
		);
	}
	await getSonioxKey(undefined, { ...opts, code: c });
}
