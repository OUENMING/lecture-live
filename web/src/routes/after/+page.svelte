<script lang="ts">
	// 课后页：这节课的双语字幕（从本机 IndexedDB 读）+ 下载 + 起名。`?session=` 指定一节；不给就列出本机存着的课。
	// 默认深色（和上课页同一个面，Owen 2026-11-19），可切亮色。课名可以下课再起：起名时把这节用过的术语并进那门课。
	// 课后 AI 笔记由服务端 runner 跑：下课后轮询会话状态，出了就在「笔记」页里读（sandbox iframe）；
	// 没出之前先给这节课的实时总结（服务端每 20 s 写的那份章节）垫着。服务端会话号存在 `cl.srv.<本机会话 id>`。
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import SiteHeader from '#lib/ui/SiteHeader.svelte';
	import { openStore, type Line, type LineStore, type SessionMeta } from '#lib/store/index.ts';
	import { marksOf, clearLocalMarks, courseLabel } from '#lib/env.ts';
	import { api } from '#lib/api/index.ts';
	import { renameCourse, retryPendingRenames, COURSE_MAX } from '#lib/after/rename.ts';
	import { downloadMarkdown, downloadText } from '#lib/after/export.ts';
	import { ApiError, type LiveSummary } from '#lib/api/index.ts';
	import { pollEvery } from '#lib/summary/poll.ts';
	import { portableNote } from '#lib/after/note-frame.ts';
	import ChapterList from '#lib/ui/ChapterList.svelte';
	import NoteFrame from '#lib/ui/NoteFrame.svelte';

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

	// ---- 课后笔记 ----
	/** none = 没传到服务器；waiting = 服务器还没开始写（含下课还没送达）；writing = 正在写 */
	type NoteStatus = 'loading' | 'none' | 'waiting' | 'writing' | 'done' | 'failed' | 'missing' | 'gone';
	const srvOf = (local: string): string | null => {
		try {
			const v = localStorage.getItem(`cl.srv.${local}`);
			return v && /^s_[0-9a-f]{12}$/.test(v) ? v : null;
		} catch {
			return null;
		}
	};
	const serverId = $derived(meta ? srvOf(meta.session_id) : null);
	const hasMeta = $derived(!!meta);
	let noteStatus = $state<NoteStatus>('loading');
	let noteHtml = $state('');
	let noteExpires = $state<number | null>(null);
	let noteSummary = $state<LiveSummary | null>(null);
	let noteError = $state('');
	let view = $state<'note' | 'lines'>('note');
	/** 用户自己点过「字幕」就别再替他切回来 */
	let viewPicked = false;
	function pickView(v: 'note' | 'lines') {
		view = v;
		viewPicked = true;
	}
	function viewKey(e: KeyboardEvent) {
		if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
		e.preventDefault();
		const next = view === 'note' ? 'lines' : 'note';
		pickView(next);
		(e.currentTarget as HTMLElement).querySelector<HTMLElement>(`[data-view="${next}"]`)?.focus();
	}

	interface NotePoll {
		status: NoteStatus;
		html?: string;
		expires?: number | null;
		summary?: LiveSummary | null;
	}
	$effect(() => {
		const id = serverId;
		noteHtml = '';
		noteSummary = null;
		noteError = '';
		noteExpires = null;
		if (!hasMeta) return;
		if (!id) {
			noteStatus = 'none';
			if (!viewPicked) view = 'lines';
			return;
		}
		noteStatus = 'loading';
		let htmlFetched = false;
		const p = pollEvery<NotePoll>({
			everyMs: 10_000,
			maxMs: 60_000,
			load: async () => {
				try {
					const s = await api.getSession(id);
					const n = s.note;
					const expires = n?.expires_at ? Date.parse(n.expires_at) || null : null;
					if (n?.state === 'done' && !htmlFetched) {
						const html = await api.getNote(id, 'html');
						// 账本说 done、对象还没到 R2（极短的窗口）：当作还在写，下轮再取
						if (!html) return { value: { status: 'writing', expires } };
						htmlFetched = true;
						return { value: { status: 'done', html, expires }, done: true };
					}
					const summary = await api.getSummary(id).catch(() => null);
					if (n?.state === 'failed') return { value: { status: 'failed', summary, expires }, done: true };
					return { value: { status: n?.state === 'running' ? 'writing' : 'waiting', summary, expires } };
				} catch (e) {
					if (e instanceof ApiError && e.kind === 'not_found') return { value: { status: 'missing' }, done: true };
					if (e instanceof ApiError && e.kind === 'gone') return { value: { status: 'gone' }, done: true };
					throw e;
				}
			},
			onchange: (st) => {
				noteError = st.error;
				const v = st.value;
				// 一次都没拿到就失败（断网）：别一直转圈，按「还没出」说，底下再挂一句连不上
				if (!v) {
					if (st.error && noteStatus === 'loading') noteStatus = 'waiting';
					return;
				}
				noteStatus = v.status;
				if (v.html) noteHtml = v.html;
				if (v.expires !== undefined) noteExpires = v.expires;
				if (v.summary) noteSummary = v.summary;
			}
		});
		return () => p.stop();
	});
	const keepUntil = $derived(
		noteExpires ? new Date(noteExpires).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' }) : ''
	);
	const noteName = $derived(`${courseLabel(meta?.course ?? '')} ${meta ? new Date(meta.started_at).toLocaleDateString('sv-SE') : ''} 笔记`.trim());
	function saveBlob(text: string, type: string, ext: string) {
		const url = URL.createObjectURL(new Blob([text], { type }));
		const a = document.createElement('a');
		a.href = url;
		a.download = `${noteName.replace(/[\\/:*?"<>|]+/g, '-')}.${ext}`;
		a.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
	let mdBusy = $state(false);
	async function downloadNoteMd() {
		if (!serverId || mdBusy) return;
		mdBusy = true;
		try {
			const md = await api.getNote(serverId, 'md');
			if (md) saveBlob(md, 'text/markdown;charset=utf-8', 'md');
		} catch (e) {
			noteError = e instanceof Error ? e.message : String(e);
		} finally {
			mdBusy = false;
		}
	}
	const downloadNoteHtml = () => noteHtml && saveBlob(portableNote(noteHtml), 'text/html;charset=utf-8', 'html');
	const noteWorking = $derived(noteStatus === 'loading' || noteStatus === 'waiting' || noteStatus === 'writing');
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
				<p class="hint">
					{#if justSaved}下课时已经把字幕下载了一份 Markdown，在「下载」文件夹。{/if}字幕存在这台电脑；{serverId
						? `字幕和笔记也存在服务器上${keepUntil ? `，保留到 ${keepUntil}` : ' 30 天'}。`
						: '这节没传到服务器。'}
				</p>
			</header>

			<div class="viewbar">
				<div
					class="views"
					role="tablist"
					aria-label="看哪一样"
					tabindex="-1"
					style:--i={view === 'note' ? 0 : 1}
					onkeydown={viewKey}
				>
					<span class="thumb" aria-hidden="true"></span>
					<button
						type="button"
						role="tab"
						id="tab-note"
						data-view="note"
						aria-controls="panel-note"
						aria-selected={view === 'note'}
						tabindex={view === 'note' ? 0 : -1}
						onclick={() => pickView('note')}
						>笔记{#if noteWorking && serverId}<span class="busy" aria-label="正在写"></span>{/if}</button
					>
					<button
						type="button"
						role="tab"
						id="tab-lines"
						data-view="lines"
						aria-controls="panel-lines"
						aria-selected={view === 'lines'}
						tabindex={view === 'lines' ? 0 : -1}
						onclick={() => pickView('lines')}>字幕<span class="n">{lines.length}</span></button
					>
				</div>
				<div class="actions">
					{#if view === 'note'}
						{#if noteStatus === 'done'}
							<button class="btn" type="button" onclick={downloadNoteHtml}>下载笔记</button>
							<button class="btn quiet" type="button" onclick={downloadNoteMd} disabled={mdBusy}>{mdBusy ? '正在取…' : 'Markdown'}</button>
						{/if}
					{:else}
						<button class="btn" type="button" onclick={downloadMd} disabled={lines.length === 0}>下载字幕</button>
						<button class="btn quiet" type="button" onclick={downloadTxt} disabled={lines.length === 0}>纯文本</button>
					{/if}
					<a class="btn quiet" href="/">再上一节</a>
				</div>
			</div>

			<div class="panel-note" id="panel-note" role="tabpanel" aria-labelledby="tab-note" hidden={view !== 'note'}>
				{#if noteStatus === 'done' && noteHtml}
					<NoteFrame html={noteHtml} dark={theme === 'dark'} title={noteName} />
				{:else if noteStatus === 'none' || noteStatus === 'missing' || noteStatus === 'gone'}
					<div class="state">
						<h2>{noteStatus === 'gone' ? '这节课在服务器上已经删除' : '这节课没有笔记'}</h2>
						<p class="muted">
							{noteStatus === 'gone'
								? '笔记跟着服务器上的数据一起删了。字幕还在这台电脑上。'
								: noteStatus === 'missing'
									? '服务器上找不到这节课，多半是换过通行码。字幕还在这台电脑上，可以下载。'
									: '笔记要服务器整理，这节课没传上去（开课时网络不通，或这个浏览器不让存）。字幕都在这台电脑上，可以下载。'}
						</p>
						<div class="state-actions"><button class="btn" type="button" onclick={() => pickView('lines')}>看字幕</button></div>
					</div>
				{:else if noteStatus === 'failed'}
					<div class="state">
						<h2>笔记没写成</h2>
						<p class="muted">服务器整理这节课时出错了。字幕都在，可以下载；{noteSummary?.chapters.length ? '下面是课上的实时总结。' : ''}</p>
					</div>
				{:else}
					<div class="state writing" aria-live="polite">
						<div class="track" aria-hidden="true"><span></span></div>
						<h2>{noteStatus === 'writing' ? '笔记正在写' : noteStatus === 'loading' ? '正在问服务器…' : '笔记排上队了'}</h2>
						<p class="muted">
							服务器把整节课的字幕整理成笔记：速览、知识点、例题、自测。一般几分钟，好了这里自动出来，不用刷新，也可以先关掉页面晚点再来。
						</p>
						{#if noteError}<p class="hint">这会儿连不上服务器，稍后自动再试。</p>{/if}
					</div>
				{/if}

				{#if noteStatus !== 'done' && noteSummary?.chapters.length}
					<div class="interim">
						<h2>课上的实时总结 <span class="muted">· {noteSummary.chapters.length} 章</span></h2>
						<ChapterList chapters={noteSummary.chapters} ended />
					</div>
				{/if}
			</div>

			<div class="body" id="panel-lines" role="tabpanel" aria-labelledby="tab-lines" hidden={view !== 'lines'} class:has-marks={markedLines.length > 0}>
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
		padding-bottom: 24px;
	}
	/* 会话页里 head 下面紧跟 viewbar（自带分隔线）；列表页的 head 仍要自己那条线 */
	.head:not(:has(+ .viewbar)) {
		padding-bottom: 28px;
		border-bottom: 1px solid var(--rule);
	}

	/* 笔记 / 字幕：滑块分段（同上课页工具条），往下读时吸在顶上，下载跟着当前那一页走 */
	.viewbar {
		position: sticky;
		top: 0;
		z-index: 2;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 10px 16px;
		margin: 0 calc(var(--gutter) * -1);
		padding: 10px var(--gutter);
		background: oklch(from var(--paper) l c h / 0.86);
		backdrop-filter: blur(16px) saturate(1.2);
		-webkit-backdrop-filter: blur(16px) saturate(1.2);
		border-bottom: 1px solid var(--rule);
	}
	.views {
		position: relative;
		display: grid;
		grid-template-columns: 1fr 1fr;
		padding: 3px;
		border-radius: var(--radius-pill);
		box-shadow: inset 0 0 0 1px var(--field-line);
	}
	.views .thumb {
		position: absolute;
		top: 3px;
		bottom: 3px;
		left: 3px;
		width: calc((100% - 6px) / 2);
		border-radius: var(--radius-pill);
		background: var(--paper-2);
		box-shadow: inset 0 0 0 1px var(--field-line);
		transform: translateX(calc(var(--i) * 100%));
		transition: transform var(--move) var(--ease);
	}
	.views button {
		position: relative;
		z-index: 1;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 7px;
		height: 32px;
		min-width: 88px;
		padding: 0 16px;
		border: 0;
		background: none;
		color: var(--ink-2);
		font: 500 var(--fs-s) / 1 var(--cl-font);
		cursor: pointer;
		transition: color var(--fast) var(--ease);
	}
	.views button[aria-selected='true'] {
		color: var(--ink);
		font-weight: 600;
	}
	.views button:hover {
		color: var(--ink);
	}
	.views button:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: -2px;
		border-radius: var(--radius-pill);
	}
	.views .n {
		font-size: var(--fs-xs);
		font-weight: 400;
		color: var(--ink-3);
		font-variant-numeric: tabular-nums;
	}
	/* 笔记还在写：标签旁一个慢慢呼吸的点（不用颜色，只用明暗） */
	.busy {
		width: 6px;
		height: 6px;
		border-radius: var(--radius-pill);
		background: var(--ink);
		animation: busy 1.6s ease-in-out infinite;
	}
	@keyframes busy {
		50% {
			opacity: 0.25;
		}
	}
	.viewbar .actions {
		margin: 0;
	}
	.viewbar .btn {
		height: 36px;
	}

	.panel-note,
	.body {
		padding-top: 28px;
	}
	.panel-note {
		max-width: 920px;
		--cl-sum-1: var(--ink);
		--cl-sum-2: var(--ink-2);
		--cl-sum-3: var(--ink-3);
		--cl-sum-line: var(--rule);
		--cl-sum-hover: var(--paper-2);
		--cl-sum-ground: var(--paper);
	}
	.panel-note[hidden],
	.body[hidden] {
		display: none;
	}
	.state {
		max-width: 620px;
		padding: 8px 0 8px;
	}
	.state h2,
	.interim h2 {
		margin: 0 0 8px;
		font-size: var(--fs-l);
		font-weight: 600;
		line-height: 1.35;
	}
	.state-actions {
		margin-top: 18px;
	}
	/* 正在写：一条细线上一段亮光来回走；减弱动效时只留静止的半截 */
	.track {
		position: relative;
		width: 160px;
		height: 2px;
		margin-bottom: 22px;
		overflow: hidden;
		border-radius: var(--radius-pill);
		background: var(--rule);
	}
	.track span {
		position: absolute;
		inset: 0 auto 0 0;
		width: 40%;
		border-radius: inherit;
		background: var(--ink);
		animation: sweep 1.8s var(--ease) infinite;
	}
	@keyframes sweep {
		from {
			transform: translateX(-100%);
		}
		to {
			transform: translateX(250%);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.track span,
		.busy {
			animation: none;
		}
	}
	.interim {
		max-width: 680px;
		margin-top: 40px;
		padding-top: 28px;
		border-top: 1px solid var(--rule);
	}
	.interim h2 {
		margin-bottom: 18px;
	}
	.interim h2 .muted {
		font-size: var(--fs-s);
		font-weight: 400;
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
