#!/usr/bin/env python3
"""填 API key 的判据 —— `keyentry` 的纯逻辑 + `notice.build_text_alert` 的几何。

    ClassLive.app/Contents/MacOS/python tests/test_keyentry.py

## 为什么这里能测「填 key」而不联网

`keyentry.save()` / `status_line()` / `has_any()` 全是**纯逻辑**（除了 `has_any`
默认会调 `load_api_key` —— 所以它**必须**能注入读端，见 `test_has_any_injected`）。
校验那两个真的会联网，本文件**不碰**。

## ⚠️ 本文件钉住的两个真实缺陷（都发生过）

1. **只补 Jev 时把已配好的 DeepSeek 抹掉** —— `save(jev=…)` 带默认空串，
   第一版会照着空串写 `credentials`。
2. ⚠️⚠️ **`NSAlert` 不调 `layout()` 就不装附件视图** —— 窗口只剩 260 宽、
   按钮竖排、输入框**根本不进视图树**，而 `accessoryView()` 照样返回那个对象
   （看起来"设过了"）。生产走 `runModal()` 看不出来，**探针不 runModal 才暴露**。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import keyentry as KE                                                # noqa: E402
import notice                                                        # noqa: E402


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    """⚠️ 每个用例各自 `with tempfile.TemporaryDirectory()` —— 它自己回收，
    **本函数刻意没有顶层 `finally`**（第一版在这里留了一行 `shutil.rmtree(...)`，
    长得像会删整个临时目录 —— 已删）。"""
    print("\n--- ① 保存后那句话：三档的唯一定义点 ---")
    # 改坏：把任一档的文案换掉 -> 对应那条红。
    check("两个都填", KE.status_line(True, True) == KE.MSG_BOTH, KE.MSG_BOTH)
    check("只填 DeepSeek", KE.status_line(True, False) == KE.MSG_DS_ONLY, KE.MSG_DS_ONLY)
    check("都没填", KE.status_line(False, False) == KE.MSG_NEITHER, KE.MSG_NEITHER)
    check("⚠️ 文案里**不许**出现「失败」这类词（都没填是合法选择，不是错误）",
          "失败" not in KE.MSG_NEITHER and "错误" not in KE.MSG_NEITHER)

    print("\n--- ② `save()`：落盘 + 权限 + 不误伤 ---")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        okk, msg = KE.save(deepseek="sk-abc", jev="apikey_x_y", root=root)
        cred, jk = root / "credentials", root / "jev-key"
        check("返回成功", okk, msg)
        check("DeepSeek 写进 credentials", cred.read_text().strip() == "sk-abc")
        check("⭐ 官方形状的 key 写进 `jev-key`（不是 jev-token）", jk.exists())
        # 改坏：把 `_write_600` 里的 `chmod(0o600)` 去掉 -> 这条红。
        check("⭐⭐ 权限是 600（密钥文件）",
              oct(cred.stat().st_mode & 0o777) == "0o600",
              oct(cred.stat().st_mode & 0o777))
        check("⭐ 父目录收成 700", oct(cred.parent.stat().st_mode & 0o777) == "0o700",
              oct(cred.parent.stat().st_mode & 0o777))
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        # ⚠️⚠️ **已存在的、权限宽松的文件也要被收紧** —— 这是那条 `chmod` 唯一不可替代的
        #    作用（新文件光靠 `touch(mode=0o600)` 就是 600 了）。
        #    第一版没测这条 → **去掉 `chmod` 判据照样全绿**（2026-09-30 变异验证抓到的）。
        wide = root / "credentials"
        wide.write_text("sk-old\n", encoding="utf-8")
        wide.chmod(0o644)
        KE.save(deepseek="sk-new", root=root)
        check("⭐⭐ **已存在的宽松文件**会被收紧成 600（不是只对新文件生效）",
              oct(wide.stat().st_mode & 0o777) == "0o600",
              oct(wide.stat().st_mode & 0o777))
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        KE.save(deepseek="sk-first", root=root)
        KE.save(jev="apikey_a_b", root=root)          # 只补 Jev
        # 改坏：`save()` 里对空白栏也照写 -> 这条红（credentials 被写成空）。
        check("⭐⭐ **只补 Jev 不抹掉已配的 DeepSeek**",
              (root / "credentials").read_text().strip() == "sk-first",
              repr((root / "credentials").read_text()))
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        okk, msg = KE.save(root=root)                 # 两栏都空
        check("⭐ 都不填 = 合法选择（成功 + 不建任何文件）",
              okk and not list(root.iterdir()), msg)
    with tempfile.TemporaryDirectory() as d:
        KE.save(jev="cc-token-xyz", root=pathlib.Path(d))
        check("非 `apikey_` 形状 -> 写 `jev-token`（CommandCode 那条）",
              (pathlib.Path(d) / "jev-token").exists())

    print("\n--- ③ `has_any()`：读端必须能换 ---")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        # 改坏：把 `load_key` 注入去掉（直接调 load_api_key）-> 这几条会去读
        #       **真实用户的 ~/.classlive/**，于是「空目录该返回 False」按构造失败。
        check("⭐⭐ 空目录 + 注入的假读端 -> (False, False)",
              KE.has_any(root=root, load_key=lambda _: None) == (False, False))
        (root / "jev-key").write_text("apikey_a_b\n", encoding="utf-8")
        check("有 jev-key -> (False, True)",
              KE.has_any(root=root, load_key=lambda _: None) == (False, True))
        check("读端说有 -> (True, True)",
              KE.has_any(root=root, load_key=lambda _: "sk-x") == (True, True))
        check("⭐ 读端抛异常 -> 当没有，不崩",
              KE.has_any(root=root, load_key=lambda _: 1 / 0) == (False, True))

    print("\n--- ③c `load_jev()`：读回已存的 Jev key（给填 key 框回填用）---")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        check("空目录 -> 空串（不抛）", KE.load_jev(root=root) == "")
        (root / "jev-token").write_text("cc-token\n", encoding="utf-8")
        check("有 jev-token -> 读回来", KE.load_jev(root=root) == "cc-token")
        (root / "jev-key").write_text("apikey_a_b\n", encoding="utf-8")
        check("⭐ key 与 token 都在 -> **key 优先**",
              KE.load_jev(root=root) == "apikey_a_b")

    print("\n--- ③b ⚠️ `validate_deepseek` 的 prompt 必须含 'json' ---")
    # ⚠️⚠️ `_chat_json` **永远**带 `response_format: json_object`，而 DeepSeek 对它的
    #    要求逐字是「Prompt must contain the word 'json' in some form」——
    #    不给就 **400**（不是 401，看起来像"key 坏了"）。
    #    第一版的 prompt 是 "Reply with the single word OK." / "ping" → 真 key 也 400。
    #    ⚠️ 这条**不联网**：读源码里那个字符串就够了。
    _src = (pathlib.Path(KE.__file__).read_text(encoding="utf-8"))
    _i = _src.index("def validate_deepseek")
    _body = _src[_i:_src.index("def validate_jev")]
    check("⭐⭐ 那句 system prompt 里出现了 `json`（大小写都算）",
          "json" in _body.lower().split('_chat_json')[1][:120],
          "⚠️ 没有 -> 真 key 也会 400")
    check("⭐ 只发一个 `_chat_json`（不多写第二处 httpx）",
          _body.count("_chat_json(") == 1, str(_body.count("_chat_json(")))

    print("\n--- ④ 那张框的几何（纯函数）---")
    check("0 栏 -> 只有内边距", notice.text_alert_height(0) == notice.TEXT_ALERT_PAD * 2,
          str(notice.text_alert_height(0)))
    check("⭐ 2 栏比 1 栏正好高一栏",
          notice.text_alert_height(2) - notice.text_alert_height(1)
          == notice.TEXT_ALERT_ROW)
    check("负数不会算出负高度", notice.text_alert_height(-3) == notice.TEXT_ALERT_PAD * 2)

    print("\n--- ⑤ ⚠️ `layout()` 真的把附件视图装进去了 ---")
    try:
        _case_appkit()
    except ImportError:
        print("  （没有 AppKit，跳过 ⑤）")
    except Exception as e:                                    # noqa: BLE001
        # ⚠️ 同 `test_live_summary` 那条教训：**一条判据自己抛了不许把整个文件带崩**
        check("⚠️ ⑤ 那一组自己抛了（**后面的没跑**）", False, f"{type(e).__name__}: {e}")

    # ⚠️ `bad` 必须**就在结算这一处**算 —— `test_entry_panel.py` 栽过一次：
    #    它算在中间，加在它后面的判据**只打印、不进统计、失败也不影响退出码**。
    bad = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for n in bad:
        print(f"  ❌ {n}")
    return 1 if bad else 0


def _case_appkit() -> None:
    """⑤ 那一组 —— **单独一个函数**，好让 `main()` 在外面加一层兜底。

    ⚠️ 判据必须查**视图树**，不能查 `accessoryView()` 那个 getter ——
       不调 `layout()` 时它照样返回对象，只是**没进视图树**（"看起来设过了"）。
    """
    import objc                                                    # noqa: F401
    from AppKit import NSApplication
    NSApplication.sharedApplication()
    a, boxes = notice.build_text_alert(
        "T", "M", [{"key": "k", "label": "L", "hint": "H"}], ("保存", "以后再说"))
    w = a.window()
    acc = a.accessoryView()
    check("附件视图建出来了且尺寸非零",
          acc is not None and acc.frame().size.width > 0,
          f"{acc.frame().size if acc else None}")

    def _in_tree(v, target, d=0):
        for s in v.subviews():
            if s is target:
                return True
            if d < 4 and _in_tree(s, target, d + 1):
                return True
        return False

    inside = acc is not None and _in_tree(w.contentView(), acc)
    check("⭐⭐ 它在**窗口视图树里**（`layout()` 生效）", inside,
          "" if inside else "⚠️ 不在 —— `build_text_alert` 里那句 `a.layout()` 没跑到")
    check("⭐ 窗口够宽装得下附件（不是那个 260 宽的退化形态）",
          w.frame().size.width >= notice.TEXT_ALERT_W,
          f"窗口 {w.frame().size.width} vs 附件 {notice.TEXT_ALERT_W}")
    check("按钮两个，且**添加顺序是「保存」在前**（= 排在最右 = 主按钮）",
          [b.title() for b in a.buttons()] == ["保存", "以后再说"],
          str([b.title() for b in a.buttons()]))
    # ⚠️ 上面那次是**显式传的**按钮，测不到默认值 —— 而默认值才是 `entry_panel`
    #    真正会用到的那一个（它不传 `buttons`）。要断言**签名本身**。
    #    第一版只测了显式传参 → 把默认值改回 ("以后再说","保存") **判据照样全绿**。
    import inspect
    _dflt = inspect.signature(notice.ask_text).parameters["buttons"].default
    check("⭐⭐ `ask_text` 的**默认**按钮顺序也是「保存」在最前（主操作在右）",
          tuple(_dflt) == ("保存", "以后再说"), str(_dflt))

    # ⚠️ 判重叠**必须同时判 x 和 y** —— 只判 y 会把「标签和输入框并排」误报成重叠
    #    （2026-09-30 我自己写过一版只判 y 的检查器，报了 20pt 的假重叠）。
    rows = [v for v in acc.subviews() if hasattr(v, "stringValue")]
    bad = []
    for i, v in enumerate(rows):
        for u in rows[i + 1:]:
            a1, a2 = v.frame(), u.frame()
            if (a1.origin.x < a2.origin.x + a2.size.width
                    and a2.origin.x < a1.origin.x + a1.size.width
                    and a1.origin.y < a2.origin.y + a2.size.height
                    and a2.origin.y < a1.origin.y + a1.size.height):
                bad.append((v.stringValue()[:14], u.stringValue()[:14]))
    check("⭐⭐ 子视图**两两不重叠**", not bad, str(bad))

    # ── ⭐ 回填：`value` 给了就预填进安全框（重开不再空白）───────────────
    # 动机（2026-10-01 作者实测）：「我填了 API 然后退出、再点进去它又变空白了」——
    # 空白看不出"存过没有"；改成回填（安全框里 = 圆点，不是明文）。
    a2, boxes2 = notice.build_text_alert(
        "T", "M", [{"key": "k", "label": "L", "value": "sekret-42",
                    "hint": "H"}], ("保存", "以后再说"))
    check("⭐⭐ `value` 回填进输入框（重开不空白）",
          boxes2["k"].stringValue() == "sekret-42",
          repr(boxes2["k"].stringValue()))
    check("⚠️ 回填的框仍是**安全框**（圆点显示，不是明文控件）",
          "Secure" in type(boxes2["k"]).__name__, type(boxes2["k"]).__name__)

    # ── ⭐ 编辑菜单：没它 ⌘V 在**所有**输入框里都是死的 ──────────────────
    # 动机（2026-10-01 作者实测）：右键 Paste 能粘、**⌘V 完全没反应**；
    # 根因是 app 从不建 mainMenu —— 文本框的快捷键靠菜单栏键等价分发。
    import panel as P
    ok1 = P.install_edit_menu()
    _mm = NSApplication.sharedApplication().mainMenu()
    _sub = (_mm.itemAtIndex_(0).submenu()
            if _mm is not None and _mm.itemAtIndex_(0) is not None else None)
    _items = list(_sub.itemArray()) if _sub is not None else []
    _pastes = [it for it in _items if it.keyEquivalent() == "v"]
    check("⭐⭐ 装上了 Edit 菜单，且 ⌘V 接的是 `paste:`",
          ok1 and len(_pastes) == 1 and "paste" in str(_pastes[0].action()),
          f"项数={len(_items)} paste={[_pastes[0].action()] if _pastes else None}")
    check("⭐ ⌘C / ⌘A 也在（拷贝与全选）",
          {"c", "a", "x", "z"} <= {it.keyEquivalent() for it in _items},
          str([it.keyEquivalent() for it in _items]))
    check("⭐ 幂等：再装一次不重复、不炸", P.install_edit_menu() is True)


if __name__ == "__main__":
    sys.exit(main())
