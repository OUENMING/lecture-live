<script lang="ts">
	// 功能介绍 + 存什么 / 存多久 + 网页版还是桌面版 + Mac 桌面版安装。
	// 数字都有出处：桌面版的来自仓库 README（3.10.0 实测），网页版对照来自 S2 穿刺（2026-11-17，同一段录音）。
	import { onMount } from 'svelte';
	import SiteHeader from '#lib/ui/SiteHeader.svelte';
	import Stream from '#lib/ui/Stream.svelte';
	import { demo, startDemo } from '#lib/demo/player.svelte.ts';

	onMount(() => {
		startDemo();
	});

	// 一行装好：脚本在 static/mac（没 uv 先装 uv → 拉仓库到 ~/lecture-live → 跑 install.sh）
	const install = 'curl -fsSL https://class.bldcam.page/mac | sh';

	let copied = $state('');
	async function copy(id: string, text: string) {
		try {
			await navigator.clipboard.writeText(text);
			copied = id;
			setTimeout(() => (copied = ''), 1600);
		} catch {
			/* 剪贴板被拒：用户自己选中复制 */
		}
	}
</script>

<svelte:head>
	<title>功能介绍 · ClassLive</title>
	<meta name="description" content="ClassLive 给英文课实时出中英双语字幕，认得课程术语，能开置顶小窗，下课自动整理笔记。网页版打开就能用，Mac 桌面版功能多一些。" />
</svelte:head>

<div class="page">
	<SiteHeader current="about" />

	<main>
		<section class="intro">
			<h1><span class="ph">一节课，</span><span class="ph">从头记到尾</span></h1>
			<p>
				老师讲的每一句，屏幕上都有中英两行字幕。下课时整节课已经存好，可以回看、下载；桌面版还会整理成笔记，重点、例题和作业截止日期都在里面。
			</p>
			<a class="cta" href="/">去开课页 →</a>
		</section>

		<section class="feature">
			<div class="text">
				<h2>字幕跟着老师走</h2>
				<p>
					上面小字是英文，下面大字是中文。最底下那句是老师正在讲的，字会一点点长出来，讲完就定下来，往上挪一行。
				</p>
			</div>
			<figure>
				<div class="live" aria-label="示例字幕">
					<Stream rows={demo.rows} draftEn={demo.draftEn} draftZh={demo.draftZh} />
				</div>
				<figcaption>MIT 9.13 The Human Brain · MIT OCW · CC BY-NC-SA 4.0</figcaption>
			</figure>
		</section>

		<section class="feature flip">
			<div class="text">
				<h2>认得这门课的术语</h2>
				<p>
					把课件传上来，程序会从里面挑出这门课的术语。识别和翻译都会参考这份术语表，也会带上前面几句，像
					<code>nation to</code> 这种听错的，大多能改回 <code>relation to</code>。
				</p>
			</div>
			<figure>
				<img src="/img/panel.webp" alt="开课面板：一门课一张卡，课件拖进来自动分课" width="1200" height="1154" loading="lazy" />
				<figcaption>桌面版截图：课件拖进来自动分到各门课。</figcaption>
			</figure>
		</section>

		<section class="pair">
			<div>
				<h2>浮在课件上面</h2>
				<p>
					点「置顶小窗」，字幕会单独开一个小窗口，盖在 PPT 放映、PDF 或别的程序上。窗口可以拖得很小，只剩一行中文也行。
				</p>
			</div>
			<div>
				<h2>刷新了也还在</h2>
				<p>
					每句定稿先存在这台电脑上，再同步到服务器。页面刷新了、网断了一会儿，之前的字幕都还在。
				</p>
			</div>
		</section>

		<section class="feature">
			<div class="text">
				<h2>下课就能看笔记</h2>
				<p>
					桌面版点「下课」以后，整节课的逐字稿会拿去整理成笔记，重点、例题、考试和截止日期都会列出来，笔记里的数字程序会再算一遍，算不对的例题就删掉。
				</p>
				<p>
					网页版现在下课能看整节双语字幕、标过「没听懂」的句子，可以下载 Markdown 或纯文本；自动整理笔记还在做。
				</p>
			</div>
			<figure>
				<img src="/img/notes.webp" alt="课后笔记：五分钟速览、串讲、数字核对" width="1200" height="918" loading="lazy" />
				<figcaption>桌面版截图。</figcaption>
			</figure>
		</section>

		<section class="numbers">
			<h2>网页版和桌面版差不多准、差不多快</h2>
			<p class="sub">同一段课堂录音，两个版本各跑了一遍（2026-11-17）：</p>
			<table>
				<thead>
					<tr><th scope="col"></th><th scope="col">网页版</th><th scope="col">桌面版</th></tr>
				</thead>
				<tbody>
					<tr><th scope="row">错字率（WER）</th><td>15.7%</td><td>16.2%</td></tr>
					<tr><th scope="row">说完到定稿（p90）</th><td>1.32 秒</td><td>1.31 秒</td></tr>
				</tbody>
			</table>
			<p class="note">错字率主要看录音质量，这段是在教室里远距离录的。桌面版 3.10.0 在一段公开课录音上测出来是 6.9%。</p>
		</section>

		<section class="privacy">
			<h2>存了什么，存多久</h2>
			<dl>
				<div>
					<dt>音频</dt>
					<dd>不保存。实时发给 Soniox 转写，转完不留。</dd>
				</div>
				<div>
					<dt>这台电脑上</dt>
					<dd>逐字稿、术语表和标记存在浏览器里，30 天后自动清掉。</dd>
				</div>
				<div>
					<dt>服务器上</dt>
					<dd>逐字稿同步一份到作者的服务器，换了页面也找得回来，30 天后删掉。翻译时英文句子经服务器发给 DeepSeek，DeepSeek 那边不留。</dd>
				</div>
				<div>
					<dt>想马上删</dt>
					<dd>在「我的课」页点「删除这台电脑上的全部记录」，这台电脑上的马上清掉。</dd>
				</div>
			</dl>
		</section>

		<section class="which">
			<h2>网页版和桌面版</h2>
			<div class="cols">
				<div>
					<h3>网页版</h3>
					<p>打开网址就能用，什么都不用装。Mac 和 Windows 都行，用电脑上的 Chrome 或 Edge 打开。置顶小窗可以盖在 PPT 放映上面。</p>
				</div>
				<div>
					<h3>Mac 桌面版<span class="rec">推荐</span></h3>
					<p>字幕窗能盖在任何全屏程序上面，全屏的 Zoom 也行。另外还有「讲一下」、实时总结、课前回顾，还能从课上认出作业截止日期，一键记进提醒事项。要用 Apple Silicon（M1 或更新）的 Mac。</p>
				</div>
			</div>
		</section>

		<section class="mac" id="mac">
			<h2>安装 Mac 桌面版<span class="rec">推荐</span></h2>
			<p class="sub">要 macOS 13 或更新，芯片是 Apple Silicon（M1 或更新）。字幕能盖在全屏 Zoom 上，功能也比网页版多。前后大概 5 分钟，多数时间在等它自己装。</p>
			<ol>
				<li>
					<h3>粘一行命令</h3>
					<p>打开「终端」，把这一行粘进去，回车。它会自己装好放进「应用程序」，Python 和依赖都带着。如果弹窗要装「开发者工具」，点安装，装完再粘一次。</p>
					<div class="code">
						<pre><code>{install}</code></pre>
						<button type="button" onclick={() => copy('install', install)}>{copied === 'install' ? '已复制' : '复制'}</button>
					</div>
				</li>
				<li>
					<h3>打开，填通行码</h3>
					<p>
						在「应用程序」里双击 <strong>ClassLive</strong>，允许它用麦克风。在「翻译引擎」那一栏填上通行码，再在某门课上点「开始上课」。以后出新版会自动更新。
					</p>
				</li>
			</ol>
			<p class="more">
				命令行用法、线上课收系统声、全部参数：<a href="https://github.com/OUENMING/lecture-live/blob/main/docs/GUIDE.md" rel="noopener">详细手册</a>
				· 源码：<a href="https://github.com/OUENMING/lecture-live" rel="noopener">GitHub</a>（MIT 开源）
			</p>
		</section>

		<section class="close">
			<p>准备好了就去开课页。</p>
			<a class="cta solid" href="/">开始上课</a>
		</section>
	</main>
</div>

<style>
	.page {
		min-height: 100dvh;
		background: var(--paper);
		color: var(--ink);
		font-family: var(--cl-font);
	}
	main {
		max-width: 1120px;
		margin: 0 auto;
		padding: 0 clamp(16px, 4vw, 56px) 96px;
	}
	/* 标题只在逗号处折行，不把一个字甩到下一行 */
	h1 .ph {
		display: inline-block;
	}
	h1 {
		margin: 0;
		font-family: var(--cl-serif);
		font-size: var(--fs-display);
		line-height: var(--lh-tight);
		font-weight: 600;
		letter-spacing: -0.02em;
		text-wrap: balance;
	}
	h2 {
		margin: 0 0 14px;
		font-family: var(--cl-serif);
		font-size: var(--fs-xl);
		line-height: 1.25;
		font-weight: 600;
		letter-spacing: -0.01em;
		text-wrap: balance;
	}
	h3 {
		margin: 0 0 8px;
		font-size: var(--fs-l);
		font-weight: 600;
	}
	p,
	li,
	dd {
		font-size: var(--fs-m);
		line-height: var(--lh-body);
		color: var(--ink-2);
		text-wrap: pretty;
	}
	p {
		margin: 0;
	}
	code {
		font-family: var(--cl-mono);
		font-size: 0.88em;
		padding: 1px 5px;
		border-radius: var(--radius-xs);
		background: var(--paper-2);
		color: var(--ink);
	}
	a {
		color: var(--ink);
	}

	.intro {
		padding: clamp(40px, 9vh, 112px) 0 clamp(56px, 10vh, 120px);
		max-width: 44rem;
	}
	.intro p {
		margin-top: 22px;
		font-size: var(--fs-l);
	}
	.cta {
		display: inline-block;
		margin-top: 28px;
		font-weight: 600;
		font-size: var(--fs-m);
		text-decoration: none;
		border-bottom: 1px solid currentColor;
		padding-bottom: 2px;
	}
	.cta.solid {
		margin-top: 0;
		padding: 15px 28px;
		border: 0;
		border-radius: var(--radius-s);
		background: var(--ink);
		color: var(--paper);
		transition: background-color var(--fast) var(--ease);
	}
	.cta.solid:hover {
		background: var(--ink-press);
	}

	.feature {
		display: grid;
		grid-template-columns: 5fr 7fr;
		gap: clamp(28px, 5vw, 72px);
		align-items: center;
		padding: clamp(40px, 7vh, 80px) 0;
	}
	.feature.flip .text {
		order: 2;
	}
	.text {
		max-width: 28rem;
	}
	figure {
		margin: 0;
	}
	figure img {
		display: block;
		width: 100%;
		height: auto;
		border-radius: var(--radius);
		box-shadow: 0 30px 60px -36px oklch(0.2 0.02 60 / 0.5);
		/* 截图是桌面版旧配色；这页不用彩色，压成灰阶 */
		filter: grayscale(1) contrast(1.04);
	}
	.flip figure img {
		max-width: 520px;
		margin-left: auto;
	}
	.live {
		height: 300px;
		padding: 20px 26px 22px;
		border-radius: var(--radius);
		background: var(--glass-ground);
		color: var(--on-glass);
		box-shadow: var(--glass-shadow);
		--cap-scale: 1.1;
	}
	figcaption {
		margin-top: 12px;
		font-size: var(--fs-s);
		color: var(--ink-3);
	}

	.pair {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: clamp(28px, 5vw, 72px);
		padding: clamp(48px, 8vh, 96px) 0;
		border-top: 1px solid var(--rule);
		border-bottom: 1px solid var(--rule);
		margin: clamp(24px, 4vh, 48px) 0;
	}

	.numbers,
	.privacy,
	.which,
	.mac {
		padding: clamp(48px, 8vh, 96px) 0 0;
	}
	.sub {
		max-width: 40rem;
	}
	table {
		margin-top: 28px;
		border-collapse: collapse;
		min-width: min(100%, 520px);
		font-variant-numeric: tabular-nums;
	}
	th,
	td {
		padding: 14px 28px 14px 0;
		text-align: left;
		border-bottom: 1px solid var(--rule);
	}
	thead th {
		font-size: var(--fs-s);
		font-weight: 500;
		color: var(--ink-3);
	}
	tbody th {
		font-weight: 400;
		color: var(--ink-2);
	}
	td {
		font-size: var(--fs-l);
		font-weight: 600;
		letter-spacing: -0.01em;
	}
	.note {
		margin-top: 16px;
		max-width: 40rem;
		font-size: var(--fs-s);
		color: var(--ink-3);
	}

	dl {
		margin: 24px 0 0;
		display: grid;
		gap: 0;
		max-width: 46rem;
	}
	dl > div {
		display: grid;
		grid-template-columns: 11rem 1fr;
		gap: 24px;
		padding: 18px 0;
		border-bottom: 1px solid var(--rule);
	}
	dt {
		font-weight: 600;
		font-size: var(--fs-m);
		padding-top: 2px;
	}
	dd {
		margin: 0;
	}

	.cols {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: clamp(20px, 3vw, 40px);
		margin-top: 24px;
	}
	.cols > div {
		padding-top: 20px;
		border-top: 1px solid var(--ink);
	}
	.cols p {
		margin: 0;
		line-height: 1.75;
		color: var(--ink-2);
	}

	.mac ol {
		list-style: none;
		counter-reset: step;
		margin: 32px 0 0;
		padding: 0;
		display: grid;
		gap: 32px;
		max-width: 46rem;
	}
	.mac li {
		counter-increment: step;
		position: relative;
		padding-left: 52px;
	}
	.mac li::before {
		content: counter(step);
		position: absolute;
		left: 0;
		top: -2px;
		width: 30px;
		height: 30px;
		display: grid;
		place-items: center;
		border-radius: 50%;
		border: 1px solid var(--ink);
		font-size: var(--fs-s);
		font-weight: 600;
		color: var(--ink);
		font-variant-numeric: tabular-nums;
	}
	.mac li strong {
		color: var(--ink);
	}
	.code {
		position: relative;
		margin-top: 12px;
		border-radius: var(--radius-s);
		background: var(--glass-ground);
	}
	pre {
		margin: 0;
		padding: 16px 84px 16px 18px;
		overflow-x: auto;
	}
	pre code {
		padding: 0;
		background: none;
		color: var(--on-glass);
		font-size: var(--fs-s);
		line-height: 1.7;
	}
	.code button {
		position: absolute;
		top: 10px;
		right: 10px;
		height: 30px;
		padding: 0 12px;
		border: 0;
		border-radius: var(--radius-xs);
		background: var(--glass-raise);
		color: var(--on-glass);
		font: 500 var(--fs-s) / 1 var(--cl-font);
		cursor: pointer;
		transition: background-color var(--fast) var(--ease);
	}
	.code button:hover {
		background: oklch(1 0 0 / 0.2);
	}
	.code button:focus-visible {
		outline: 2px solid var(--on-glass);
		outline-offset: 2px;
	}
	/* 「推荐」角标：墨色实底小字，和页面黑白基调一致 */
	.rec {
		display: inline-block;
		margin-left: 10px;
		padding: 2px 8px;
		border-radius: 999px;
		background: var(--ink);
		color: var(--paper);
		font: 500 var(--fs-xs) / 1.5 var(--cl-font);
		letter-spacing: 0.02em;
		vertical-align: middle;
		position: relative;
		top: -2px;
	}
	.more {
		margin-top: 32px;
		font-size: var(--fs-s);
	}

	.close {
		margin-top: clamp(64px, 12vh, 140px);
		padding-top: 48px;
		border-top: 1px solid var(--rule);
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 20px;
		flex-wrap: wrap;
	}
	.close p {
		font-family: var(--cl-serif);
		font-size: var(--fs-xl);
		font-weight: 600;
		color: var(--ink);
	}

	@media (max-width: 820px) {
		.feature,
		.pair,
		.cols {
			grid-template-columns: 1fr;
		}
		.feature.flip .text {
			order: 0;
		}
		.flip figure img {
			margin-left: 0;
		}
		dl > div {
			grid-template-columns: 1fr;
			gap: 4px;
		}
	}
</style>
