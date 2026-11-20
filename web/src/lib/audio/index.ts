// lib/audio —— 麦克风、worklet 重采样、电平。M1 实现。
// 起点：~/classlive-web-spike/s2/web/worklet.js（127-tap sinc → 16 kHz、100 ms 一帧、s16le；S2 实测 WER 追平桌面）。
// 默认约束：echoCancellation 开 / noiseSuppression 关 / autoGainControl 关（R1 结论）。
// 采音走 worklet 还是 MediaRecorder，等 S2 真麦两组结果定。

export interface AudioFrame {
	/** 16 kHz 单声道 s16le，100 ms = 1600 样本 */
	pcm: ArrayBuffer;
	/** 帧序号；封口判据的「推时钟」挂在这个节拍上，不用 setInterval（PLAN A10） */
	seq: number;
}

export interface AudioSource {
	start(onFrame: (f: AudioFrame) => void): Promise<void>;
	stop(): void;
	/** 0..1 电平，给「6 秒没声音提示检查麦克风」用（E1） */
	readonly level: number;
	/** 正在采的流（课堂录音克隆它；换麦后会变成新流）。可选 —— 磁带源没有。 */
	readonly stream?: MediaStream | null;
	/**
	 * 上课中途换输入设备：只换流与源节点，帧序号 / 回调 / 下游连接全保留（W14）。
	 * 可选 —— 磁带源等实现没有。resolve false = 换失败，原设备还在采。
	 */
	switchDevice?(deviceId: string): Promise<boolean>;
	/** 当前设备被拔掉（track ended）时回调一次；可选，实现里只调一次 */
	onDeviceLost?(cb: () => void): void;
}

export { listMics, onDevicesChanged } from './devices';
export type { MicDevice, DevicesDeps, DevicesLike } from './devices';
