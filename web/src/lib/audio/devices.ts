// lib/audio/devices.ts —— 麦克风设备清单 + 插拔通知（W14）。
//
// 对外：listMics() → [{ deviceId, label }]；onDevicesChanged(cb) → 取消订阅函数。
// 为什么 label 允许空串：没授权麦克风之前浏览器不给名字（Chrome / Safari 都一样）；
// 本模块不编假名字，界面自己决定显示「麦克风 1」这类占位。
// 为什么取依赖可注入：node 测试里没有 navigator.mediaDevices，注入假 MediaDevices 即可测。
// 降级：没有 mediaDevices（非安全上下文 / 老浏览器）或 enumerateDevices 抛错 → 空清单、空订阅，绝不抛。

export interface MicDevice {
	/** 设备稳定 id（没授权时也可能为空串，此时只能当「默认输入」用） */
	deviceId: string;
	label: string;
}

/** 只需要这几个方法；MediaDevices 的真身与测试替身都满足 */
export type DevicesLike = {
	enumerateDevices(): Promise<ArrayLike<{ kind?: string; deviceId?: string; label?: string }>>;
	addEventListener?(type: 'devicechange', cb: (ev: Event) => void): void;
	removeEventListener?(type: 'devicechange', cb: (ev: Event) => void): void;
};

export interface DevicesDeps {
	/** 不传则取全局 navigator.mediaDevices；显式传 null 表示「没有」 */
	mediaDevices?: DevicesLike | null;
}

function devicesOf(deps: DevicesDeps): DevicesLike | null {
	if (deps.mediaDevices !== undefined) return deps.mediaDevices;
	if (typeof navigator === 'undefined') return null;
	return (navigator.mediaDevices as unknown as DevicesLike | undefined) ?? null;
}

/** 列出输入设备；拿不到就是空数组（界面按「只剩默认」处理） */
export async function listMics(deps: DevicesDeps = {}): Promise<MicDevice[]> {
	const md = devicesOf(deps);
	if (!md) return [];
	try {
		const all = await md.enumerateDevices();
		const out: MicDevice[] = [];
		for (let i = 0; i < all.length; i++) {
			const d = all[i];
			if (d?.kind !== 'audioinput') continue;
			out.push({ deviceId: d.deviceId ?? '', label: d.label ?? '' });
		}
		return out;
	} catch {
		return [];
	}
}

/** 插拔 / 授权变化时回调（`devicechange`）；返回取消订阅函数 */
export function onDevicesChanged(cb: () => void, deps: DevicesDeps = {}): () => void {
	const md = devicesOf(deps);
	if (!md || typeof md.addEventListener !== 'function') return () => {};
	const handler = () => cb();
	try {
		md.addEventListener('devicechange', handler);
	} catch {
		return () => {};
	}
	let off = false;
	return () => {
		if (off) return;
		off = true;
		try {
			md.removeEventListener?.('devicechange', handler);
		} catch {
			/* 已经摘掉了 */
		}
	};
}
