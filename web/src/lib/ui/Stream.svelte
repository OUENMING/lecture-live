<script lang="ts">
	// 字幕流（门面、上课页、小窗共用）：最新一句贴底，旧句往上走、逐级变淡。
	//
	// 顺滑：不给每一行做动画，只给整列做一次 FLIP。以「上一句定稿」为锚，量它离列底的距离；
	// 新句定稿 / 草稿换行让锚往上挪了 d，就先把整列瞬间下移 d、再用一个 transform 过渡滑回 0。只动 transform。
	// 顶上的旧行被删掉不会动锚，所以不会跳。
	//
	// 回看（上课页）：往上滚就停止跟随，旧句全部恢复不透明、可以点；底部出「回到最新」。
	// 滚回底部、点按钮或调 follow() 恢复跟随。门面和小窗不开回看（reviewable=false）。
	import { onMount } from 'svelte';
	import type { CapRow } from '#lib/demo/player.svelte.ts';
	import Grow from './Grow.svelte';

	type Mode = 'both' | 'zh' | 'en';
	let {
		rows,
		draftEn = '',
		draftZh = '',
		mode = 'both',
		marked = new Set<number>(),
		onmark,
		reviewable = false,
		announce = false,
		following = $bindable(true)
	}: {
		rows: CapRow[];
		draftEn?: string;
		draftZh?: string;
		mode?: Mode;
		marked?: ReadonlySet<number>;
		onmark?: (row: CapRow) => void;
		reviewable?: boolean;
		/** 读屏：把最新一句定稿念出来（上课页开，门面 / 小窗不开） */
		announce?: boolean;
		following?: boolean;
	} = $props();

	// 读屏只念最新定稿；Tab 只停一句（最新那句），上下方向键在句子之间走，不用按几百次 Tab 才到侧栏
	const latest = $derived(rows.at(-1));
	function rowKey(e: KeyboardEvent, row: CapRow) {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			onmark?.(row);
			return;
		}
		if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
		const el = e.currentTarget as HTMLElement;
		const next = (e.key === 'ArrowUp' ? el.previousElementSibling : el.nextElementSibling) as HTMLElement | null;
		if (!next?.dataset.id) return;
		e.preventDefault();
		following = false;
		next.focus();
	}

	let vp: HTMLDivElement;
	let col: HTMLDivElement;
	const DUR = 420;

	/** 跳到某一句（侧栏里点标记 / 时间轴用） */
	export function jumpTo(id: number) {
		const el = col.querySelector<HTMLElement>(`[data-id="${id}"]`);
		if (!el) return;
		following = false;
		el.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
		el.classList.remove('flash');
		void el.offsetWidth;
		el.classList.add('flash');
	}
	export function follow() {
		following = true;
		vp.scrollTop = vp.scrollHeight;
	}

	onMount(() => {
		const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
		let anchor: HTMLElement | null = null;
		let anchorDist = 0;
		const dist = (el: HTMLElement) => col.offsetHeight - el.offsetTop;
		const pickAnchor = () => {
			const rowsEls = col.querySelectorAll<HTMLElement>('.row[data-id]');
			anchor = rowsEls[rowsEls.length - 1] ?? null;
			anchorDist = anchor ? dist(anchor) : 0;
		};
		pickAnchor();
		vp.scrollTop = vp.scrollHeight;

		const ro = new ResizeObserver(() => {
			if (!following) {
				pickAnchor();
				return;
			}
			const d = anchor && anchor.isConnected ? dist(anchor) - anchorDist : 0;
			vp.scrollTop = vp.scrollHeight;
			pickAnchor();
			if (!d || reduce) return;
			// 还在滑的话，从此刻的位置接着滑，不会顿一下
			const now = new DOMMatrixReadOnly(getComputedStyle(col).transform).m42;
			col.style.transition = 'none';
			col.style.transform = `translate3d(0, ${now + d}px, 0)`;
			void col.offsetHeight;
			col.style.transition = `transform ${DUR}ms var(--ease)`;
			col.style.transform = 'translate3d(0, 0, 0)';
		});
		ro.observe(col);
		return () => ro.disconnect();
	});

	// 只有用户往上的意图才停止跟随；滚到底自动恢复
	function onwheel(e: WheelEvent) {
		if (reviewable && e.deltaY < 0 && vp.scrollHeight > vp.clientHeight) following = false;
	}
	let touchY = 0;
	function ontouchstart(e: TouchEvent) {
		touchY = e.touches[0].clientY;
	}
	function ontouchmove(e: TouchEvent) {
		if (reviewable && e.touches[0].clientY - touchY > 8) following = false;
	}
	function onscroll() {
		if (!following && vp.scrollHeight - vp.scrollTop - vp.clientHeight < 4) following = true;
	}
</script>

<div class="wrap" data-mode={mode} class:reviewing={!following}>
	<div
		class="viewport"
		role="log"
		aria-live="off"
		aria-label="字幕"
		class:locked={!reviewable}
		bind:this={vp}
		{onwheel}
		{ontouchstart}
		{ontouchmove}
		{onscroll}
	>
		<div class="col" bind:this={col}>
			{#each rows as row, i (row.id)}
				<!-- svelte-ignore a11y_no_noninteractive_tabindex -- 只有传了 onmark 时才有 role=button 和 tabindex -->
				<div
					class="row"
					class:marked={marked.has(row.id)}
					class:markable={!!onmark}
					data-id={row.id}
					style:--age={rows.length - 1 - i}
					role={onmark ? 'button' : undefined}
					tabindex={onmark ? (i === rows.length - 1 ? 0 : -1) : undefined}
					aria-pressed={onmark ? marked.has(row.id) : undefined}
					aria-label={onmark ? `${marked.has(row.id) ? '取消标记' : '标记没听懂'}：${row.zh || row.en}` : undefined}
					onclick={onmark ? () => onmark(row) : undefined}
					onkeydown={onmark ? (e) => rowKey(e, row) : undefined}
				>
					<p class="en" lang="en">{row.en}</p>
					<p class="zh" lang="zh-CN">{row.zh || row.en}</p>
				</div>
			{/each}
			<div class="row draft" class:empty={!draftEn && !draftZh}>
				<p class="en" lang="en"><Grow text={draftEn} /></p>
				<p class="zh" lang="zh-CN"><Grow text={draftZh} /></p>
			</div>
		</div>
	</div>
	{#if announce}
		<p class="sr" aria-live="polite">{latest ? latest.zh || latest.en : ''}</p>
	{/if}
	{#if reviewable && !following}
		<button class="latest" type="button" onclick={follow}>
			<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3.5 8.5 8 13l4.5-4.5" /></svg>
			回到最新
		</button>
	{/if}
</div>

<style>
	.wrap {
		position: relative;
		height: 100%;
		/* 字号在这里按 --cap-scale 现算：:root 上的 --cap-zh 已经在根上算死了，子元素改 --cap-scale 不会传上去 */
		--z: calc(var(--cap-base, 18px) * var(--cap-scale, 1));
	}
	.viewport {
		height: 100%;
		overflow-y: auto;
		overscroll-behavior: contain;
		scrollbar-width: none;
		/* 顶部淡出：旧句子慢慢退场，而不是被一刀切 */
		mask-image: linear-gradient(to bottom, transparent 0, #000 32%);
		transition: mask-image var(--move);
	}
	.viewport::-webkit-scrollbar {
		display: none;
	}
	.viewport.locked {
		overflow: hidden;
	}
	.reviewing .viewport {
		mask-image: linear-gradient(to bottom, transparent 0, #000 10%, #000 88%, transparent);
	}
	.col {
		min-height: 100%;
		display: flex;
		flex-direction: column;
		justify-content: flex-end;
		gap: calc(var(--z) * 0.9);
		/* 左右留出悬停底色和标记竖线的位置，不然会被滚动容器切掉 */
		padding: 0 calc(var(--z) * 0.75) 2px;
		will-change: transform;
	}
	.row {
		position: relative;
		max-width: 34em;
		font-size: var(--z);
		/* 越旧越淡：最新定稿 1，往上每行降一档，最淡 --age-floor；回看时全部恢复 */
		opacity: max(var(--age-floor, 0.5), calc(1 - var(--age) * 0.14));
		transition: opacity var(--move) var(--ease);
	}
	.reviewing .row {
		opacity: 0.86;
	}
	p {
		margin: 0;
		text-wrap: pretty;
		overflow-wrap: anywhere;
	}
	.en {
		font-size: calc(var(--z) * var(--en-ratio, 0.58));
		line-height: 1.45;
		letter-spacing: 0.003em;
		color: var(--on-glass-2);
	}
	.zh {
		margin-top: 0.18em;
		font-size: var(--z);
		line-height: 1.42;
		font-weight: 500;
		letter-spacing: 0.012em;
		color: var(--on-glass);
		font-feature-settings: 'palt';
	}
	[data-mode='zh'] .en,
	[data-mode='en'] .zh {
		display: none;
	}
	[data-mode='en'] .en {
		font-size: calc(var(--z) * 0.86);
		line-height: 1.42;
		color: var(--on-glass);
	}
	[data-mode='zh'] .zh {
		margin-top: 0;
	}
	.draft {
		opacity: 1;
	}
	.draft .en {
		color: var(--draft-en);
	}
	.draft .zh {
		color: var(--draft-zh);
		font-weight: 400;
	}
	.draft.empty {
		visibility: hidden;
	}
	.draft p:empty::before {
		content: '\200b';
	}

	/* 可标记：悬停露出一圈底色；标记过的句子左边一根竖线（不用彩色：强调 = 更亮 + 线） */
	.markable {
		cursor: pointer;
		border-radius: var(--radius-s);
	}
	.markable::after {
		content: '';
		position: absolute;
		inset: -0.25em -0.5em;
		border-radius: inherit;
		background: var(--glass-hover);
		opacity: 0;
		transition: opacity var(--fast);
		pointer-events: none;
	}
	.markable:hover::after,
	.markable:focus-visible::after {
		opacity: 1;
	}
	.markable:focus-visible {
		outline: none;
	}
	.marked {
		opacity: 1;
	}
	.marked::before {
		content: '';
		position: absolute;
		left: -0.75em;
		top: 0.2em;
		bottom: 0.2em;
		width: 3px;
		border-radius: var(--radius-pill);
		background: var(--on-glass);
	}
	.row:global(.flash)::after {
		/* 跳回某句时那一下高亮：要停够久让眼睛找到，是唯一超过 --stage 的时长 */
		animation: flash 1400ms ease-out;
	}
	@keyframes flash {
		0%,
		30% {
			opacity: 1;
		}
		100% {
			opacity: 0;
		}
	}

	.latest {
		position: absolute;
		left: 50%;
		bottom: 8px;
		transform: translateX(-50%);
		display: inline-flex;
		align-items: center;
		gap: 6px;
		height: 36px;
		padding: 0 16px 0 12px;
		border: 0;
		border-radius: var(--radius-pill);
		background: var(--on-glass);
		color: var(--glass-ground);
		font: 600 var(--fs-s) / 1 var(--cl-font);
		cursor: pointer;
		box-shadow: 0 10px 30px -10px oklch(0 0 0 / 0.5);
		animation: pop var(--move) var(--ease) both;
	}
	.latest:active {
		transform: translateX(-50%) scale(0.97);
	}
	.latest svg {
		width: 15px;
		height: 15px;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.6;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}
	@keyframes pop {
		from {
			opacity: 0;
			transform: translate(-50%, 8px);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.latest {
			animation: none;
		}
	}
</style>
