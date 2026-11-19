<script lang="ts">
	// 上课界面：整屏都是字幕。桌面版同款深色 + 白字，可切亮色。
	// 整个房间就是门面那扇窗放大来的（view-transition-name: stage）。
	// 工具条鼠标不动 2.5 秒就隐去，动一下 / 键盘聚焦就回来——屏幕留给字幕。
	// 默认开真课（麦克风 → Soniox → Python 核心 → 翻译 → 本机落盘，`lib/live/connect.ts`）；
	// `?demo=1` 播放示例回放；`?state=listening|nomic|reconnect` 只给设计稿预览空状态与出错状态。
	import { onMount, mount, unmount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import Stream from '#lib/ui/Stream.svelte';
	import PipCaptions from '#lib/ui/PipCaptions.svelte';
	import MicMenu from '#lib/ui/MicMenu.svelte';
	import { demo, startDemo, type CapRow } from '#lib/demo/player.svelte.ts';
	import { connectLive, type LiveHandle } from '#lib/live/connect.ts';
	import { ApiError } from '#lib/api/index.ts';
	import { saveMarks } from '#lib/env.ts';
	import { holdWakeLock, watchHidden, type WakeLockHandle } from '#lib/live/wake.ts';
	import { downloadMarkdown } from '#lib/after/export.ts';

	type Phase = 'live' | 'demo' | 'listening' | 'nomic' | 'reconnect';
	const SCALES = [1.15, 1.35, 1.6, 1.9, 2.25];

	// 课名可以不填（Owen 2026-11-19）：空串照样开课，界面上显示「未命名的课」，下课在课后页起名
	const courseRaw = $derived((page.url.searchParams.get('course') ?? '').trim());
	const course = $derived(courseRaw || '未命名的课');
	const phase = $derived<Phase>(
		page.url.searchParams.get('demo') === '1' ? 'demo' : ((page.url.searchParams.get('state') as Phase) || 'live')
	);

	// 真会话：开课前领 key 失败（码 / 额度 / 网络）落在 liveError，开课后的状态全读 session
	let live = $state<LiveHandle | null>(null);
	let liveError = $state('');
	let ending = $state(false);
	let micNote = $state('');
	async function pickMic(id: string): Promise<boolean> {
		const a = live?.audio;
		if (!a?.switchDevice) return false;
		const ok = await a.switchDevice(id);
		if (ok) micNote = '';
		return ok;
	}

	const src = $derived(live?.session ?? demo);
	const sess = $derived(live?.session ?? null);
	const STATUS_TEXT: Record<string, string> = {
		starting: '正在连接',
		listening: '在听',
		silent: '没听到声音',
		reconnecting: '重连中',
		ended: '已下课',
		error: '出错了'
	};
	const statusText = $derived(
		phase === 'live'
			? liveError
				? '没开成'
				: sess
					? STATUS_TEXT[sess.status]
					: '正在连接'
			: phase === 'reconnect'
				? '重连中'
				: phase === 'nomic'
					? '没听到声音'
					: phase === 'demo'
						? '回放中'
						: '在听'
	);
	const statusOk = $derived(
		phase === 'live' ? !liveError && (sess?.status === 'listening' || sess?.status === 'starting' || !sess) : phase === 'demo' || phase === 'listening'
	);

	let theme = $state<'dark' | 'light'>('dark');
	let scaleIdx = $state(2);
	let elapsed = $state(0);
	// 同步状态是普通 getter（不是响应式）：跟着每秒的计时数字一起重读
	const syncView = $derived.by(() => {
		void elapsed;
		return live?.sync ?? null;
	});
	let pipWin = $state<Window | null>(null);
	let confirmEnd = $state(false);
	let idle = $state(false);
	let full = $state(false);
	type Mode = 'both' | 'zh' | 'en';
	const MODES: { id: Mode; label: string }[] = [
		{ id: 'both', label: '双语' },
		{ id: 'zh', label: '中文' },
		{ id: 'en', label: 'English' }
	];
	let mode = $state<Mode>('both');
	let rail = $state(true);
	/** 窄窗口（和课件并排时）工具条只留状态 / 小窗 / 下课，其余收进「更多」 */
	let more = $state(false);
	function segKey(e: KeyboardEvent) {
		if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
		e.preventDefault();
		const i = MODES.findIndex((m) => m.id === mode);
		const next = MODES[(i + (e.key === 'ArrowRight' ? 1 : MODES.length - 1)) % MODES.length];
		setMode(next.id);
		(e.currentTarget as HTMLElement).querySelector<HTMLElement>(`[data-mode="${next.id}"]`)?.focus();
	}
	let following = $state(true);
	let stream = $state<ReturnType<typeof Stream>>();
	/** 标记过的句子存副本：播放器只留最近若干句，标记不能跟着滚掉 */
	let marks = $state<CapRow[]>([]);
	const markedIds = $derived(new Set(marks.map((m) => m.id)));
	/** 每句是开课后第几秒出来的（上课页自己的钟；进页面之前就有的句子记 0） */
	const arrived = new Map<number, number>();
	const at = (id: number) => arrived.get(id) ?? 0;
	$effect.pre(() => {
		for (const r of src.rows) if (!arrived.has(r.id)) arrived.set(r.id, live ? r.t : elapsed);
	});
	const fmt = (t: number) => `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
	/** 时间轴一分钟一格（一节课 50–90 格，侧栏放得下；一句一格的话一小时七百多格会把标记列表挤出去）。
	 *  格高 = 这分钟说了多少句；有标记的格亮；点一格跳到这分钟第一句 */
	const minutesAxis = $derived.by(() => {
		const buckets: { m: number; first: CapRow; n: number; marked: boolean }[] = [];
		for (const r of src.rows) {
			const m = Math.floor(at(r.id) / 60);
			let b = buckets.at(-1);
			if (!b || b.m !== m) buckets.push((b = { m, first: r, n: 0, marked: false }));
			b.n++;
			if (markedIds.has(r.id)) b.marked = true;
		}
		const peak = Math.max(1, ...buckets.map((b) => b.n));
		return buckets.map((b) => ({ ...b, len: b.n / peak }));
	});

	function toggleMark(row: CapRow) {
		marks = markedIds.has(row.id) ? marks.filter((m) => m.id !== row.id) : [...marks, row].sort((a, b) => a.id - b.id);
		if (live) saveMarks(live.session_id, marks.map((m) => m.id));
	}
	function markLatest() {
		const last = src.rows.at(-1);
		if (last && !markedIds.has(last.id)) toggleMark(last);
	}
	function jump(id: number) {
		if (src.rows.some((r) => r.id === id)) stream?.jumpTo(id);
	}

	const pipSupported = typeof globalThis !== 'undefined' && 'documentPictureInPicture' in globalThis;
	const clock = $derived(
		`${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`
	);

	let idleTimer: ReturnType<typeof setTimeout> | undefined;
	function wake() {
		idle = false;
		clearTimeout(idleTimer);
		idleTimer = setTimeout(() => {
			// 有东西聚焦在工具条里（键盘用户）就不藏
			if (!document.querySelector('.bar :focus-visible')) idle = true;
		}, 2500);
	}

	onMount(() => {
		try {
			const t = localStorage.getItem('cl.capTheme');
			if (t === 'light' || t === 'dark') theme = t;
			const raw = localStorage.getItem('cl.capScale');
			const s = raw === null ? NaN : Number(raw);
			if (Number.isInteger(s) && s >= 0 && s < SCALES.length) scaleIdx = s;
			const m = localStorage.getItem('cl.capMode');
			if (m === 'both' || m === 'zh' || m === 'en') mode = m;
			const r = localStorage.getItem('cl.rail');
			rail = r === null ? innerWidth >= 1100 : r === '1';
		} catch {
			/* 不记也行 */
		}
		if (phase === 'demo') startDemo(9);
		else demo.pause();
		let gone = false;
		// 上课期间别让屏幕睡（桌面版 power.py 的网页对应物；挡不住合盖）
		let screenLock: WakeLockHandle | null = null;
		let unHidden = () => {};
		if (phase === 'live') {
			screenLock = holdWakeLock();
			// 后台标签页：只记下来（A11 要量后台 5 分钟是否断流），不打扰
			unHidden = watchHidden((span) => {
				if (import.meta.env.DEV) console.info('[class] hidden', Math.round((span.shownAt - span.hiddenAt) / 1000), 's');
			});
		}
		if (phase === 'live') {
			connectLive(courseRaw)
				.then((h) => {
					if (gone) {
						void h.end();
						return;
					}
					live = h;
					h.audio.onDeviceLost?.(() => (micNote = '刚才的麦克风断开了，已换回系统默认麦克风'));
					// 只在 dev：给 scripts/e2e-live.mjs 读延迟（`__clLive.session.latency()`）
					if (import.meta.env.DEV) (globalThis as unknown as { __clLive?: LiveHandle }).__clLive = h;
					return h.session.start();
				})
				.catch((err) => {
					liveError = err instanceof ApiError ? err.message : `开课失败：${err instanceof Error ? err.message : String(err)}`;
				});
		}
		const t0 = performance.now();
		// 只是界面上的计时数字，不推任何逻辑时钟
		const id = setInterval(() => (elapsed = Math.floor((performance.now() - t0) / 1000)), 1000);
		const onFs = () => (full = !!document.fullscreenElement);
		document.addEventListener('fullscreenchange', onFs);
		wake();
		return () => {
			clearInterval(id);
			clearTimeout(idleTimer);
			document.removeEventListener('fullscreenchange', onFs);
			demo.pause();
			pipWin?.close();
			gone = true;
			screenLock?.release();
			unHidden();
			void live?.end();
		};
	});

	function persist(key: string, v: string) {
		try {
			localStorage.setItem(key, v);
		} catch {
			/* 不记也行 */
		}
	}
	function setTheme(t: 'dark' | 'light') {
		theme = t;
		persist('cl.capTheme', t);
	}
	function setScale(i: number) {
		scaleIdx = Math.max(0, Math.min(SCALES.length - 1, i));
		persist('cl.capScale', String(scaleIdx));
	}
	function setMode(m: Mode) {
		mode = m;
		persist('cl.capMode', m);
	}
	function toggleRail() {
		rail = !rail;
		persist('cl.rail', rail ? '1' : '0');
	}
	function toggleFull() {
		if (document.fullscreenElement) document.exitFullscreen();
		else document.documentElement.requestFullscreen().catch(() => {});
	}

	function onKey(e: KeyboardEvent) {
		if (e.metaKey || e.ctrlKey || e.altKey) return;
		if ((e.target as HTMLElement | null)?.closest?.('input, textarea')) return;
		if (e.key === 'f' || e.key === 'F') toggleFull();
		else if (e.key === '=' || e.key === '+') setScale(scaleIdx + 1);
		else if (e.key === '-') setScale(scaleIdx - 1);
		else if (e.key === 'm' || e.key === 'M') markLatest();
		else if ((e.key === 'p' || e.key === 'P') && pipSupported) void togglePip();
		else if (e.key === 'Escape' && !following) stream?.follow();
		else return;
		e.preventDefault();
	}

	function copyStyles(to: Document) {
		for (const sheet of Array.from(document.styleSheets)) {
			try {
				const s = to.createElement('style');
				s.textContent = Array.from(sheet.cssRules, (r) => r.cssText).join('\n');
				to.head.append(s);
			} catch {
				if (sheet.href) {
					const l = to.createElement('link');
					l.rel = 'stylesheet';
					l.href = sheet.href;
					to.head.append(l);
				}
			}
		}
	}

	async function togglePip() {
		if (pipWin) {
			pipWin.close();
			return;
		}
		const dpip = (globalThis as unknown as { documentPictureInPicture: { requestWindow(o: object): Promise<Window> } })
			.documentPictureInPicture;
		// 小窗尺寸记住：每个人摆课件的方式不同，上次拖成什么样这次就还是什么样
		let size = { width: 560, height: 200 };
		try {
			const raw = JSON.parse(localStorage.getItem('cl.pipSize') ?? 'null');
			if (raw && raw.width >= 240 && raw.height >= 80) size = { width: raw.width, height: raw.height };
		} catch {
			/* 用默认 */
		}
		const w = await dpip.requestWindow(size);
		copyStyles(w.document);
		w.document.title = `ClassLive · ${course}`;
		// 焦点在小窗里时按键也要管用（M 标记、+/− 字号、P 收回）
		w.addEventListener('keydown', onKey);
		const app = mount(PipCaptions, {
			target: w.document.body,
			props: {
				player: src,
				theme: () => theme,
				// 小窗是学生真正盯着的那块：状态出岔子要在这里也看得见，字号 / 模式跟主页面走
				view: () => ({ status: statusText, ok: statusOk, scale: SCALES[scaleIdx], mode, marked: markedIds }),
				onmark: toggleMark,
				onmarkLatest: markLatest,
				onscale: (d: number) => setScale(scaleIdx + d),
				onback: () => {
					window.focus();
					w.close();
				}
			}
		});
		pipWin = w;
		w.addEventListener('pagehide', () => {
			persist('cl.pipSize', JSON.stringify({ width: w.innerWidth, height: w.innerHeight }));
			unmount(app);
			pipWin = null;
		});
	}

	async function endClass() {
		if (!confirmEnd) {
			confirmEnd = true;
			setTimeout(() => (confirmEnd = false), 3000);
			return;
		}
		demo.pause();
		if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
		if (live) {
			ending = true;
			const h = live;
			await h.end();
			// 下课自动下载一份 Markdown（Owen 2026-11-19）：本机和服务器之外，用户手里再有一份，换电脑、清浏览器都不怕
			let saved = false;
			try {
				if (h.store) {
					const lines = await h.store.linesOf(h.session_id);
					const meta = (await h.store.listSessions()).find((m) => m.session_id === h.session_id) ?? null;
					if (lines.length) {
						downloadMarkdown(meta, lines, markedIds);
						saved = true;
					}
				}
			} catch {
				/* 下载不成也照常进课后页，那里还有下载按钮 */
			}
			await goto(`/after?session=${encodeURIComponent(h.session_id)}${saved ? '&saved=1' : ''}`);
			return;
		}
		await goto('/');
	}
</script>

<svelte:head><title>{course} · ClassLive</title></svelte:head>
<svelte:window
	onkeydown={onKey}
	onbeforeunload={(e) => {
		// 上课中关标签页 / 刷新：浏览器弹一句确认。字幕已逐句存好，拦的是「不小心关掉、这节课就断了」
		if (live && !ending && sess?.status !== 'ended') e.preventDefault();
	}}
/>

<div
	class="room"
	class:idle
	class:with-rail={rail}
	data-cap={theme}
	style:--cap-scale={SCALES[scaleIdx]}
	onpointermove={wake}
	onpointerdown={wake}
	onfocusin={wake}
	role="presentation"
>
	<header class="bar" class:more-open={more}>
		<div class="state">
			<span class="dot" class:on={statusOk} class:warn={!statusOk}></span>
			<span class="status">{statusText}</span>
			<span class="course" title={course}>{course}</span>
			<span class="clock">{clock}</span>
		</div>

		<div class="tools" role="toolbar" aria-label="上课控制">
			<div
				class="seg opt"
				role="radiogroup"
				aria-label="显示哪种字幕"
				tabindex="-1"
				style:--i={MODES.findIndex((m) => m.id === mode)}
				onkeydown={segKey}
			>
				<span class="seg-thumb" aria-hidden="true"></span>
				{#each MODES as m (m.id)}
					<button
						type="button"
						role="radio"
						data-mode={m.id}
						aria-checked={mode === m.id}
						tabindex={mode === m.id ? 0 : -1}
						onclick={() => setMode(m.id)}>{m.label}</button
					>
				{/each}
			</div>
			{#if phase === 'live' && live}
				<MicMenu onpick={pickMic} />
			{/if}
			{#if pipSupported}
				<button class="tool wide" type="button" onclick={togglePip} aria-pressed={!!pipWin}>
					<svg viewBox="0 0 16 16" aria-hidden="true"
						><rect x="1.5" y="3" width="13" height="10" rx="2" /><rect x="8" y="8" width="5" height="3.5" rx="1" class="fill" /></svg
					>
					<span>{pipWin ? '收回小窗' : '置顶小窗'}</span>
				</button>
			{/if}
			<div class="group opt">
				<button class="tool" type="button" onclick={() => setScale(scaleIdx - 1)} disabled={scaleIdx === 0} aria-label="字小一点" title="字小一点（−）">
					<span class="a sm">A</span>
				</button>
				<button
					class="tool"
					type="button"
					onclick={() => setScale(scaleIdx + 1)}
					disabled={scaleIdx === SCALES.length - 1}
					aria-label="字大一点"
					title="字大一点（+）"
				>
					<span class="a">A</span>
				</button>
			</div>
			<button
				class="tool opt"
				type="button"
				onclick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
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
			<button class="tool opt" type="button" onclick={toggleFull} aria-pressed={full} aria-label={full ? '退出全屏' : '全屏'} title={full ? '退出全屏（F）' : '全屏（F）'}>
				{#if full}
					<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2v4H2M10 2v4h4M6 14v-4H2M10 14v-4h4" /></svg>
				{:else}
					<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6V2h4M14 6V2h-4M2 10v4h4M14 10v4h-4" /></svg>
				{/if}
			</button>
			<button class="tool opt rail-btn" type="button" onclick={toggleRail} aria-pressed={rail} aria-label={rail ? '收起侧栏' : '展开侧栏'} title={rail ? '收起侧栏' : '展开侧栏'}>
				<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="1.5" y="2.5" width="13" height="11" rx="2" /><path d="M10 2.5v11" /></svg>
			</button>
			<button class="tool more-btn" type="button" onclick={() => (more = !more)} aria-expanded={more} aria-label="更多设置" title="更多设置">
				<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3.5" cy="8" r="0.6" /><circle cx="8" cy="8" r="0.6" /><circle cx="12.5" cy="8" r="0.6" /></svg>
			</button>
			<button class="end" class:confirm={confirmEnd} type="button" onclick={endClass}>
				{ending ? '正在收尾…' : confirmEnd ? '再点一次下课' : '下课'}
			</button>
		</div>
	</header>

	<main class="stage">
		{#if phase === 'live' && (liveError || sess?.status === 'error')}
			<div class="empty">
				<p class="big">{liveError ? '这节课没开成' : '出错了'}</p>
				<p class="small">{liveError || sess?.message}</p>
				<button class="soft" type="button" onclick={() => goto('/')}>回开课页（可换通行码）</button>
			</div>
		{:else if phase === 'live' && src.rows.length === 0 && !src.draftEn}
			<div class="empty">
				{#if sess?.status === 'silent'}
					<p class="big">6 秒没听到声音</p>
					<p class="small">检查麦克风有没有选对、被静音。</p>
					<div><MicMenu onpick={pickMic} compact={false} /></div>
				{:else if sess?.status === 'reconnecting'}
					<p class="big">网断了，正在重连</p>
					<p class="small">连上后接着出。</p>
				{:else if sess?.status === 'listening'}
					<p class="big">正在听，等老师开口…</p>
					<p class="small">说完一句稍等一下。</p>
				{:else if sess?.step === 'mic' || !sess}
					<p class="big">正在接麦克风…</p>
					<p class="small">浏览器问麦克风时点允许。</p>
				{:else if sess.step === 'asr'}
					<p class="big">正在连识别…</p>
					<p class="small">一般两三秒。</p>
				{:else}
					<p class="big">正在准备识别核心…</p>
					<p class="small">第一次要下载约 13 MB，之后浏览器会记住。</p>
				{/if}
			</div>
		{:else if phase === 'demo' || phase === 'live'}
			{#if micNote}
				<p class="banner" role="status">{micNote}</p>
			{:else if phase === 'live' && sess && sess.status !== 'listening' && sess.status !== 'starting' && sess.status !== 'ended'}
				<p class="banner" role="status">{sess.status === 'silent' ? '6 秒没听到声音，检查一下麦克风' : sess.status === 'reconnecting' ? '网断了，正在重连…' : sess.message}</p>
			{/if}
			<Stream
				bind:this={stream}
				bind:following
				rows={src.rows}
				draftEn={src.draftEn}
				draftZh={src.draftZh}
				{mode}
				marked={markedIds}
				onmark={toggleMark}
				reviewable
				announce={phase === 'live'}
			/>
		{:else}
			<div class="empty">
				{#if phase === 'listening'}
					<p class="big">正在听，等老师开口…</p>
					<p class="small">说完一句稍等一下。</p>
				{:else if phase === 'nomic'}
					<p class="big">6 秒没听到声音</p>
					<p class="small">检查麦克风有没有选对、被静音。</p>
					<div><MicMenu onpick={async () => false} compact={false} /></div>
				{:else}
					<p class="big">网断了，正在重连</p>
					<p class="small">第 2 次尝试。这段时间的声音先存在本机，连上后把字幕补上。</p>
				{/if}
			</div>
		{/if}
	</main>

	{#if rail && (phase === 'demo' || phase === 'live')}
		<aside class="rail" aria-label="这节课">
			<section>
				<h2>这节课</h2>
				<dl class="stats">
					<div><dt>已上</dt><dd>{clock}</dd></div>
					<div><dt>字幕</dt><dd>{src.rows.length} 句</dd></div>
					<div><dt>标记</dt><dd>{marks.length} 处</dd></div>
				</dl>
				{#if phase === 'live'}
					<p class="saved">
						{live?.store?.persistent === false
							? '这个浏览器不让存，关掉标签页字幕就没了'
							: syncView?.phase === 'synced'
								? `已存本机，已同步 ${syncView.acked} 句`
								: syncView?.phase === 'offline'
									? '已存本机；服务器暂时连不上，连上后自动补传'
									: syncView?.phase === 'error'
										? '已存本机；同步到服务器出错了，下课会自动下载一份'
										: '字幕逐句存在这台电脑里'}
					</p>
				{/if}
				<!-- 时间轴：一分钟一格，有标记的那格亮；点一格跳到那分钟回看 -->
				<div class="ticks" role="group" aria-label="时间轴（一分钟一格）">
					{#each minutesAxis as b (b.m)}
						<button
							type="button"
							class="tick"
							class:on={b.marked}
							style:--len={b.len}
							title={`第 ${b.m + 1} 分钟 · ${b.n} 句`}
							aria-label={`第 ${b.m + 1} 分钟，${b.n} 句${b.marked ? '，有标记' : ''}`}
							onclick={() => jump(b.first.id)}
						></button>
					{/each}
				</div>
			</section>

			<section class="marks">
				<h2>没听懂的地方</h2>
				{#if marks.length === 0}
					<p class="hint">点一下哪句字幕，或按 <kbd>M</kbd> 标记最新一句。标记会列在这里，点它就跳回那句。</p>
				{:else}
					<ol>
						{#each marks as m (m.id)}
							<li>
								<button type="button" class="mark" onclick={() => jump(m.id)}>
									<span class="t">{fmt(at(m.id))}</span>
									<span class="txt">{m.zh || m.en}</span>
								</button>
								<button type="button" class="x" onclick={() => toggleMark(m)} aria-label="取消这个标记">
									<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" /></svg>
								</button>
							</li>
						{/each}
					</ol>
				{/if}
			</section>

			<footer class="keys">
				<span><kbd>M</kbd> 标记</span>
				<span><kbd>F</kbd> 全屏</span>
				{#if pipSupported}<span><kbd>P</kbd> 小窗</span>{/if}
				<span><kbd>+</kbd><kbd>−</kbd> 字号</span>
				<span>往上滚回看，<kbd>Esc</kbd> 回到最新</span>
			</footer>
		</aside>
	{/if}
</div>

<style>
	.room {
		view-transition-name: stage;
		position: relative;
		height: 100dvh;
		overflow: hidden;
		background:
			radial-gradient(120% 70% at 50% 115%, var(--room-glow), transparent 60%),
			var(--glass-ground);
		color: var(--on-glass);
		font-family: var(--cl-font);
		-webkit-font-smoothing: antialiased;
		--room-glow: oklch(0.28 0.012 60);
		/* 整屏字幕的基准字号随屏幕宽度走：1440 宽约 19px，2560 宽约 25px，再乘 A−/A+ 的倍数 */
		--cap-base: clamp(16px, 0.55vw + 11px, 26px);
		--z: calc(var(--cap-base) * var(--cap-scale));
		transition:
			background-color var(--move) var(--ease),
			color var(--move) var(--ease);
	}
	.room[data-cap='light'] {
		--room-glow: oklch(0.93 0.008 80);
	}
	.room.idle {
		cursor: none;
	}
	.room :global(::selection) {
		background: oklch(1 0 0 / 0.2);
	}
	.room[data-cap='light'] :global(::selection) {
		background: oklch(0.2 0.01 60 / 0.14);
	}

	/* 工具条浮在字幕上方，不占字幕的高度 */
	.bar {
		position: absolute;
		inset: 0 0 auto;
		z-index: 1;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 10px 16px;
		padding: 14px clamp(16px, 3vw, 32px) 28px;
		background: linear-gradient(to bottom, var(--glass-ground) 40%, transparent);
		transition:
			opacity var(--move) var(--ease),
			transform var(--move) var(--ease);
	}
	.idle .bar {
		opacity: 0;
		transform: translateY(-6px);
		pointer-events: none;
	}
	.state {
		display: flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
		flex: 1 1 260px;
		font-size: var(--fs-s);
	}
	.dot {
		flex: none;
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--on-glass-3);
	}
	.dot.on {
		background: var(--on-glass);
		animation: breathe 2.4s var(--ease) infinite;
	}
	.dot.warn {
		background: transparent;
		box-shadow: inset 0 0 0 1.5px var(--on-glass);
	}
	@keyframes breathe {
		50% {
			opacity: 0.4;
		}
	}
	.status {
		flex: none;
		color: var(--on-glass-2);
	}
	.course {
		min-width: 0;
		padding-left: 10px;
		border-left: 1px solid var(--glass-line);
		font-weight: 600;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.clock {
		flex: none;
		color: var(--on-glass-3);
		font-variant-numeric: tabular-nums;
	}

	.tools {
		display: flex;
		align-items: center;
		gap: 4px;
		flex: none;
		margin-left: auto;
	}
	.group {
		display: flex;
	}
	button {
		font-family: inherit;
		color: inherit;
	}
	.tool {
		height: 34px;
		min-width: 34px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 7px;
		padding: 0 8px;
		border: 0;
		border-radius: var(--radius-s);
		background: transparent;
		color: var(--on-glass-2);
		font-size: var(--fs-s);
		cursor: pointer;
		transition:
			background-color var(--fast) var(--ease),
			color var(--fast) var(--ease),
			transform var(--fast) var(--ease);
	}
	.tool.wide {
		padding: 0 12px;
	}
	.tool:hover:not(:disabled) {
		background: var(--glass-hover);
		color: var(--on-glass);
	}
	.tool:active:not(:disabled) {
		transform: scale(0.95);
	}
	.tool:disabled {
		color: var(--on-glass-3);
		opacity: 0.5;
		cursor: default;
	}
	.tool[aria-pressed='true'] {
		color: var(--on-glass);
		background: var(--glass-hover);
	}
	.tool:focus-visible,
	.end:focus-visible,
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
	svg .fill {
		fill: currentColor;
		stroke: none;
	}
	.a {
		font: 600 var(--fs-m) / 1 var(--cl-font);
	}
	.a.sm {
		font-size: var(--fs-xs);
	}
	.end {
		margin-left: 8px;
		height: 34px;
		padding: 0 16px;
		border: 0;
		border-radius: var(--radius-s);
		background: var(--on-glass);
		color: var(--glass-ground);
		font-size: var(--fs-s);
		font-weight: 600;
		white-space: nowrap;
		cursor: pointer;
		transition:
			opacity var(--fast) var(--ease),
			transform var(--fast) var(--ease);
	}
	.end:hover {
		opacity: 0.88;
	}
	.end:active {
		transform: scale(0.97);
	}

	/* 字幕占满整屏：上缘留出工具条的位置，往上的旧句在 Stream 里自己淡出 */
	.stage {
		position: absolute;
		inset: 0;
		transition: right var(--move) var(--ease);
		box-sizing: border-box;
		padding: clamp(72px, 10vh, 120px) clamp(20px, 7vw, 140px) clamp(28px, 9vh, 96px);
	}
	.stage > :global(*) {
		max-width: min(1320px, calc(var(--z) * 34));
		margin: 0 auto;
	}

	.with-rail .stage {
		right: var(--rail-w);
	}

	/* 模式切换：三段，底下一块滑块跟着走 */
	.seg {
		position: relative;
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		margin-right: 6px;
		padding: 3px;
		border-radius: var(--radius-pill);
		box-shadow: inset 0 0 0 1px var(--glass-line);
	}
	.seg button {
		position: relative;
		z-index: 1;
		height: 28px;
		padding: 0 12px;
		border: 0;
		background: none;
		color: var(--on-glass-2);
		font-size: var(--fs-xs);
		cursor: pointer;
		transition: color var(--fast) var(--ease);
	}
	.seg button[aria-checked='true'] {
		color: var(--on-glass);
	}
	.seg button:focus-visible {
		outline: 2px solid var(--on-glass);
		outline-offset: -2px;
		border-radius: var(--radius-pill);
	}
	.seg-thumb {
		position: absolute;
		top: 3px;
		bottom: 3px;
		left: 3px;
		width: calc((100% - 6px) / 3);
		border-radius: var(--radius-pill);
		background: var(--glass-hover);
		box-shadow: inset 0 0 0 1px var(--glass-line);
		transform: translateX(calc(var(--i) * 100%));
		transition: transform var(--move) var(--ease);
	}

	/* 侧栏：这节课的时间轴 + 标记。工具条隐去时它只变淡，不消失 */
	.rail {
		position: absolute;
		top: 0;
		right: 0;
		bottom: 0;
		width: var(--rail-w);
		box-sizing: border-box;
		display: flex;
		flex-direction: column;
		gap: 28px;
		padding: 76px 22px 22px;
		border-left: 1px solid var(--glass-line);
		background: oklch(from var(--glass-ground) calc(l + 0.015) c h);
		font-size: var(--fs-s);
		transition: opacity var(--move) var(--ease);
		animation: rail-in var(--move) var(--ease) both;
	}
	@keyframes rail-in {
		from {
			opacity: 0;
			transform: translateX(16px);
		}
	}
	.idle .rail {
		opacity: 0.8;
	}
	.room {
		--rail-w: clamp(260px, 22vw, 340px);
	}
	.rail h2 {
		margin: 0 0 12px;
		font-size: var(--fs-xs);
		font-weight: 600;
		color: var(--on-glass-2);
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		margin: 0 0 16px;
	}
	.stats dt {
		font-size: var(--fs-xs);
		color: var(--on-glass-3);
	}
	.stats dd {
		margin: 3px 0 0;
		font-size: var(--fs-l);
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	.ticks {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 3px;
		min-height: 26px;
	}
	.tick {
		width: 6px;
		height: calc(6px + var(--len) * 20px);
		padding: 0;
		border: 0;
		border-radius: var(--radius-pill);
		background: var(--on-glass-3);
		opacity: 0.45;
		cursor: pointer;
		transition:
			opacity var(--fast) var(--ease),
			transform var(--fast) var(--ease);
	}
	.tick:hover {
		opacity: 1;
		transform: scaleY(1.15);
	}
	.tick.on {
		background: var(--on-glass);
		opacity: 1;
	}
	.marks {
		flex: 1;
		min-height: 0;
		display: flex;
		flex-direction: column;
	}
	.hint {
		margin: 0;
		line-height: 1.7;
		color: var(--on-glass-3);
	}
	.marks ol {
		margin: 0 -8px;
		padding: 0;
		list-style: none;
		overflow-y: auto;
		scrollbar-width: thin;
	}
	.marks li {
		position: relative;
		display: flex;
		align-items: flex-start;
		animation: rail-in var(--move) var(--ease) both;
	}
	.mark {
		flex: 1;
		min-width: 0;
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 10px;
		padding: 8px;
		border: 0;
		border-radius: var(--radius-s);
		background: none;
		text-align: left;
		cursor: pointer;
		transition: background-color var(--fast) var(--ease);
	}
	.mark:hover {
		background: var(--glass-hover);
	}
	.mark .t {
		color: var(--on-glass-3);
		font-variant-numeric: tabular-nums;
	}
	.mark .txt {
		line-height: 1.55;
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.x {
		flex: none;
		width: 28px;
		height: 28px;
		margin-top: 4px;
		display: grid;
		place-items: center;
		padding: 0;
		border: 0;
		border-radius: var(--radius-s);
		background: none;
		color: var(--on-glass-3);
		opacity: 0;
		cursor: pointer;
		transition: opacity var(--fast) var(--ease);
	}
	.marks li:hover .x,
	.x:focus-visible {
		opacity: 1;
	}
	.x:hover {
		background: var(--glass-hover);
		color: var(--on-glass);
	}
	kbd {
		display: inline-block;
		min-width: 1.6em;
		padding: 1px 5px;
		border-radius: var(--radius-xs);
		box-shadow: inset 0 0 0 1px var(--glass-line);
		font: 500 var(--fs-xs) / 1.5 var(--cl-font);
		text-align: center;
		color: var(--on-glass-2);
	}
	.keys {
		display: flex;
		flex-wrap: wrap;
		gap: 8px 14px;
		font-size: var(--fs-xs);
		color: var(--on-glass-3);
	}
	.keys kbd + kbd {
		margin-left: 3px;
	}

	/* 字幕已经在出、状态出了岔子：不清屏，只在字幕上方挂一条 */
	.banner {
		position: absolute;
		left: 50%;
		top: 76px;
		z-index: 1;
		transform: translateX(-50%);
		margin: 0;
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 7px 16px 7px 12px;
		border-radius: var(--radius-pill);
		background: var(--glass-raise);
		box-shadow: inset 0 0 0 1px var(--glass-line);
		color: var(--on-glass);
		font-size: var(--fs-s);
		font-weight: 600;
		max-width: min(90vw, 640px);
	}
	/* 提醒不用颜色：空心圆点 + 字重 */
	.banner::before {
		content: '';
		flex: none;
		width: 7px;
		height: 7px;
		border-radius: 50%;
		box-shadow: inset 0 0 0 1.5px currentColor;
	}
	.saved {
		margin: 10px 0 0;
		font-size: var(--fs-xs);
		color: var(--on-glass-3);
	}
	.empty {
		height: 100%;
		display: flex;
		flex-direction: column;
		justify-content: flex-end;
		gap: 10px;
		padding-bottom: 8px;
	}
	.big {
		margin: 0;
		font-size: var(--z);
		font-weight: 500;
		line-height: 1.35;
	}
	.small {
		margin: 0;
		max-width: 36em;
		font-size: calc(var(--z) * 0.58);
		line-height: 1.6;
		color: var(--on-glass-2);
	}
	.soft {
		align-self: flex-start;
		margin-top: 8px;
		height: 38px;
		padding: 0 16px;
		border: 1px solid var(--glass-line);
		border-radius: var(--radius-s);
		background: var(--glass-hover);
		font-size: var(--fs-s);
		cursor: pointer;
	}
	.more-btn {
		display: none;
	}

	@media (max-width: 1100px) {
		.tool.wide span {
			display: none;
		}
	}
	@media (max-width: 860px) {
		.with-rail .stage {
			right: 0;
		}
		.rail,
		.rail-btn {
			display: none;
		}
		.seg button {
			padding: 0 9px;
		}
	}
	/* 窄窗口：一行放得下状态 / 小窗 / 下课；其余点「更多」才出来，出来时工具条换行 */
	@media (max-width: 720px) {
		.bar {
			padding-bottom: 18px;
		}
		.state {
			flex-basis: 0;
		}
		.course,
		.clock {
			display: none;
		}
		.opt {
			display: none;
		}
		.rail-btn {
			display: none !important;
		}
		.more-open .opt:not(.rail-btn) {
			display: flex;
		}
		.more-btn {
			display: inline-flex;
		}
		.more-open .tools {
			flex-wrap: wrap;
			justify-content: flex-end;
		}
		.stage {
			padding-top: 64px;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.dot.on {
			animation: none;
		}
	}
</style>
