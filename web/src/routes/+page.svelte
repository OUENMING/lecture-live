<script lang="ts">
	// 开课页 = 门面。主角是那扇正在出字幕的窗（真课回放，不是样机）；点「开始上课」，这扇窗原地放大成上课界面。
	// 开课只要一步：课名和术语都可选（Owen 2026-11-19）。没填课名时术语存成「下一节的草稿」，下课在课后页命名时并进那门课。
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import DemoSlides from '#lib/demo/DemoSlides.svelte';
	import Stream from '#lib/ui/Stream.svelte';
	import SiteHeader from '#lib/ui/SiteHeader.svelte';
	import { demo, startDemo } from '#lib/demo/player.svelte.ts';
	import {
		browserVerdict,
		recentCourses,
		rememberCourse,
		hasPass,
		savePass,
		termsText,
		parseTerms,
		type BrowserVerdict
	} from '#lib/env.ts';
	import { ApiError, api, checkPasscode } from '#lib/api/index.ts';
	import { pullTerms, retryPendingTerms, saveTermsSynced, shareEnabled, setShareEnabled } from '#lib/terms/cloud.ts';

	let verdict = $state<BrowserVerdict>('ok');
	let courses = $state<string[]>([]);
	let course = $state('');
	let pass = $state('');
	let needPass = $state(true);
	let error = $state('');
	let starting = $state(false);
	let copied = $state(false);
	// 换了课就换成那门课存着的术语表；课名为空 = 下一节的草稿。用户输入直接覆盖（可写的 $derived）
	// 云端术语拉回来以后 bump 一下，让下面的 $derived 重读本机
	let termsRev = $state(0);
	// 本机存的那份跟着课名走；用户一动手（敲字 / 从课件生成）就以他的为准，
	// 之后改课名、云端拉回都不再盖掉正在编辑的内容（OCR C：原来改一个字课名就把刚敲的术语清空）
	let typed = $state<string | null>(null);
	const stored = $derived((void termsRev, termsText(course.trim())));
	const terms = $derived(typed ?? stored);
	let share = $state(true);
	// 门面回放里点一句 = 标成没听懂（和上课页同一个手势，只在这台电脑这次打开有效）
	let demoMarked = $state.raw<ReadonlySet<number>>(new Set());
	function toggleDemoMark(row: { id: number }) {
		const n = new Set(demoMarked);
		if (n.has(row.id)) n.delete(row.id);
		else n.add(row.id);
		demoMarked = n;
	}
	let moreOpen = $state(false);
	const termCount = $derived(parseTerms(terms).length);
	const moreSummary = $derived(
		[course.trim(), termCount ? `${termCount} 个术语` : ''].filter(Boolean).join(' · ') || '可选，下课再填也行'
	);
	// 从课件生成候选术语（第一次点会下载约 13 MB 的 Python 运行时）
	let prepBusy = $state(false);
	let prepNote = $state('');
	let fileInput = $state<HTMLInputElement>();
	function prepFrom(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const files = [...(input.files ?? [])];
		input.value = '';
		void prepFiles(files);
	}
	// 课件可以直接拖到这页任何地方（和桌面版一样）：松手就展开「课名和术语」并开始挑
	const PREP_EXT = /\.(pdf|pptx|docx)$/i;
	let dragging = $state(false);
	let dragDepth = 0;
	const hasFiles = (e: DragEvent) => !!e.dataTransfer?.types.includes('Files');
	function onDragEnter(e: DragEvent) {
		if (!hasFiles(e) || verdict !== 'ok') return;
		e.preventDefault();
		dragDepth++;
		dragging = true;
	}
	function onDragOver(e: DragEvent) {
		if (!hasFiles(e) || verdict !== 'ok') return;
		e.preventDefault();
		if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
	}
	function onDragLeave() {
		dragDepth = Math.max(0, dragDepth - 1);
		if (!dragDepth) dragging = false;
	}
	function onDrop(e: DragEvent) {
		if (!hasFiles(e) || verdict !== 'ok') return;
		e.preventDefault();
		dragDepth = 0;
		dragging = false;
		const all = [...(e.dataTransfer?.files ?? [])];
		const files = all.filter((f) => PREP_EXT.test(f.name));
		moreOpen = true;
		if (!files.length) {
			prepNote = '只认 PDF / PPTX / DOCX。';
			return;
		}
		if (prepBusy) return;
		void prepFiles(files);
	}
	async function prepFiles(files: File[]) {
		if (!files.length) return;
		const c = course.trim();
		prepBusy = true;
		prepNote = '正在读课件（第一次要下载约 13 MB）…';
		try {
			if (needPass) {
				if (!pass.trim()) throw new ApiError('passcode', 'missing', '先填通行码。');
				await checkPasscode(pass.trim());
				savePass(pass.trim());
				needPass = false;
			}
			const { termsFromFiles } = await import('#lib/prep/browser.ts');
			const r = await termsFromFiles(files, c || '这节课', (p) => (prepNote = `正在从课件里挑术语（${p.done}/${p.total}）…`));
			const before = parseTerms(terms).length;
			const merged = parseTerms([terms, ...r.terms].join('\n'));
			typed = merged.join('\n');
			void saveTermsSynced(api, c, terms);
			const added = merged.length - before;
			const bad = r.failed.map((f) => `${f.name}：${f.reason}`).join('；');
			prepNote = (r.terms.length ? `挑出 ${r.terms.length} 个，新加 ${added} 个。` : '没挑出术语。') + (bad ? ` 没读成：${bad}` : '');
		} catch (err) {
			if (err instanceof ApiError && err.kind === 'passcode') needPass = true;
			prepNote = err instanceof ApiError ? err.message : `生成失败：${err instanceof Error ? err.message : String(err)}`;
		} finally {
			prepBusy = false;
		}
	}
	const TERMS_PLACEHOLDER = '一行一个：人名、专有名词、课上常说的缩写\nCobb-Douglas\nMarginal rate of substitution';

	onMount(() => {
		verdict = browserVerdict();
		courses = recentCourses().slice(0, 3);
		needPass = !hasPass();
		share = shareEnabled();
		startDemo();
		// 术语以云端为准：换电脑、清过浏览器也找得回来（通行码就是账号）
		if (!needPass) {
			void retryPendingTerms(api).catch(() => {});
			void pullTerms(api)
				.then((r) => {
					if (r.updated) termsRev++;
				})
				.catch(() => {});
		}
	});

	async function copyLink() {
		try {
			await navigator.clipboard.writeText(location.href);
			copied = true;
		} catch {
			copied = false;
		}
	}

	async function start(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		const c = course.trim();
		if (needPass && !pass.trim()) {
			error = '请填通行码。';
			(document.getElementById('pass') as HTMLInputElement | null)?.focus();
			return;
		}
		starting = true;
		// 第一次填码：先问一次 Worker 这个码能不能用（领一把临时 key，不花对话额度），不行就留在这页说清楚
		if (needPass) {
			try {
				await checkPasscode(pass.trim());
			} catch (err) {
				error = err instanceof ApiError ? err.message : '连不上服务器。检查一下网络，或者开着代理就先关掉再试。';
				starting = false;
				return;
			}
			savePass(pass.trim());
		}
		if (c) rememberCourse(c);
		// 写本机 + 推上云（失败自己排队，不挡开课）
		void saveTermsSynced(api, c, terms);
		// 麦克风权限在上课页开课时要（同一个页面会话，这次点击算用户手势）
		await goto(c ? `/class?course=${encodeURIComponent(c)}` : '/class');
	}
</script>

<svelte:head>
	<title>ClassLive · 英文课，实时看中文</title>
	<meta name="description" content="老师讲英文，屏幕上实时出中英双语字幕，下课出笔记。" />
</svelte:head>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="page" ondragenter={onDragEnter} ondragover={onDragOver} ondragleave={onDragLeave} ondrop={onDrop}>
	<SiteHeader />
	{#if dragging}
		<div class="drop" aria-hidden="true">
			<p>松手，从课件里挑术语</p>
			<span>PDF / PPTX / DOCX</span>
		</div>
	{/if}

	<main class="hero">
		<header class="lead">
			<h1><span class="ph">英文课，</span><span class="ph">实时看中文。</span></h1>
			<p class="lede">叽叽喳喳</p>
			<p class="sub">老师讲英文，屏幕上一句句出中文。整节课自动存好，下课再看。</p>
		</header>

		<section class="stage" aria-label="示例：一节英文讲座的实时字幕">
			<!-- 一块「屏幕」：课件在后面照常放，字幕是浮在上面的磨砂窗 —— 和真上课时一样 -->
			<div class="screen">
				<DemoSlides t={demo.pos} />
				<div class="cap">
					<div class="cap-top">
						<span class="dot" class:on={demo.playing}></span>
						<span>实时字幕</span>
						<span class="cap-hint">点一句，标成没听懂</span>
					</div>
					<div class="cap-body">
						<Stream rows={demo.rows} draftEn={demo.draftEn} draftZh={demo.draftZh} marked={demoMarked} onmark={toggleDemoMark} />
					</div>
				</div>
			</div>
			<p class="caption">
				MIT 9.13 The Human Brain · Nancy Kanwisher ·
				<a href="https://ocw.mit.edu/courses/9-13-the-human-brain-spring-2019/" target="_blank" rel="noopener">MIT OCW</a> · CC BY-NC-SA 4.0
			</p>
		</section>

		<aside class="act">
			{#if verdict === 'mobile'}
				<div class="notice" role="status">
					<strong>请在电脑上打开</strong>
					<p>上课要用电脑上的 Chrome 或 Edge。把链接发到电脑上：</p>
					<button type="button" class="go" onclick={copyLink}>{copied ? '已复制链接' : '复制链接'}</button>
				</div>
			{:else}
				<form class="start" onsubmit={start} novalidate>
					{#if needPass}
						<div class="field">
							<label for="pass">通行码</label>
							<input
								id="pass"
								name="pass"
								autocomplete="off"
								spellcheck="false"
								placeholder="cl- 开头"
								aria-describedby="pass-hint"
								bind:value={pass}
								disabled={verdict !== 'ok'}
							/>
							<p class="hint" id="pass-hint">发你链接的朋友会给你。只填这一次。</p>
						</div>
					{/if}

					{#if courses.length}
						<div class="quick" role="group" aria-label="上过的课：点一下带上那门课的术语">
							{#each courses as c (c)}
								<button
									type="button"
									class="chip"
									aria-pressed={course.trim() === c}
									onclick={() => (course = course.trim() === c ? '' : c)}>{c}</button
								>
							{/each}
						</div>
					{/if}

					<button class="go" type="submit" disabled={verdict !== 'ok' || starting}>
						{starting ? (needPass ? '正在核对通行码…' : '正在进教室…') : '开始上课'}
					</button>
					{#if error}<p class="err" role="alert">{error}</p>{/if}

					<details class="more" bind:open={moreOpen}>
						<summary>课名和术语<span class="sum-note">{moreSummary}</span></summary>
						<div class="more-body">
							<div class="field">
								<label for="course">课名</label>
								<input
									id="course"
									name="course"
									list="course-list"
									autocomplete="off"
									spellcheck="false"
									placeholder="如 ECON10770"
									bind:value={course}
									disabled={verdict !== 'ok'}
								/>
								<datalist id="course-list">
									{#each courses as c (c)}<option value={c}></option>{/each}
								</datalist>
							</div>
							<div class="field">
								<label for="terms">术语</label>
								<textarea
									id="terms"
									rows="4"
									spellcheck="false"
									placeholder={TERMS_PLACEHOLDER}
									value={terms}
									oninput={(e) => (typed = e.currentTarget.value)}
									disabled={verdict !== 'ok'}
								></textarea>
								<div class="prep">
									<button type="button" class="link" onclick={() => fileInput?.click()} disabled={prepBusy || verdict !== 'ok'}>
										{prepBusy ? '正在生成…' : '从课件生成'}
									</button>
									<span class="hint">或把课件拖到这页上 · PDF / PPTX / DOCX</span>
									<input
										bind:this={fileInput}
										class="file"
										type="file"
										accept=".pdf,.pptx,.docx"
										multiple
										onchange={prepFrom}
										tabindex="-1"
										aria-hidden="true"
									/>
								</div>
								{#if prepNote}<p class="hint" role="status">{prepNote}</p>{/if}
								<label class="share">
									<input
										type="checkbox"
										checked={share}
										onchange={(e) => {
											share = e.currentTarget.checked;
											setShareEnabled(share);
										}}
									/>
									<span>和相近课的同学共享术语</span>
								</label>
								<p class="hint">术语跟着通行码存在云端，换电脑也在。</p>
							</div>
						</div>
					</details>

					{#if verdict === 'other'}
						<p class="err" role="alert">请换电脑上的 Chrome 或 Edge：字幕要浮在课件上，只有它们支持置顶小窗。</p>
					{:else}
						<p class="hint fine">
							{#if !needPass}通行码已保存 · <button type="button" class="link" onclick={() => (needPass = true)}>更换</button> · {/if}开课时浏览器会问麦克风权限。不存录音；字幕存在这台电脑，也同步到服务器存 30 天。
						</p>
					{/if}
				</form>
			{/if}
		</aside>
	</main>
</div>

<style>
	.page {
		min-height: 100dvh;
		display: grid;
		grid-template-rows: auto 1fr;
		background: var(--paper);
		color: var(--ink);
		font-family: var(--cl-font);
	}

	/* 版式：标题一整行压在上面；下面一行，左边是窗（主角），右边是开课。
	   窗是全页唯一的深色块，开课按钮是第二个，两块墨挨着，眼睛从字幕走到按钮。 */
	.hero {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(280px, 340px);
		grid-template-areas:
			'lead lead'
			'stage act';
		column-gap: clamp(32px, 4vw, 64px);
		row-gap: clamp(28px, 4vh, 48px);
		align-content: start;
		/* 左右边距与 SiteHeader 同一个 clamp：标题、窗和左上角 logo 对齐在同一条线上 */
		padding: clamp(12px, 3vh, 40px) clamp(16px, 4vw, 56px) clamp(32px, 6vh, 72px);
		width: 100%;
		box-sizing: border-box;
	}
	.lead {
		grid-area: lead;
	}
	/* 标题只在逗号处折行，不把一个字甩到下一行 */
	h1 .ph {
		display: inline-block;
	}
	h1 {
		margin: 0;
		font-family: var(--cl-serif);
		font-size: var(--fs-display);
		font-weight: 600;
		line-height: var(--lh-tight);
		letter-spacing: -0.02em;
		text-wrap: balance;
	}
	.lede {
		margin: 14px 0 0;
		font-size: var(--fs-s);
		letter-spacing: 0.3em;
		color: var(--ink-3);
	}
	.sub {
		margin: 10px 0 0;
		max-width: 34em;
		font-size: var(--fs-l);
		line-height: 1.55;
		color: var(--ink-2);
		text-wrap: pretty;
	}

	/* 字幕窗：用真回放，字号放大到上课时坐在一米外看的样子 */
	.stage {
		grid-area: stage;
		min-width: 0;
	}
	/* 屏幕：16:9，课件铺满；上面压一块深色磨砂字幕窗（桌面版悬浮窗的样子） */
	.screen {
		position: relative;
		aspect-ratio: 16 / 9;
		max-height: min(56vh, 560px);
		width: 100%;
		overflow: hidden;
		border-radius: var(--radius);
		box-shadow:
			0 0 0 1px oklch(0.2 0.01 60 / 0.1),
			0 2px 6px oklch(0.2 0.02 60 / 0.08),
			0 40px 80px -36px oklch(0.2 0.02 60 / 0.45);
	}
	.cap {
		view-transition-name: stage;
		position: absolute;
		left: 4%;
		right: 4%;
		bottom: 5%;
		height: 54%;
		display: grid;
		grid-template-rows: auto minmax(0, 1fr);
		padding: 12px clamp(16px, 2.2vw, 30px) 16px;
		border-radius: var(--radius);
		/* 压在浅色课件上：比通用 --glass 更深、几乎无彩度，免得透出来发灰发黄；顶上一道很淡的反光 */
		background:
			linear-gradient(180deg, oklch(1 0 0 / 0.055), transparent 38%),
			oklch(0.16 0.003 60 / 0.86);
		backdrop-filter: blur(24px) saturate(1.2);
		-webkit-backdrop-filter: blur(24px) saturate(1.2);
		box-shadow:
			inset 0 1px 0 oklch(1 0 0 / 0.12),
			inset 0 0 0 1px var(--glass-line),
			var(--glass-shadow);
		color: var(--on-glass);
		-webkit-font-smoothing: antialiased;
		--cap-scale: 1.12;
	}
	.cap-top {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: var(--fs-xs);
		color: var(--on-glass-3);
	}
	.cap-hint {
		margin-left: auto;
		opacity: 0;
		transition: opacity var(--fast) var(--ease);
	}
	.cap:hover .cap-hint {
		opacity: 1;
	}
	.dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		box-shadow: inset 0 0 0 1.5px var(--on-glass-3);
	}
	.dot.on {
		background: var(--on-glass);
		box-shadow: none;
		animation: breathe 2.4s var(--ease) infinite;
	}
	@keyframes breathe {
		50% {
			opacity: 0.35;
		}
	}
	.cap-body {
		min-height: 0;
		padding-top: 8px;
	}
	.caption {
		margin: 12px 0 0;
		font-size: var(--fs-xs);
		line-height: 1.6;
		color: var(--ink-3);
	}
	.caption a {
		color: inherit;
	}

	/* 开课：按钮在最上，可选项折叠在下面 */
	.act {
		grid-area: act;
		align-self: end;
		padding-bottom: calc(var(--fs-xs) * 1.6 + 12px); /* 底边对齐窗，不算窗下那行说明 */
	}
	.start {
		display: grid;
		gap: 14px;
	}
	.field {
		display: grid;
		gap: 6px;
	}
	label {
		font-size: var(--fs-s);
		font-weight: 500;
		color: var(--ink-2);
	}
	input,
	textarea {
		border-radius: var(--radius-s);
		border: 1px solid var(--field-line);
		background: oklch(1 0 0);
		color: var(--ink);
		caret-color: var(--ink);
		transition:
			border-color var(--fast) var(--ease),
			box-shadow var(--fast) var(--ease);
	}
	input {
		height: 44px;
		padding: 0 14px;
		font: var(--fs-m) / 1 var(--cl-font);
	}
	textarea {
		padding: 10px 14px;
		font: var(--fs-s) / 1.6 var(--cl-font);
		resize: vertical;
		min-height: 88px;
	}
	input::placeholder,
	textarea::placeholder {
		color: var(--ink-3);
	}
	input:hover:not(:disabled),
	textarea:hover:not(:disabled) {
		border-color: var(--field-line-hover);
	}
	input:focus-visible,
	textarea:focus-visible {
		outline: none;
		border-color: var(--ink);
		box-shadow: 0 0 0 3px var(--focus-ring);
	}
	input:disabled,
	textarea:disabled {
		background: var(--paper-2);
		color: var(--ink-3);
	}

	.go {
		height: 52px;
		width: 100%;
		border: 0;
		border-radius: var(--radius-s);
		background: var(--ink);
		color: var(--paper);
		font: 600 var(--fs-m) / 1 var(--cl-font);
		letter-spacing: 0.04em;
		cursor: pointer;
		box-shadow: 0 8px 18px -10px oklch(0.2 0.01 60 / 0.6);
		transition:
			transform var(--fast) var(--ease),
			background-color var(--fast) var(--ease);
	}
	.go:hover:not(:disabled) {
		background: var(--ink-press);
	}
	.go:active:not(:disabled) {
		transform: scale(0.985);
	}
	.go:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 3px;
	}
	.go:disabled {
		background: oklch(0.2 0.01 60 / 0.22);
		box-shadow: none;
		cursor: not-allowed;
	}

	.more {
		border-top: 1px solid var(--rule);
		padding-top: 12px;
	}
	.more summary {
		display: flex;
		align-items: baseline;
		gap: 10px;
		font-size: var(--fs-s);
		font-weight: 500;
		color: var(--ink);
		cursor: pointer;
		list-style: none;
		border-radius: var(--radius-xs);
	}
	.more summary::-webkit-details-marker {
		display: none;
	}
	.more summary::after {
		content: '';
		width: 6px;
		height: 6px;
		margin-left: auto;
		align-self: center;
		border-right: 1.5px solid var(--ink-3);
		border-bottom: 1.5px solid var(--ink-3);
		transform: rotate(45deg) translateY(-2px);
		transition: transform var(--move) var(--ease);
	}
	.more[open] summary::after {
		transform: rotate(-135deg) translateY(-2px);
	}
	.sum-note {
		font-weight: 400;
		color: var(--ink-3);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.more summary:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 3px;
	}
	.more-body {
		display: grid;
		gap: 14px;
		padding-top: 14px;
	}
	.chip {
		height: 28px;
		padding: 0 12px;
		border: 1px solid var(--field-line);
		border-radius: var(--radius-pill);
		background: none;
		color: var(--ink-2);
		font: var(--fs-xs) / 1 var(--cl-font);
		cursor: pointer;
		transition:
			border-color var(--fast) var(--ease),
			color var(--fast) var(--ease);
	}
	.chip[aria-pressed='true'] {
		border-color: var(--ink);
		background: var(--ink);
		color: var(--paper);
	}
	.quick {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.chip:hover {
		border-color: var(--ink);
		color: var(--ink);
	}
	.chip:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 2px;
	}
	.prep {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 4px 10px;
	}
	.link {
		padding: 0;
		border: 0;
		background: none;
		font: 500 var(--fs-s) / 1.6 var(--cl-font);
		color: var(--ink);
		text-decoration: underline;
		text-underline-offset: 3px;
		cursor: pointer;
	}
	.link:disabled {
		color: var(--ink-3);
		cursor: progress;
	}
	.link:focus-visible {
		outline: 2px solid var(--ink);
		outline-offset: 3px;
		border-radius: var(--radius-xs);
	}
	.file {
		display: none;
	}
	.share {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		font-size: var(--fs-s);
		font-weight: 400;
		line-height: 1.6;
		color: var(--ink-2);
		cursor: pointer;
	}
	.share input {
		margin: 4px 0 0;
		accent-color: var(--ink);
	}
	.hint,
	.err {
		margin: 0;
		font-size: var(--fs-s);
		line-height: 1.6;
	}
	.hint {
		color: var(--ink-3);
	}
	.hint .link {
		font-size: inherit;
	}
	.fine {
		font-size: var(--fs-xs);
	}
	.err {
		color: var(--ink);
		font-weight: 600;
	}

	/* 拖课件进来：整页蒙一层纸色，中间一句话。不用彩色，用虚线框 */
	.drop {
		position: fixed;
		inset: 12px;
		z-index: 10;
		display: grid;
		place-content: center;
		gap: 8px;
		text-align: center;
		border-radius: var(--radius);
		border: 1.5px dashed var(--ink-3);
		background: oklch(from var(--paper) l c h / 0.94);
		pointer-events: none;
		animation: rise var(--fast) var(--ease) both;
	}
	.drop p {
		margin: 0;
		font-size: var(--fs-l);
		font-weight: 600;
	}
	.drop span {
		font-size: var(--fs-s);
		color: var(--ink-3);
	}
	.notice {
		display: grid;
		gap: 10px;
		padding: 20px;
		border-radius: var(--radius);
		background: var(--paper-2);
	}
	.notice strong {
		font-size: var(--fs-m);
	}
	.notice p {
		margin: 0;
		font-size: var(--fs-s);
		line-height: 1.65;
		color: var(--ink-2);
	}

	/* 进场：只有一个时刻 —— 标题和窗一起浮上来，200 ms 内收住 */
	@media (prefers-reduced-motion: no-preference) {
		.lead,
		.stage,
		.act {
			animation: rise var(--move) var(--ease) both;
		}
		.stage {
			animation-delay: 60ms;
		}
		.act {
			animation-delay: 100ms;
		}
	}
	@keyframes rise {
		from {
			opacity: 0;
			transform: translate3d(0, 10px, 0);
		}
	}

	@media (max-width: 900px) {
		.hero {
			grid-template-columns: minmax(0, 1fr);
			grid-template-areas:
				'lead'
				'act'
				'stage';
		}
		.act {
			align-self: start;
			padding-bottom: 0;
		}
		.screen {
			max-height: none;
		}
		.cap {
			--cap-scale: 0.95;
		}
	}
	@media (max-width: 520px) {
		/* 手机竖屏：屏幕改 4:5，课件只露上面一截，字幕窗占下大半 */
		.screen {
			aspect-ratio: 4 / 5;
		}
		.cap {
			left: 3%;
			right: 3%;
			bottom: 3%;
			height: 66%;
			--cap-scale: 0.85;
			padding: 10px 14px 12px;
		}
		.sub {
			font-size: var(--fs-m);
		}
	}
</style>
