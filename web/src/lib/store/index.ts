// lib/store —— 逐句落盘的接口（M3-a）。实现：idb.ts（真 IndexedDB）、memory.ts（兜底/测试）、sync.ts（同步队列）。
// 规矩：定稿句先写本机再同步到 Worker；每句带 seq，重复提交服务器按 (session_id, seq) 幂等。
// 服务端的 session 才是权威，本机 sessions 只记课程 / 起止 / 已确认序号，给界面和课后补传用。

export interface Line {
	session_id: string;
	seq: number;
	en: string;
	zh: string;
	t_s: number;
}

/** 本机会话元数据；时间一律 epoch 毫秒（服务端用 ISO 字符串，转在 api 层做） */
export interface SessionMeta {
	session_id: string;
	course: string;
	started_at: number;
	ended_at: number | null;
	/** 服务器已确认到的连续序号；ack 只增不减 */
	acked_seq: number;
}

export interface LineStore {
	/** false = 退回内存实现（隐私模式等），界面据此提示「关掉标签页就没了」 */
	readonly persistent: boolean;

	/** 记课程与开课时间；幂等（已存在不动，免得盖掉 acked_seq） */
	openSession(session_id: string, course?: string): Promise<void>;
	/** 记下课时间；幂等（已记过不动） */
	closeSession(session_id: string): Promise<void>;
	/**
	 * 改课名（课名可下课后再填）。只动 `course`，不碰 acked_seq / 起止时间；
	 * 本机没有这节课就抛（调用方要能看见「改错了地方」）。
	 */
	renameSession(session_id: string, course: string): Promise<void>;

	/** 幂等：同一 session_id+seq 重复写不报错、不重复 */
	append(line: Line): Promise<void>;
	/** 还没被服务器确认的句子，按 seq 升序 */
	pending(session_id: string): Promise<Line[]>;
	/** 只增不减：upto_seq 比已记的小就忽略 */
	ack(session_id: string, upto_seq: number): Promise<void>;

	/** 这一节的全部句子，按 seq 升序 */
	linesOf(session_id: string): Promise<Line[]>;
	/** 全部会话，最近开的在前 */
	listSessions(): Promise<SessionMeta[]>;
	/** 「删除我的全部数据」 */
	deleteAll(): Promise<void>;
	/** 本机只留 days 天（PLAN Q2 的 30）；返回删掉的会话数 */
	purgeOlderThan(days?: number): Promise<number>;
}

export { openStore, type OpenOptions } from './idb';
export { memoryStore, type MemoryOptions } from './memory';
export {
	createSyncer,
	type LineSink,
	type SyncClock,
	type SyncDeps,
	type SyncParams,
	type SyncState,
	type Syncer
} from './sync';
