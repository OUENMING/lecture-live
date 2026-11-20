// 网页版系统提示词的 SHA-256 白名单 + user 段长度上限 —— **本文件由脚本生成，别手改**。
// 为什么：提示词不是秘密（OWASP LLM07），但「谁能拿通行码当任意模型接口」是真风险 —— 网页只放行
// 这四条 system prompt（Pyodide 跑桌面同一份代码拼出来），改一个字就不认：
//   翻译三条（定稿 / 草稿 / 只矫正）来自 `prompt_core.py`；术语候选一条来自 `prep.py` 的 `_prompt()`。
// 生成：python3 server/asr-worker/scripts/gen-prompt-allow.py；校验走 `--check`（不一致非零退出）。
// 判据：test-web-prompt.mjs。

// 翻译三条的来源：/Users/owen/lecture-live-prompt/prompt_core.py（prompt_core.py 的 sha256 = 3eec05d1ea77…）
// 术语候选的来源：/Users/owen/lecture-live-web/web/static/core-py/prep.py（切片的 sha256 = 551d996b290a…）
// 生成时间：2026-11-18 05:43:36
// 四条常量各算一次；比对在 prompt-guard.js（Web Crypto，Node 测试同样可用）。

export const PROMPT_SHA256 = new Set([
  "8b482c98d6d7cd6a00c89f17d0a01d98d5fd1ae58a614bbb0b839da579bc83b5",   // SYSTEM_PROMPT_CLOUD
  "de17e42ff8483c5e097f816be868ec7b40e8d5b88d1a518d6a8d2ae80a31f458",   // DRAFT_SYSTEM_CLOUD
  "ecdd71cdf812534ab88623743ae93b101c48202aa8621247abe10a9af8adb345",   // FIX_SYSTEM_CLOUD
  "d9f41b12a96c74362029d1eee2fddecc4c04d1adc7364e009be010d735f0b0e7",   // PREP_SYSTEM
]);
//: 术语候选那条的 user 段自成一档（它喂的是整块课件，跟翻译的逐句 prompt 不是一个量级）。
export const PREP_PROMPT_SHA256 = "d9f41b12a96c74362029d1eee2fddecc4c04d1adc7364e009be010d735f0b0e7";

// 上限 = 2 × 实际最大值（余量给「术语表又长了两行」这类漂移，不给出「随便塞点别的」）。
// actual 的来源（生成脚本自己算的，改输入要连这里的注释一起改）：
//   术语 60 条 × 每条约 40 字符（translator.TERM_LOAD_LIMIT）
//   上下文 19 句 × 每句 300 字符（DEFAULT_CTX_CHUNK=10 的块对齐窗口上界；vad.MAX_UTTERANCE_S=12 s）
//   课程名 80 字符、译名 8 条（prompt_core.TERM_ZH_MAX）
//   三条布局取最大：块对齐定稿 / 只矫正 / 草稿
export const MAX_USER_SOURCE = { actual: 9533, terms: 60, ctx: 19, utt: 300 };
export const MAX_USER_CHARS = MAX_USER_SOURCE.actual * 2;

// 术语候选那条的上限，算法同翻译（actual × 2）。actual 真拼一次算的：用 `prep._extract_user`
// 把「一条占满 `CHUNK_CHARS`」的最坏一块拼出来。旁边另给一个**按常量算**的上界（bound）——
// `CHUNK_CHARS` + `_chunks` 每块的 16 字符记账差 + 抬头余量。两者不许倒挂：
// actual > bound 说明上面那几条注释里的常量过期了，`--check` 会直接报出来。
export const PREP_MAX_USER_SOURCE = { actual: 30102, bound: 34016, chunk: 30000, header: 4000 };
export const PREP_MAX_USER_CHARS = PREP_MAX_USER_SOURCE.actual * 2;
