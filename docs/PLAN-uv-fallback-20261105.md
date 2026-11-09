# PLAN · 没有 uv 的机器，自动更新也能补依赖（2026-11-05）

> 状态：**已实现（2026-11-09，未发版）** —— `update.py::_pip_install_cmd`（uv → ensurepip → pip）+ `cl` 改走 `--run-step deps`；判据 `tests/test_update.py` G2/G3。
> 不重建 `.app` · 只改 Python / bash
> 起因：美国用户 `bldus666666` 的 `update.log` 每个版本都是
> `补依赖没成：FileNotFoundError: [Errno 2] No such file or directory: 'uv'`
> 相关记忆：`classlive-update-without-uv`、`classlive-311-errors-20261104`

## 1. 结论先说

- **不需要重建 `.app`**。`make-app.sh` 的构建指纹只含 `make-app.sh` + `tools/make_icon.py`
  （`fingerprint()`），**不含 `requirements.txt`，也不含 `update.py` / HTML**。
  依赖是直接装进 `.app` 那个 python 的 site-packages 的，本来就不用重建。
- 自动更新的收尾 `finish()` 用**拉下来的新代码**跑（`_finish_in_child` → `update.py --finish`），
  所以这个兜底能随一次纯代码更新到达老用户；**不需要先发一个专门的重建版**。
- 周末可以和其他代码改动（面板断线 pill 等）一起发；唯一例外见 §6。

## 2. 现状（两条路，都会断）

### 2.1 自动更新（下课退出 / 面板后台）

`update.py` 的 `run_step("deps")`（约 1628 行）：

```python
r = subprocess.run(
    ["uv", "pip", "install", "--python", sys.executable, "-q",
     "-r", str(HERE / "requirements.txt")],
    cwd=HERE, env=_child_env(), capture_output=True, text=True, timeout=900)
```

**只有 uv，没有 fallback。** `_child_env()` 只补 `~/.local/bin` 和 `/opt/homebrew/bin`；
他的机器两处都没有 uv → `FileNotFoundError` → `update.log` 记一笔，返回 `ok=False`。

因为 `mark_reqs_installed()` 从未成功，`~/.classlive/reqs-installed` 戳记一直空
（`deps.stamp_ok=false`），所以**每次启动/更新都再试一次、再失败**。

### 2.2 `cl update`（终端）

`cl` 里已有 fallback：

```bash
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PY" -q -r requirements.txt
else
  "$PY" -m pip install -q -r requirements.txt
fi
```

但 `.app` 里的 python 是 uv 建的空 venv，**不带 pip**（实测 `No module named pip`），
所以第二条也走不通。`cl` 文件头 39–40 行已经写明这个事实。

## 3. 改法

### 3.1 `update.py`：deps 步骤加 ensurepip 兜底

新增一个私有 helper（位置放在 `run_step` 上方）：

```python
def _pip_install_cmd() -> list[str] | None:
    """找 uv；没有就确保 pip，返回可执行的安装命令。拿不到 pip 返回 None。"""
    import shutil
    uv = shutil.which("uv", path=_child_env().get("PATH"))
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, "-q",
                "-r", str(HERE / "requirements.txt")]
    # .app 的 python 带 ensurepip，但不带 pip（2026-11-04 实测 pip 25.0.1）
    r = subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                       cwd=HERE, env=_child_env(), capture_output=True, text=True,
                       timeout=300)
    if r.returncode != 0:
        return None
    return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "-q", "-r", str(HERE / "requirements.txt")]
```

`run_step("deps")` 里：

```python
cmd = _pip_install_cmd()
if cmd is None:
    err = (r.stderr or r.stdout or "").strip().splitlines()
    return {"ok": False, "error": "没有 uv，pip 也起不来：" + (err[-3:] if err else "")}
r = subprocess.run(cmd, ...)
```

成功仍然 `mark_reqs_installed()`；失败仍留最后三行。

### 3.2 `cl`：别维护第二套动作

把 `cl` 的 uv/pip 分支换成：

```bash
"$PY" update.py --run-step deps || return $?
```

`run_step("deps")` 成功时已经 `mark_reqs_installed()`，不需要再 `--mark-reqs`。
这样「判据」（`--steps`）和「动作」（`--run-step deps`）都只有一份。
⚠️ 改完要跑 `tests/test_update.py`，里面有对 `cl` 的字符串/路径判据（B12 一带）。

### 3.3 判据（新增到 `tests/test_update.py`）

1. **有 uv**：`shutil.which("uv")` 返回假路径 → 命令第一条是 uv，**不**碰 ensurepip。
2. **无 uv、有 ensurepip**：假 python 依次接住 `-m ensurepip --upgrade` 和 `-m pip install ...`
   → 断言顺序、`mark_reqs_installed()` 被调用。
3. **无 uv、ensurepip 失败**：返回 `ok=False`，**不**写戳记。
4. **已安装的包不升级**：现有环境满足 `>=` 时 pip 默认不升级（见 §5）。
5. 用沙盒 `HOME` / `STATE_DIR`，不要碰真 `~/.classlive`（沿用现有测试基建）。

### 3.4 文档

- `update.py` 文件头 / `run_step` docstring 说明「uv 或 ensurepip+pip」。
- `docs/HANDBOOK.md` § 更新那条补一句：没有 uv 的机器走 ensurepip。

## 4. 发布顺序

- **推荐**：先发一个「不带新依赖」的版本，把兜底带上；下一版再加依赖。
  这样即使兜底有 bug，也不会同时叠加缺包。
- **也可以**：兜底和新依赖同一版——因为 `_finish_in_child` 拉完代码后用新代码跑，
  能吃到新兜底。但这样两个变量一起动，出问题不好定位。
- **不用**重建 `.app`；用户下次启动/自动更新即可生效。
- 周末批次建议：`uv 兜底` + `面板 3.11 断线 pill`（都是代码层，不改构建输入）。

## 5. 风险：没有 lock

`requirements.txt` 全是下界。**空环境**里 `pip install -r` 会装最新
（实测 dry-run 会解析到 `sherpa-onnx 2.0.1`，而当前装的是 1.13.8）；
但在他**已有环境**里，pip 默认不会升级已满足约束的包（`pip install` 不带 `--upgrade`），
所以实际升级面比空环境小。仍然建议：

- 至少在兜底里加 `--upgrade-strategy only-if-needed`（pip 默认就是它，写出来防未来变）；
- 真要长期自动补，考虑给 `sherpa-onnx` / `mlx` 这类 C/API 敏感的包钉死小版本，或加 lock。
- `uv pip install` 的升级策略要单独确认一次，不要假设和 pip 一样。

## 6. 例外：什么时候仍然需要重建 `.app`

- 改 `make-app.sh` 或 `tools/make_icon.py` → 指纹变 → 走 `run_step("app")` → 跑 `make-app.sh`
  → **make-app.sh 自己要求 uv**（`command -v uv`）。这类改动对没 uv 的机器仍然会失败。
- 本次计划只改 `update.py` / `cl` / HTML / 测试，**不碰这两个文件**，所以不受影响。
- 如果周末一定要改图标或打包脚本：要么先让他装 uv，要么把 make-app 的 uv 依赖也做兜底
  （更难：它要用 uv 管理独立 python 建 venv）。

## 7. 验收清单

- [ ] 沙盒测试：无 uv → ensurepip → pip → 写戳记（三条分支）
- [ ] 真机（Owen）模拟：`PATH` 去掉 uv，跑 `update.py --run-step deps`（用假 requirements / 沙盒 HOME）
- [ ] `scripts/check.py` 全绿
- [ ] 发版后看美国用户 `update.log`：不再出现 `FileNotFoundError: 'uv'`，`deps.stamp_ok` 变 true
- [ ] 下次他启动后确认 note.html 补传（与本计划无关，但同一批观察）
