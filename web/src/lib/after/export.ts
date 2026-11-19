// lib/after/export.ts —— 把一节课的字幕做成文件交给用户：课后页的两个下载按钮、上课页「下课」时自动下载都走这里，
// 保证两处下到的是同一份东西。❓ = 上课时标过「没听懂」的句子。
import type { Line, SessionMeta } from '../store';
import { courseLabel } from '../env';

const fmt = (t: number) => `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

/** 文件名主干：「ECON10770-2026-11-19」；没起名用 ClassLive */
export function fileBase(meta: SessionMeta | null): string {
	const name = (meta?.course.trim() || 'ClassLive').replace(/[\\/:*?"<>|]/g, '-');
	return `${name}-${meta ? new Date(meta.started_at).toISOString().slice(0, 10) : 'class'}`;
}

export function toMarkdown(meta: SessionMeta | null, lines: Line[], marked: ReadonlySet<number>): string {
	const head = `# ${meta ? courseLabel(meta.course) : ''} 课堂字幕\n\n${meta ? new Date(meta.started_at).toLocaleString('zh-CN') : ''} · ${lines.length} 句\n\n`;
	const body = lines
		.map((l) => `${marked.has(l.seq) ? '❓ ' : ''}**[${fmt(l.t_s)}]** ${l.en}\n\n${l.zh || '（这句没译出）'}\n`)
		.join('\n');
	return head + body;
}

export function toText(lines: Line[]): string {
	return lines.map((l) => `[${fmt(l.t_s)}] ${l.en}\n${l.zh}`).join('\n\n') + '\n';
}

/** 触发浏览器下载（进「下载」文件夹，不弹窗） */
export function saveFile(name: string, text: string, type: string): void {
	const url = URL.createObjectURL(new Blob([text], { type }));
	const a = document.createElement('a');
	a.href = url;
	a.download = name;
	a.click();
	setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadMarkdown(meta: SessionMeta | null, lines: Line[], marked: ReadonlySet<number>): void {
	saveFile(`${fileBase(meta)}.md`, toMarkdown(meta, lines, marked), 'text/markdown;charset=utf-8');
}
export function downloadText(meta: SessionMeta | null, lines: Line[]): void {
	saveFile(`${fileBase(meta)}.txt`, toText(lines), 'text/plain;charset=utf-8');
}
