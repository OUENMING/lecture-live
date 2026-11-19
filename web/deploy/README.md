# deploy —— class.bldcam.page（VPS + nginx，Cloudflare 灰云直连）

产物是纯静态 SPA（`web/build/`）。目录：`nginx-class.bldcam.page.conf` 站配置、`publish.sh` 发版/回滚。

## 首次上线（按顺序）
1. VPS 上装 nginx + certbot；`ufw allow 80,443`；DNS 给 `class` 加 A 记录指向 VPS，**灰云（DNS only）**。
2. 先拿证书（此时 80 端口还没被占用也能过 http-01）：
   `sudo certbot certonly --nginx -d class.bldcam.page`
3. 放配置：`sudo cp nginx-class.bldcam.page.conf /etc/nginx/sites-available/` →
   `sudo ln -s /etc/nginx/sites-available/nginx-class.bldcam.page.conf /etc/nginx/sites-enabled/` →
   `sudo nginx -t && sudo systemctl reload nginx`。
4. 发第一版：本机 `web/` 下 `deploy/publish.sh`（会 `npm run build` 再 rsync）。
5. 验收：`curl -sI https://class.bldcam.page/ | head -1` 200；`curl -sI https://class.bldcam.page/vendor/pyodide/314.0.8/pyodide.asm.wasm`
   应是 `200` + `content-type: application/wasm` + `cache-control: ... immutable`。
6. 证书续期由 certbot 的 timer 负责，`systemctl list-timers | grep certbot` 确认。

## 回滚
- `deploy/publish.sh --rollback` → `current` 切回上一版（静态文件，nginx 不用 reload）。
- 手动：`ls ~/classlive-web/releases` 选一版，`ln -sfn ~/classlive-web/releases/<版> ~/classlive-web/current.tmp && mv -Tf ~/classlive-web/current.tmp ~/classlive-web/current`。

## 注意
- `/v1/`、`/soniox/` 两段反代**默认注释掉**；现在网页直连 `asr.bldcam.page` 与 `stt-rt.soniox.com`，国内实测后再开。
- `/vendor/` 缓存一年 immutable：换 Pyodide / pdf.js 版本时路径里的 `<ver>` 会变，不会撞缓存。
- 发版保留最近 5 版，`/vendor/` 从上一版复用（不重复传 14 MB）。
