// web/tests/live/resume.test.ts —— 刷新 / 重开浏览器接回同一节课（lib/live/resume.ts）的离线自测。
// 钉的判据：心跳新鲜 + 同课名才接回；过期 / 换课名 / 坏记录一律开新课；只清自己的记录；
//          同一节课只许一个标签页拿到锁（刷新瞬间旧锁还没放时短等重试）。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
	ACTIVE_KEY, RESUME_WINDOW_MS, clearActive, holdClassLock, pickResume, readActive, tryTakeClassLock, writeActive,
	type KV, type LockApi
} from '../../src/lib/live/resume.ts';

const SID = 'web-20261120-103000-abc123';
const SID2 = 'web-20261120-110000-def456';
function mem(): KV & { raw: Map<string, string> } {
	const raw = new Map<string, string>();
	return { raw, getItem: (k) => raw.get(k) ?? null, setItem: (k, v) => void raw.set(k, v), removeItem: (k) => void raw.delete(k) };
}

test('心跳新鲜 + 同课名 → 接回；课名首尾空格不算不同', () => {
	const kv = mem();
	writeActive(kv, { session_id: SID, course: 'ECON10740', beat: 1000 });
	assert.equal(pickResume(kv, ' ECON10740 ', 1000 + 5000), SID);
});

test('过窗口 / 换课名 / 时钟倒退 / 没记录 → 不接回', () => {
	const kv = mem();
	assert.equal(pickResume(kv, 'A', 0), null);
	writeActive(kv, { session_id: SID, course: 'A', beat: 1000 });
	assert.equal(pickResume(kv, 'A', 1000 + RESUME_WINDOW_MS + 1), null);
	assert.equal(pickResume(kv, 'B', 2000), null);
	assert.equal(pickResume(kv, 'A', 500), null);
});

test('空课名也能接回（课名可下课后再填）', () => {
	const kv = mem();
	writeActive(kv, { session_id: SID, course: '', beat: 1000 });
	assert.equal(pickResume(kv, '', 2000), SID);
	assert.equal(pickResume(kv, '  ', 2000), SID);
});

test('坏记录 / 乱会话号 / 没有存储 → null，不抛', () => {
	const kv = mem();
	kv.raw.set(ACTIVE_KEY, '{bad');
	assert.equal(readActive(kv), null);
	kv.raw.set(ACTIVE_KEY, JSON.stringify({ session_id: 'x/../y', course: 'A', beat: 1 }));
	assert.equal(readActive(kv), null);
	assert.equal(pickResume(null, 'A', 1), null);
	writeActive(null, { session_id: SID, course: 'A', beat: 1 });
});

test('clearActive 只清自己的：别的标签页开了新一节就不能误删', () => {
	const kv = mem();
	writeActive(kv, { session_id: SID2, course: 'A', beat: 1 });
	clearActive(kv, SID);
	assert.equal(readActive(kv)?.session_id, SID2);
	clearActive(kv, SID2);
	assert.equal(readActive(kv), null);
});

function fakeLocks(): LockApi & { held: Set<string>; drop(name: string): void } {
	const held = new Set<string>();
	const releasers = new Map<string, () => void>();
	return {
		held,
		drop: (n) => releasers.get(n)?.(),
		async request(name, opts, cb) {
			if (opts.ifAvailable && held.has(name)) return cb(null);
			held.add(name);
			const p = Promise.resolve(cb({ name }));
			releasers.set(name, () => held.delete(name));
			return p.finally(() => held.delete(name));
		}
	};
}

test('同一节课只有一个持有者；release 后别人能拿', async () => {
	const locks = fakeLocks();
	const a = await holdClassLock(locks, SID);
	assert.ok(a);
	assert.equal(await holdClassLock(locks, SID), null);
	assert.ok(await holdClassLock(locks, SID2), '另一节课不受影响');
	a!.release();
	await new Promise((r) => setTimeout(r, 0));
	assert.ok(await holdClassLock(locks, SID));
});

test('刷新瞬间旧锁还没放：短等几次后拿到；一直占着则放弃', async () => {
	const locks = fakeLocks();
	const old = await holdClassLock(locks, SID);
	let waits = 0;
	const got = await tryTakeClassLock(locks, SID, async () => {
		waits += 1;
		if (waits === 2) old!.release();
		await new Promise((r) => setTimeout(r, 0));
	});
	assert.ok(got);
	assert.equal(waits, 2);

	const locks2 = fakeLocks();
	await holdClassLock(locks2, SID);
	let n = 0;
	assert.equal(await tryTakeClassLock(locks2, SID, async () => void n++, 3), null);
	assert.equal(n, 2);
});

test('浏览器没有 Web Locks → 当作拿到（只靠心跳判据）', async () => {
	assert.ok(await holdClassLock(null, SID));
	assert.ok(await tryTakeClassLock(null, SID));
});
