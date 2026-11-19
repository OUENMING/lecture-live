// 磁带辅助：本机磁带 / S1 的 CPython 基线都在时才对得上（课堂数据不进仓库）。
// 缺任何一个 → 测试 skip 并说明，不造假数据。
import fs from 'node:fs';
import path from 'node:path';

/** 真磁带（Soniox 原始消息，每条带 `_wall`） */
export const TAPE_DIR = process.env.CL_TAPE_DIR ?? '/Users/owen/.classlive/eval/diar_spike';
/** S1 跑出来的 CPython 基线（每拍一行：draft / sealed） */
export const BASE_DIR = process.env.CL_CORE_BASE ?? '/Users/owen/classlive-web-spike/s1/out';

export interface Baseline {
	i: number;
	wall: number;
	draft: string;
	sealed: { text: string; lang: string; start: number; end: number }[];
}

export function readLines(p: string): string[] | null {
	if (!fs.existsSync(p)) return null;
	return fs
		.readFileSync(p, 'utf8')
		.split('\n')
		.filter((l) => l.trim() !== '');
}

export function tapePath(name: string): string {
	return path.join(TAPE_DIR, name, 'prod_ctx.jsonl');
}

export function basePath(name: string): string {
	return path.join(BASE_DIR, `${name}.cpython.jsonl`);
}

/** 磁带 → 原始消息数组；缺文件返回 null 并给 t.diagnostic 用的一句说明 */
export function loadTape(name: string): { lines: string[]; missing: string | null } {
	const lines = readLines(tapePath(name));
	return lines ? { lines, missing: null } : { lines: [], missing: tapePath(name) };
}

export function loadBaseline(name: string): Baseline[] | null {
	const lines = readLines(basePath(name));
	return lines ? lines.map((l) => JSON.parse(l) as Baseline) : null;
}

/**
 * 基线里的定稿句序列（与一首会话里砼出来的一一对应）。
 *
 * 压成数组：`[text, lang, start, end]` 四元组，方便逐句比。
 */
export function baselineSentences(base: readonly Baseline[]): [string, string, number, number][] {
	return base.flatMap((b) => b.sealed.map((s) => [s.text, s.lang, s.start, s.end] as [string, string, number, number]));
}
