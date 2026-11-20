<script lang="ts">
	// 课后笔记的阅读框：服务端 note.html 放进 sandbox iframe（只给 allow-scripts + 弹出外链），
	// 高度跟内容走（iframe 里的桥接脚本回报），整页只有一条滚动条；页内锚点交给本页滚。
	// 换主题 = 重新生成 srcdoc（笔记的亮 / 暗是 data-theme 定的，同一张纸的颜色由 readTheme 从本页 token 读）。
	import { embedNote, isNoteMessage, readTheme } from '#lib/after/note-frame.ts';

	interface Props {
		html: string;
		dark: boolean;
		title?: string;
	}
	let { html, dark, title = '课后笔记' }: Props = $props();

	let frame = $state<HTMLIFrameElement>();
	let height = $state(640);
	let ready = $state(false);
	let doc = $state('');

	// 先挂上框、读到 token 再出 srcdoc（token 在 [data-surface] 上，框本身读得到继承值）
	$effect(() => {
		if (!frame) return;
		ready = false;
		doc = embedNote(html, readTheme(frame, dark));
	});

	$effect(() => {
		const onMsg = (e: MessageEvent) => {
			// sandbox 里的笔记 origin 是 "null"，只认「消息来自这个框」（validate-origins：opaque origin 靠 source 判）
			if (!frame || e.source !== frame.contentWindow || !isNoteMessage(e.data)) return;
			if (e.data.cl === 'note-h') {
				height = Math.max(200, Math.min(e.data.h, 200_000));
				ready = true;
			} else {
				const top = frame.getBoundingClientRect().top + scrollY + e.data.y - 84; // 让开吸顶的「笔记 / 字幕」条
				scrollTo({ top, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
			}
		};
		addEventListener('message', onMsg);
		return () => removeEventListener('message', onMsg);
	});
</script>

<div class="note" class:ready style:--h={`${height}px`}>
	<iframe
		bind:this={frame}
		{title}
		srcdoc={doc}
		sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
		referrerpolicy="no-referrer"
		loading="eager"
	></iframe>
</div>

<style>
	.note {
		position: relative;
	}
	iframe {
		display: block;
		width: 100%;
		height: var(--h);
		border: 0;
		background: transparent;
		opacity: 0;
		transition: opacity var(--move) var(--ease);
	}
	.ready iframe {
		opacity: 1;
	}
</style>
