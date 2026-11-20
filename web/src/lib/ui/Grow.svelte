<script lang="ts">
	// 正在长的那句话：已经显示过的部分不动，新冒出来的字从模糊里浮出来。
	// 只比较前后两次文本的公共前缀；识别回改（前缀变了）时，从改动处开始算新字。
	let { text }: { text: string } = $props();

	let prev = '';
	const parts = $derived.by(() => {
		let i = 0;
		while (i < prev.length && i < text.length && prev[i] === text[i]) i++;
		prev = text;
		return [text.slice(0, i), text.slice(i)] as const;
	});
</script>

{parts[0]}{#key text.length}<span class="new">{parts[1]}</span>{/key}

<style>
	.new {
		animation: grow var(--move) var(--ease) both;
	}
	@keyframes grow {
		from {
			opacity: 0;
			filter: blur(4px);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.new {
			animation: none;
		}
	}
</style>
