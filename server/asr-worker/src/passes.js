// Durable Object 壳：通行码表 / 设备 / 用量。全部逻辑在 passes-core.js（不依赖 cloudflare:*，才测得了）。
import { DurableObject } from "cloudflare:workers";
import { PassCore } from "./passes-core.js";

export class Passes extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new PassCore(ctx.storage);
  }
  fetch(request) { return this.core.handle(request); }
}
