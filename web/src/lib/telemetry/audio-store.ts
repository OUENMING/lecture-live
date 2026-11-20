// lib/telemetry/audio-store.ts —— 课堂录音分段的本地仓库（库 `cl-audio`，一条 = blob + session + n + mime）。
// 对外：createAudioStore({indexedDB?}) → { put, list, remove, total, oldest, maxSeq }。
// 为什么单独一层：录音段比逐句大得多，落盘失败不能拖垮上课 —— 打不开 IndexedDB 就退回内存（本页有效），
// 所有方法一律不抛，失败用返回值（false / 空数组）交代，调用方自己决定要不要报事件。
// 主键 [session, n]：同一节课内序号递增，重开页面接着录也不会覆盖已传过 / 待传的段。

export interface AudioChunk {
	session: string;
	n: number;
	mime: string;
	at: number;
	size: number;
	blob: Blob;
}

export interface AudioStore {
	readonly persistent: boolean;
	put(chunk: AudioChunk): Promise<boolean>;
	/** 按 (session, n) 升序列出；不给 session 就是全部（补传 / 清理用） */
	list(session?: string): Promise<AudioChunk[]>;
	remove(session: string, n: number): Promise<boolean>;
	/** 全部段的字节总量（超 200 MB 时用来挑最旧的丢） */
	total(): Promise<number>;
	/** 跨会话最旧的一段（先 `at` 再 `session`/`n`） */
	oldest(): Promise<AudioChunk | null>;
	/** 这节课已有的最大序号（新段从这里接着编号） */
	maxSeq(session: string): Promise<number>;
}

const DB_NAME = 'cl-audio';
const DB_VERSION = 1;
const CHUNKS = 'chunks';

/** 内存版：隐私模式 / IDB 打不开时用它，语义与 IDB 版一致 */
function memoryAudioStore(): AudioStore {
	const rows: AudioChunk[] = [];
	const byKey = (a: AudioChunk, b: AudioChunk): number =>
		a.session === b.session ? a.n - b.n : a.session < b.session ? -1 : 1;
	const oldestOf = (xs: AudioChunk[]): AudioChunk | null =>
		xs.length ? xs.slice().sort((a, b) => a.at - b.at || byKey(a, b))[0] : null;
	return {
		persistent: false,
		async put(c) {
			const i = rows.findIndex((r) => r.session === c.session && r.n === c.n);
			if (i >= 0) rows[i] = c;
			else rows.push(c);
			return true;
		},
		async list(session) {
			return rows.filter((r) => session === undefined || r.session === session).sort(byKey);
		},
		async remove(session, n) {
			const i = rows.findIndex((r) => r.session === session && r.n === n);
			if (i < 0) return false;
			rows.splice(i, 1);
			return true;
		},
		async total() {
			return rows.reduce((s, r) => s + r.size, 0);
		},
		async oldest() {
			return oldestOf(rows);
		},
		async maxSeq(session) {
			return rows.reduce((m, r) => (r.session === session && r.n > m ? r.n : m), 0);
		}
	};
}

function openDb(factory: IDBFactory): Promise<IDBDatabase> {
	return new Promise((resolve, reject) => {
		let req: IDBOpenDBRequest;
		try {
			req = factory.open(DB_NAME, DB_VERSION);
		} catch (e) {
			reject(e);
			return;
		}
		req.onupgradeneeded = () => {
			const db = req.result;
			if (!db.objectStoreNames.contains(CHUNKS)) db.createObjectStore(CHUNKS, { keyPath: ['session', 'n'] });
		};
		req.onsuccess = () => resolve(req.result);
		req.onerror = () => reject(req.error ?? new Error('打不开 cl-audio'));
		req.onblocked = () => reject(new Error('cl-audio 被其他标签页占用'));
	});
}

/** 一条只读 / 只写事务：回调里同步发请求，事务提交后 resolve；任何异常都 reject（上层统一吞） */
function run<T>(
	db: IDBDatabase,
	mode: IDBTransactionMode,
	body: (tx: IDBTransaction, finish: (v: T) => void) => void
): Promise<T> {
	return new Promise((resolve, reject) => {
		let out!: T;
		let tx: IDBTransaction;
		try {
			tx = db.transaction(CHUNKS, mode);
		} catch (e) {
			reject(e);
			return;
		}
		tx.oncomplete = () => resolve(out);
		tx.onerror = () => reject(tx.error ?? new Error('cl-audio 事务失败'));
		tx.onabort = () => reject(tx.error ?? new Error('cl-audio 事务中止'));
		try {
			body(tx, (v) => {
				out = v;
			});
		} catch (e) {
			reject(e);
		}
	});
}

function idbAudioStore(db: IDBDatabase): AudioStore {
	const all = () =>
		run<AudioChunk[]>(db, 'readonly', (tx, finish) => {
			const req = tx.objectStore(CHUNKS).getAll() as IDBRequest<AudioChunk[]>;
			req.onsuccess = () =>
				finish(
					req.result.sort((a, b) =>
						a.session === b.session ? a.n - b.n : a.session < b.session ? -1 : 1
					)
				);
		});
	return {
		persistent: true,
		async put(c) {
			await run<void>(db, 'readwrite', (tx, finish) => {
				tx.objectStore(CHUNKS).put({ ...c });
				finish(undefined);
			});
			return true;
		},
		async list(session) {
			const rows = await all();
			return session === undefined ? rows : rows.filter((r) => r.session === session);
		},
		async remove(session, n) {
			await run<void>(db, 'readwrite', (tx, finish) => {
				tx.objectStore(CHUNKS).delete([session, n]);
				finish(undefined);
			});
			return true;
		},
		async total() {
			return (await all()).reduce((s, r) => s + r.size, 0);
		},
		async oldest() {
			const rows = await all();
			return rows.length
				? rows.slice().sort((a, b) => a.at - b.at || a.session.localeCompare(b.session) || a.n - b.n)[0]
				: null;
		},
		async maxSeq(session) {
			const rows = await all();
			return rows.reduce((m, r) => (r.session === session && r.n > m ? r.n : m), 0);
		}
	};
}

/** 打开仓库；打不开（无痕 / 被策略禁掉）退回内存实现，`persistent` 说明是哪一种。永不抛。 */
export async function createAudioStore(opts: { indexedDB?: IDBFactory | null } = {}): Promise<AudioStore> {
	const factory = opts.indexedDB === undefined ? globalThis.indexedDB : opts.indexedDB;
	if (!factory) return memoryAudioStore();
	try {
		return idbAudioStore(await openDb(factory));
	} catch {
		return memoryAudioStore();
	}
}
