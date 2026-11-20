// 唯一的界面状态：主页面和置顶小窗（Document PiP）读同一个对象。
// 只有 core 推来的快照能改它；组件只读。
import { EMPTY_VIEWSTATE, type ViewState } from './viewstate';

class ViewStore {
	#vs = $state.raw<ViewState>(EMPTY_VIEWSTATE);

	get current(): ViewState {
		return this.#vs;
	}

	/** core 每出一版快照调一次；rev 不前进的旧快照丢掉（E4：旧连接迟到的事件不许影响新会话）。 */
	apply(next: ViewState): boolean {
		if (next.rev <= this.#vs.rev && next.rev !== 0) return false;
		this.#vs = next;
		return true;
	}

	reset(): void {
		this.#vs = EMPTY_VIEWSTATE;
	}
}

export const view = new ViewStore();
