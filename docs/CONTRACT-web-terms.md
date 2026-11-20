# 术语上云 · 接口契约（W19 服务端 / W20 客户端共用，2026-11-19；W24 扩三层同）

身份 = 通行码（与 /v1/web/session 完全同一套鉴权：Bearer 通行码 + X-ClassLive-Client: web + Origin 白名单 + X-ClassLive-Install 校形状）。

## 个人术语（权威）
- `GET /v1/web/terms` → 200 `{ "courses": { "<课名>": { "text": "<一行一个>", "updated_at": <epoch ms> } } }`（没有就 `{courses:{}}`）
- `PUT /v1/web/terms/:course`（course 做 encodeURIComponent），body `{ "text": string, "share": boolean }`
  → 200 `{ "course", "updated_at" }`。text 空串 = 删掉这门课。
  校验：课名与开课同一套规则（1–80 字）；text ≤ 20000 字符；按行拆、trim、去空行、去重后 ≤ 500 条，单条 ≤ 80 字、去控制字符；不合法 400 `{"error":"bad_body"}`。幂等。
- 草稿（空课名）不上云。

## 共享池（只是加分项，不影响个人术语）
- 只对「课号」开：course_key = 课名去空格转大写，且匹配 `^[A-Z]{2,5}\d{4,5}[A-Z]?$`（如 ECON10770）；自由文字课名不进池。
  subject = 课号的字母前缀（ECON10770 → ECON）；field = 前缀的大类（见下表）；未知前缀 → field null，不进大类池。
- PUT 时 share=true：把这门课的术语记成这枚码（codeHash16）的贡献，覆盖它之前的贡献；share=false 或 text 空 → 撤掉它的贡献。
  存储键 `share:<课号>:<codeHash16>`（W19 起就没动过）。
- `GET /v1/web/terms/shared?course=<课名>` → 200
  `{ "course_key": string|null, "subject": string|null, "field": string|null,
     "terms": [{ "term": string, "users": number, "tier": "course"|"subject"|"field" }] }`
  不是课号 → `{course_key:null, subject:null, field:null, terms:[]}`。
- 三层（越远越杂，所以门槛越高、条数越少 —— Soniox context 会把识别往这些词上拉）：
  - `course`：同一课号，≥2 个不同码，最多 200
  - `subject`：同前缀的**其它**课号（不含本课），≥2 个不同码，最多 100
  - `field`：同大类的**其它**课号前缀（不含本前缀），≥3 个不同码（= ≥3 门课写过），最多 60
- 排序：先按 tier（course → subject → field），层内按 users 降序、同数按字母序。
- 跨层去重：term 按 小写 + 折叠空白 比较，同词只出现一次，**保留更近的层**。三层相加 ≤ 300。
- 一次请求最多 list SHARED_LIST_MAX = 80 个课号池（现在整张表 44 个前缀，到不了；超了按字母序截断 + 一行日志）。
- 不返回谁贡献的，只返回人数。旧回包兼容：客户端把没有 tier 的条目当 `course`。

## 大类表（来源 = UCD 课号前缀；常量在 `server/asr-worker/src/web-terms-core.js` 的 `FIELD_PREFIXES`）
- stem：MATH STAT PHYS CHEM COMP EEEN MEEN CVEN CHEN BMOL BIOL BIOC GEOL ENVB ACM MST EGEN
- business：ECON ACC FIN MKT MGT BMGT BUS BANL MIS IB ENT HRM
- health：MDSA PHPS NMHS PHAR RDGY PHYC VET
- social：SOC PSY POL HIS PHIL LAW ARCH GEOG
