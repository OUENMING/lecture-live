// 「说完 → 中文首字」的汇总：中位数 / p90 / 最大值 + 拆分（封口耗时、首字耗时）。
// 对外：`latencyOf(samples)` → `LatencyReport`；`samples` 是每句的 `{t_sealed, t_zh_first}`（毫秒）。
// 为什么口径这样定：桌面 1.55 / 2.0 那两个数就是 `t_sealed → t_zh_first`（`stream_eval.py` 的
// `seg_to_zh`），封口耗时 = 段音频结束 → 封口，首字耗时 = 封口 → 中文首字；两段相加才对得上总延迟。
// 空样本不许返回 0 冒充「很快」：一律 `null`（没数据 ≠ 达标）。

export interface LatencySample {
	/** 这句音频结束的**墙钟毫秒**（与 `tSealed` 同一只钟）；拿不到就是 null（总延迟退回封口时刻算） */
	tAudioEnd?: number | null;
	/** 封口时刻（毫秒，墙钟） */
	tSealed: number;
	/** 中文首字到（毫秒，墙钟）；没译出就是 null */
	tZhFirst: number | null;
}

export interface LatencyReport {
	n: number;
	/** 说完 → 中文首字（毫秒）：中位数 / p90 / 最大值 */
	p50: number | null;
	p90: number | null;
	max: number | null;
	/** 拆分（各取中位数）：段结束 → 封口、封口 → 中文首字 */
	sealP50: number | null;
	zhP50: number | null;
	/** 一句都没译出（界面会显示英文） */
	failed: number;
}

/** 最近的百分位（小样本也稳定；与桌面 `stream_eval._pcts` 同一种取法）。 */
export function percentile(sorted: readonly number[], q: number): number | null {
	if (!sorted.length) return null;
	const i = Math.min(sorted.length - 1, Math.max(0, Math.ceil(q * sorted.length) - 1));
	return sorted[i];
}

export function latencyOf(samples: readonly LatencySample[]): LatencyReport {
	const totals: number[] = [];
	const seals: number[] = [];
	const zhs: number[] = [];
	let failed = 0;
	for (const s of samples) {
		if (s.tZhFirst === null) {
			failed += 1;
			continue;
		}
		// 「说完 → 中文首字」从**音频结束**算起（没有音频结束时刻才退回封口时刻）：
		// 封口那 1 s 是用户真在等的，漏掉它就会把自己量得比桌面快一倍
		const from = typeof s.tAudioEnd === 'number' ? s.tAudioEnd : s.tSealed;
		totals.push(Math.max(0, s.tZhFirst - from));
		if (typeof s.tAudioEnd === 'number') seals.push(Math.max(0, s.tSealed - s.tAudioEnd));
		zhs.push(Math.max(0, s.tZhFirst - s.tSealed));
	}
	totals.sort((a, b) => a - b);
	seals.sort((a, b) => a - b);
	zhs.sort((a, b) => a - b);
	return {
		n: totals.length,
		p50: percentile(totals, 0.5),
		p90: percentile(totals, 0.9),
		max: totals.length ? totals[totals.length - 1] : null,
		sealP50: percentile(seals, 0.5),
		zhP50: percentile(zhs, 0.5),
		failed
	};
}
