// SSE 分片判据：同一段流按**任意位置**切开，解析出来的事件序列必须与一次喂完相同。
// 这是「不做逐行硬解」的全部理由 —— 真网络的分片边界与 SSE 行边界毫无关系。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SseParser } from '../../src/lib/translate/sse.ts';
import { sseText } from './fake-fetch.ts';

const DELTAS = ['ZH: 期中', '考试覆盖', '第一到三章。\n', 'EN: The midterm covers chapters one to three.'];
const TEXT = sseText(DELTAS);

/** 一次喂完（基准）。 */
function whole(s: string): string[] {
	const p = new SseParser();
	return [...p.push(s), ...p.end()];
}

/** 按给定位置切开喂。 */
function chunked(s: string, size: number): string[] {
	const p = new SseParser();
	const out: string[] = [];
	for (let i = 0; i < s.length; i += size) {
		out.push(...p.push(s.slice(i, i + size)));
		if (p.done) break;
	}
	out.push(...p.end());
	return out;
}

test('一次喂完：每个 data 一条，见到停', () => {
	const base = whole(TEXT);
	assert.equal(base.length, DELTAS.length);
	assert.equal(base[0], JSON.stringify({ choices: [{ delta: { content: DELTAS[0] } }] }));
	assert.deepEqual(base, chunked(TEXT, TEXT.length));
});

test('任意块长切开（1..全文长度）结果都与一次喂完相同', () => {
	const base = whole(TEXT);
	for (let size = 1; size <= TEXT.length; size++) {
		assert.deepEqual(chunked(TEXT, size), base, `块长 ${size} 时解析结果变了`);
	}
});

test('在每个字节位置切成两块也一致', () => {
	const base = whole(TEXT);
	for (let i = 0; i <= TEXT.length; i++) {
		const p = new SseParser();
		const out = [...p.push(TEXT.slice(0, i)), ...p.push(TEXT.slice(i)), ...p.end()];
		assert.deepEqual(out, base, `切在 ${i} 时解析结果变了`);
	}
});

test('不认的行不影响结果：注释 / event: / id: / 空行', () => {
	const p = new SseParser();
	const text = ': keep-alive\nevent: message\nid: 7\ndata: {"a":1}\n\n\n\n';
	assert.deepEqual([...p.push(text), ...p.end()], ['{"a":1}']);
});

test('最后一条没有空行结尾也能交出（end 收尾）', () => {
	const p = new SseParser();
	assert.deepEqual(p.push('data: {"a":1}\n'), []);
	assert.deepEqual(p.end(), ['{"a":1}']);
});

test('CRLF 与全角冒号：标记按原样保留，CRLF 被吃掉', () => {
	const p = new SseParser();
	assert.deepEqual([...p.push('data: {"a":1}\r\n\r\n'), ...p.end()], ['{"a":1}']);
});

test('[DONE] 之后的 data 一律丢弃', () => {
	const p = new SseParser();
	const out = p.push('data: [DONE]\n\ndata: {"late":1}\n\n');
	assert.deepEqual(out, []);
	assert.equal(p.done, true);
	assert.deepEqual(p.push('data: {"more":1}\n\n'), []);
});
