// classlive-panel-proxy（cl-proxy.bldcam.page）：远程面板 data.bldcam.page/cl/ 的只读 CORS 代理。
// 最早是 Muse 2026-11-22 放上去的（给它自己的看板用，不要 key）；2026-11-23 改成：
//   · 要 key：`Authorization: Bearer <PROXY_KEY>` 或 `?key=<PROXY_KEY>`（Worker secret，和面板 key 不是同一把）。
//     ⚠️ 不要 key 时任何人都能读到全部通行码 / 用量 / 余额 / 课堂原文 —— 远程面板 11-23 起回完整码了。
//   · 放行面与远程面板一致：/、/local、/api/*、/raw/<id>、/note/<id>、/assets/katex/*（面板 key 只在这里，不进浏览器）。
//   · 只有 GET（写操作远程面板本来就 403，这里不转 POST）。
var UPSTREAM_BASE = 'https://data.bldcam.page/cl';
var EXACT = ['/', '/local', '/digest', '/digest.md'];
var PREFIX = ['/api/', '/raw/', '/note/', '/assets/katex/'];

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    'Access-Control-Max-Age': '86400'
  };
}

function same(a, b) {                                   // 等长逐字比较，不早退
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  var d = 0;
  for (var i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

function deny(status, msg) {
  var h = corsHeaders();
  h['Content-Type'] = 'application/json';
  return new Response(JSON.stringify({ error: msg }), { status: status, headers: h });
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: corsHeaders() });
    if (request.method !== 'GET') return deny(405, 'read-only: GET only');
    var url = new URL(request.url);
    var path = url.pathname;
    if (path.length > 1 && path.endsWith('/')) path = path.slice(0, -1);
    var isAsset = path.indexOf('/assets/katex/') === 0;       // 开源库，沙箱里的笔记页带不上 key
    if (!isAsset) {
      var got = (request.headers.get('authorization') || '').replace(/^Bearer\s+/i, '') || url.searchParams.get('key') || '';
      if (!env.PROXY_KEY || !same(got, env.PROXY_KEY)) return deny(401, 'need key: Authorization: Bearer <key> or ?key=<key>');
    }
    if (EXACT.indexOf(path) === -1 && !PREFIX.some(function (p) { return path.indexOf(p) === 0; })) return deny(404, 'not found');
    var upstream = new URL(UPSTREAM_BASE + path);
    upstream.search = url.search;
    upstream.searchParams.delete('key');
    if (path.indexOf('/api/') === 0 && !upstream.searchParams.has('fresh')) upstream.searchParams.set('fresh', '1');
    try {
      var res = await fetch(upstream.toString(), { method: 'GET', headers: { 'Authorization': 'Bearer ' + env.PANEL_KEY } });
      var headers = new Headers(res.headers);
      headers.set('Access-Control-Allow-Origin', '*');
      headers.set('Cache-Control', 'no-store');
      headers.delete('Set-Cookie');
      return new Response(res.body, { status: res.status, headers: headers });
    } catch (e) {
      return deny(502, 'upstream_failed');
    }
  }
};
