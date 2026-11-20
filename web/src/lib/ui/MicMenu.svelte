<script lang="ts">
	// 上课页工具条的「换麦克风」：列出浏览器给的输入设备，点一个就换（识别不断，帧序号连续 —— lib/audio/mic.ts switchDevice）。
	// 选过的设备记在本机（cl.mic），下次开课直接用它。没授权前浏览器不给设备名，显示「麦克风 1 / 2…」。
	import { onMount } from 'svelte';
	import { listMics, onDevicesChanged, type MicDevice } from '#lib/audio/devices.ts';

	let {
		onpick,
		current = '',
		open = $bindable(false),
		compact = true
	}: {
		/** 选中一个设备；返回 false = 没换成（界面保持原选中） */
		onpick: (deviceId: string) => Promise<boolean>;
		current?: string;
		open?: boolean;
		/** true = 工具条图标按钮；false = 文字按钮（「没听到声音」空态里用） */
		compact?: boolean;
	} = $props();

	let mics = $state<MicDevice[]>([]);
	let picked = $state('');
	let busy = $state(false);
	let failed = $state('');
	let root = $state<HTMLDivElement>();
	const selected = $derived(picked || current);

	async function refresh() {
		mics = await listMics();
	}

	onMount(() => {
		void refresh();
		const off = onDevicesChanged(() => void refresh());
		const onDown = (e: PointerEvent) => {
			if (open && root && !root.contains(e.target as Node)) open = false;
		};
		document.addEventListener('pointerdown', onDown);
		return () => {
			off();
			document.removeEventListener('pointerdown', onDown);
		};
	});

	async function pick(id: string) {
		if (busy) return;
		busy = true;
		failed = '';
		const ok = await onpick(id).catch(() => false);
		busy = false;
		if (ok) {
			picked = id;
			open = false;
			try {
				localStorage.setItem('cl.mic', id);
			} catch {
				/* 不记也能用 */
			}
		} else {
			failed = '这个麦克风打不开，换一个试试';
		}
	}

	function toggle() {
		open = !open;
		if (open) void refresh();
	}
</script>

<div class="mic" bind:this={root}>
	<button
		class={compact ? 'tool' : 'soft'}
		type="button"
		onclick={toggle}
		aria-expanded={open}
		aria-haspopup="menu"
		aria-label="换麦克风"
		title="换麦克风"
	>
		{#if compact}
			<svg viewBox="0 0 16 16" aria-hidden="true"
				><rect x="5.5" y="1.5" width="5" height="8.5" rx="2.5" /><path d="M3 7.5a5 5 0 0 0 10 0M8 12.5v2" /></svg
			>
		{:else}
			换一个麦克风
		{/if}
	</button>
	{#if open}
		<div class="menu" role="menu" aria-label="选麦克风">
			{#if mics.length === 0}
				<p class="empty">浏览器没给出任何麦克风。看看系统设置里有没有接上。</p>
			{:else}
				{#each mics as m, i (m.deviceId || i)}
					<button
						type="button"
						role="menuitemradio"
						aria-checked={selected === m.deviceId || (!selected && i === 0)}
						disabled={busy}
						onclick={() => pick(m.deviceId)}
					>
						<span class="tick" aria-hidden="true"></span>
						<span class="name">{m.label || `麦克风 ${i + 1}`}</span>
					</button>
				{/each}
			{/if}
			{#if failed}<p class="err" role="alert">{failed}</p>{/if}
		</div>
	{/if}
</div>

<style>
	.mic {
		position: relative;
		display: inline-flex;
	}
	.tool {
		height: 34px;
		min-width: 34px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 0 8px;
		border: 0;
		border-radius: var(--radius-s);
		background: transparent;
		color: var(--on-glass-2);
		cursor: pointer;
		transition:
			background-color var(--fast) var(--ease),
			color var(--fast) var(--ease);
	}
	.tool:hover,
	.tool[aria-expanded='true'] {
		background: var(--glass-hover);
		color: var(--on-glass);
	}
	.tool:focus-visible,
	.menu button:focus-visible,
	.soft:focus-visible {
		outline: 2px solid var(--on-glass);
		outline-offset: 2px;
	}
	svg {
		width: 16px;
		height: 16px;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.4;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.soft {
		font: inherit;
		font-size: var(--fs-s);
		height: 38px;
		padding: 0 16px;
		border-radius: var(--radius-s);
		border: 1px solid var(--glass-line);
		background: var(--glass-hover);
		color: var(--on-glass);
		cursor: pointer;
	}
	.menu {
		position: absolute;
		top: calc(100% + 8px);
		right: 0;
		z-index: 3;
		min-width: 240px;
		max-width: min(340px, calc(100vw - 32px));
		padding: 6px;
		border-radius: var(--radius-s);
		border: 1px solid var(--glass-line);
		background: var(--glass);
		backdrop-filter: var(--glass-blur);
		box-shadow: var(--glass-shadow);
		display: grid;
		gap: 2px;
	}
	.mic:has(.soft) .menu {
		right: auto;
		left: 0;
		top: auto;
		bottom: calc(100% + 8px);
	}
	.menu button {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 8px 10px;
		border: 0;
		border-radius: var(--radius-xs);
		background: transparent;
		color: var(--on-glass);
		font: inherit;
		font-size: var(--fs-s);
		text-align: left;
		cursor: pointer;
	}
	.menu button:hover:not(:disabled) {
		background: var(--glass-hover);
	}
	.menu button:disabled {
		opacity: 0.5;
		cursor: progress;
	}
	.tick {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		flex: none;
	}
	[aria-checked='true'] .tick {
		background: currentColor;
	}
	.name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.empty,
	.err {
		margin: 0;
		padding: 8px 10px;
		font-size: var(--fs-s);
		line-height: 1.5;
		color: var(--on-glass-2);
	}
	.err {
		color: var(--on-glass);
	}
</style>
