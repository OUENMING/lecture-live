<script lang="ts">
	// 置顶小窗（Document PiP）里的字幕：和主页面读同一个字幕源（演示回放或真会话），只留最后几行定稿 + 草稿。
	// 学生上课时真正盯着的是这扇小窗，所以常用的事在这里就能做完：点一句 = 标记没听懂；
	// 鼠标移进来右上角露出 A− A+ / 标记最新 / 回到大窗，移开就藏，不挡字。
	// 状态出岔子（断网、没声音）顶上挂一条，正常时什么都不显示。字号 / 模式 / 主题跟主页面走。
	import Stream from './Stream.svelte';
	import type { CapRow } from '#lib/demo/player.svelte.ts';

	type CapSource = { readonly rows: CapRow[]; readonly draftEn: string; readonly draftZh: string };
	type View = { status: string; ok: boolean; scale: number; mode: 'both' | 'zh' | 'en'; marked?: ReadonlySet<number> };
	let {
		player,
		theme,
		view = () => ({ status: '', ok: true, scale: 1, mode: 'both' }),
		onmark,
		onmarkLatest,
		onscale,
		onback
	}: {
		player: CapSource;
		theme: () => 'dark' | 'light';
		view?: () => View;
		onmark?: (row: CapRow) => void;
		onmarkLatest?: () => void;
		onscale?: (delta: number) => void;
		onback?: () => void;
	} = $props();
	const tail = $derived(player.rows.slice(-4));
	const v = $derived(view());
	let flash = $state(false);
	function markLatest() {
		onmarkLatest?.();
		flash = true;
		setTimeout(() => (flash = false), 900);
	}
</script>

<div class="pip" data-cap={theme()} style:--cap-scale={Math.min(v.scale, 1.6)}>
	{#if !v.ok}
		<p class="warn" role="status">{v.status}</p>
	{/if}
	<div class="body">
		<Stream rows={tail} draftEn={player.draftEn} draftZh={player.draftZh} mode={v.mode} marked={v.marked} {onmark} />
	</div>
	<div class="ctl" role="toolbar" aria-label="小窗控制">
		{#if onscale}
			<button type="button" onclick={() => onscale(-1)} aria-label="字小一点" title="字小一点（−）"><span class="a sm">A</span></button>
			<button type="button" onclick={() => onscale(1)} aria-label="字大一点" title="字大一点（+）"><span class="a">A</span></button>
		{/if}
		{#if onmarkLatest}
			<button type="button" class:done={flash} onclick={markLatest} aria-label="标记最新一句没听懂" title="标记最新一句（M）">
				<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11M4 3h7.5l-1.8 3 1.8 3H4" /></svg>
			</button>
		{/if}
		{#if onback}
			<button type="button" onclick={onback} aria-label="回到大窗" title="回到大窗（P）">
				<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="1.5" y="3" width="13" height="10" rx="2" /><path d="M7 9.5 11 5.5M8 5.5h3v3" /></svg>
			</button>
		{/if}
	</div>
</div>

<style>
	.pip {
		position: relative;
		height: 100dvh;
		box-sizing: border-box;
		display: grid;
		grid-template-rows: auto minmax(0, 1fr);
		padding: 10px 16px 12px;
		/* 小窗本身是一块不透明窗口：后面没有可模糊的东西，磨砂退成实色 */
		background: var(--glass-ground);
		color: var(--on-glass);
		font-family: var(--cl-font);
		-webkit-font-smoothing: antialiased;
	}
	.body {
		/* 钉在第 2 行（1fr）：没告警时它不该落进 auto 行，否则高度退成内容高、字幕不贴底（OCR C） */
		grid-row: 2;
		min-height: 0;
	}
	.warn {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 0 0 6px;
		font-size: var(--fs-xs);
		font-weight: 600;
	}
	.warn::before {
		content: '';
		width: 7px;
		height: 7px;
		border-radius: 50%;
		box-shadow: inset 0 0 0 1.5px currentColor;
	}

	/* 控制条：平时不在，鼠标进窗或键盘聚焦才浮出；底色和窗同色，压在最旧那行（已经淡出）上面 */
	.ctl {
		position: absolute;
		top: 6px;
		right: 6px;
		display: flex;
		gap: 2px;
		padding: 2px;
		border-radius: var(--radius-s);
		background: var(--glass-ground);
		box-shadow: inset 0 0 0 1px var(--glass-line);
		opacity: 0;
		pointer-events: none; /* 看不见就点不到：别让点字幕误触「回到大窗」 */
		transform: translateY(-4px);
		transition:
			opacity var(--fast) var(--ease),
			transform var(--fast) var(--ease);
	}
	.pip:hover .ctl,
	.ctl:focus-within {
		opacity: 1;
		pointer-events: auto;
		transform: none;
	}
	@media (hover: none) {
		.ctl {
			opacity: 1;
			pointer-events: auto;
			transform: none;
		}
	}
	.ctl button {
		width: 28px;
		height: 28px;
		display: grid;
		place-items: center;
		padding: 0;
		border: 0;
		border-radius: var(--radius-xs);
		background: none;
		color: var(--on-glass-2);
		cursor: pointer;
		transition:
			background-color var(--fast) var(--ease),
			color var(--fast) var(--ease);
	}
	.ctl button:hover,
	.ctl button.done {
		background: var(--glass-hover);
		color: var(--on-glass);
	}
	.ctl button:focus-visible {
		outline: 2px solid var(--on-glass);
		outline-offset: -2px;
	}
	.a {
		font: 600 var(--fs-s) / 1 var(--cl-font);
	}
	.a.sm {
		font-size: var(--fs-xs);
	}
	svg {
		width: 15px;
		height: 15px;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.4;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	@media (prefers-reduced-motion: reduce) {
		.ctl {
			transform: none;
		}
	}
</style>
