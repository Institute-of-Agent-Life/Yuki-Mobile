# QA-003 Desktop 交接

這個自驗用固定版 Desktop 和 reference client 經 QA-002 fixture 交接同一筆 Work。兩端使用 QA-001 代理的本機 loopback 網址。Android A01 由 MOB-008 在真機上驗收。

## 準備

依 [QA-002 說明](../server/README.md) 設定 Yuki 固定版和 Python 虛擬環境。使用 Desktop `a5687a66cf74485c94394e1008ddfbc0ebd926a2`。從 Desktop 原始碼建置時，把該提交匯出到 APFS 暫存目錄，在匯出目錄執行 `npm ci` 與 `npm run build`。

從 Yuki-Mobile 根目錄啟動 runner。如有對應的固定版 Desktop Git checkout，可將 `YUKI_DESKTOP_ROOT` 指到該 checkout，讓 runner 將核對過的 commit 寫進報告。

~~~sh
python3 acceptance/run.py --case desktop-handoff --client reference \
  --server mobile-fixture --output acceptance/out/desktop-handoff
~~~

首次開啟時用 `YUKI_DESKTOP_DATA` 指到一個空的測試 profile。建置版可用下列命令啟動，`desktop_build` 指向匯出的 Desktop 目錄，`desktop_profile` 指向本次測試專用的目錄。

~~~sh
YUKI_DESKTOP_DATA="$desktop_profile" "$desktop_build/node_modules/.bin/electron" "$desktop_build"
~~~

## 依提示操作

1. `before.pair`。在 Desktop 選「連接已有 Yuki」。用 runner 顯示的 Client URL 和 state 檔的 `owner_pairing_code` 配對，輸入 Electron 主程序的 PID。state 檔限本機使用。
2. `before.send`。在桌面對話送出 runner 顯示的完整 Message，回終端按 Enter。runner 會核對先 arm、後送出，以及原 Work 的觸發回執。
3. `before.link`。打開 Desktop 設定的「裝置」，選「產生綁定碼」。把完整連結貼到 runner 的隱藏輸入。
4. `before.close`。用 ⌘Q 結束 Desktop，等主程序退出後按 Enter。runner 會核對 Work 仍在等待。Reference client 會兌換 Desktop 簽發的碼，補充 Work、恢復遺失回覆的請求，再下載工作區同名檔已改寫後的原成果版本。
5. `after.reopen`、`after.save`。用同一個 profile 重開 Desktop，輸入新的主程序 PID。打開「原工作整合測試」，確認已完成且只出現一筆「已依原工作完成」。打開該結果的 `same.md`，點「存到…」，輸入實際另存檔的絕對路徑。
6. `after.screenshot`、`after.confirm`。截取顯示原 Work 與單筆結果的 Desktop 畫面，輸入 PNG 的絕對路徑。確認畫面一致後輸入 `yes`，保持 Desktop 執行至 runner 結束。

`desktop-handoff.json` 記錄配對碼反查、A05 同號重放、A10 三方 SHA-256 與畫面確認結果。`run-manifest.json` 記錄整次驗收，`desktop-result.png` 保存重開後的畫面。用獨立測試 profile 截圖，只保留這次的合成測試內容。

## MOB-008 接入

沿用 `before` 與 `after`。將 QA-001 的 HTTPS 入口傳入 `before(..., client_url=...)`，讓 Desktop 簽發的連結和 QR 帶入該網址。真機階段接收 `HandoffContext`，以手機實際配對的 ID 和 `/me` 呼叫 `verify_phone_pair`，完成補充後用 `publish_and_change` 確定成果版本和工作區變更。下載原版本並回傳 `PhoneDownload`，由 `after` 驗證兩端的獨立檔案。
