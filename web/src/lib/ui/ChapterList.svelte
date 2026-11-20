<script lang="ts">
	// 实时总结的章节列表（上课页侧栏 + 课后页笔记出来之前都用它）。纯展示：数据、跳转都由外面给。
	// 一条细竖线串起各章（时间顺序），正在讲的那章圆点在呼吸；封好的章默认收起成「标题 + 两行」，点开看全文和要点。
	// 颜色只走 --cl-sum-* 三档（外面按玻璃 / 纸面各给一套），默认是玻璃面的值。
	import type { SummaryChapter } from '#lib/api/index.ts';

	interface Props {
		chapters: SummaryChapter[];
		/** 这章第一句是第几分几秒（拿不到回 null，就不显示时间） */
		timeOf?: (seq: number) => string | null;
		/** 给了就在展开处出「跳到这段字幕」；canJump 判这一句还在不在屏上 */
		onjump?: (seq: number) => void;
		canJump?: (seq: number) => boolean;
		/** 课后：没有「正在讲」，所有章都按封好的画 */
		ended?: boolean;
	}
	let { chapters, timeOf, onjump, canJump, ended = false }: Props = $props();

	let open = $state<Record<number, boolean>>({});
	const toggle = (idx: number) => (open[idx] = !open[idx]);
</script>

<ol class="chapters">
	{#each chapters as c (c.idx)}
		{@const live = !ended && !c.closed}
		{@const expanded = live || !!open[c.idx]}
		{@const t = timeOf?.(c.start_seq) ?? null}
		<li class:live class:expanded>
			<span class="node" aria-hidden="true"></span>
			{#if live}
				<div class="head static">
					<span class="title">{c.title || '这一章'}<span class="now">正在讲</span></span>
					{#if t}<span class="meta"><span class="t">{t}</span></span>{/if}
				</div>
			{:else}
				<button class="head" type="button" aria-expanded={expanded} onclick={() => toggle(c.idx)}>
					<span class="title">{c.title || `第 ${c.idx} 章`}</span>
					{#if t}<span class="meta"><span class="t">{t}</span></span>{/if}
				</button>
			{/if}
			{#if c.summary}
				<p class="sum" class:clamp={!expanded}>{c.summary}</p>
			{/if}
			{#if expanded && c.points.length}
				<ul class="pts">
					{#each c.points as p, i (i)}<li>{p}</li>{/each}
				</ul>
			{/if}
			{#if expanded && !live && onjump && (canJump?.(c.start_seq) ?? true)}
				<button class="jump" type="button" onclick={() => onjump(c.start_seq)}>
					跳到这段字幕
					<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" /></svg>
				</button>
			{/if}
		</li>
	{/each}
</ol>

<style>
	.chapters {
		--fg: var(--cl-sum-1, var(--on-glass));
		--fg-2: var(--cl-sum-2, var(--on-glass-2));
		--fg-3: var(--cl-sum-3, var(--on-glass-3));
		--line: var(--cl-sum-line, var(--glass-line));
		--hover: var(--cl-sum-hover, var(--glass-hover));
		--ground: var(--cl-sum-ground, var(--glass-ground));
		position: relative;
		margin: 0;
		padding: 0;
		list-style: none;
		color: var(--fg);
	}
	.chapters > li {
		position: relative;
		padding: 0 0 18px 22px;
		animation: in var(--move) var(--ease) both;
	}
	/* 竖线：从这一章的圆点连到下一章；最后一章不画尾巴 */
	.chapters > li:not(:last-child)::before {
		content: '';
		position: absolute;
		left: 3.5px;
		top: 14px;
		bottom: -6px;
		width: 1px;
		background: var(--line);
	}
	@keyframes in {
		from {
			opacity: 0;
			transform: translateY(6px);
		}
	}
	.node {
		position: absolute;
		left: 0;
		top: 7px;
		width: 8px;
		height: 8px;
		border-radius: var(--radius-pill);
		background: var(--fg-3);
		box-shadow: 0 0 0 3px var(--ground);
	}
	.live .node {
		background: var(--fg);
	}
	/* 正在讲：圆点外一圈慢慢扩散（只有这一处动；减弱动效时关掉） */
	.live .node::after {
		content: '';
		position: absolute;
		inset: -4px;
		border-radius: inherit;
		box-shadow: 0 0 0 1px var(--fg-2);
		animation: breathe 2.4s var(--ease) infinite;
	}
	@keyframes breathe {
		0% {
			opacity: 0.9;
			transform: scale(0.6);
		}
		100% {
			opacity: 0;
			transform: scale(1.6);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.live .node::after {
			animation: none;
			opacity: 0.5;
		}
	}

	.head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 10px;
		width: calc(100% + 12px);
		margin: 0 -6px;
		padding: 2px 6px 4px;
		border: 0;
		border-radius: var(--radius-xs);
		background: none;
		color: inherit;
		font: inherit;
		text-align: left;
	}
	button.head {
		cursor: pointer;
		transition: background-color var(--fast) var(--ease);
	}
	button.head:hover {
		background: var(--hover);
	}
	.head:focus-visible,
	.jump:focus-visible {
		outline: 2px solid var(--fg);
		outline-offset: 1px;
	}
	.title {
		min-width: 0;
		font-size: var(--fs-m);
		font-weight: 600;
		line-height: 1.45;
		overflow-wrap: anywhere;
		text-wrap: pretty;
	}
	.meta {
		flex: none;
		display: inline-flex;
		align-items: baseline;
		gap: 8px;
		font-size: var(--fs-xs);
		color: var(--fg-3);
	}
	.t {
		font-variant-numeric: tabular-nums;
	}
	/* 「正在讲」跟在标题字后面走，不占右边一栏（那样会把长标题挤成两截） */
	.now {
		display: inline-block;
		margin-left: 8px;
		padding: 0 7px;
		border-radius: var(--radius-pill);
		box-shadow: inset 0 0 0 1px var(--line);
		font-size: var(--fs-xs);
		font-weight: 500;
		line-height: 18px;
		color: var(--fg-2);
		vertical-align: 1px;
		white-space: nowrap;
	}
	.sum {
		margin: 2px 0 0;
		font-size: var(--fs-s);
		line-height: 1.7;
		color: var(--fg-2);
		overflow-wrap: anywhere;
	}
	.live .sum {
		color: var(--fg);
	}
	.sum.clamp {
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.pts {
		margin: 8px 0 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: 6px;
		font-size: var(--fs-s);
		line-height: 1.6;
		color: var(--fg-2);
	}
	.pts li {
		position: relative;
		padding-left: 12px;
	}
	.pts li::before {
		content: '';
		position: absolute;
		left: 0;
		top: 0.72em;
		width: 4px;
		height: 1px;
		background: var(--fg-3);
	}
	.jump {
		display: inline-flex;
		align-items: center;
		gap: 2px;
		margin: 8px 0 0 -6px;
		padding: 4px 6px;
		border: 0;
		border-radius: var(--radius-xs);
		background: none;
		color: var(--fg-2);
		font: 500 var(--fs-xs) / 1.4 var(--cl-font);
		cursor: pointer;
		transition:
			background-color var(--fast) var(--ease),
			color var(--fast) var(--ease);
	}
	.jump:hover {
		background: var(--hover);
		color: var(--fg);
	}
	.jump svg {
		width: 12px;
		height: 12px;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.5;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
</style>
