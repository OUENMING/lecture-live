// lib/telemetry/sender.ts —— 遥测的唯一网络出口（`POST /v1/web/telemetry`）与分批口径。
// 对外：wireSend(currentSession) → TelemetrySend；MAX_BATCH_EVENTS / MAX_BATCH_BYTES / cost。
// 为什么单拎：分批上限（服务端 ≤500 条 / ≤64 KB）与请求头（identity 那套 + install6）只在这里定义一次，
// buffer 只管「攒、丢、退避、什么时候发」。keepalive 只在页面隐藏时用，body 已由 buffer 压到 ≤56 KB。

import { apiBase } from '../api/http';
import { headers as identityHeaders, installId } from '../api/identity';
import type { TelemetryEvent, TelemetrySend } from './buffer';

/** 单批条数上限（服务端 500） */
export const MAX_BATCH_EVENTS = 500;
/** 单批字节上限（服务端 64 KB；留 8 KB 给 session_id / install6 / 头） */
export const MAX_BATCH_BYTES = 56 * 1024;
/** 上报的安装号前缀长度（只够「同一台机器的多节课」关联，不认人） */
export const INSTALL6 = 6;

const enc = typeof TextEncoder !== 'undefined' ? new TextEncoder() : null;
function utf8Len(str: string): number {
	return enc ? enc.encode(str).length : str.length * 3;
}

/** 一条事件在请求体里的大致字节数（含数组分隔符） */
export function cost(ev: TelemetryEvent): number {
	try {
		// 按 UTF-8 字节算（OCR A：原来数 UTF-16 码元，中文栈 / 消息实际体积是 2–3 倍，整批超 64 KB 被 413）
		return utf8Len(JSON.stringify(ev)) + 1;
	} catch {
		return 2;
	}
}

/** 默认发送端：15 s 封顶；`x-cl-telemetry: off` 原样回报，由 buffer 决定停发 */
export function wireSend(currentSession: () => string | null): TelemetrySend {
	return async (events, { keepalive }) => {
		const body: Record<string, unknown> = { events, install6: installId().slice(0, INSTALL6) };
		const sid = currentSession();
		if (sid) body.session_id = sid;
		const ctl = new AbortController();
		const timer = setTimeout(() => ctl.abort(), 15_000);
		try {
			const res = await fetch(apiBase() + '/v1/web/telemetry', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json', ...identityHeaders('web_telemetry') },
				body: JSON.stringify(body),
				signal: ctl.signal,
				...(keepalive ? { keepalive: true } : {})
			});
			// OCR A：5xx / 429 是临时的 → 抛错让 buffer 退避重发；其余 4xx 重发也没用（码不对 / 体积），当发过了
			if (res.status >= 500 || res.status === 429) throw new Error(`telemetry ${res.status}`);
			return { off: res.headers.get('x-cl-telemetry') === 'off' };
		} finally {
			clearTimeout(timer);
		}
	};
}
