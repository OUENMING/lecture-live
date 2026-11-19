// lib/session —— 一次真会话的接线（采音 → 识别 → Python 核心 → 界面形状）。
// 对外：`LiveSession`（界面只读它的 rows / draftEn / draftZh / playing / status / level / message）
// 与 `tapeReplay`（同一份接线换成磁带源，不连网也能端到端跑）。
export {
	LiveSession,
	LOUD_LEVEL,
	SILENCE_FRAMES,
	type CapRow,
	type LiveDeps,
	type LiveStatus,
	type TranslationEvent,
	type TranslationHooks
} from './live.svelte';
export { tapeReplay, parseTape, type TapeMessage, type TapeOptions, type TapeReplay } from './tape';
