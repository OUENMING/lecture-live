// 原生 IndexedDB 实现的 LineStore（接口见 index.ts）：库 classlive，lines 主键 [session_id, seq]，sessions 主键 session_id。
// 用原生 API 不引依赖：这是「不丢」的地基，多一个运行时包就多一条发版风险。
// 打不开（隐私模式 / Safari 无痕 / 被策略禁掉）不抛错，退回 memoryStore 并把 persistent 置 false，让界面能提示。
// 幂等靠「同一事务里先 get 再 put」，不靠进程内互斥，多标签页同时写也不会重复。
// 一个操作只开一条事务、回调里接着发请求（不跨 await 续事务），事务结果在 oncomplete 时交回。

import type { Line, LineStore, SessionMeta } from './index';
import { memoryStore } from './memory';

const DB_NAME = 'classlive';
const DB_VERSION = 1;
const LINES = 'lines';
const SESSIONS = 'sessions';
const DAY_MS = 86_400_000;
const SEQ_MAX = Number.MAX_SAFE_INTEGER;

export interface OpenOptions {
	/** 库名（测试里用独立名字，免得互相污染） */
	name?: string;
	/** 默认 globalThis.indexedDB；传 null 可强制走内存实现 */
	indexedDB?: IDBFactory | null;
	now?: () => number;
}

/**
 * 跑一条事务：body 里同步发请求并挂回调，`finish(v)` 记下结果，事务提交后才 resolve。
 * 出错 / 中止一律 reject —— 落盘失败必须让调用方看见，不能静默吞掉。
 */
function run<T>(
	db: IDBDatabase,
	stores: string | string[],
	mode: IDBTransactionMode,
	body: (tx: IDBTransaction, finish: (v: T) => void) => void
): Promise<T> {
	return new Promise((resolve, reject) => {
		let result!: T;
		let tx: IDBTransaction;
		try {
			tx = db.transaction(stores, mode);
		} catch (e) {
			reject(e);
			return;
		}
		tx.oncomplete = () => resolve(result);
		tx.onerror = () => reject(tx.error ?? new Error('IndexedDB 事务失败'));
		tx.onabort = () => reject(tx.error ?? new Error('IndexedDB 事务被中止'));
		try {
			body(tx, (v) => {
				result = v;
			});
		} catch (e) {
			reject(e);
		}
	});
}

function bySeq(a: Line, b: Line): number {
	return a.seq - b.seq;
}

function openDb(factory: IDBFactory, name: string): Promise<IDBDatabase> {
	return new Promise((resolve, reject) => {
		let request: IDBOpenDBRequest;
		try {
			request = factory.open(name, DB_VERSION);
		} catch (e) {
			reject(e);
			return;
		}
		request.onupgradeneeded = () => {
			const db = request.result;
			if (!db.objectStoreNames.contains(LINES)) {
				db.createObjectStore(LINES, { keyPath: ['session_id', 'seq'] });
			}
			if (!db.objectStoreNames.contains(SESSIONS)) {
				db.createObjectStore(SESSIONS, { keyPath: 'session_id' });
			}
		};
		request.onsuccess = () => resolve(request.result);
		request.onerror = () => reject(request.error ?? new Error('打不开 IndexedDB'));
		// 别的标签页占着旧版本时别干等：退回内存实现，界面会提示
		request.onblocked = () => reject(new Error('IndexedDB 被其他标签页占用'));
	});
}

function freshSession(session_id: string, started_at: number, course?: string): SessionMeta {
	return { session_id, course: course ?? '', started_at, ended_at: null, acked_seq: 0 };
}

/**
 * 打开库；打不开就返回内存实现（persistent = false）。
 * 名字由调用方定，测试里用 classlive-test-<n>。
 */
export async function openStore(opts: OpenOptions = {}): Promise<LineStore> {
	const now = opts.now ?? Date.now;
	const name = opts.name ?? DB_NAME;
	const factory = opts.indexedDB === undefined ? globalThis.indexedDB : opts.indexedDB;
	if (!factory) return memoryStore({ now });

	let db: IDBDatabase;
	try {
		db = await openDb(factory, name);
	} catch {
		return memoryStore({ now });
	}

	return {
		persistent: true,

		async openSession(session_id, course) {
			await run<void>(db, SESSIONS, 'readwrite', (tx, finish) => {
				const st = tx.objectStore(SESSIONS);
				const cur = st.get(session_id) as IDBRequest<SessionMeta | undefined>;
				cur.onsuccess = () => {
					const meta = cur.result;
					if (meta === undefined) st.put(freshSession(session_id, now(), course));
					else if (course !== undefined && !meta.course) st.put({ ...meta, course });
					finish(undefined);
				};
			});
		},

		async closeSession(session_id) {
			await run<void>(db, SESSIONS, 'readwrite', (tx, finish) => {
				const st = tx.objectStore(SESSIONS);
				const cur = st.get(session_id) as IDBRequest<SessionMeta | undefined>;
				cur.onsuccess = () => {
					const meta = cur.result ?? freshSession(session_id, now());
					if (meta.ended_at === null) st.put({ ...meta, ended_at: now() });
					finish(undefined);
				};
			});
		},

		async renameSession(session_id, course) {
			let missing = false;
			await run<void>(db, SESSIONS, 'readwrite', (tx, finish) => {
				const st = tx.objectStore(SESSIONS);
				const cur = st.get(session_id) as IDBRequest<SessionMeta | undefined>;
				cur.onsuccess = () => {
					if (cur.result === undefined) {
						missing = true;
						return;
					}
					st.put({ ...cur.result, course });
					finish(undefined);
				};
			});
			if (missing) throw new Error(`本机没有这节课：${session_id}`);
		},

		async append(line) {
			await run<void>(db, [LINES, SESSIONS], 'readwrite', (tx, finish) => {
				const lines = tx.objectStore(LINES);
				const sessions = tx.objectStore(SESSIONS);
				const existing = lines.get([line.session_id, line.seq]);
				existing.onsuccess = () => {
					if (existing.result === undefined) lines.put({ ...line });
				};
				const meta = sessions.get(line.session_id) as IDBRequest<SessionMeta | undefined>;
				meta.onsuccess = () => {
					if (meta.result === undefined) sessions.put(freshSession(line.session_id, now()));
				};
				finish(undefined);
			});
		},

		async pending(session_id) {
			return run<Line[]>(db, [LINES, SESSIONS], 'readonly', (tx, finish) => {
				const meta = tx.objectStore(SESSIONS).get(session_id) as IDBRequest<SessionMeta | undefined>;
				meta.onsuccess = () => {
					const from = (meta.result?.acked_seq ?? 0) + 1;
					const all = tx
						.objectStore(LINES)
						.getAll(IDBKeyRange.bound([session_id, from], [session_id, SEQ_MAX])) as IDBRequest<Line[]>;
					all.onsuccess = () => finish(all.result.toSorted(bySeq));
				};
			});
		},

		async ack(session_id, upto_seq) {
			await run<void>(db, SESSIONS, 'readwrite', (tx, finish) => {
				const st = tx.objectStore(SESSIONS);
				const cur = st.get(session_id) as IDBRequest<SessionMeta | undefined>;
				cur.onsuccess = () => {
					const meta = cur.result ?? freshSession(session_id, now());
					if (upto_seq > meta.acked_seq) st.put({ ...meta, acked_seq: upto_seq });
					finish(undefined);
				};
			});
		},

		async linesOf(session_id) {
			return run<Line[]>(db, LINES, 'readonly', (tx, finish) => {
				const all = tx
					.objectStore(LINES)
					.getAll(
						IDBKeyRange.bound([session_id, 0], [session_id, SEQ_MAX])
					) as IDBRequest<Line[]>;
				all.onsuccess = () => finish(all.result.toSorted(bySeq));
			});
		},

		async listSessions() {
			return run<SessionMeta[]>(db, SESSIONS, 'readonly', (tx, finish) => {
				const all = tx.objectStore(SESSIONS).getAll() as IDBRequest<SessionMeta[]>;
				all.onsuccess = () => finish(all.result.toSorted((a, b) => b.started_at - a.started_at));
			});
		},

		async deleteAll() {
			await run<void>(db, [LINES, SESSIONS], 'readwrite', (tx, finish) => {
				tx.objectStore(LINES).clear();
				tx.objectStore(SESSIONS).clear();
				finish(undefined);
			});
		},

		async purgeOlderThan(days = 30) {
			return run<number>(db, [LINES, SESSIONS], 'readwrite', (tx, finish) => {
				const floor = now() - days * DAY_MS;
				const sessions = tx.objectStore(SESSIONS);
				const lines = tx.objectStore(LINES);
				let removed = 0;
				const all = sessions.getAll() as IDBRequest<SessionMeta[]>;
				all.onsuccess = () => {
					for (const meta of all.result) {
						if (meta.ended_at === null || meta.ended_at >= floor) continue;
						sessions.delete(meta.session_id);
						lines.delete(IDBKeyRange.bound([meta.session_id, 0], [meta.session_id, SEQ_MAX]));
						removed++;
					}
					finish(removed);
				};
			});
		}
	};
}
