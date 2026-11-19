// 内存版 LineStore：IndexedDB 打不开（隐私模式 / 被策略禁掉）时的兜底，也是测试里的对照实现。
// 语义与 idb.ts 逐条对齐（幂等 append、只增不减 ack、pending 升序），差别只在「关掉标签页就没了」。
// 不引依赖、纯同步计算，给同步队列的测试当真源用。

import type { Line, LineStore, SessionMeta } from './index';

export interface MemoryOptions {
	/** 起点时间，给 purgeOlderThan 的测试定基准；默认 Date.now() */
	now?: () => number;
	/** 故意标 false：调用方据此提示「本机存不下来」 */
	persistent?: boolean;
}

export function memoryStore(opts: MemoryOptions = {}): LineStore {
	const now = opts.now ?? Date.now;
	const lines = new Map<string, Map<number, Line>>();
	const sessions = new Map<string, SessionMeta>();

	const bucket = (session_id: string): Map<number, Line> => {
		let m = lines.get(session_id);
		if (!m) {
			m = new Map();
			lines.set(session_id, m);
		}
		return m;
	};

	const meta = (session_id: string): SessionMeta => {
		let s = sessions.get(session_id);
		if (!s) {
			s = { session_id, course: '', started_at: now(), ended_at: null, acked_seq: 0 };
			sessions.set(session_id, s);
		}
		return s;
	};

	return {
		persistent: opts.persistent ?? false,

		async openSession(session_id, course) {
			const s = sessions.get(session_id);
			if (!s) {
				sessions.set(session_id, {
					session_id,
					course: course ?? '',
					started_at: now(),
					ended_at: null,
					acked_seq: 0
				});
			} else if (course !== undefined && !s.course) {
				s.course = course;
			}
		},

		async closeSession(session_id) {
			const s = meta(session_id);
			if (s.ended_at === null) s.ended_at = now();
		},

		async renameSession(session_id, course) {
			const s = sessions.get(session_id);
			if (!s) throw new Error(`本机没有这节课：${session_id}`);
			s.course = course;
		},

		async append(line) {
			const m = bucket(line.session_id);
			if (!m.has(line.seq)) m.set(line.seq, { ...line });
			meta(line.session_id);
		},

		async pending(session_id) {
			const acked = sessions.get(session_id)?.acked_seq ?? 0;
			const m = lines.get(session_id);
			if (!m) return [];
			return [...m.values()].filter((l) => l.seq > acked).sort((a, b) => a.seq - b.seq);
		},

		async ack(session_id, upto_seq) {
			const s = meta(session_id);
			if (upto_seq > s.acked_seq) s.acked_seq = upto_seq;
		},

		async linesOf(session_id) {
			const m = lines.get(session_id);
			if (!m) return [];
			return [...m.values()].sort((a, b) => a.seq - b.seq);
		},

		async listSessions() {
			return [...sessions.values()]
				.sort((a, b) => b.started_at - a.started_at)
				.map((s) => ({ ...s }));
		},

		async deleteAll() {
			lines.clear();
			sessions.clear();
		},

		async purgeOlderThan(days = 30) {
			const floor = now() - days * 86_400_000;
			let removed = 0;
			for (const s of [...sessions.values()]) {
				if (s.ended_at !== null && s.ended_at < floor) {
					sessions.delete(s.session_id);
					lines.delete(s.session_id);
					removed++;
				}
			}
			return removed;
		}
	};
}
