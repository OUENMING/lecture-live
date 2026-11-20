# loop —— runner 主循环（契约 §7）：每 5 s 领一条笔记任务，线程里跑，主线程心跳。
# 管什么：领任务 / 30 s 心跳（LeaseLost → 叫停 worker、不交回）/ 20 s 捎带 summary_job.tick
#   / SIGTERM 优雅退出。日志一行一事件到 stdout。
# 对外接口：`python -m server.runner.loop` 或 `python server/runner/loop.py`；测试可直调
#   `run_loop(client, *, home, log, stop, claim_every, beat_every, tick_every)`。
# ⚠️ HOME 必须在任何桌面 import 之前指到 RUNNER_HOME（`paths.STATE_ROOT` import 时算）。
from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from server.runner import client as client_mod          # noqa: E402
from server.runner.client import LeaseLost               # noqa: E402


def _log(s: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {s}", flush=True)


def _load_summary_job(log):
    """W24 的实时总结支：`import summary_job` 失败就整支跳过（契约 §7）。"""
    try:
        from server.runner import summary_job            # noqa: PLC0415
        return summary_job
    except Exception as e:                               # noqa: BLE001
        log(f"summary_job 不可用，实时总结整支跳过（{type(e).__name__}）")
        return None


def _work(client, job, home, log, lease_lost):
    """worker 线程体：run_note 自己交回；这里只兜「永不抛」之外的最后一层。"""
    try:
        if lease_lost.is_set():
            return
        from server.runner import note_job              # noqa: PLC0415
        note_job.run_note(client, job, home=home, log=log)
    except Exception as e:                              # noqa: BLE001
        log(f"run_note 异常（停手不交）：{type(e).__name__}: {str(e)[:160]}")


def _beat(client, job, lease_lost: threading.Event, log) -> None:
    try:
        until = client.heartbeat(job)
        log(f"心跳 {job.get('job_id')} → {until}")
    except LeaseLost as e:
        log(f"租约丢了（{e}）→ 叫停 worker、不交回")
        lease_lost.set()
        ev = (job or {}).get("_cancel")
        if ev is not None:
            ev.set()
    except Exception as e:                              # noqa: BLE001
        log(f"心跳失败（下个周期再试）：{type(e).__name__}: {str(e)[:120]}")


def _tick(summary, client, state: dict, log) -> None:
    try:
        summary.tick(client, state, time.time())
    except Exception as e:                              # noqa: BLE001
        log(f"summary tick 失败（跳过本轮）：{type(e).__name__}: {str(e)[:120]}")


def run_loop(client, *, home: Path, log=_log, stop: threading.Event | None = None,
             claim_every: float = 5.0, beat_every: float = 30.0,
             tick_every: float = 20.0) -> None:
    """主循环。home = 隔离桌面 HOME；stop 置位（SIGTERM）→ 等当前 worker 收尾后退出。"""
    stop = stop or threading.Event()
    summary = _load_summary_job(log)
    summary_state: dict = {}
    job: dict | None = None
    worker: threading.Thread | None = None
    lease_lost = threading.Event()
    last = {"claim": 0.0, "beat": 0.0, "tick": 0.0}
    ticker: threading.Thread | None = None

    while not stop.is_set():
        now = time.monotonic()
        # ---- 心跳（主线程）：worker 活着才发 ----
        if worker is not None and worker.is_alive() and now - last["beat"] >= beat_every:
            last["beat"] = now
            _beat(client, job, lease_lost, log)
        # ---- worker 收尾 ----
        if worker is not None and not worker.is_alive():
            worker.join()
            log(f"任务 {job.get('job_id')} 线程结束")
            worker = None
            job = None
            lease_lost.clear()
        # ---- 领任务（手上没活才领，最多 1 条）----
        if worker is None and now - last["claim"] >= claim_every:
            last["claim"] = now
            try:
                job = client.claim()
            except Exception as e:                      # noqa: BLE001
                log(f"claim 失败：{type(e).__name__}: {str(e)[:120]}")
                job = None
            if job:
                lease_lost.clear()
                # 私有键：心跳发现 LeaseLost 时从这里叫停 run_note（不进任何交回体）
                job["_cancel"] = threading.Event()
                worker = threading.Thread(
                    target=_work, args=(client, job, home, log, lease_lost),
                    daemon=True, name=f"note-{job.get('job_id')}")
                worker.start()
                last["beat"] = now          # 领到即记一拍，30 s 后才第一跳
                log(f"领到任务 {job.get('job_id')}（attempt {job.get('attempt')}，"
                    f"{(job.get('input') or {}).get('course')}）")
        # ---- 实时总结支（W24）：放自己的线程 —— tick 里的 HTTP 慢了（单请求最坏 ~90 s）也不拖住心跳 ----
        if now - last["tick"] >= tick_every and summary is not None \
                and (ticker is None or not ticker.is_alive()):
            last["tick"] = now
            ticker = threading.Thread(target=_tick, args=(summary, client, summary_state, log),
                                      daemon=True, name="summary-tick")
            ticker.start()
        stop.wait(0.25)
    if worker is not None and worker.is_alive():
        log("等待在跑的任务收尾（SIGTERM）…")
        # 等的时候照常心跳：笔记收尾常要 1–2 分钟，不跳租约 90 s 就被收回、交回只会吃 409
        last_beat = time.monotonic()
        while worker.is_alive():
            worker.join(0.25)
            if worker.is_alive() and time.monotonic() - last_beat >= beat_every:
                last_beat = time.monotonic()
                _beat(client, job, lease_lost, log)
    log("runner 退出")


def main() -> None:
    base = os.environ.get("RUNNER_BASE") or "https://asr.bldcam.page"
    token = os.environ.get("RUNNER_TOKEN") or ""
    home_s = (os.environ.get("RUNNER_HOME") or "").strip()
    if not token:
        raise SystemExit("缺 RUNNER_TOKEN（Worker secret，形如 rn_<32 hex>）")
    if not home_s:
        raise SystemExit("缺 RUNNER_HOME（隔离桌面 HOME，runner 通行码放里面）")
    home = Path(home_s).expanduser()
    # ⚠️ 必须在任何桌面 import（→ paths）之前：STATE_ROOT 是 import 时算的
    home = home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    os.environ.pop("OBSIDIAN_VAULT", None)
    log = _log
    log(f"runner 起：{base} · HOME={home} · name={os.environ.get('RUNNER_NAME') or '(hostname)'}")
    client = client_mod.RunnerClient(base, token)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):       # 非主线程 / 平台不支持：跳过
            pass
    try:
        run_loop(client, home=home, log=log, stop=stop)
    except KeyboardInterrupt:
        log("runner 退出")


if __name__ == "__main__":
    main()
