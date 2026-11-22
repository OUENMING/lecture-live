// 说话人标注（桌面 3.13.0 搬到网页）：rt.py 封口句带 Soniox 编号与字母、投影把字母挂到行上。
// rt.py 这段用 CPython 跑（与 Pyodide 同一份源码；纯标准库）。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { test } from "node:test";
import {
  EMPTY_PROJ,
  project,
  type Snapshot,
} from "../../src/lib/core/project.ts";

const corePy = path.resolve(import.meta.dirname, "../../static/core-py");

const PY = `
import json, sys
sys.path.insert(0, ${JSON.stringify(corePy)})
import rt
c = rt.RealtimeCore()
def tok(t, s, sp): return {"text": t, "start_ms": s, "end_ms": s + 250, "is_final": True, "speaker": sp}
msgs = [
  [tok(w, i * 300, "1") for i, w in enumerate([" Today", " we", " talk", " about", " market", " power."])],
  # 换人、没有句末标点：也得在换人处断开
  [tok(" Can", 3000, "2"), tok(" you", 3300, "2"), tok(" repeat", 3600, "2"), tok(" that", 3900, "2"),
   tok(" Sure", 4500, "1"), tok(" yes.", 4800, "1")],
  [tok(" No", 6000, "")],  # 没标签的 token：不算说话人
]
out = []
for m in msgs:
    out += json.loads(c.feed(json.dumps({"tokens": m})))["sealed"]
out += json.loads(c.flush())["sealed"]
print(json.dumps([[x["text"].strip(), x["speaker"], x["spk"]] for x in out]))
`;

test("rt.py：主讲不标、别人发 B、换人处断句、没标签不标", () => {
  const run = spawnSync("python3", ["-I", "-c", PY], { encoding: "utf8" });
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(JSON.parse(run.stdout), [
    ["Today we talk about market power.", "1", ""],
    ["Can you repeat that", "2", "B"],
    ["Sure yes.", "1", ""],
    ["No", "", ""],
  ]);
});

test("投影：只有带字母的行才有 spk 键（主讲行与旧快照逐字不变）", () => {
  const snap: Snapshot = {
    now: 5,
    draft: "",
    draft_start: null,
    n_msgs: 1,
    seq: 0,
    notice: null,
    sealed: [
      { text: "a", lang: "", start: 0, end: 1, speaker: "1", spk: "" },
      { text: "b", lang: "", start: 1, end: 2, speaker: "2", spk: "B" },
      { text: "c", lang: "", start: 2, end: 3 },
    ],
  };
  const { view } = project(EMPTY_PROJ, snap);
  assert.deepEqual(
    view.rows.map((r) => r.spk),
    [undefined, "B", undefined],
  );
  assert.equal("spk" in view.rows[0], false);
});
