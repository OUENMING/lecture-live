<script lang="ts">
	// 课后页：这节课的双语字幕（从本机 IndexedDB 读）+ 下载 + 起名。`?session=` 指定一节；不给就列出本机存着的课。
	// 默认深色（和上课页同一个面，Owen 2026-11-19），可切亮色。课名可以下课再起：起名时把这节用过的术语并进那门课。
	// 课后 AI 笔记要服务端跑（PLAN B 片），这一版先把字幕完整交到用户手里。
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import SiteHeader from '#lib/ui/SiteHeader.svelte';
	import { openStore, type Line, type LineStore, type SessionMeta } from '#lib/store/index.ts';
	import { marksOf, clearLocalMarks, courseLabel } from '#lib/env.ts';
	import { api } from '#lib/api/index.ts';
	import { renameCourse, retryPendingRenames, COURSE_MAX } from '#lib/after/rename.ts';
	import { downloadMarkdown, downloadText } from '#lib/after/export.ts';

	let store = $state<LineStore | null>(null);
	let sessions = $state<SessionMeta[]>([]);
	let lines = $state<Line[]>([]);
	let meta = $state<SessionMeta | null>(null);
	let loaded = $state(false);
	let slow = $state(false);
	const sid = $derived(page.url.searchParams.get('session') ?? '');
	/** 从上课页「下课」过来、已经自动下载过一份 */
	const justSaved = $derived(page.url.searchParams.get('saved') === '1');
	const marked = $derived(new Set(sid ? marksOf(sid) : []));
	const markedLines = $derived(lines.filter((l) => marked.has(l.seq)));

	let theme = $state<'dark' | 'light'>('dark');
	function toggleTheme() {
		theme = theme === 'dark' ? 'light' : 'dark';
		try {
			localStorage.setItem('cl.afterTheme', theme);
		} catch {
			/* 不记也行 */
		}
	}

	const fmt = (t: number) => `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
	const day = (ms: number) =>
		new Date(ms).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
	const minutes = (m: SessionMeta) => (m.ended_at ? Math.max(1, Math.round((m.ended_at - m.started_at) / 60000)) : null);
	/** 「11/18 04:43 · 52 分钟」—— 拼成一串，免得模板里块边界吃掉空格 */
	const when = (m: SessionMeta) => [day(m.started_at), minutes(m) ? `${minutes(m)} 分钟` : ''].filter(Boolean).join(' · ');

	async function load(id: string) {
		if (!store) return;
		sessions = await store.listSessions();
		meta = sessions.find((s) => s.session_id === id) ?? null;
		lines = id && meta ? await store.linesOf(id) : [];
		loaded = true;
	}

	onMount(() => {
		try {
			const t = localStorage.getItem('cl.afterTheme');
			if (t === 'light' || t === 'dark') theme = t;
		} catch {
			/* 默认深色 */
		}
		// 读本机一般几十毫秒；5 秒还没好多半是浏览器存储卡住了，说一声
		const slowTimer = setTimeout(() => (slow = true), 5000);
		openStore()
			.then((s) => {
				store = s; // 读数据由下面的 $effect 接手（store / sid 变了都重读）
			})
			.catch(() => {
				// openStore 自己会退回内存实现；真走到这里只当「本机什么都没有」
				loaded = true;
			})
			.finally(() => clearTimeout(slowTimer));
		// 上次没改成的服务端课名：网络回来了就补上，不打扰
		void retryPendingRenames(api).catch(() => {});
		return () => clearTimeout(slowTimer);
	});

	$effect(() => {
		void load(sid);
	});

	// 起名：没起过名的课标题处直接是输入框；起过的点「改名」才出来
	let naming = $state(false);
	let nameDraft = $state('');
	let nameBusy = $state(false);
	let nameNote = $state('');
	const unnamed = $derived(!!meta && !meta.course.trim());
	function startNaming() {
		nameDraft = meta?.course.trim() ?? '';
		nameNote = '';
		naming = true;
	}
	async function submitName(e: SubmitEvent) {
		e.preventDefault();
		if (!store || !meta || nameBusy) return;
		nameBusy = true;
		nameNote = '';
		try {
			const r = await renameCourse({ store, api, local_session_id: meta.session_id, course: nameDraft });
			naming = false;
			await load(sid);
			nameNote = [
				r.addedTerms ? `${r.addedTerms} 个术语已存进这门课。` : '',
				r.serverQueued ? '服务器那边这次没改成，联网后会自动补上。' : ''
			]
				.filter(Boolean)
				.join('');
		} catch (err) {
			nameNote = err instanceof Error ? err.message : String(err);
		} finally {
			nameBusy = false;
		}
	}

	let confirmWipe = $state(false);
	async function wipe() {
		if (!confirmWipe) {
			confirmWipe = true;
			setTimeout(() => (confirmWipe = false), 4000);
			return;
		}
		await store?.deleteAll();
		clearLocalMarks();
		confirmWipe = false;
		sessions = [];
	}

	const downloadMd = () => downloadMarkdown(meta, lines, marked);
	const downloadTxt = () => downloadText(meta, lines);
</script>

<svelte:head><title>{meta ? `${courseLabel(meta.course)} · 课后` : '我的课'} · ClassLive</title></svelte:head>

<div class="page" data-surface={theme === 'dark' ? 'dark' : undefined}>
	<div class="head-row">
		<SiteHeader current="after" />
		<button
			class="theme"
			type="button"
			onclick={toggleTheme}
			aria-label={theme === 'dark' ? '换成亮色' : '换成深色'}
			title={theme === 'dark' ? '换成亮色' : '换成深色'}
		>
			{#if theme === 'dark'}
				<svg viewBox="0 0 16 16" aria-hidden="true"
					><circle cx="8" cy="8" r="3" /><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" /></svg
				>
			{:else}
				<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13 9.6A5.5 5.5 0 0 1 6.4 3a5.5 5.5 0 1 0 6.6 6.6Z" /></svg>
			{/if}
		</button>
	</div>

	<main class="wrap">
		{#if store && !store.persistent}
			<p class="notice" role="status">这个浏览器不让网页存东西，关掉标签页字幕就没了。先下载一份。</p>
		{/if}

		{#if !loaded}
			<p class="muted" role="status">{slow ? '读本机记录比平时慢，刷新一下试试。' : '正在读本机记录…'}</p>
		{:else if sid && !meta}
			<header class="head">
				<h1 class="serif">这节课不在这台电脑上</h1>
				<p class="muted">字幕存在开课的那台电脑的浏览器里。换了电脑、换了浏览器，或者清过浏览器数据，这里就读不到。</p>
				<div class="actions"><a class="btn" href="/after">看这台电脑上的课</a></div>
			</header>
		{:else if sid && meta}
			<header class="head">
				{#if unnamed || naming}
					<form class="name" onsubmit={submitName}>
						<label for="course-name">{unnamed ? '给这节课起个名' : '改课名'}</label>
						<div class="name-row">
							<!-- svelte-ignore a11y_autofocus -->
							<input
								id="course-name"
								autocomplete="off"
								spellcheck="false"
								maxlength={COURSE_MAX}
								placeholder="如 ECON10770"
								bind:value={nameDraft}
								autofocus={naming}
							/>
							<button class="btn primary" type="submit" disabled={nameBusy || !nameDraft.trim()}>{nameBusy ? '正在存…' : '存'}</button>
							{#if naming}<button class="btn quiet" type="button" onclick={() => (naming = false)}>取消</button>{/if}
						</div>
						<p class="hint">这节用过的术语会一起存进这门课。</p>
					</form>
				{:else}
					<h1>{courseLabel(meta.course)}</h1>
				{/if}
				<p class="muted meta-line">
					{when(meta)} · {lines.length} 句
					{#if !unnamed && !naming}· <button class="link" type="button" onclick={startNaming}>改名</button>{/if}
				</p>
				{#if nameNote}<p class="hint" role="status">{nameNote}</p>{/if}
				<div class="actions">
					<button class="btn primary" type="button" onclick={downloadMd} disabled={lines.length === 0}>下载 Markdown</button>
					<button class="btn" type="button" onclick={downloadTxt} disabled={lines.length === 0}>下载纯文本</button>
					<a class="btn quiet" href="/">再上一节</a>
				</div>
				<p class="hint">
					{#if justSaved}下课时已经下载了一份 Markdown，在「下载」文件夹。{/if}字幕存在这台电脑，也同步到服务器存 30 天。课后 AI 笔记网页版还在做。
				</p>
			</header>

			<div class="body" class:has-marks={markedLines.length > 0}>
				{#if markedLines.length}
					<aside class="marked" aria-label="没听懂的地方">
						<h2>没听懂的地方 · {markedLines.length}</h2>
						<ol>
							{#each markedLines as l (l.seq)}
								<li><a href={`#l${l.seq}`}><span class="t">{fmt(l.t_s)}</span><span>{l.zh || l.en}</span></a></li>
							{/each}
						</ol>
					</aside>
				{/if}

				{#if lines.length === 0}
					<p class="muted">这节课没有存下字幕（可能开课后老师还没开口就下课了）。</p>
				{:else}
					<ol class="lines">
						{#each lines as l (l.seq)}
							<li id={`l${l.seq}`} class:flag={marked.has(l.seq)}>
								<span class="t">{fmt(l.t_s)}</span>
								<div>
									<p class="en">{l.en}</p>
									<p class="zh">{l.zh || '（这句没译出）'}</p>
								</div>
							</li>
						{/each}
					</ol>
				{/if}
			</div>
		{:else}
			<header class="head">
				<h1 class="serif">我的课</h1>
				<p class="muted">存在这台电脑浏览器里的课，也同步到服务器存 30 天。</p>
			</header>
			{#if sessions.length === 0}
				<p class="muted">还没有上过课。<a href="/">去开一节</a></p>
			{:else}
				<ul class="list">
					{#each sessions as s (s.session_id)}
						<li>
							<a href={`/after?session=${encodeURIComponent(s.session_id)}`}>
								<span class="c" class:untitled={!s.course.trim()}>{courseLabel(s.course)}</span>
								<span class="muted">{when(s)}</span>
							</a>
						</li>
					{/each}
				</ul>
				<button class="btn quiet wipe" class:armed={confirmWipe} type="button" onclick={wipe}>
					{confirmWipe ? '再点一次，全部删掉（不能恢复）' : '删除这台电脑上的全部记录'}
				</button>
			{/if}
		{/if}
	</main>
</div>

<style>
	.page {
		min-height: 100dvh;
		background: var(--paper);
		color: var(--ink);
		font-family: var(--cl-font);
		-webkit-font-smoothing: antialiased;
		transition:
			background-color var(--move) var(--ease),
			color var(--move) var(--ease);
		--gutter: clamp(16px, 4vw, 56px);
	}
	.head-row {
		position: relative;
	}
	/* 主题按钮挂在导航右边，不挤进 SiteHeader 的结构 */
	.theme {
		position: absolute;
		top: 50%;
		right: calc(var(--gutter) - 8px);
		transform: translateY(-50%);
		width: 34px;
		height: 34px;
		display: grid;
		place-items: center;
		padding: 0;
		border: 0;
		border-radius: var(--radius-s);
		background: none;
		color: var(--ink-2);
		cursor: pointer;
		transition:
			background-color var(--fast) var(--ease),
			color var(--fast) var(--ease);
	}
	.head-row :global(.top) {
		padding-right: calc(var(--gutter) + 40px);
	}
	.theme:hover {
		background: var(--paper-2);
		color: var(--ink);
	}
	.theme:focus-visible {
		outline: 2px solid var(--ink);
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

	/* 内容左缘和 logo 对齐（同一个 --gutter），不居中漂在中间 */
	.wrap {
		padding: clamp(16px, 4vh, 40px) var(--gutter) 96px;
	}
	.head {
		max-width: 720px;
		padding-bottom: 28px;
		border-bottom: 1px solid var(--rule);
	}
	h1 {
		margin: 0 0 8px;
		font-size: var(--fs-xl);
		font-weight: 600;
		line-height: var(--lh-tight);
		letter-spacing: -0.01em;
		overflow-wrap: anywhere;
	}
	h1.serif {
		font-family: var(--cl-serif);
	}
	.muted {
		margin: 0;
		font-size: var(--fs-m);
		line-height: var(--lh-body);
		color: var(--ink-3);
	}
	.muted a {
		color: var(--ink);
	}
	.meta-line {
		font-size: var(--fs-s);
	}
	.hint {
		margin: 8px 0 0;
		font-size: var(--fs-s);
		line-height: 1.6;
		color: var(--ink-3);
	}
	.notice {
		max-width: 720px;
		margin: 0 0 24px;
		padding: 12px 16px;
		border-radius: var(--radius-s);
		box-shadow: inset 0 0 0 1px var(--field-line);
		font-size: var(--fs-s);
		font-weight: 600;
		line-height: 1.6;
	}

	.name {
		display: grid;
		gap: 8px;
		margin-bottom: 8px;
	}
	.name label {
		font-size: var(--fs-s);
		font-weight: 500;
		color: var(--ink-2);
	}
	.name-row {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}
	.name input {
		flex: 1 1 240px;
		height: 48px;
		padding: 0 14px;
		border-radius: var(--radius-s);
		border: 1px solid var(--field-line);
		background: var(--paper-2);
		color: var(--ink);
		caret-color: var(--ink);
		font: 600 var(--fs-l) / 1 var(--cl-font);
		transition:
			border-color var(--fast) var(--ease),
			box-shadow var(--fast) var(--ease);
	}
	.name input::placeholder {
		color: var(--ink-3);
		font-weight: 400;
	}
	.name input:hover {
		border-color: var(--field-line-hover);
	}
	.name input:focus-visible {
		outline: none;
		border-color: var(--ink);
		box-shadow: 0 0 0 3px var(--focus-ring);
	}
	.name .btn {
		height: 48px;
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin: 22px 0 6px;
	}
	.btn {
		display: inline-flex;
		align-items: center;
		height: 40px;
		padding: 0 16px;
		border-radius: var(--radius-s);
		border: 1px solid var(--field-line);
		background: none;
		color: var(--ink);
		font: 500 var(--fs-s) / 1 var(--cl-font);
		text-decoration: none;
		white-space: nowrap;
		cursor: pointer;
		transition:
			background-color var(--fast) var(--ease),
			border-color var(--fast) var(--ease),
			transform var(--fast) var(--ease);
	}
	.btn:hover:not(:disabled) {
		background: var(--paper-2);
		border-color: var(--field-line-hover);
	}
	.btn:active:not(:disabled) {
		transform: scale(0.98);
	}
	.btn:focus-visible,
	.link:focus-visible,
	.list a:focus-visible,
	.marked a:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 2px;
	}
	.btn.primary {
		background: var(--ink);
		border-color: var(--ink);
		color: var(--paper);
		font-weight: 600;
	}
	.btn.primary:hover:not(:disabled) {
		background: var(--ink-2);
		border-color: var(--ink-2);
	}
	.btn.quiet {
		border-color: transparent;
		color: var(--ink-2);
	}
	.btn:disabled {
		opacity: 0.4;
		cursor: default;
	}
	.link {
		padding: 0;
		border: 0;
		background: none;
		color: var(--ink-2);
		font: inherit;
		text-decoration: underline;
		text-underline-offset: 3px;
		cursor: pointer;
	}
	.link:hover {
		color: var(--ink);
	}

	/* 正文：字幕一列；有标记时宽屏右边多一列「没听懂的地方」，跟着滚 */
	.body {
		display: grid;
		grid-template-columns: minmax(0, 720px);
		column-gap: clamp(32px, 5vw, 80px);
	}
	@media (min-width: 1100px) {
		.body.has-marks {
			grid-template-columns: minmax(0, 720px) minmax(220px, 300px);
		}
		.body.has-marks .marked {
			grid-column: 2;
			grid-row: 1;
			align-self: start;
			position: sticky;
			top: 24px;
			border-bottom: 0;
		}
	}
	.lines,
	.list,
	.marked ol {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.lines li {
		display: grid;
		grid-template-columns: 48px 1fr;
		gap: 12px;
		padding: 16px 0;
		border-bottom: 1px solid var(--rule);
		scroll-margin-top: 24px;
	}
	.t {
		font-family: var(--cl-mono);
		font-size: var(--fs-xs);
		color: var(--ink-3);
		padding-top: 2px;
		font-variant-numeric: tabular-nums;
	}
	/* 英文在上、中文在下，和上课时一样 */
	.en {
		margin: 0 0 4px;
		font-size: var(--fs-s);
		line-height: 1.55;
		color: var(--ink-2);
	}
	.zh {
		margin: 0;
		font-size: var(--fs-m);
		line-height: var(--lh-body);
	}
	.lines li.flag {
		margin: 0 -12px;
		padding-left: 12px;
		padding-right: 12px;
		border-radius: var(--radius-s);
		background: var(--paper-2);
	}
	.lines li:target {
		outline: 2px solid var(--ink);
		outline-offset: -2px;
		border-radius: var(--radius-s);
	}
	.marked {
		padding: 20px 0 12px;
		border-bottom: 1px solid var(--rule);
	}
	.marked h2 {
		margin: 0 0 10px;
		font-size: var(--fs-s);
		font-weight: 600;
	}
	.marked ol {
		display: grid;
		gap: 2px;
	}
	.marked a {
		display: grid;
		grid-template-columns: 48px 1fr;
		gap: 12px;
		padding: 6px 0;
		color: var(--ink);
		text-decoration: none;
		font-size: var(--fs-s);
		line-height: 1.55;
		border-radius: var(--radius-xs);
	}
	.marked a:hover span:last-child {
		text-decoration: underline;
		text-underline-offset: 3px;
	}

	.list {
		max-width: 720px;
	}
	.list a {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 12px;
		padding: 16px 0;
		border-bottom: 1px solid var(--rule);
		color: var(--ink);
		text-decoration: none;
	}
	.list a:hover .c {
		text-decoration: underline;
		text-underline-offset: 3px;
	}
	.list .muted {
		flex: none;
		font-size: var(--fs-s);
	}
	.c {
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.c.untitled {
		font-weight: 400;
		color: var(--ink-2);
	}
	.wipe {
		margin: 32px 0 0 -16px; /* 字和列表左缘对齐 */
		color: var(--ink-3);
	}
	.wipe.armed {
		color: var(--ink);
		border-color: var(--ink);
	}
</style>
