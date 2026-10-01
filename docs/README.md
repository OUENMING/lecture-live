# docs/ —— 分区与索引

> 这个目录会乱，因为文档是按「一轮工作」长出来的，不是按主题。
> 本文件只答一件事：**哪份是哪类、什么时候读**。
> ⚠️ **它不含任何状态判断。** 某份计划做没做完、哪条账还开着 ——
> 那些**必然腐坏**，唯一去处是 `HANDBOOK.md §10`（自己也会旧，但旧了会很明显）。
> 本文件只写「这份是什么」，不写「这份对不对」。

## 三层分区

| 层 | 位置 | 怎么对待 |
|---|---|---|
| ⭐ **活文档** | 本目录根的 `PLAN-*.md` / `RESEARCH-*.md` / `DESIGN.md` / `HANDBOOK.md` | 设计依据与依据的存档 |
| 🗄️ **归档快照** | `archive/`（9 份） | ⚠️ **只当史料读。里面的「下一步」「还剩什么」一律不作数** |
| 🔬 **可复跑实验** | `experiments/` | **结论的证据。** 实测数字出自这里，可重跑 |

## 活文档

### 入口

| 文件 | 什么时候读 |
|---|---|
| `../CLAUDE.md` | 改代码前。文件地图 + 本仓库的雷区 |
| `HANDBOOK.md` | 从零上手。现状、硬规矩、全部事故、**§10 公开的账** |
| `PLAN-roadmap.md` | 该做什么、按什么顺序 |

### 计划与设计（`PLAN-*`）

⚠️ **做没做完不在这里查** —— 见 `HANDBOOK.md §10`。下表只说每份**管什么**。

| 文件 | 管什么 | 什么时候读 |
|---|---|---|
| `PLAN-live-summary.md` | AI 实时总结（原子 / 章节 / 纲要） | 改 `live_summary.py` / `chapter.py` 前 |
| `PLAN-test-mode.md` | 测试模式（采集 → 打包 → 上传） | 改 `testmode.py` / `upload.py` 前 |
| `PLAN-entry-panel.md` | P3 第二批 · 课程卡片面板 | 改 `entry_panel.py` 前 |
| `PLAN-p3-prep.md` | P3 第一批 · 课件 → 候选术语 | 改 `prep.py` / `extract.py` 前 |
| `PLAN-update-mechanism.md` | 分级更新 / 引导式更新 | 改 `update.py` / `whatsnew.py` 前 |
| `PLAN-panel-ux.md` | 面板启用逻辑 · UX 定案 | 做新面板前 |
| `PLAN-notes-and-ui.md` | 笔记复习层重做 + UI 动效 | ⚠️ **§0 是病根诊断**（「知识点详解」像复述），其余设计已被现实现代 |
| `PLAN-ai-explain-qa.md` | 按需讲解 + 追问线程 | 改问答链路前 |
| `PLAN-audio-gain.md` | 收音电平归一化（增益上限 / 两级归一） | 调 `capture.PeakNormalizer` 前 |
| `PLAN-p1-app-launcher.md` | P1 脱离终端（`.app` 启动器） | 改 `make-app.sh` / `install.sh` 前 |

### 调研（`RESEARCH-*`）

调研类的时效性比计划类长 —— 前提变了结论就要重跑。**引用前先看「口径」那一节的日期。**

| 文件 | 主题 | 什么时候读 |
|---|---|---|
| `RESEARCH-macos-aesthetic.md` | macOS 视觉语言 | ⭐ 动 `overlay.py` 外观前 |
| `RESEARCH-live-caption-rollup.md` | 2 行 roll-up 的台账 | ⭐ 动草稿或字幕行前 |
| `RESEARCH-entry-and-export.md` | 导入入口 + 无 Obsidian 时的导出 | 做 P4/P5 前 |
| `RESEARCH-product-shape.md` | 产品形态 + VPS 评估 | 做 P4 前 |
| `RESEARCH-p3-seams.md` | P3 的代码接缝钉到 `file:line` | ⚠️ **行号会漂**，按符号名查 |
| `RESEARCH-p3-extract.md` | 课件抽文本选型 | 同上 |
| `RESEARCH-p3-community.md` | 社区与同类产品怎么做「开课前的准备」 | 同上 |

### 长期

| 文件 | 说明 |
|---|---|
| `DESIGN.md` | 深度工程笔记：每条性能数字、每个坑的原始记录。**「实测」数字的可信出处** |
| `HANDBOOK.md` | ⭐ 从零上手的全景手册 |
| `archive/` | 9 份某一天的快照，2026-09-24 → 09-30 |
| `experiments/` | 20 个可复跑脚本 + `experiments/RESULTS-live-summary.md` |

## archive/ 是什么

都是**某一轮交接 / 现状 / 评审 / 作者原话**的快照。归档的理由统一是：
**它们讲的「下一步」在归档当天就已经做完了，留在原地会引人去读一份过期的账。**

保留而不删，是因为它们记着**决策过程**（为什么这么做、哪些路被否了）——
那部分不会过期，而仓库根的 `../CHANGELOG.md` 只记结果。

⚠️ 归档文档里的「还剩什么」是**当时的账**，不是现在的。现在的账唯一看 `HANDBOOK.md §10`。

## 怎么验这份索引没烂

本文件不含状态判断，只含**路径**。路径会坏（文件改名/搬家），所以抽查这两条：

```sh
# 1. 死链：只查 docs/ 前缀的（裸名如 CHANGELOG.md 在仓库根，不在这条判据范围）
grep -rno "docs/[A-Za-z0-9_-]*\.md" --include="*.md" . 2>/dev/null \
  | sed 's/:[0-9]*:/ /' | sort -u | awk '{print $2}' | sort -u \
  | while read f; do [ -f "$f" ] || echo "死链: $f"; done
#   ⚠️ 已知的**故意例外**：`docs/REVIEW-2026-09-24.md` 出现在 CHANGELOG 与
#   RESEARCH-entry-and-export 里 —— 那两处记的是「**它本来就不在仓库里**」。

# 2. 本文件列的每一份是否还在 docs/ 下（排除 ../ 那些仓库根的）
grep -oE '`[A-Za-z][a-zA-Z0-9_-]*\.md`' docs/README.md | tr -d '`' | grep -v '^CHANGELOG' \
  | sort -u | while read f; do [ -f "docs/$f" ] || echo "不在 docs/: $f"; done
```

**做没做完、哪条账开着** —— 不在这里查，见 `HANDBOOK.md §10`。