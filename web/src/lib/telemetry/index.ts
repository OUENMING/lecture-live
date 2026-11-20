// lib/telemetry/index.ts —— 网页版遥测与课堂录音的对外出口（只做 re-export，不含逻辑）。
// 界面 / 会话层只从这里 import：`createTelemetry`（缓冲发送）+ `installCollectors`（被动采集）
// + `createClassRecorder` / `resumePending`（录音分段落库与上传）。
// 接线（什么时候建、传给谁）不在这一层 —— 这一层任何函数都不改上课行为。

export { createTelemetry } from './buffer';
export type { Telemetry, TelemetryEvent, TelemetryOptions, TelemetrySend } from './buffer';

export { installCollectors } from './collect';
export type { CollectorOptions } from './collect';

export { createClassRecorder, resumePending } from './recorder';
export type { AudioUpload, ClassRecorder, RecorderOptions, ResumeOptions } from './recorder';

export { createAudioStore } from './audio-store';
export type { AudioChunk, AudioStore } from './audio-store';

export { MAX_BATCH_BYTES, MAX_BATCH_EVENTS } from './sender';
export { putChunk } from './upload';
