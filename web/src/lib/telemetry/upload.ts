// lib/telemetry/upload.ts —— 录音段上传的唯一网络出口（`PUT /v1/web/audio/:session/:n`）。
// 对外：putChunk(session, n, blob, mime) → Promise<void>；不是 2xx 就抛，调用方决定留着还是重试。
// 为什么单拎：recorder 与 resumePending（页面重开补传）必须走同一条路径 —— 头、编码、优先级只有一处定义。
// 低优先级：`priority: 'low'` 让浏览器先顾上课的实时流（不支持的浏览器会忽略这个字段，不是错）。

import { apiBase } from '../api/http';
import { headers } from '../api/identity';

export async function putChunk(session: string, n: number, blob: Blob, mime: string): Promise<void> {
	const init: RequestInit & { priority?: string } = {
		method: 'PUT',
		headers: { 'Content-Type': mime || 'application/octet-stream', ...headers('web_audio') },
		body: blob,
		priority: 'low'
	};
	const url = `${apiBase()}/v1/web/audio/${encodeURIComponent(session)}/${n}`;
	const res = await fetch(url, init);
	if (!res.ok) throw new Error(`audio ${res.status}`);
}
