#!/usr/bin/env python3
"""复核「更新弹窗什么时候弹」—— 隔离在临时目录，**不碰真仓库**。

    ClassLive.app/Contents/MacOS/python tests/test_popup_schedule.py

为什么单独一个文件：这段判定链的每个分支都对应一个**实测出来的坏行为**
（降级弹空白卡、勾了永久失明、被 `.update-seen` 挡死），
而这些分支在真实仓库里很难复现 —— 必须用 fixture。

新节奏（作者 2026-09-25 定）：
    **不勾「本版本不再提示」→ 每次启动都弹**，直到勾了它。
    勾了 → 这个版本彻底不弹；出了新版本 → 重新开始弹。

逐条验：
  A 首次安装 -> 不弹
  B 更新过 -> 弹
  C **弹完之后再启动 -> 还弹**（这是新行为，旧实现只弹一次）
  D 勾了「本版本不再提示」-> 不弹
  E **勾过之后出了新版本 -> 又弹**
  F 降级 -> **不弹**（C 修复点）
  G 看点为空 -> **不弹**（D 修复点，防空白卡片）
  H skip 是旧版本 -> 仍弹
  I 环境变量关 -> 不弹
"""
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="cl_popup_"))
sys.path.insert(0, str(ROOT))
N = [0]
RESULTS = []


def load_main_isolated(where: pathlib.Path):
    shutil.copy(ROOT / "main.py", where / "main.py")
    spec = importlib.util.spec_from_file_location(f"m{where.name}", where / "main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def world(version, *, seen=None, skip=None):
    N[0] += 1
    d = TMP / f"w{N[0]}"
    d.mkdir(parents=True)
    (d / "VERSION").write_text(version + "\n", encoding="utf-8")
    # ⚠️ fixture 而不是真 CHANGELOG：真库里没有比 3.6.3 更新的版本，
    #    没法测"出了新版本" —— 而用不存在的版本号会让 todo 算空（我第一版就这么错的）。
    # ⚠️ fixture 而不是真 CHANGELOG：真库里没有比 3.6.3 更新的版本，
    #    没法测"出了新版本" —— 而用不存在的版本号会让 todo 算空（我第一版就这么错的）。
    # 3.6.5 **故意不给任何小节** -> 用来测"看点为空不弹"那条守卫。
    chunks = []
    for v in ("3.7.0", "3.6.9", "3.6.5", "3.6.4", "3.6.3", "3.6.2", "3.6.1", "3.6.0"):
        if v == "3.6.9":
            chunks.append("## [3.6.9] - 2026-01-01\n\n（这个版本故意没有任何小节）\n\n")
        else:
            chunks.append(f"## [{v}] - 2026-01-01\n\n### 更新看点\n\n- {v} 的看点\n\n")
    (d / "CHANGELOG.md").write_text("# Changelog\n\n" + "".join(chunks), encoding="utf-8")
    if seen is not None:
        (d / ".update-seen").write_text(seen, encoding="utf-8")
    if skip is not None:
        (d / ".update-skip").write_text(skip, encoding="utf-8")
    return d


def run(name, version, *, seen=None, skip=None, env_off=False, expect=True, second=False):
    """跑一次（或连跑两次）判定。second=True 时在第一次之后**再跑一次**验证"还弹"。"""
    d = world(version, seen=seen, skip=skip)
    if env_off:
        os.environ["CLASSLIVE_NO_UPDATE_CHECK"] = "1"
    try:
        m = load_main_isolated(d)
        r1 = m._whatsnew_payload()
        r2 = m._whatsnew_payload() if second else None
    finally:
        os.environ.pop("CLASSLIVE_NO_UPDATE_CHECK", None)
    p1 = r1 is not None
    ok = (p1 == expect)
    extra = ""
    if second:
        p2 = r2 is not None
        ok = ok and (p2 == expect)
        extra = f"  第二次={p2}"
    RESULTS.append((name, ok))
    seen_after = (d / ".update-seen").read_text().strip() if (d / ".update-seen").exists() else "(无)"
    print(f"  {'✅' if ok else '❌'} {name}")
    print(f"        VERSION={version} seen={seen!r} skip={skip!r}{' 环境关' if env_off else ''}"
          f"  ->  {'弹' if p1 else '不弹'}{extra}   期望 {'弹' if expect else '不弹'}")
    print(f"        跑完后 .update-seen={seen_after!r}")


print(f"隔离目录: {TMP}\n")
print("新节奏逐条验证")
run("A 首次安装（无 seen 无 skip）", "3.6.3", expect=False)
run("B 更新过（seen=旧版）", "3.6.3", seen="3.6.2", expect=True)
run("C 弹完之后**再启动** -> 还弹", "3.6.3", seen="3.6.2", expect=True, second=True)
run("D 勾了「本版本不再提示」（skip==VERSION）", "3.6.3", seen="3.6.2", skip="3.6.3", expect=False)
run("E 勾过之后**出了新版本** -> 又弹", "3.7.0", seen="3.6.3", skip="3.6.3", expect=True)
run("F 降级（seen 比 VERSION 新）-> 不弹", "3.6.2", seen="3.6.3", expect=False)
run("H skip 是旧版本 -> 仍弹", "3.7.0", seen="3.6.3", skip="3.6.0", expect=True)
run("I 环境变量关 -> 不弹", "3.6.3", seen="3.6.2", env_off=True, expect=False)

print("\nG：看点为空 -> 不弹（防空白卡片）")
# 3.6.5 在 fixture 里**故意没写「更新看点」也没有任何小节** -> summary 应为空
d = world("3.6.9", seen="3.6.5")
m = load_main_isolated(d)
summ = m._whatsnew_body(m._prev_version("3.6.9"), "3.6.9")
r = m._whatsnew_payload()
ok_g = (not (summ or "").strip()) and (r is None)
RESULTS.append(("G 看点为空时不弹", ok_g))
print(f"  _whatsnew_body(prev=3.6.5, cur=3.6.9) = {summ!r}")
print(f"  _whatsnew_payload() = {r!r}")
print(f"  {'✅' if ok_g else '❌'} 空看点被拦住，没有弹出空白卡片")

print("\n=== ocr review 发现的四个回归（2026-09-25）===")

# J 旧格式 .update-skip = "1" —— 老用户本地会残留，不能让 todo 退化成"全部版本"
d = world("3.6.5", seen="3.6.4", skip="1")
m = load_main_isolated(d)
summ = m._whatsnew_body(m._prev_version("3.6.5"), "3.6.5")
r = m._whatsnew_payload()
mm = dict(RESULTS)
ok_j = r is not None and "3.6.3" not in r["summary"] and "3.6.0" not in r["summary"]
RESULTS.append(("J 旧格式 skip='1' 不会报出全部历史", ok_j))
print(f"  {'✅' if ok_j else '❌'} J 旧格式 skip='1'")
print(f"        summary = {r['summary']!r}" if r else "        （不弹）")
print(f"        期望：只含 3.6.5，不含 3.6.3/3.6.0 这些无关旧版")

# K 跨版本合并：seen=3.6.0 跳到 3.6.5，必须报中间的
d = world("3.6.5", seen="3.6.0")
m = load_main_isolated(d)
r = m._whatsnew_payload()
s = r["summary"] if r else ""
ok_k = all(v in s for v in ("3.6.4", "3.6.3", "3.6.2", "3.6.1"))
RESULTS.append(("K 跨版本升级必须合并中间的版本", ok_k))
print(f"\n  {'✅' if ok_k else '❌'} K 跨版本合并（seen=3.6.0 -> cur=3.6.5）")
print(f"        summary = {s!r}")
print(f"        期望：含 3.6.1 / 3.6.2 / 3.6.3 / 3.6.4")

# L seen 不在 CHANGELOG 里 -> 只报当前版，不能退化成"全部"
d = world("3.6.5", seen="9.9.9")
m = load_main_isolated(d)
r = m._whatsnew_payload()
s = (r["summary"] if r else "")
ok_l = ("3.6.0" not in s) and ("3.6.1" not in s)
RESULTS.append(("L seen 不在库里时只报当前版", ok_l))
print(f"\n  {'✅' if ok_l else '❌'} L seen 不在库里（seen=9.9.9）")
print(f"        summary = {s!r}")

# M 全新 clone 的用户必须**迟早**能弹出来（首次不弹，但 seen 要种下去）
d = world("3.6.5")
m = load_main_isolated(d)
r1 = m._whatsnew_payload()          # 首次 -> 不弹
seeded = (d / ".update-seen").exists()
r2 = m._whatsnew_payload()          # 第二次 -> 应当弹（seen 已种下）
ok_m = (r1 is None) and seeded and (r2 is not None)
RESULTS.append(("M 首次安装种下 seen，第二次能弹", ok_m))
print(f"\n  {'✅' if ok_m else '❌'} M 全新 clone：首次不弹 -> 种 seen -> 第二次弹")
print(f"        首次={'弹' if r1 else '不弹'}  .update-seen 被种下={seeded}"
      f"  第二次={'弹' if r2 else '不弹'}")
print(f"        ⚠️ 不种的话：文件永远不存在 -> 每次启动都命中\"首次安装\" -> **永远不弹**")

bad = [n for n, ok in RESULTS if not ok]
print(f"\n{'=' * 60}\n{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
for n in bad:
    print(f"  ❌ {n}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if bad else 0)
