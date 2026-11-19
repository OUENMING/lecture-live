<script lang="ts">
	// 门面回放背后的「课件」：跟着回放时间换页，让字幕看起来是浮在一节真课上的（桌面版就是这么用的）。
	// 内容是按 MIT 9.13 Lecture 4 这一段讲的东西重画的示意页，只用墨色线条，不放原课件截图。
	// 字号全部用容器宽度（cqw）：窗多大，课件就按比例缩多大，像一张真的屏幕截图。
	let { t }: { t: number } = $props();

	type Slide = { at: number; title: string; points: string[]; fig: 'face' | 'levels' | 'views' | 'net' | 'eyes' };
	const SLIDES: Slide[] = [
		{
			at: 0,
			title: 'Face recognition: what do we want to know?',
			points: ['What are the inputs and outputs?', 'Why is it hard?'],
			fig: 'face'
		},
		{
			at: 100,
			title: "Marr's levels of analysis",
			points: ['Computational theory: what is the problem?', 'Algorithm: how is it solved?'],
			fig: 'levels'
		},
		{
			at: 236,
			title: 'Why not just store a template?',
			points: ['Match incoming pixels to a stored image', 'Different angles, lighting, expressions'],
			fig: 'views'
		},
		{
			at: 366,
			title: 'How do machines do it?',
			points: ['VGG-Face: a deep network', 'Trained on millions of images'],
			fig: 'net'
		},
		{
			at: 524,
			title: 'Behavioral data',
			points: ['What can people do with a face?', 'Identity, emotion, gaze, age'],
			fig: 'eyes'
		}
	];
	const idx = $derived(Math.max(0, SLIDES.findLastIndex((s) => t >= s.at)));
</script>

<div class="deck" aria-hidden="true">
	{#each SLIDES as s, i (s.at)}
		<div class="slide" class:on={i === idx}>
			<header>
				<span>MIT 9.13 · The Human Brain</span>
				<span>Lecture 4 · Cognitive Neuroscience Methods</span>
			</header>
			<div class="main">
				<div class="text">
					<h3>{s.title}</h3>
					<ul>
						{#each s.points as p (p)}<li>{p}</li>{/each}
					</ul>
				</div>
				<svg class="fig" viewBox="0 0 120 100">
					{#if s.fig === 'face'}
						<ellipse cx="60" cy="50" rx="26" ry="34" />
						<path d="M48 45h8M64 45h8M60 50v11h-3M53 71h14" />
					{:else if s.fig === 'levels'}
						{#each [0, 1, 2] as k (k)}
							<rect x="22" y={14 + k * 26} width="76" height="18" rx="3" />
							{#if k < 2}<path class="thin" d="M60 {32 + k * 26}v8" />{/if}
						{/each}
					{:else if s.fig === 'views'}
						{#each [-1, 0, 1] as k (k)}
							<g transform="translate({28 + (k + 1) * 32} 50)">
								<ellipse rx="13" ry="18" />
								<path d="M{-6 + k * 3} -3h3M{3 + k * 3} -3h3M{k * 4} 2v5M{-5 + k * 3} 11h9" />
							</g>
						{/each}
						<path class="thin" d="M10 82h100" />
					{:else if s.fig === 'net'}
						{#each [0, 1, 2, 3] as c (c)}
							{#each Array.from({ length: 4 - (c === 3 ? 3 : 0) }, (_, r) => r) as r (r)}
								<circle cx={18 + c * 28} cy={c === 3 ? 50 : 20 + r * 20} r="4" />
								{#if c < 3}
									{#each Array.from({ length: c === 2 ? 1 : 4 }, (_, q) => q) as q (q)}
										<path class="thin" d="M{22 + c * 28} {20 + r * 20}L{42 + c * 28} {c === 2 ? 50 : 20 + q * 20}" />
									{/each}
								{/if}
							{/each}
						{/each}
					{:else}
						<path d="M20 50q20-22 40 0q-20 22-40 0zM60 50q20-22 40 0q-20 22-40 0z" />
						<circle cx="40" cy="50" r="6" /><circle cx="80" cy="50" r="6" />
					{/if}
				</svg>
			</div>
		</div>
	{/each}
</div>

<style>
	.deck {
		position: absolute;
		inset: 0;
		container-type: inline-size;
		background: oklch(0.985 0.003 85);
		color: oklch(0.22 0.01 60);
	}
	.slide {
		position: absolute;
		inset: 0;
		display: grid;
		grid-template-rows: auto 1fr;
		opacity: 0;
		transition: opacity var(--stage) var(--ease);
	}
	.slide.on {
		opacity: 1;
	}
	header {
		display: flex;
		justify-content: space-between;
		padding: 2.2cqw 4.4cqw;
		border-bottom: 1px solid oklch(0.22 0.01 60 / 0.12);
		font-size: max(9px, 1.25cqw);
		letter-spacing: 0.04em;
		color: oklch(0.22 0.01 60 / 0.55);
	}
	.main {
		display: grid;
		grid-template-columns: 1fr 24cqw;
		gap: 4cqw;
		align-content: start;
		padding: 3.4cqw 4.4cqw 0;
	}
	h3 {
		margin: 0;
		font-size: max(15px, 3.5cqw);
		font-weight: 650;
		line-height: 1.15;
		letter-spacing: -0.02em;
		text-wrap: balance;
	}
	ul {
		margin: 2.2cqw 0 0;
		padding: 0;
		list-style: none;
		font-size: max(10.5px, 1.9cqw);
		line-height: 1.6;
		color: oklch(0.22 0.01 60 / 0.78);
	}
	li::before {
		content: '';
		display: inline-block;
		width: 0.42em;
		height: 1px;
		margin: 0 0.6em 0.3em 0;
		background: currentColor;
	}
	.fig {
		width: 100%;
		fill: none;
		stroke: oklch(0.22 0.01 60 / 0.8);
		stroke-width: 1.4;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.fig .thin {
		stroke-width: 0.6;
		stroke: oklch(0.22 0.01 60 / 0.35);
	}
	@container (max-width: 520px) {
		header span + span {
			display: none;
		}
		header {
			font-size: 10px;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.slide {
			transition: none;
		}
	}
</style>
