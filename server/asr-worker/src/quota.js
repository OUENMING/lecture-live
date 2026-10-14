// Durable Object 壳：单实例，全部逻辑在 quota-core.js（那边不依赖 cloudflare:*，才测得了）。
// 排查用 `wrangler tail`。
// ⚠️ 2026-10-14 加了**一条**只读路由 `/admin/state`（管理面板的系统额度卡要用）。
//    其余照样不做查询 / 管理路由。注意**鉴权不在这一层** —— 只有 handler.js 的 admin()（认 ADMIN_TOKEN）
//    会把请求转发进来，DO 谁都信；所以 `/admin/state` 绝不能从别的路径暴露。
import { DurableObject } from "cloudflare:workers";
import { QuotaCore } from "./quota-core.js";

export class Quota extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new QuotaCore(ctx.storage);
  }
  fetch(request) { return this.core.handle(request); }
}
