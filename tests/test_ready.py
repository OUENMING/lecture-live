#!/usr/bin/env python3
"""就绪层的判据 —— 「什么算缺」「什么不许自动下」。

    ClassLive.app/Contents/MacOS/python tests/test_ready.py

## 这个文件钉住了什么

1. ⭐⭐ **`unknown` ≠ `missing`** —— 老用户 / 手动装的模型没有戳，**它们能用**。
   判成缺失就是白烧 **1.2 GB** 流量重下一份。同族错误：`readiness_line` 那条
   「把读失败显示成 0，等于跟用户谎报」。
2. ⭐⭐ **Qwen3（本地翻译模型）绝不自动下** —— 它是可选的，由用户点才下。
3. ⭐ **`stale` 不自动换** —— 换版是用户的决定（`doctor.py` / `update.py` 两处都写着）。
4. ⭐ **麦克风 `unknown` 不拦人**（`notice.mic_permission` 那条：查不出来要按老行为继续）。
5. **翻译引擎那一项永远不把就绪条变成「没准备好」** —— 两边都不选也能上课。

⚠️ 全程不碰真实模型、不起窗口：`ready` 的外部输入都从参数进。
   只有第 6 组用 `tempfile` 造一个假的模型目录树。
⚠️ 每条都问过「把实现改坏它会不会红」—— 第 1、2 组**自带变异验证**。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import doctor                                                      # noqa: E402
import ready                                                       # noqa: E402

CASES: list[tuple[str, object]] = []
FAIL: list[str] = []


def case(name: str):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


REQ = [m for m in doctor.MODELS if m.required]        # 三个必下的
OPT = [m for m in doctor.MODELS if not m.required]    # Qwen3

_ALL_UNKNOWN = {m.path: "unknown" for m in doctor.MODELS}
_ALL_MISSING = {m.path: "missing" for m in doctor.MODELS}
_ALL_OK = {m.path: "ok" for m in doctor.MODELS}


@case("⭐⭐ 老用户（模型在、没戳）→ 一件都不算缺")
def t_unknown_is_not_missing():
    got = ready.required_left(_ALL_UNKNOWN)
    assert got == [], (
        f"`unknown` 被当成缺了 —— 会重下 {sum(m.mb for m in got):.0f} MB。"
        f"老用户的模型**能用**，没戳只说明「不是我们装的」")


@case("⭐⭐ 变异验证：把 unknown 并进 missing → 上面那条必须红")
def t_unknown_mutation():
    orig = ready.required_left

    def mutant(states):
        # 变异：只要不是 ok 就算缺（这是最容易写出的那一版）
        return [m for m in doctor.MODELS
                if m.required and states.get(m.path, "missing") != "ok"]
    ready.required_left = mutant
    try:
        got = mutant(_ALL_UNKNOWN)
    finally:
        ready.required_left = orig
    assert len(got) == len(REQ), (
        f"变异没被抓住（得到 {len(got)} 件）—— 上面那条判据没有区分能力")


@case("⭐⭐ Qwen3 绝不出现在「还差」里（它是可选的）")
def t_qwen_never_in_left():
    for states in (_ALL_MISSING, _ALL_UNKNOWN, _ALL_OK):
        left = [m.path for m in ready.required_left(states)]
        assert OPT[0].path not in left, (
            "Qwen3 被算进必下了 —— 它是**可选**的，由用户点才下")
    # 逐字断言：整条就绪条的输出里都不许出现它的路径
    for states in (_ALL_MISSING, _ALL_OK):
        txt = repr(ready.items(perm="authorized", states=states, has_key=False))
        assert OPT[0].path not in txt, "就绪条里出现了 Qwen3 的路径"


@case("⭐⭐ 没配 key 时只报状态，**不触发任何下载动作**")
def t_no_key_no_download():
    it = ready.engine_item(has_key=False, local_state="missing")
    assert it["state"] != ready.TODO, (
        "没配 key 被标成 todo —— 两边都不选**也能上课**（回退本地），"
        "标成 todo 会让整条就绪条看起来没准备好")
    assert it["offer_local"] is True, "该给一个「下载本地模型」的入口"
    # ⚠️ 返回值里**不许**有任何"已经在下"的语义 —— 下载只能由点击触发
    assert "busy" not in str(it), f"engine_item 不该自己开始下：{it}"


@case("配了 key → 不再劝他下本地模型（但也不假装没有）")
def t_has_key_hides_offer():
    assert ready.engine_item(has_key=True, local_state="ok")["offer_local"] is False
    assert ready.engine_item(has_key=True,
                             local_state="missing")["offer_local"] is True


@case("⭐ 麦克风 unknown 不拦人（就绪条不因此变成红的）")
def t_mic_unknown():
    it = ready.mic_item("unknown")
    assert it["state"] == ready.UNKNOWN, f"该是 unknown，得到 {it['state']}"
    assert it["state"] != ready.WARN, (
        "把「查不出来」画成警告 = 让用户去修一个不存在的问题")


@case("麦克风被拒 → 警告态 + 有话说")
def t_mic_denied():
    it = ready.mic_item("denied")
    assert it["state"] == ready.WARN and "系统设置" in it["detail"]


@case("⭐ 状态**只由 `m.path` 决定** —— 别的目录非空救不了它")
def t_canonical_path_only():
    """⚠️ 第一版这条是**空的**：它只断言了「夹具的路径不是规范路径」，
    根本没碰实现。改成真的能区分的形式 —— 见下面那次变异验证。

    真正的风险是：有人把 `model_state` 写成「扫 `~/models/*` 看有没有像模型的目录」。
    那样一个不相干的目录就会把它救活，而规范路径是空的。
    """
    with tempfile.TemporaryDirectory() as d:
        canon = pathlib.Path(d) / "canon"
        canon.mkdir()
        (canon / "m.onnx").write_bytes(b"x" * 16)
        other = pathlib.Path(d) / "other-looks-like-a-model"
        other.mkdir()
        (other / "m.onnx").write_bytes(b"y" * 16)
        m = REQ[0]._replace(path=str(canon))

        assert doctor.model_state(m) == "unknown", (
            f"规范路径里有文件、没戳 → 该是 unknown，得到 {doctor.model_state(m)}")

        # ⭐ 把规范路径清空：`other` 还满是"模型文件"，但状态**必须**变 missing
        for f in canon.iterdir():
            f.unlink()
        assert doctor.model_state(m) == "missing", (
            f"规范路径空了却还是 {doctor.model_state(m)} —— "
            f"说明有人在扫别的目录（那是「一台机器两份」的入口）")


@case("⭐⭐ stale 只报状态，不自动换")
def t_stale_no_autoswap():
    st = dict(_ALL_OK)
    st[REQ[0].path] = "stale"
    left = ready.required_left(st)
    assert left == [], "stale 被算成缺了 —— 换版是**用户的决定**，不许自动换"
    it = ready.models_item(st)
    assert it["state"] == ready.WARN and "旧版" in it["detail"]
    assert it["left"] == [], "stale 不该进 left（那是「要下」的清单）"


@case("三件全 ok → 就绪；缺一件 → todo 且报出体积")
def t_models_item_states():
    assert ready.models_item(_ALL_OK)["state"] == ready.OK
    st = dict(_ALL_OK)
    st[REQ[0].path] = "missing"
    it = ready.models_item(st)
    assert it["state"] == ready.TODO
    assert "640 MB" in it["detail"], f"该报出体积，得到 {it['detail']!r}"


@case("体积说人话：≥1024 MB 换成 GB，且总数与 Model.mb 一致")
def t_size_text():
    assert ready._mb(640) == "640 MB"
    assert ready._mb(1179) == "1.2 GB"
    total = sum(m.mb for m in REQ)
    assert 1100 < total < 1250, (
        f"必下合计 {total:.0f} MB —— 与实测的约 1.18 GB 差太远，"
        f"要么模型换了要么 mb 填错了")


@case("⭐ `Model.size` 是从 `mb` 派生的，不是第二个定义点")
def t_size_is_derived():
    m = REQ[2]                                     # Whisper：带 extra 那句
    assert f"{m.mb:g}" in m.size, f"size 里该有 mb 那个数：{m.size!r}"
    assert "解开后" in m.size, "extra 那句该跟在后面"
    # ⚠️ 判据要指向**那个位置**：是 `Model` 上的 property，不是一个同名字段
    #    （字段的话就能和 mb 各自漂，而这里要的正是「不能漂」）
    assert isinstance(REQ[0].__class__.size, property), (
        "size 该是 property —— 是字段就说明「一句话 + 一个数」又变成两处定义了")


@case("⭐ `hf_cached` 对不存在的 repo 离线给 False（**不是** None）")
def t_hf_cached_absent():
    got = doctor.hf_cached("classlive-does-not-exist/nope-xyz")
    assert got is False, (
        f"该是 False，得到 {got!r} —— `None` 的倒向是 unknown（当成有），"
        f"那会让「没下过」显示成「可能有了」")
    assert doctor.hf_cached("classlive-does-not-exist/nope-xyz", must=("config.json",)) is False


@case("⭐⭐ 本机 Qwen3 缓存按 HF 的定义「不完整」，但**能用** → 必须说 True")
def t_hf_cached_usable():
    """⚠️ 2026-09-28 实测踩到的真缺陷，形状如下：

    `snapshot_download(local_files_only=True)` 对一份**完全可用**的 Qwen3 抛
    `IncompleteSnapshotError` —— 缺的是 `.gitattributes` 和 `README.md`
    **两个文档文件**，而 `model.safetensors`（938 MB）、`config.json`、tokenizer
    **全都在**。原因：`mlx_lm.load()` 只下它要用的那几个。

    拿「整个 repo 全不全」当判据，就会给一个能用的模型报「缓存可能不完整」——
    正是本仓库最忌讳的那种**谎报**。判据问的必须是「**跑得起来的文件在不在**」。
    """
    cache = (pathlib.Path.home() / ".cache/huggingface/hub"
             / "models--mlx-community--Qwen3-1.7B-4bit")
    if not cache.is_dir():
        # ⚠️ **用 `_Skip`，不要 `return`**（2026-09-28 审查指出）：运行器把
        #    「正常返回」打成 ✅，于是没有这个缓存的干净机器/CI 上，这条**关键回归**
        #    （「可用但 HF 判为不完整 → 必须 True」）会被计成"通过"，其实一次都没跑。
        raise _Skip("这台机器没下过 Qwen3 的 HF 缓存")
    assert OPT[0].at_hf, "（夹具自检：Qwen3 那条该标了 at_hf）"
    got = doctor.hf_cached(OPT[0].src)
    assert got is True, (
        f"得到 {got!r} —— 把能用的模型报成不可用。缺的只是 .gitattributes/README.md "
        f"两个文档文件；权重与 config 都在")


class _Skip(Exception):
    """这台机器**没有这个前提**（例如没下过那个 HF 缓存）—— 不等于通过。"""


SKIP: list[tuple[str, str]] = []


def main_() -> int:
    print("=" * 60)
    for name, fn in CASES:
        try:
            fn()
        except _Skip as e:
            # ⚠️ 「没跑过」必须和「跑过且通过」分开（2026-09-28 审查指出）：
            #    原来靠 `return` 跳过，而下面那个 `else` 会把它打成 ✅。
            print(f"  ⚪ {name}\n      （跳过：{e}）")
            SKIP.append((name, str(e)))
        except AssertionError as e:
            print(f"  ❌ {name}\n      {str(e)[:220]}")
            FAIL.append(name)
        except Exception as e:                                 # noqa: BLE001
            print(f"  ❌ {name}\n      {type(e).__name__}: {str(e)[:200]}")
            FAIL.append(name)
        else:
            print(f"  ✅ {name}")
    print("=" * 60)
    print(f"{len(CASES) - len(FAIL)}/{len(CASES)} 通过")
    if SKIP:
        print(f"⚠️ 另有 {len(SKIP)} 条**跳过**（前提不在本机，没跑过）：")
        for _n, _w in SKIP:
            print(f"  ⚪ {_n} —— {_w}")
    for n in FAIL:
        print(f"  ❌ {n}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
