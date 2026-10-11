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

- **更新方式默认 `manual`**：只有作者**特意注明**「后台自动更新」才写 `### 更新方式 auto`，默认不写那一节。
  别因为「这版没依赖变化」自作主张；也不能靠版本号推（`3.4.0 → 3.5.0` 是 minor 号却必须装新模型）。
- **文案先给作者确认**：他对措辞有明确品味（改过两次）。该专业的必须专业（破坏性 / 丢数据 / 安全），其余可带点趣味。
- 卡片按**物理行**取「更新看点」，折行会被当新条目（R17 钉着）。
- 几版攒在一起发时，合并后的更新日志要再给作者看一次。
