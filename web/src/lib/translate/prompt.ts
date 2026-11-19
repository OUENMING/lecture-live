// 提示词的唯一来源是 `static/core-py/prompt_core.py`（Pyodide 里跑桌面同一份代码，rt.py 只搬运）。
// 对外：`PromptBuilder`（请求体 + 草稿判据，全部 **async**）与 `pyPrompt` / `pyPromptFromPyodide` 两个造法。
// 为什么 async：真链路里 Python 在 Web Worker（`prompt-worker.ts`），只能消息往返；同步接口会逼出
// 「主线程也塞一份 Pyodide」的第二个 13 MB。测试里同步 Pyodide 包一层 Promise 即可。
// 为什么不在 TS 里拼字符串：逐字相同靠 tests/translate/fixtures/（桌面 CPython 生成），在 TS 重写一遍
// prompt 就等于两边各有一份，迟早漂移。

/** 一句的术语/课程配置（字段与 `rt.py:_config` 一一对应；缺一个都当空 = 桌面空术语表）。 */
export interface TermConfig {
	domain?: string;
	terms?: string[];
	core?: string[];
	always?: string[];
	term_zh?: Record<string, string>;
	/** 块对齐窗口；10 = 桌面 `DEFAULT_CTX_CHUNK` */
	ctx_chunk?: number;
	/** `ctx_chunk=0` 时生效 */
	max_context?: number;
}

export interface Prompt {
	system: string;
	user: string;
}

/** 新草稿的判据结果（`rt.py:draft_begin`）：`clear` = 界面该清掉上一句中文。 */
export interface DraftBegin {
	clear: boolean;
	gen: number;
	seq: number;
}

/** 跑一个 rt.py 导出函数（同步那份只给本地 Pyodide 用）。 */
export type PythonCall = (name: string, args: readonly unknown[]) => unknown;

export interface PromptBuilder {
	/** 定稿句请求体（桌面 `fix_and_translate_stream`，含末尾任务指令） */
	final(en: string, context: readonly string[], cfg?: TermConfig): Promise<Prompt>;
	/** 草稿句请求体（桌面 `translate_draft`） */
	draft(en: string, context: readonly string[], cfg?: TermConfig): Promise<Prompt>;
	draftBegin(seg: number, text: string): Promise<DraftBegin>;
	draftAccept(seq: number, seg: number, gen: number): Promise<boolean>;
	draftFinal(seg: number): Promise<boolean>;
}

function parse(json: unknown): unknown {
	if (typeof json !== 'string') throw new Error('core-py 的翻译入口没有回字符串');
	return JSON.parse(json) as unknown;
}

/** 把 rt.py 的几个入口包成 `PromptBuilder`（同步实现，外层补 Promise）。 */
export function pyPrompt(call: PythonCall): PromptBuilder {
	const cfgOf = (cfg: TermConfig | undefined): string => JSON.stringify(cfg ?? {});
	return {
		final: async (en, context, cfg) =>
			parse(call('build_request', [en, JSON.stringify([...context]), cfgOf(cfg)])) as Prompt,
		draft: async (en, context, cfg) =>
			parse(call('build_draft_request', [en, JSON.stringify([...context]), cfgOf(cfg)])) as Prompt,
		draftBegin: async (seg, text) => parse(call('draft_begin', [seg, text])) as DraftBegin,
		draftAccept: async (seq, seg, gen) => Boolean(call('draft_accept', [seq, seg, gen])),
		draftFinal: async (seg) => Boolean(call('draft_final', [seg]))
	};
}

/** Pyodide 实例的最小面（`globals.get` 拿到的是 JS 函数，直接调）。 */
export interface PyodideGlobals {
	globals: { get(name: string): unknown };
}

/** 默认从 rt.py 取的这几个名字（换测试替身时逐个给）。 */
export const PROMPT_ENTRY = [
	'build_request',
	'build_draft_request',
	'draft_begin',
	'draft_accept',
	'draft_final'
] as const;

/** 从一份已经 `import rt` 的 Pyodide 造 `PromptBuilder`。 */
export function pyPromptFromPyodide(
	py: PyodideGlobals,
	names: readonly string[] = PROMPT_ENTRY
): PromptBuilder {
	const fns = new Map<string, (...a: unknown[]) => unknown>();
	for (const n of names) {
		const f = py.globals.get(n);
		if (typeof f !== 'function') throw new Error(`core-py 没导出 ${n}`);
		fns.set(n, f as (...a: unknown[]) => unknown);
	}
	return pyPrompt((name, args) => {
		const f = fns.get(name);
		if (!f) throw new Error(`core-py 没导出 ${name}`);
		return f(...args);
	});
}
