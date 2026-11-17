"""悬浮窗的**样式参数**（2026-11-17 换皮）—— 与网页版 `s3/app/src/lib/tokens.css` 同源。

一份数，两边用：桌面 AppKit 读这里，网页 CSS 抄这里（注释里写了对应的 CSS 变量名）。
改了这里，记得同步 tokens.css（反过来也一样）。

⚠️ 只放**观感**参数（颜色 / 透明度 / 圆角 / 字号 / 时长）。几何算术（行高、行数档位、
   面板最小尺寸）仍在 `overlay.py` —— 那些是实测标定出来的，和观感不是一回事。

⭐ 观感三条（Owen 11-17 认可的稿：~/classlive-web-spike/s3/app/src/routes/desktop）：
   ① 只用白色 —— 没有琥珀 / 彩色强调；层次 = 白字 alpha + 字重。
   ② 控件平时退后（~35%），鼠标进窗才全亮；状态（在听小点、章节条、档位胶囊）不退。
   ③ 旧字幕逐行退后：最新一行最亮，往上每行暗一档，最上面那行接近淡出。
"""
from __future__ import annotations

# ---- 材质（--cl-glass / --cl-radius）----
#: 磨砂上压的那层黑。`panel.SCRIM_ALPHA` 读它。
SCRIM_ALPHA = 0.38
#: 面板圆角。`panel.CORNER_RADIUS` 读它。
CORNER_RADIUS = 16.0

# ---- 白字层次（--cl-text / --cl-text-2 / --cl-text-3 / --cl-draft-*）----
TEXT = 1.00          # 中文定稿
TEXT_EN = 0.80       # 英文小字（定稿）
TEXT_2 = 0.65        # 次要：章节标题、术语解析
TEXT_3 = 0.46        # 更次要：「实时总结」这种标签、时间
DRAFT_EN = 0.50
DRAFT_ZH = 0.78

# ---- ③ 旧字幕逐行退后（稿：opacity = 1 − age × 0.22）----
#: 视觉层号 0（最新）→ 1 → 2 → 3+ 的亮度倍数。`transcript_view.D_DIM` 读它。
#: 只在跟随态生效（往回翻时全亮 —— 那时人在读旧的）。
AGE_DIM = (1.0, 0.78, 0.56, 0.34)

# ---- ② 顶栏控件（稿 .tool opacity 0.35，.win:hover → 1）----
#: 鼠标不在窗里时顶栏按钮（❓ / 字幕 / – / ✕）的 alpha。
BAR_IDLE_ALPHA = 0.35
#: 档位胶囊是**状态**不是动作（稿里它不退）。
CHIP_IDLE_ALPHA = 1.0

# ---- 字号（--cl-zh / --cl-en）----
#: 中文定稿 18pt Medium —— 行高档位 `overlay.LINE_TIERS` 是按 18pt 实测标定的，别单改。
#: 11-17 量过：18→20 时一行折两行的比例从 53% 升到 57%，档位要重标；观感收益不抵风险，留 18。
ZH_PT = 18.0
#: 英文小字 12pt（11-17 由 11 提一档：最旧那行退到 α0.34 后 11pt 太细；12/18 = 0.67，与稿 13/20 同比例）。
#: 行高 15（11pt 是 14）→ `overlay.ROW_EN_H` 跟着它算。
EN_PT = 12.0

# ---- 窗口（稿 .win 980 宽；桌面默认值，用户拖过的尺寸照旧记在 `.window`）----
DEFAULT_WIDTH = 860.0
