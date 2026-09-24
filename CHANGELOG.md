# Changelog

本文件记录 ClassLive 的显著变更。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

> **本项目没有 pip 包，安装方式就是 `git clone`** —— 所以升级 = `git pull`。
> 每次 `pull` 后请对照本文档确认要不要补依赖或模型（见各版本的「升级须知」）。

## [Unreleased]

### Changed
- **两个 ASR 模型都改为必需**，不再有静默回退。
  - 此前 `whisper-turbo` 缺失会警告后回退 Parakeet（"能用但少一档质量"）；
    现在**缺任何一个都在启动时报错并退出**，提示跑 `cl doctor`。
  - 理由：定稿模型是转写质量的主要来源（有效词数 +33%），静默少掉它是
    "看着正常但打了折"，而用户不会知道。**启动时说清楚，比课上悄悄降质好。**
  - `--final-model-dir ""` 这个关闭开关随之取消。

## [3.4.0] - 2026-09-24

### Added
- **定稿增强模型**：定稿路径可选接入 Whisper-large-v3-turbo（`--final-model-dir`）。
  同批真实课堂录音实测：有效词数 **+33%**、段尾无终止标点 **22%→14%**、空转写 **9%→2%**。
  草稿路径不变（Whisper 只有 4× 实时，供不上"每秒一份草稿"）。模型缺失会自动回退 Parakeet。
- **三档模式**：悬浮窗顶栏一个按钮循环「双语 / 只英·校 / 纯转录」。
  第三档**完全不调 LLM**（零 API、零首字延迟），补上了此前"不想联网且不要中文"的空缺。
- `requirements.txt` —— 此前依赖只在 README 里手打，环境不可复现。
- `cl doctor` —— 只读自检（版本/依赖/模型/术语表），缺什么打印该跑的命令。
  **不自动下载、不自动安装**。
- 启动时的**一次性**更新提示：落后远程时打印一行，同一版本只提示一次，可用
  `CLASSLIVE_NO_UPDATE_CHECK=1` 关闭。只问本地 git，不发 HTTP。
- `docs/experiments/` —— 9 个可复跑的对照实验脚本（换模型 / 降噪 A/B / VAD 调参 / 缩放探针）。
  README、DESIGN 里的"实测"数字大多能在这里找到对应脚本。
- 悬浮窗可自由缩放：原生四角/四边拖拽、缩放光标、最小 280px 宽 / 1 行高。
- 窗口尺寸记忆（`.window`），下次启动恢复。

### Changed
- **悬浮窗「翻译开关」由 2 态改为 3 态**。旧的两态行为 = 新的「只英·校」档。
  代码层面：`on_translate` 回调的入参从 `bool` 变为 `str`（`"both"` / `"en"` / `"raw"`）。
- **双语行的竖排顺序改为「英文在上、中文在下」**。依据是三条独立同行评审研究：
  双语字幕里 L2 行被系统性略读，而**只有"哪一行在上面"被证实能改变注意分配**
  （不是字重/字号/颜色）。想回退：`transcript_view.EN_LINE_ON_TOP = False`。
- 定稿路径的执行顺序改为**先跑 Whisper、退化才回退 Parakeet**（实测省 14%，因为原来那趟
  Parakeet 绝大多数时候都是白跑的）。

### Fixed
- `obsidian_writer._overview` 用整体替换而非合并 —— 模型漏返回字段时会丢掉第一段已算好的概览。
- `build_notes.TermNotes.add()` 硬编码全局 `AUTO_FILE`，而 `__init__` 按传入 `path` 推导 ——
  自定义路径时读写不是同一个文件（测试里 `TermNotes(临时目录)` 会写坏真实缓存）。
- `asr.py` 三个模型文件各自独立解析，int8 与非 int8 混用不会报错但识别结果"看着正常全错" —— 加一致性校验。
- `cl-bg.py` 把 `argv[1]` 插进 `bash -c` 命令串（命令注入）；日志权限 0644 → 0600。
- `translator._load_terms` 裸读术语表（非 UTF-8 / 权限问题会抛异常）。
- `cloud_translator._stream_chat` 不重置 `last_usage`，空流时会把上一次用量算两遍。
- `.gitignore` 未覆盖 `.env` / `*.key` / `*.pem` / `id_rsa*` 等常见密钥形态。
- `probe_scroll.py` 屏幕提示与判定互相矛盾（断言反转后的遗留）。
- `CLAUDE.md` / `ARCHITECTURE.md` 里"`polish.py`、`tests/` 不在 git 里"的**过期断言** ——
  它们其实早已提交，文档没回头改（已在文档里标明快照日期与现查方式）。

### Removed
- 顶栏「展开」按钮。它与「滚动权限」耦合，而滚动权限已与收起/展开解耦（拉窗口 = 看几句，
  滚动 = 往回翻，两者正交）。`_apply_mode` 保留 —— 答案接管仍在用它。

### 升级须知（3.3 → 3.4）
1. `git pull` 后跑一次 `uv pip install --python .venv/bin/python -r requirements.txt`
   （本次没有新增依赖，但今后以此为准）。
2. 可选：装定稿模型（~1GB），命令见 `cl doctor` 的输出。
3. `on_translate` 回调签名变了 —— **只影响自己改过 `overlay.py` 的人**。

---

## [3.3.0] 及更早

早期版本没有维护 changelog。历史见 `git log`。
