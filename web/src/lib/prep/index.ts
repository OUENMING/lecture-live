// `lib/prep/` —— 开课前的准备（浏览器版）：「课件 → 候选术语」。
// 对外只认一个函数：`prepareTerms(files, course, { py })` → `{ terms, stats, failed }`（另有 `onProgress`）。
// 拆成四个文件各有唯一职责：`py.ts` 是 Pyodide 接缝（判断全在切片里）、`pdf.ts` 是 PDF 抽文字、
// `llm.ts` 是一次非流式对话、`prepare.ts` 是装配。判据见 `web/tests/prep/`。
export { prepareTerms, PREP_TERM_LIMIT } from './prepare';
export type {
	PrepareOptions,
	PrepFailure,
	PrepFile,
	PrepProgress,
	PrepResult,
	PrepStatsSummary
} from './prepare';
export { pdfBlocks, pagesToBlocks, PDFJS_VERSION, PDFJS_CDN, PDFJS_VENDOR, pdfModuleUrl, pdfWorkerUrl } from './pdf';
export { prepLLM, PREP_MODEL, PREP_MAX_TOKENS, PREP_TEMPERATURE, PREP_DEADLINE_MS } from './llm';
export type { PrepCandidate } from './llm';
export { pyPrep, stagePrep } from './py';
export type { PrepBlock, PrepExtract, PrepGroup, PrepPython, PrepRow, PrepStats } from './py';
