import pkg from './package.json' with { type: 'json' };
import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

function buildId(): string {
	const t = new Date().toISOString().replace(/[-:]/g, '').slice(0, 13); // 20261120T0310
	// git 短哈希由 deploy/publish.sh 经环境变量给（这里不引 node 类型，免得为一行加 @types/node）
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const sha = String((globalThis as any).process?.env?.CL_GIT_SHA ?? "").slice(0, 16);
	return sha ? `${t}-${sha}` : t;
}

export default defineConfig({
	// 客户端版本号（`lib/api/identity.ts` 的 X-ClassLive-Version）：单一来源是 package.json。
	// 构建号（`__CL_BUILD__`）：UTC 构建时刻 + git 短哈希，只进遥测（版本头仍是 package.json 版本 ——
	// Worker 每码只留 8 个版本键，每次构建一个新值会把面板版本分布冲掉）。
	define: { __CL_VERSION__: JSON.stringify(pkg.version), __CL_BUILD__: JSON.stringify(buildId()) },
	// 本机开发：线上接口 Worker 还没部署网页 CORS（W3），dev 时同源转发过去（.env.development 里 VITE_API_BASE=/api）
	server: {
		proxy: {
			'/api': {
				target: 'https://asr.bldcam.page',
				changeOrigin: true,
				rewrite: (p) => p.replace(/^\/api/, ''),
				// 网页会话接口按 Origin 白名单放行：本机联调要冒充线上网页的来源时，DEV_PROXY_ORIGIN=https://class.bldcam.page npm run dev
				headers: { origin: (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env?.DEV_PROXY_ORIGIN || 'https://asr.bldcam.page' }
			}
		}
	},
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// 纯静态产物；未预渲染的路由由 200.html 兜底（wrangler 的 SPA 模式）
			adapter: adapter({ fallback: '200.html' })
		})
	]
});
