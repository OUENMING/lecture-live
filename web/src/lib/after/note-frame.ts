// lib/after/note-frame.ts —— 把服务端 note.html（桌面 note_html.py 同一份自包含页面）改成能嵌进课后页的样子。
// 对外：portableNote(html) → 可下载 / 可嵌入的原文；embedNote(html, theme) → iframe srcdoc。
// 为什么嵌 iframe 而不是 innerHTML：笔记自带脚本（分步展开 / 对答案 / 画图十字光标）和整页 CSS，
// 放进 sandbox（不给 allow-same-origin）既能跑这些交互，又碰不到本页的通行码和本机存储。
// 嵌入版做四件事：① KaTeX 改走钉版本的 CDN（原文是桌面笔记旁边的相对路径，网页上取不到；取不到时笔记自己退回 TeX 源码）；
// ② 颜色换成课后页的 token（同一张纸，不像贴了一张别人的页面）、去掉它自己的标题 / 主题钮 / 进度条（本页已有）；
// ③ 高度回报给父页（iframe 跟内容一样高，整页一条滚动条）；④ 页内锚点 / scrollIntoView 交给父页滚。

export interface NoteTheme {
	dark: boolean;
	paper: string;
	paper2: string;
	ink: string;
	ink2: string;
	ink3: string;
	rule: string;
	font: string;
}

/** 消息只认这几种，父页按 event.source 再核一次是不是自己的 iframe */
export type NoteMessage = { cl: 'note-h'; h: number } | { cl: 'note-y'; y: number };

const KATEX_LOCAL = /\.classlive-assets\/katex\/(\d+\.\d+\.\d+)\//g;

/** KaTeX 相对路径 → jsDelivr 钉版本（下载下来的那份离线时同样退回 TeX 源码，不白屏） */
export function portableNote(html: string): string {
	return html.replace(KATEX_LOCAL, 'https://cdn.jsdelivr.net/npm/katex@$1/dist/');
}

const css = (t: NoteTheme) => `
:root,:root[data-theme],:root[data-theme=dark],:root:not([data-theme=light]){
 --bg:${t.paper};--card:${t.paper2};--fg:${t.ink};--muted:${t.ink3};--line:${t.rule};--code:${t.paper2};--accent:${t.ink2};
 color-scheme:${t.dark ? 'dark' : 'light'}}
html,body{background:transparent!important}
body{font-family:${t.font};-webkit-font-smoothing:antialiased;overflow:hidden}
#prog,#topbar,.wrap>h1{display:none!important}
.wrap{max-width:none!important;margin:0!important;padding:4px 2px 24px!important}
.wrap>.meta:first-of-type{border-left:0;padding-left:0;margin-top:0}
a{color:inherit;text-underline-offset:3px}
::selection{background:${t.dark ? 'oklch(1 0 0 / 0.2)' : 'oklch(0.2 0.01 60 / 0.14)'}}
@media print{body{overflow:visible}}
`;

// 注意：这段跑在 sandbox 里（opaque origin），只能 postMessage，读不到父页任何东西
const bridge = `(function(){
var last=0,post=function(){var h=Math.ceil(document.documentElement.scrollHeight);if(h!==last){last=h;parent.postMessage({cl:'note-h',h:h},'*')}};
var go=function(el){parent.postMessage({cl:'note-y',y:el.getBoundingClientRect().top+scrollY},'*')};
try{new ResizeObserver(post).observe(document.body)}catch(e){setInterval(post,500)}
addEventListener('load',post);post();
Element.prototype.scrollIntoView=function(){go(this)};
document.addEventListener('click',function(e){var a=e.target&&e.target.closest&&e.target.closest('a[href]');if(!a)return;
var h=a.getAttribute('href')||'';
if(h.charAt(0)==='#'){var t=document.getElementById(decodeURIComponent(h.slice(1)));if(t){e.preventDefault();go(t)}}
else{a.target='_blank';a.rel='noopener noreferrer'}},true);
})();`;

/** 原文 → iframe srcdoc（主题定死在 data-theme 上，换主题就重新生成一份） */
export function embedNote(html: string, theme: NoteTheme): string {
	let out = portableNote(html);
	const htmlTag = /<html\b([^>]*)>/i;
	out = htmlTag.test(out)
		? out.replace(htmlTag, (_m, attrs: string) => `<html${attrs.replace(/\sdata-theme=("[^"]*"|'[^']*'|\S+)/i, '')} data-theme="${theme.dark ? 'dark' : 'light'}">`)
		: `<html data-theme="${theme.dark ? 'dark' : 'light'}">${out}</html>`;
	const style = `<style id="cl-embed">${css(theme)}</style>`;
	out = /<\/head>/i.test(out) ? out.replace(/<\/head>/i, `${style}</head>`) : style + out;
	const script = `<script>${bridge}</script>`;
	out = /<\/body>/i.test(out) ? out.replace(/<\/body>(?![\s\S]*<\/body>)/i, `${script}</body>`) : out + script;
	return out;
}

/** 从页面上读当前 token（课后页亮 / 暗两套都在 tokens.css，这里不抄一份） */
export function readTheme(el: HTMLElement, dark: boolean): NoteTheme {
	const cs = getComputedStyle(el);
	const v = (name: string, fb: string) => cs.getPropertyValue(name).trim() || fb;
	return {
		dark,
		paper: v('--paper', dark ? '#1a1816' : '#fbfaf7'),
		paper2: v('--paper-2', dark ? '#24211e' : '#f3f1ec'),
		ink: v('--ink', dark ? '#f2f0ec' : '#22201d'),
		ink2: v('--ink-2', dark ? '#cfccc6' : '#5a5650'),
		ink3: v('--ink-3', dark ? '#aeaaa3' : '#76726b'),
		rule: v('--rule', dark ? 'rgba(255,255,255,.1)' : 'rgba(0,0,0,.1)'),
		font: v('--cl-font', 'system-ui, sans-serif')
	};
}

export function isNoteMessage(d: unknown): d is NoteMessage {
	if (!d || typeof d !== 'object') return false;
	const m = d as Record<string, unknown>;
	return (m.cl === 'note-h' && typeof m.h === 'number') || (m.cl === 'note-y' && typeof m.y === 'number');
}
