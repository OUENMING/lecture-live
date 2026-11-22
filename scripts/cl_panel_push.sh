#!/bin/zsh
# 把 Mac 本机的会话名 + 上传台账推给 VPS 上的远程只读面板（scripts/cl_panel_remote.py --import-local）。
# launchd 每 10 分钟跑一次（~/Library/LaunchAgents/page.bldcam.cl-panel-push.plist）；Mac 合盖期间不推，
# 远程摘要顶上写着最后推送时刻。只读 sessions/ 的文件名和 ~/.classlive/upload-ledger.json，不碰内容。
set -eu
cd "${0:A:h}/.."
/usr/bin/python3 - <<'PY' | /usr/bin/ssh -o ConnectTimeout=10 -o BatchMode=yes bldcam \
  '~/cl-panel/venv/bin/python ~/cl-panel/scripts/cl_panel_remote.py --import-local'
import json, pathlib
names = sorted({p.name.split(".")[0] for p in pathlib.Path("sessions").iterdir()})
try:
    ledger = json.loads((pathlib.Path.home() / ".classlive" / "upload-ledger.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    ledger = {}
print(json.dumps({"sessions": names, "ledger": ledger if isinstance(ledger, dict) else {}}, ensure_ascii=False))
PY
