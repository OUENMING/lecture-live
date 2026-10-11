---
paths:
  - "*.sh"
  - "cl"
  - "cl-x"
  - "scripts/*.sh"
  - "update.py"
  - "tools/make_icon.py"
  - "requirements.txt"
---

# shell 入口与 `.app` 构建

动 `make-app.sh` / `install.sh` 前先读它们的**文件头注释**（`install.sh` 的完整论证在 `docs/PLAN-p1-app-launcher.md` §3.11）。

- **`$VAR` 紧跟非 ASCII 必须写 `${VAR}`**：bash 在 UTF-8 下把那个字节吞进变量名，`set -u` 直接 unbound，
  而且只在出错路径的提示文案里咬人。R27 + 改完即查的 hook 已经钉着。
- **给别的脚本/进程看的判据，先问「它出错时倒向哪边」**。`make-app.sh --up-to-date` 是 `0` = 最新、
  非 `0` = 该重建 —— 反过来的话脚本自己一出错就被读成「最新」，静默跳过重建（09-26 实测踩到）。
- **构建戳记 `ClassLive.app/Contents/.build-stamp`** = 构建输入的指纹。判据只在 `make-app.sh --up-to-date`
  一份实现里（`update._app_stale_reason()` 调它、`--fingerprint` 是取值口子），别在 `install.sh` / `update.py` 再算一遍。
  - `cl update` 会把 `.app` 一起跟上（`update.py --run-step app`，先问戳记，没变就跳过）。
  - 指纹**不含 `requirements.txt`**（依赖由 deps 步骤直接装进 `.app` 的 python）。
  - 指纹**不含 `VERSION`**：它每版都变、全仓没有代码读者，放进去 = 每发一版所有用户白重建 1–3 分钟，
    而重建可能打断正在录的课。别加回去（`tests/test_update.py` B13 钉着）。
  - ⚠️ 反过来：**改 `make-app.sh` / `tools/make_icon.py` 本身就会变指纹** → 所有用户下次更新都要重建。非必要不碰。
- **运行环境在 `ClassLive.app/Contents/` 里**，不是仓库根的 `.venv`（让 macOS 把它当 bundle，麦克风授权框才写「ClassLive」）。
  里面的 python **没有 pip**：装包用 `uv pip install --python ClassLive.app/Contents/MacOS/python …`。
  拿基础解释器别用 `uv python find 3.12`（不加 `--managed-python` 会返回 Homebrew framework 构建，方案前提不成立）。
- `cl course` 的模糊匹配只在 `courses.py` 一处，别在 shell 里再写一份。
- 碰过更新机制：`scripts/check.py --changed` 会补跑 `tests/test_update.py`（`pull()` 三条安全边界 + 自动更新分级）。
  自动更新静默失败只写 `~/Library/Logs/ClassLive/update.log`。
