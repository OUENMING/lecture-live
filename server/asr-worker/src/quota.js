// Durable Object 壳：单实例，全部逻辑在 quota-core.js（那边不依赖 cloudflare:*，才测得了）。
// 排查用 `wrangler tail`；故意不做查询 / 管理路由。
import { DurableObject } from "cloudflare:workers";
import { QuotaCore } from "./quota-core.js";

export class Quota extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new QuotaCore(ctx.storage);
  }
  fetch(request) { return this.core.handle(request); }
}
