<script lang="ts">
	let { current = '' }: { current?: '' | 'about' | 'after' } = $props();
</script>

<header class="top">
	<a class="mark" href="/" aria-label="ClassLive 开课页">
		<img src="/favicon.png" alt="" width="22" height="22" />
		<span>ClassLive</span>
	</a>
	<nav>
		<a href="/after" aria-current={current === 'after' ? 'page' : undefined}>我的课</a>
		<a href="/about" aria-current={current === 'about' ? 'page' : undefined}>功能介绍</a>
		<a href="/about#mac">Mac 桌面版</a>
		<span class="links">
			<a class="icon" href="https://bldcam.page" target="_blank" rel="noopener" aria-label="菠萝丁的摄影集" title="菠萝丁的摄影集">
				<svg viewBox="0 0 24 24" aria-hidden="true"
					><path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2.2l1.4-2h5.8l1.4 2h2.2A1.5 1.5 0 0 1 20 8.5v9A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z" /><circle
						cx="12"
						cy="13"
						r="3.4"
					/></svg
				>
			</a>
			<a class="icon" href="https://github.com/OUENMING" target="_blank" rel="noopener" aria-label="GitHub" title="GitHub">
				<!-- GitHub 官方 mark（Octicons mark-github） -->
				<svg viewBox="0 0 16 16" aria-hidden="true" class="solid"
					><path
						d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"
					/></svg
				>
			</a>
		</span>
	</nav>
</header>

<style>
	.top {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 22px clamp(16px, 4vw, 56px);
		font-family: var(--cl-font);
	}
	.mark {
		display: inline-flex;
		align-items: center;
		gap: 9px;
		color: var(--ink);
		text-decoration: none;
		font-weight: 600;
		font-size: var(--fs-m);
		letter-spacing: -0.01em;
	}
	.mark img {
		border-radius: var(--radius-xs);
	}
	nav {
		display: flex;
		align-items: center;
		gap: 22px;
	}
	/* 作者的两个外链：只用黑白图标，和导航文字之间一根细竖线隔开 */
	.links {
		display: flex;
		align-items: center;
		gap: 4px;
		padding-left: 14px;
		border-left: 1px solid var(--rule);
	}
	.icon {
		width: 30px;
		height: 30px;
		display: grid;
		place-items: center;
		border-radius: var(--radius-xs);
	}
	.icon:hover {
		background: var(--paper-2);
	}
	.icon svg {
		width: 20px;
		height: 20px;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.5;
		stroke-linejoin: round;
	}
	.icon svg.solid {
		width: 17px;
		height: 17px;
		fill: currentColor;
		stroke: none;
	}
	nav a {
		color: var(--ink-2);
		text-decoration: none;
		font-size: var(--fs-s);
		transition: color var(--fast) var(--ease);
	}
	nav a:hover,
	nav a[aria-current='page'] {
		color: var(--ink);
	}
	nav a:focus-visible,
	.mark:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 4px;
		border-radius: var(--radius-xs);
	}
	/* 窄屏让出「Mac 桌面版」，「我的课」是回到字幕的唯一入口，不能藏 */
	@media (max-width: 520px) {
		nav {
			gap: 16px;
		}
		nav > a[href='/about#mac'] {
			display: none;
		}
		.links {
			padding-left: 8px;
		}
	}
</style>
