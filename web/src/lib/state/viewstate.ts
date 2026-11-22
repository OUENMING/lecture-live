// ViewState v0 —— 网页版的数据契约（TASKS P1「ViewState v0」的 TS 镜像）。
// Python 侧（P1-3）是真源：字段可增不可改名；改这里先改 Python 并让回放判据过。
// 只含可 JSON 序列化的数据，不含回调。

export type RowKind = 'draft' | 'final';
export type ZhState = 'none' | 'draft' | 'final' | 'failed';

export interface Row {
	/** 稳定标识，草稿转定稿时不变 */
	id: string;
	kind: RowKind;
	en: string;
	zh: string;
	zh_state: ZhState;
	seg: number;
	t_s: number;
	/** 命中的术语 [原文, 译法] */
	terms: [string, string][];
	/** 说话人字母（主讲不标 → 没有这个键）；界面显示「说话人 B」 */
	spk?: string;
}

export interface Status {
	/** 识别来源，如 "soniox" */
	source?: string;
	listening?: boolean;
	/** 降级 / 失败提示（U6：失败必须出现在这里，不许静默） */
	notice?: string | null;
	[key: string]: unknown;
}

export interface ViewState {
	/** 每次变化 +1 */
	rev: number;
	rows: Row[];
	status: Status;
	course: string;
	elapsed_s: number;
}

export const EMPTY_VIEWSTATE: ViewState = { rev: 0, rows: [], status: {}, course: '', elapsed_s: 0 };
