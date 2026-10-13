---
paths:
  - "CHANGELOG.md"
  - "VERSION"
  - "whatsnew.py"
---

# 发版

**流程**：写 CHANGELOG 条目 → **给作者看、他确认** → bump `VERSION` → commit → `git tag` →
push main + tag → `gh release create` → 弹更新卡片给他看效果。
（push / tag / release 会被 `.claude/hooks/pre_bash.py` 拦一下：作者点头后命令前加 `CLASSLIVE_OWEN_OK=1`。）

- **更新方式默认 `auto`**（2026-10-13 作者改的；3.9.4 起客户端「不写 = auto」）：有破坏性变更、
  要用户先读须知的版本才写 `### 更新方式 manual`。不能靠版本号推（`3.4.0 → 3.5.0` 是 minor 号却必须装新模型）。
  ⚠️ 3.9.3 及更早的客户端仍按「不写 = manual」—— 想让它们也自动装，那一版照旧显式写 `auto`。
- **文案先给作者确认**：他对措辞有明确品味（改过两次）。该专业的必须专业（破坏性 / 丢数据 / 安全），其余可带点趣味。
- 卡片按**物理行**取「更新看点」，折行会被当新条目（R17 钉着）。
- 几版攒在一起发时，合并后的更新日志要再给作者看一次。
