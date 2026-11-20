// lib/asr —— Soniox 连接、重连、临时 key。M1 实现。
// 起点：s2 的 main.js（断线指数退避重连 + 断线期间缓冲补发、15 s keepalive，S2b 自测 26/26）。

export type AsrConn = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed' | 'failed';

export interface AsrClient {
	/** 帧进 */
	send(pcm: ArrayBuffer): void;
	/** 原始消息出（原样交给 core，不在 JS 里解析 token） */
	onMessage(cb: (raw: string) => void): void;
	readonly state: AsrConn;
	close(): void;
}
