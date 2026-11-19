<script lang="ts">
	import './layout.css';
	import { onNavigate } from '$app/navigation';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	// 门面 → 上课的原地过渡（View Transitions；Chrome / Edge 有，别的浏览器直接切）
	onNavigate((navigation) => {
		if (!document.startViewTransition) return;
		return new Promise((resolve) => {
			document.startViewTransition(async () => {
				resolve();
				await navigation.complete;
			});
		});
	});
</script>

<svelte:head>
	<link rel="icon" href="/favicon.png" />
</svelte:head>

{@render children()}
