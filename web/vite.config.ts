import pkg from './package.json' with { type: 'json' };
import tailwindcss from '@tailwindcss/vite';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	// 客户端版本号（`lib/api/identity.ts` 的 X-ClassLive-Version）：单一来源是 package.json。
	define: { __CL_VERSION__: JSON.stringify(pkg.version) },
	// 本机开发：线上接口 Worker 还没部署网页 CORS（W3），dev 时同源转发过去（.env.development 里 VITE_API_BASE=/api）
	server: {
		proxy: {
			'/api': {
				target: 'https://asr.bldcam.page',
				changeOrigin: true,
				rewrite: (p) => p.replace(/^\/api/, ''),
				headers: { origin: 'https://asr.bldcam.page' }
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
