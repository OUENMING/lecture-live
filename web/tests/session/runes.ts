// Node 里跑 `*.svelte.ts`（runes）的最小垫片：把 `$state` / `$state.raw` 映射到 Svelte 的
// client 运行时原语。`live.svelte.ts` 的读写都是普通属性访问，Svelte 编译后也是这两句，
// 所以垫出来的行为与组件里一致（只是不触发渲染，测试也不需要）。
//
// 为什么不用 svelte 编译器：tsx 不认识 runes；为一条测试把编译插件塞进 node --import 的代价
// 比这三行大得多，而且会把测试与编译器版本绑死。真编译由 `npm run check` 管。
import * as client from 'svelte/internal/client';

type StateFn = (<T>(v: T) => T) & { raw: <T>(v: T) => T };

const stateFn = (<T>(v: T) => client.state<T>(v)) as StateFn;
stateFn.raw = <T>(v: T) => client.state<T>(v);

(globalThis as unknown as { $state: StateFn }).$state = stateFn;
