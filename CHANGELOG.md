# Changelog

本文件记录 ClassLive 的显著变更。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

> **本项目没有 pip 包，安装方式就是 `git clone`** —— 所以升级 = `git pull`。
> 每次 `pull` 后请对照本文档确认要不要补依赖或模型（见各版本的「升级须知」）。

## [3.5.0] - 2026-09-24

### Added
- **`cl test` 测试模式** —— 跑一节真实课，采全量指标（逐段 ASR 耗时/电平/波峰因数/
  置信度/用了哪个模型/是否回退 + 资源占用 + VAD 诊断），收尾打成一个 `bundle.zip`
  方便发给作者。默认录音频（约 28MB/15 分钟）；`--no-record-audio` 只留 ~10KB 的纯指标包。
  ⚠️ 含课堂音频与逐字转录，可能含其他同学的声音 —— 启动与收尾都会提醒。
- **`cl update`** —— 一条命令更新：拉代码 + **按需**补依赖 + 自检。三条边界：
  工作区有本地改动时**停手**（绝不 stash/丢弃）；`--ff-only`（分叉时响亮失败，
  不静默造 merge）；**绝不自动下模型**（只打印命令）。

### ⚠️ Breaking changes
- **两个 ASR 模型都改为必需**，取消静默回退。此前 `whisper-turbo` 缺失会警告后
  回退 Parakeet（"能用但少一档质量"）；**现在缺任何一个都在启动时报错并退出**。
  理由：定稿模型是转写质量的主要来源（实测**有效词数 +44%**，8/8 窗口一致），
  静默少掉它是"看着正常但打了折"。**升级后请跑 `cl doctor` 或 `cl update`。**

### Fixed
- `translator` 懒加载的**真竞态**：`self._model, self._tokenizer = load(...)` 是两条
  STORE_ATTR 且 model 在前，另一线程可能恰在两条之间读到 `_model` 已非 None，
  于是拿着还是 `None` 的 `_tokenizer` 去 `apply_chat_template`。
  **修法：换成 tokenizer 先、model 后** —— `_model` 成为真正的就绪标志。
  （`auto` 模式下模型不预热，**首次本地调用就发生在 worker 线程上**，这是会走到的路径。）
- `cl-bg.py` 默认日志从 `/tmp` 固定路径移到 `~/Library/Logs/ClassLive/`，
  并加 `O_NOFOLLOW`。实测无 `O_NOFOLLOW` 时**符号链接注入是成功的**。
- `rebuild_note.py`：`--vault ""`（或 `OBSIDIAN_VAULT` 设为空串 —— `os.environ.get`
  在变量存在但为空时**不回退默认值**）会走到 `Path(None)` 抛 TypeError。
- `build_notes.py` 两处**数据丢失风险**：
  ① `save_to` 改为**原子写**（.tmp + `os.replace`），且备份失败**不再静默**；
  ② 分类时模型没给出可识别档位不再**无条件降级成 basic** —— 有 `detail` 的按 gloss 处理，
  否则会留下"level=basic 但 detail 是 80–160 字整段"的错位状态，且**永久留在库里**。
- `cache_probe.py`：参数校验（非数字/`n<=0`/无 ASR 行都友好报错）；
  请求数口径拆成 `requests` / `calls`（原来拿"有 usage 的请求数"当总请求数，两个数同时偏小）。
- `probe_scroll.py`：提示标签不再只在开头取一次高度，阶段切换后重算位置。
- `build_notes.py`：`load_raw()` 改显式传参 —— 它的**无参默认值在定义时绑定**，
  而 `save_to(NOTES_FILE)` 是**调用时**取全局，两者一旦不一致就是"读一个写另一个"。

### Changed
- README 路线图按实测重排：把**已经做完并否决**的几件事从待办移走
  （降噪 −41% / 调 VAD 零差异 / 换模型 4 个全败 / 课号匹配 p=0.69 / 热词段错误 / WPE 测不出），
  并新增「为什么不做降噪 / 人声分离 / 频段滤波」一节。
- 删掉产品用不到的依赖与模型：`nara-wpe` / `scipy` / `bottleneck`、`~/models/denoise`。
  逐个核对 17 个 `add_argument`，无死参数。

### 升级须知（3.4.0 → 3.5.0）
1. **`cl update`**（或 `git pull`）—— 本次 `requirements.txt` **没有变化**，不需要重装依赖。
2. **确认两个 ASR 模型都在**：跑 `cl doctor`；缺哪个它会打印下载命令。
3. 本次是**破坏性变更**：模型不全时会启动失败（以前只是警告）。

## [3.4.0] - 2026-09-24

### Added
- **定稿增强模型**：定稿路径可选接入 Whisper-large-v3-turbo（`--final-model-dir`）。
  同批真实课堂录音实测：有效词数 **+33%**、段尾无终止标点 **22%→14%**、空转写 **9%→2%**。
  草稿路径不变（Whisper 只有 4× 实时，供不上"每秒一份草稿"）。模型缺失会自动回退 Parakeet。

  > ⚠️ **2026-09-24 更正**（3.5.0 时重测）：词数增益实测是 **+44%**（不是 +33%，当时报低了）；
  > 而"段尾无终止标点 22%→14%"的幅度**落在噪声带内**（该指标 n≈11 段时底线 ±18 个百分点），
  > **不足以判定** —— 保留原文不改写当日记录，但别把那个数字当结论。
  > 定性证据（`part`→`pot`、`he jokes`→`heat up`）不受指标噪声影响，仍然成立。
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
