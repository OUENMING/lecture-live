// lib/live/sync.ts —— 一节课的「本机 → 服务端」同步：开课、按批推句子、下课、断网排队。
// 对外：attachSync({ store, local_session_id, course, api, clock }) → { state, push, flush, end, stop }。
// 为什么把开课塞进发送端：推送与「服务端会话号从哪来」是同一件事的两面 —— 没会话号就先开课，
// 开课失败即发送失败，于是节拍、退避（1 s→2→4…封顶 60 s）、下课补齐全都由 `lib/store/sync.ts`
// 的 createSyncer 一处管，这里不再自己排定时器（那会多出一套节拍，也会把开课前的 push 折叠掉）。
// 服务端 session_id 存 localStorage `cl.srv.<local_session_id>`：刷新页面后拿它续推，不重开一节课。

import { ApiError, type WebSessionOptions, type WireLine } from '../api';
import type { Line, LineStore } from '../store';
import { createSyncer, type SyncClock, type Syncer } from '../store/sync';

export type SyncPhase = 'off' | 'syncing' | 'synced' | 'offline' | 'error';

/** 给界面画小字用的只读快照 */
export interface SyncView {
	phase: SyncPhase;
	/** 服务端确认到的连续序号；0 = 还没确认过 */
	acked: number;
	/** 一句话，界面可直接显示 */
	message: string;
	/** 服务端会话号；还没开成是 null */
	server_id: string | null;
}

/** 同步用到的三个接口（测试可整块替身，不必造一个完整的 api 对象） */
export interface SyncApi {
	startSession(
		course: string,
		started_at: number,
		idemKey: string,
		opts?: WebSessionOptions
	): Promise<{ session_id: string }>;
	putLines(id: string, lines: WireLine[], opts?: WebSessionOptions): Promise<{ confirmed_seq: number }>;
	endSession(
		id: string,
		info: { ended_at: number; lines_total: number },
		opts?: WebSessionOptions
	): Promise<{ job_id: string }>;
}

export interface AttachParams {
	store: Pick<LineStore, 'pending' | 'ack' | 'linesOf'>;
	local_session_id: string;
	course: string;
	api: SyncApi;
	clock?: SyncClock;
	/** 开课时刻（默认时钟现在）；进 startSession 的 started_at */
	startedAt?: number;
	/** 批次与节拍：默认每 10 句或 30 s */
	params?: { batchLines?: number; flushMs?: number; maxBatch?: number };
}

export interface AttachedSync {
	state(): SyncView;
	/** 有新句子了（立刻返回，同步在后台自己走） */
	push(): void;
	/** 不等节拍，现在推一轮（下课 / 关页面前） */
	flush(): Promise<void>;
	/** 下课：flush → endSession；可重复调用，只发一次 */
	end(): Promise<void>;
	/** 停掉所有定时器（不改服务端状态） */
	stop(): void;
}

const realClock: SyncClock = {
	now: () => Date.now(),
	setTimer: (ms, cb) => {
		const id = setTimeout(cb, ms);
		return () => clearTimeout(id);
	}
};

const KEY = (local: string) => `cl.srv.${local}`;
const readKey = (local: string): string | null => {
	try {
		return localStorage.getItem(KEY(local)) || null;
	} catch {
		return null;
	}
};
const writeKey = (local: string, srv: string): void => {
	try {
		localStorage.setItem(KEY(local), srv);
	} catch {
		/* 存不住：这一程仍能推，刷新后才丢 */
	}
};

const kindOf = (e: unknown) => (e instanceof ApiError ? e.kind : '');
const reasonOf = (e: unknown) => (e instanceof ApiError ? e.reason : '');

export function attachSync(p: AttachParams): AttachedSync {
	const clock = p.clock ?? realClock;
	const startedAt = p.startedAt ?? clock.now();
	const params = {
		batchLines: p.params?.batchLines ?? 10,
		flushMs: p.params?.flushMs ?? 30_000,
		maxBatch: p.params?.maxBatch ?? 50,
		baseBackoffMs: 1_000,
		maxBackoffMs: 60_000
	};

	let serverId: string | null = readKey(p.local_session_id);
	let phase: SyncPhase = 'off';
	let message = '还没开始同步';
	let acked = 0;
	let starting: Promise<string> | null = null;
	/** 收场了（已下课 / 数据已删 / 单句都太大）：不再推 */
	let finished = false;
	let stopped = false;
	let syncher: Syncer | null = null;
	let ending: Promise<void> | null = null;

	const state = (): SyncView => ({ phase, acked, message, server_id: serverId });

	/** 410：服务端说这节课没了 —— 停止同步 */
	const gone = () => {
		finished = true;
		syncher?.stop();
		phase = 'error';
		message = '这节课的数据已在服务器上删除 —— 不再同步';
	};

	/** 409 session_ended：当作完成（笔记任务早就在那儿了） */
	const alreadyEnded = () => {
		finished = true;
		syncher?.stop();
		phase = 'synced';
		message = '这节课已经下课 —— 剩下的句子不再上传';
	};

	/** 开课（幂等键 = 本机会话 id，重试 / 刷新都不会开出第二节课）；失败就抛，让发送端去退避 */
	const ensureStarted = (): Promise<string> => {
		if (serverId) return Promise.resolve(serverId);
		if (starting) return starting;
		starting = p.api
			.startSession(p.course, startedAt, p.local_session_id)
			.then((res) => {
				serverId = res.session_id;
				writeKey(p.local_session_id, res.session_id);
				phase = 'syncing';
				message = '正在同步';
				return res.session_id;
			})
			.catch((e) => {
				if (kindOf(e) === 'gone') gone();
				else {
					phase = 'offline';
					message = '连不上服务器 —— 课堂照常，稍后自动补传';
				}
				throw e;
			})
			.finally(() => {
				starting = null;
			});
		return starting;
	};

	/**
	 * 一批一次。413（单批太大）就地拆半再发：同一批层层退避只会反复撞同一堵墙。
	 * 其它失败先标好界面状态，再抛出去让 createSyncer 退避。
	 */
	const send = async (lines: Line[], depth = 0): Promise<number> => {
		const id = await ensureStarted();
		try {
			const res = await p.api.putLines(id, lines.map(toWire));
			return res.confirmed_seq;
		} catch (e) {
			const mine = kindOf(e);
			if (mine === 'too_large' && lines.length > 1 && depth < 2) {
				const mid = Math.ceil(lines.length / 2);
				const a = await send(lines.slice(0, mid), depth + 1);
				const b = await send(lines.slice(mid), depth + 1);
				return Math.max(a, b);
			}
			if (mine === 'gone') gone();
			else if (reasonOf(e) === 'session_ended') alreadyEnded();
			else if (mine === 'too_large') {
				finished = true;
				syncher?.stop();
				phase = 'error';
				message = '单句都发不上去 —— 这节课不再上传';
			} else if (kindOf(e) === 'not_found') {
				// 服务端没有这个会话（换过码 / 服务端清过）：丢掉旧号，下次从头开课
				writeKey(p.local_session_id, '');
				serverId = null;
				phase = 'offline';
				message = '服务器上找不到这节课 —— 会重新开课';
			} else {
				phase = 'offline';
				message = mine === 'quota' ? '服务器让等一等 —— 恢复后自动补齐' : '网络不通 —— 句子先存本机';
			}
			throw e;
		}
	};

	const attach = (): Syncer => {
		syncher ??= createSyncer({
			store: p.store,
			session_id: p.local_session_id,
			putLines: async (_id, lines) => ({ acked_seq: await send(lines) }),
			clock,
			params,
			onState: (s) => {
				if (finished || stopped) return;
				acked = s.last_acked ?? 0;
				if (s.in_flight) {
					phase = 'syncing';
					message = '正在同步';
				} else if (s.last_error) {
					phase = 'offline';
					message = '连接不稳 —— 恢复后自动补齐';
				} else if (s.last_acked !== null) {
					phase = 'synced';
					message = `已同步 ${s.last_acked} 句`;
				}
			}
		});
		return syncher;
	};

	const push = () => {
		if (finished || stopped) return;
		attach().push();
	};

	const flush = async () => {
		if (finished || stopped) return;
		await attach().flush();
	};

	const end = () => {
		ending ??= (async () => {
			await flush();
			if (!serverId) {
				phase = 'error';
				message = '这节课没同步上 —— 本机还留着';
				return;
			}
			const id = serverId;
			finished = true;
			syncher?.stop();
			const lines = await p.store.linesOf(p.local_session_id).catch(() => []);
			try {
				await p.api.endSession(id, { ended_at: clock.now(), lines_total: lines.length });
				phase = 'synced';
				message = '已上传 —— 笔记在生成';
			} catch (e) {
				if (kindOf(e) === 'gone') return gone();
				if (reasonOf(e) === 'session_ended') return alreadyEnded();
				finished = false;
				phase = 'offline';
				message = '下课没送到服务器 —— 本机还留着';
			}
		})();
		return ending;
	};

	const stop = () => {
		stopped = true;
		syncher?.stop();
	};

	// 开课一挂上就去开（不等人攒够 10 句）：`started_at` 才是真开课时刻；失败也一样不挡上课，
	// 下一次 push / 退避重试会再来（`.catch` 健在这里，免得上层吞不掉的未处理拒绝）
	if (!serverId) void ensureStarted().catch(() => {});

	return { state, push, flush, end, stop };
}

/** 本机行 → 线上形状（服务端要 `t`；只推定稿句） */
function toWire(l: Line): WireLine {
	return { seq: l.seq, t: l.t_s, en: l.en, zh: l.zh, state: 'final', src: 'asr' };
}
