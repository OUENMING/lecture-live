// lib/translate —— 网页版翻译接线（定稿句 + 草稿句），提示词由 Pyodide 里的 prompt_core 拼。
// 对外：`Translator`（`final` / `draft` / `noteFinal` / `close`）、`latencyOf`（中位数 / p90 / 最大值）、
// `createPrompt` 或 `pyPromptFromPyodide`（造请求体工厂）、`streamChat`（流式请求的唯一出口）。
// 界面不直接用它：`lib/session` 负责接线与打点，界面只读那里投影出来的 rows / draftZh。

export { Translator, type ChatFn, type TranslatorDeps, type TraceEvent } from './translator';
export {
	pyPrompt,
	pyPromptFromPyodide,
	PROMPT_ENTRY,
	type Prompt,
	type PromptBuilder,
	type PythonCall,
	type TermConfig,
	type DraftBegin
} from './prompt';
export { createPrompt, type PromptClientOptions, type PromptWorker } from './prompt-client';
export { TwoLineParser, looksLikeEcho, type TwoLineResult } from './format';
export { SseParser } from './sse';
export { streamChat, MODEL, MAX_TOKENS, DEADLINE_MS, type ChatError, type StreamChatOptions } from './stream';
export { latencyOf, percentile, type LatencyReport, type LatencySample } from './latency';
