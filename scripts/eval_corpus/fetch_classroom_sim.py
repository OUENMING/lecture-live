#!/usr/bin/env python3
"""把「模拟真实课堂」语料要用的**原始素材**下到 `~/.classlive/eval/classroom_sim/`。

为什么要有这一步（而不是把素材塞进仓库）：
  · 体积 —— 光 RIR 库就 1.3 GB，OCW 视频几百 MB；
  · 许可 —— BBC 音效库是「个人/教育/研究用途」，OCW 字幕是 CC BY-NC-SA，
    和本仓库的 MIT 许可不能混放（与 `~/.classlive/eval/ocw-14.01-lec11/` 同一条理由）。
所以仓库里只放**怎么重建**：本脚本 + `make_classroom_sim.py`。

素材三类（各自的用途写在 `docs/experiments/classroom_sim_20261011/README.md`）：
  1. `sources/`  —— 真课视频 + 人工字幕（大阶梯教室 / 小班·暑期学校），提供**语音**与**真值**；
  2. `rirs_noises/` —— OpenSLR SLR28（Apache-2.0）：实测/仿真 RIR + MUSAN babble
     等**点源噪声**，这是 CHiME / REVERB 那套做法的标准件；
  3. `sfx/` —— BBC Sound Effects（RemArc）里挑出来的课堂/人群音效，提供**短时高噪事件**。

用法：
    ClassLive.app/Contents/MacOS/python scripts/eval_corpus/fetch_classroom_sim.py [--check] [--only sources|sfx|rir]
`--check` 只列现状（哪些在、哪些缺、多大），不下载 —— 语料被人删过之后先跑这个。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                   # 自己所在目录 → acoustics
sys.path.insert(0, str(HERE.parents[0]))        # scripts/ → fetch_model
sys.path.insert(0, str(HERE.parents[1]))        # 仓库根 → capture
import acoustics as A  # noqa: E402
from capture import load_file  # noqa: E402
from fetch_model import Source, download_file  # noqa: E402

BASE = pathlib.Path(os.path.expanduser("~/.classlive/eval/classroom_sim"))
IA = "https://archive.org/download"

# ---------- 1. 真课视频（含人工字幕，做 WER 真值）----------
# 选片理由（用 `acoustics.py` 量过，数字见实验 README）：
#   hall     = MIT 18.06 线性代数第 1 讲（Strang），26-100 那间**大阶梯教室**，观众在场、有掌声笑声；
#   seminar  = MIT RES.9-003 Brains, Minds and Machines 暑期学校第 1 讲，**小报告厅**，
#              观众提问多 —— 对应"小班 / workshop"那一档。
SOURCES = {
    "hall": {
        "item": "MIT18.06S05_MP4",
        "video": "01.mp4", "srt": "01.srt", "out": "MIT18.06_lec01",
    },
    "seminar": {
        "item": "MITRES.9-003SU15",
        "video": "MITRES9_003SU15_Lecture_1-1_300k.mp4",
        "srt": "MITRES9_003SU15_Lecture_1-1_300k.srt",
        "out": "BMM_lec1-1",
    },
}

# ---------- 2. RIR / 点源噪声（OpenSLR SLR28, Apache-2.0）----------
# 为什么不用自己造的 RIR：CHiME-3 之后的通行做法就是「干净语音 × **实测** RIR + 真实噪声」，
# 而 SLR28 正好把 RWCP / REVERB-2014 / AIR 的实测 RIR 与 MUSAN 的点源噪声
# 统一到 16 kHz —— 和生产采样率一致，卷积不需要重采样。
SLR28 = {
    "url": "https://openslr.elda.org/resources/28/rirs_noises.zip",
    "zip": "rirs_noises.zip",
    "sha_note": "openslr.org 官方件；镜像换过就重下，不用 -k 绕证书",
}

# ---------- 3. 课堂音效（BBC Sound Effects / RemArc）----------
# 每条都写了「拿它当什么用」。`bed_` = 铺底的连续环境音；`burst_` = 短时高噪事件；
# `tone_` = 电器/空调底噪。挑的偏"成人 + 室内 + 大厅"，因为大学课堂不是小学教室。
BBC_ITEM = "BBCSoundEffectsComplete"
SFX = {
    # —— 大阶梯教室：底噪 ——
    "bed_hall_murmur": ("Crowds_ Interior/Audience murmur (Recorded in Broadcasting House Concert Hall, London).07003059.flac", "bed"),
    "bed_hall_large_echo": ("Indoor Crowds/Large crowd murmuring in a large room with echo..07006135.flac", "bed"),
    "bed_hall_chatter30": ("Audience In Hall/Audience in hall, mixed chatter, fairly quiet. (30 people).07047085.flac", "bed"),
    "bed_hall_dining_us": ("American Chatter/Large mixed crowd in a dining hall with American chatter. (Recorded at a U.S. Air Force base in England.).07037244.flac", "bed"),
    "bed_hall_expectant": ("Schools _ Crowds/Large expectant crowd in hall, general chatter. (Live acoustic recording.).07050214.flac", "bed"),
    "bed_hall_700": ("700 People In Large Hall/700 people in a large hall. (Recorded in House of Lords during the opening of Parliament).07010060.flac", "bed"),
    "bed_hall_3people": ("3 People In Hall/Three people in a hall with speech and chatter, fairly quiet with no traffic..07010061.flac", "bed"),
    # —— 小班 / 研讨室：底噪 ——
    "bed_sem_chatter8": ("Crowds_ Interior/Crowd, interior, 8 men & women, loud excited chatter..07039097.flac", "bed"),
    "bed_sem_party": ("Crowds_ Interior/Crowd, interior, 8 men & women, party laughter & chatter..07039096.flac", "bed"),
    "bed_sem_lab": ("Children - Middle & Secondary Schools/Chatter in laboratory during free period (no distinct speech) - 1968 (68D, reprocessed).07072142.flac", "bed"),
    "bed_sem_classroom": ("Schools _ Crowds/Mixed Comprehensive School, classroom atmosphere, 5th Year Pupils chattering, chairs occasionally moved & door banged..07050200.flac", "bed"),
    "bed_sem_library": ("Library/Atmosphere_ Small public library, quiet, near issue desk.07025129.flac", "bed"),
    # —— 房间底噪（HVAC / 电器）——
    "tone_strip_light": ("Household Effects_ Refrigerator & Lighting Hums/A strip lighting hum..07002027.flac", "tone"),
    "tone_fridge": ("Household Effects_ Refrigerator & Lighting Hums/A refrigerator hum with switch on and off..07002031.flac", "tone"),
    # —— 短时高噪事件 ——
    "burst_applause250": ("Applause/Applause at concert, audience of 250 people..07047045.flac", "burst"),
    "burst_applause50": ("Applause/Applause at concert, audience of 50 people..07047043.flac", "burst"),
    "burst_laugh_crowd": ("Laughter/Laughter, medium sized crowd..07038280.flac", "burst"),
    "burst_laugh_hearty": ("Laughter/Hearty Laughter, 30 people..07038277.flac", "burst"),
    "burst_laugh_students": ("Crowd Reaction & Chatter/Hearty laughter, 20-30 years old. (Interior.).07018054.flac", "burst"),
    "burst_laugh6": ("Crowd Reaction & Chatter/Six bursts of laughter from group of 20-30 year-olds. (4 seconds between each burst).07018056.flac", "burst"),
    "burst_gasp36": ("Crowd Reaction & Chatter/Gasp, 36 people, 20-30 years old. (Interior.).07018046.flac", "burst"),
    "burst_hooray": ("Crowd Reaction & Chatter/_Hip hip hooray_, 50 people, 20-30 years old. (Interior.).07018050.flac", "burst"),
    "burst_dispersing": ("Crowd Reaction & Chatter/People dispersing after a meeting, some distant speech, 20-30 years old. (Interior.).07018057.flac", "burst"),
    "burst_boo": ("Crowd Reaction & Chatter/Jeering & booing, 50 people, 20-30 years old. (Interior.).07018048.flac", "burst"),
    "burst_slowclap": ("Crowd Reaction & Chatter/Slow hand-clap into fast rhythm, 20-30 year olds. (Interior.).07018053.flac", "burst"),
    "burst_cough_male": ("Comedy 3/One male coughing - 1980 (169B, reprocessed).07071005.flac", "burst"),
    "burst_sneeze_man": ("Coughing, Breathing, Etc/Man, sneezes seven times..07034128.flac", "burst"),
    "burst_whisper": ("Crowds_ Interior/Crowd, interior, 8 men & women, tense & hushed whispering..07039098.flac", "burst"),
    "burst_chair_squeak": ("Crash 2/Chairs squeaking in hall (89C).07054106.flac", "burst"),
    "burst_chairs_fall": ("Crash 2/Pile of chairs falling (84F).07054075.flac", "burst"),
    "burst_chairs_setup": ("Children - Middle & Secondary Schools/Chairs being set up in hall with some quiet periods and chatter (no distinct speech - wide perspective) - 1968 (68c, reprocessed).07072140.flac", "burst"),
    "burst_desk": ("Doors & Drawers/A roll-top desk opened and closed..07011072.flac", "burst"),
    "burst_door_scrape": ("Church Doors/Church Door_ Interior, unbolted, open and close with scrape..07027094.flac", "burst"),
    "burst_paper_rustle": ("Interiors - Concert Halls/Applause, at 0_14_ atmosphere, settling down, score rustles, at 0_25_ quartet tuning up, quiet - 1972 (1C10,reprocessed).07057014.flac", "burst"),
    "burst_classroom_scrape": ("Girls_ Secondary School/Girls_ Secondary School classroom, speech and clatter prior to lesson, ends with _shshsh_. (Occasional desk scraped on floor.).07009107.flac", "burst"),
    "burst_bell_lesson": ("Children - Middle & Secondary Schools/Bell rings, children leave classroom (rec. London, reprocessed) - 1980 (3C9, reprocessed).07072118.flac", "burst"),
    "burst_dining_crockery": ("School Dining Hall/School Dining Hall, busy atmosphere with some indistinct speech & sounds of crockery being used..07021060.flac", "burst"),
}


def _download(url: str, dst: pathlib.Path) -> None:
    """下一个文件。走 `fetch_model.download_file` —— 它已经有断点续传（`-C -`）、
    最低速率放弃、以及「返回的是一页 HTML（被网络拦了）」的守卫；自己写一遍 curl
    就是把这些再踩一遍（原来那份只有 `-fL --retry 3`）。
    非 GitHub 的直连源，`mirror=False` → 不做 sha 校验（这些站点本来也没给过校验值）。
    """
    if dst.exists() and dst.stat().st_size > 0:
        print(f"  已有 {dst.name}（{dst.stat().st_size / 1e6:.1f} MB），跳过")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"  下 {url}")
    ok, why = download_file([Source("直连", url, False)], dst, None)
    if not ok:
        raise RuntimeError(f"下载失败：{dst.name} —— {why}")


def _download_ranges(url: str, dst: pathlib.Path, workers: int = 8) -> None:
    """**分段并发**下一个大文件，再按顺序 `cat` 拼回去。

    为什么要这么下（实测）：openslr 单连接只给 ~100 KB/s，1.3 GB 要四十分钟；
    8 段并发合起来约 1.5 MB/s，同一份文件 ~15 分钟（那 313 MB 的尾段实测 3.5 分钟）。
    ⚠️ 分段下载**必须校验**：拼完要能正常解压（zip 每条有 CRC），
    否则会静默拼出一个坏包 —— 这也是这里不自己写续传、而只做"整段重下"的原因。
    """
    head = subprocess.run(["curl", "-sIL", "-m", "30", url], capture_output=True, text=True)
    size = next((int(l.split(":")[1]) for l in head.stdout.splitlines()
                 if l.lower().startswith("content-length")), 0)
    if not size:
        raise RuntimeError(f"拿不到文件大小：{url}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    parts = dst.parent / (dst.name + ".parts")
    parts.mkdir(exist_ok=True)
    step = -(-size // workers)

    def one(i: int) -> None:
        lo = i * step
        hi = min(size - 1, lo + step - 1)
        subprocess.run(["curl", "-fL", "--retry", "5", "--retry-delay", "2",
                        "-r", f"{lo}-{hi}", "-o", str(parts / f"p{i}"), url], check=True)

    print(f"  分段并发下 {size / 1e6:.0f} MB × {workers} 段…")
    # ⚠️ 先拼到 `.part`、过了大小校验再改名：直接写 `dst` 的话，中途失败（断网 / Ctrl-C /
    #    磁盘满）会留下一个**看着像下好了**的半截文件，而 `do_rir` 的守卫是
    #    `if not z.exists()` → 下次重跑直接跳过下载，永远卡在同一个坏包上。
    tmp = dst.with_name(dst.name + ".part")
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, range(workers)))
        import shutil
        with open(tmp, "wb") as out:
            for i in range(workers):
                # 流式拷贝：`read_bytes()` 会把整段（1.3 GB÷8 ≈ 163 MB）一次性读进内存，
                # 段数一调小峰值就跟着涨（workers=1 时就是整包）
                with open(parts / f"p{i}", "rb") as f:
                    shutil.copyfileobj(f, out, 1 << 20)
        got = tmp.stat().st_size
        if got != size:
            raise RuntimeError(f"拼接后大小不对：{got} ≠ {size}")
        os.replace(tmp, dst)          # 原子改名
    finally:
        for f in parts.glob("p*"):
            f.unlink(missing_ok=True)
        parts.rmdir()
        tmp.unlink(missing_ok=True)


def _decode_to_wav(src: pathlib.Path, dst: pathlib.Path) -> None:
    """解成 16 kHz 单声道 wav —— 与生产采样率一致（`capture.SR`）。
    解码用生产那层 `load_file`、写盘用 `acoustics.save_wav`，这里不再自带一份 ffmpeg 命令行。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    A.save_wav(str(dst), load_file(str(src)))


def do_sources(base: pathlib.Path) -> None:
    """两个 mp4 并发下（archive.org 单连接只有 ~250 KB/s，串行要十几分钟）。"""
    print("[sources] 真课视频 + 字幕")
    d = base / "sources"
    # ⚠️ 字段名与扩展名分开写：`SOURCES` 里的键是 `video`（不是 `mp4`），
    #    直接拿 `s["mp4"]` 会 KeyError —— 而且是在并发 map 里抛，整条建库流程全挂。
    jobs = [(f"{IA}/{s['item']}/{urllib.parse.quote(s[field])}", d / f"{s['out']}.{ext}")
            for s in SOURCES.values() for field, ext in (("video", "mp4"), ("srt", "srt"))]
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        list(ex.map(lambda j: _download(*j), jobs))


#: 从 SLR28 里直接拿来当"房间本底噪声"的实测录音（REVERB-2014 大房间的 isotropic noise）。
#: 和 BBC 那批"人群"叠在一起，才是完整的教室底噪：一群人 + 房间自己的声音。
RVB_NOISE = ["RVB2014_type1_noise_largeroom1_1.wav", "RVB2014_type1_noise_largeroom1_4.wav"]


def do_rir(base: pathlib.Path) -> None:
    """SLR28 一包两用：**实测 RIR**（AIR 数据库的 `lecture` / `meeting` 房间，
    附在 `real_rirs_isotropic_noises/` 里）+ **实测房间噪声**（REVERB-2014）。

    ⚠️ 下载与解压的守卫要盯**同一个东西**：原来只守 `unzip`，于是解压完删掉 zip 之后
    再跑一次（哪怕只是想补别的素材）都会重新拉 1.3 GB —— 纯白等。
    """
    print("[rir] OpenSLR SLR28（实测 RIR + 房间噪声 + MUSAN 点源噪声，1.3 GB）")
    d = base / "rirs_noises"
    if not (d / "RIRS_NOISES").exists():
        z = d / SLR28["zip"]
        if not z.exists():
            _download_ranges(SLR28["url"], z)
        print("  解压（zip 自带 CRC，解压不报错即分段拼接无误）…")
        # ⚠️ **只解要用的那一支**：整包解开是 3.78 GB / 61273 个文件，而本实验只读
        #    `real_rirs_isotropic_noises/` 下的 6 条 AIR RIR + 2 条 RVB 噪声 ——
        #    `simulated_rirs/`（2.1 GB）与 `pointsource_noises/`（651 MB）全仓零引用。
        #    要加素材时改这一行；zip 里每条有 CRC，少解不会让校验失效。
        subprocess.run(["unzip", "-q", "-o", str(z),
                        "RIRS_NOISES/real_rirs_isotropic_noises/*", "-d", str(d)], check=True)
        z.unlink(missing_ok=True)          # 解压完删掉：省 1.3 GB，重建时重下
    src = d / "RIRS_NOISES" / "real_rirs_isotropic_noises"
    for i, name in enumerate(RVB_NOISE, 1):
        dst = base / "sfx" / "tone" / f"tone_rvb2014_{i}.wav"
        if (src / name).exists() and not dst.exists():
            _decode_to_wav(src / name, dst)
            print(f"  → {dst.relative_to(base)}")


def do_sfx(base: pathlib.Path) -> None:
    """BBC 音效：**下 mp3、不下 flac** —— 同一个声音 flac 是 mp3 的 ~10 倍大
    （31.7 MB vs 2.8 MB），而这里只要它的**环境底噪与瞬时包络**；128 kbps 的编解码噪声
    在 −70 dBFS 上下，比铺底噪声低 30 dB 以上，测不出来。
    并发下（archive.org 单连接只给 ~100 KB/s，串行 37 条要一个多小时）。"""
    print("[sfx] BBC 课堂音效 → 16 kHz 单声道 wav")
    tmp = base / "sfx" / "_raw"

    def one(item: tuple[str, tuple[str, str]]) -> str:
        name, (rel, kind) = item
        dst = base / "sfx" / kind / f"{name}.wav"
        if dst.exists():
            return f"  跳过 {name}"
        raw = tmp / f"{name}.mp3"
        url = f"{IA}/{BBC_ITEM}/sounds/{urllib.parse.quote(rel[:-5] + '.mp3')}"
        try:
            _download(url, raw)
            _decode_to_wav(raw, dst)
            raw.unlink(missing_ok=True)
            return f"  → {dst.relative_to(base)}"
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            # ⚠️ 原来是 `except subprocess.CalledProcessError` —— 而 `_download` 与
            #    `capture.load_file` 抛的都是 `RuntimeError`，那条分支从来没生效过，
            #    任何一条音效失败都会直接掀掉整个 `do_sfx`。这里逐条报错、继续下其余的；
            #    真缺了哪条，`make_classroom_sim.require_sfx()` 会在合成前一次性报出来。
            return f"  ✗ {name}: {e}"

    with ThreadPoolExecutor(max_workers=8) as ex:
        for msg in ex.map(one, SFX.items()):
            print(msg, flush=True)



def check(base: pathlib.Path) -> None:
    """列现状。语料被人删过、或换机器重建时先跑这个。"""
    def sz(p: pathlib.Path) -> str:
        """体积与文件数**一遍走完**（原来两次 rglob + 每项三次 stat，6 万文件上是 12 万次 syscall）。"""
        if p.is_dir():
            n = k = 0
            for root, _dirs, files in os.walk(p):
                for name in files:
                    try:
                        n += os.stat(os.path.join(root, name)).st_size
                    except OSError:
                        continue
                    k += 1
            return f"{n / 1e6:8.1f} MB / {k:5d} 个文件"
        return f"{p.stat().st_size / 1e6:8.1f} MB" if p.exists() else "       缺"

    print(f"语料根目录 {base}")
    print(f"  sources/     {sz(base / 'sources')}")
    print(f"  rirs_noises/ {sz(base / 'rirs_noises')}")
    print(f"  sfx/         {sz(base / 'sfx')}")
    miss = [n for n, (_, k) in SFX.items() if not (base / "sfx" / k / f"{n}.wav").exists()]
    miss += [f"tone_rvb2014_{i}" for i in range(1, len(RVB_NOISE) + 1)
             if not (base / "sfx" / "tone" / f"tone_rvb2014_{i}.wav").exists()]
    for s in SOURCES.values():
        for ext in ("mp4", "srt"):
            if not (base / "sources" / f"{s['out']}.{ext}").exists():
                miss.append(f"{s['out']}.{ext}")
    print("  缺：" + ("（无）" if not miss else ", ".join(miss[:12]) + (" …" if len(miss) > 12 else "")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(BASE), help="语料根（默认 ~/.classlive/eval/classroom_sim）")
    ap.add_argument("--check", action="store_true", help="只列现状，不下载")
    ap.add_argument("--only", choices=["sources", "sfx", "rir"], help="只做其中一类")
    a = ap.parse_args()
    base = pathlib.Path(a.base).expanduser()
    if a.check:
        check(base)
        return 0
    # 三批素材**互不依赖** → 并发跑：首次建库的墙钟从「求和」变成「取最大」
    # （SLR28 那 1.3 GB 约 15 分钟、两段 OCW 视频约 7 分钟、BBC 那 37 条约 2 分钟）。
    steps = [(n, f) for n, f in (("rir", do_rir), ("sources", do_sources), ("sfx", do_sfx))
             if not a.only or a.only == n]
    with ThreadPoolExecutor(max_workers=len(steps)) as ex:
        list(ex.map(lambda nf: nf[1](base), steps))
    check(base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
