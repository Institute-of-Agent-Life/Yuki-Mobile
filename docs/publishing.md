# 發布候選包與驗收證據

## 候選包

Android 候選 APK 用本人金鑰在本機簽名，可附到 GitHub Releases。簽名材料留在本機。

基本版候選包以實際要交付的同一份已簽名 release 包重跑 Wi-Fi 與行動網路的 `env-check`，再跑 A01～A12。比對各次證據的 build SHA-256，確認必過案例皆為 `passed`。

## 整理公開證據

保留原始 manifest、配對交接檔、私聊內容、網址、原始識別碼及截圖在本機。`acceptance/out/` 用於存放本機驗收輸出。對每份要分享的 manifest 產生一份新的公開 JSON。

```sh
python3 tools/redact_evidence.py acceptance/out/A01/run-manifest.json acceptance/out/A01/public-manifest.json
```

輸入是 UTF-8 JSON 物件，必填的 `case_id` 為 `A01`～`A20`、`smoke`、`desktop-handoff` 或 `env-check`。`status` 為 `passed`、`failed`、`blocked` 或 `not_run`。指定尚未存在的輸出檔名。

工具只輸出公開欄位。版本、型號或雜湊欄位格式有誤時，工具只回報欄位名稱並停止輸出。

- `started_at` 為含時區的時間。`client_platform` 為 `android`、`ios`、`desktop` 或 `reference`，`build_type` 為 `debug` 或 `release`。
- `device_model`、`os_build` 和 `script_model_version` 填機型、OS build 與腳本模型版本。標籤以英文字母或數字開頭，最多 64 個 ASCII 字元，可含空格、逗號、小數點、加號、底線、連字號與括號。
- `backend_commit`、`desktop_commit`、`mobile_commit` 填完整 40 位十六進位 commit。`build_sha256`、`file_sha256`、`artifact_sha256` 填完整 64 位十六進位雜湊。
- `entry_kind` 為 `app`、`chat`、`qq_private` 或 `qq_group`。`network` 為 `wifi` 或 `cellular`。`tls_trusted` 和 `test_route_blocked` 使用 JSON 布林值。
- `id_aliases` 填案例內預先分配的別名，例如 `"work_id": "work-2"`。可用的 ID 類型為 `work`、`binding`、`conversation`、`request`、`event`、`artifact`、`attachment`、`operation`、`person` 與 `device`，格式為類型加連字號及 1～999 的流水號。只有頂層 `work_id` 等原 ID 時，工具在該案例內以 `work-1` 等別名表示。別名與原 ID 的對照留在本機。
- `test_entry_alias` 填 `entry-1` 至 `entry-999`。只有 `test_entry` 或 `server_url` 原值時，工具在該案例內以 `entry-1` 表示。原始網址與路徑前綴留在本機。

公開 JSON 省略步驟、預期、觀察、重跑命令、路徑與圖片。上傳前核對公開欄位確實是機型、版本與案例別名，並以完整雜湊對回候選包。要附截圖時另存新副本，遮蔽私聊、帳號、裝置資訊、網址、條碼與 QR 碼，檢視處理後的像素及檔案中繼資料。

## 檢查提交內容

準備好要提交的檔案後，在倉庫內執行以下命令。

```sh
python3 tools/check_repo.py
python3 tools/redact_evidence.py --self-test
```

掃描器讀取 Git 暫存區的檔名和 blob，檢查被強制加入的 AppleDouble、`.DS_Store`、簽名檔、私密設定與本機驗收輸出。內容規則辨識有值的裝置 token、配對碼、私鑰標頭和常見 Authorization 驗證標頭。檢出時只回報規則與安全的相對路徑。`acceptance/out/repo-check.json` 記錄結果，結束碼為 `0` 通過、`1` 檢出、`2` 掃描或寫報告失敗。掃描範圍為目前 Git 暫存區。自我測試使用臨時倉庫和合成資料。

CI 執行相同命令與測試。MOB-001 提供 `tools/contract_lock.py` 後，CI 會以固定 SHA 將 Yuki 和 Yuki-Desktop 檢出到 runner 上的獨立目錄，再匯出 Yuki 原始碼副本執行相容檢查。MOB-002 提供 `android/gradlew` 後，CI 會建置 debug APK。

## 本機故障驗收與 HTTPS 入口

將固定 Yuki `6cf15321` 匯出到倉庫外的 APFS 目錄，並在匯出目錄的 `.yuki-commit` 記錄完整 SHA。令 `YUKI_BACKEND_TEST_ROOT` 指向該目錄，`YUKI_BACKEND_TEST_PYTHON` 指向以固定依賴安裝的 Python 3.12 虛擬環境。

```sh
python3 acceptance/run.py --case smoke --client reference --server reference --output acceptance/out/smoke
python3 tools/redact_evidence.py acceptance/out/smoke/run-manifest.json acceptance/out/smoke/public-manifest.json
```

查閱 `harness-smoke.json` 的原請求回執、retry 查找時序及上游轉發計數。此命令啟動隔離後端和本機代理，結束時清理測試環境。多個案例的 `run-summary.json` 列出各案狀態，要分享證據時使用各案例的 `run-manifest.json`。

將固定 HTTPS 主機的反向隧道接到本機手機代理。以下用 18770。入口允許登記的測試來源，從根路徑轉送並保留 `/role/test` 前綴。Android 以普通 HTTPS 連線。

```sh
python3 acceptance/run.py serve --server reference --port 18770
```

在另一個終端驗證同一個 HTTPS base URL。以環境變數提供 URL，保留路徑前綴。

```sh
python3 acceptance/run.py env-check --public-url "$YUKI_DEVICE_TEST_URL" --output acceptance/out/host-endpoint
```

`device-endpoint.json` 記錄 Python 預設信任鏈、`/meta`、測試控制路徑及非公開路徑的 HTTP 結果。缺少可用入口時回傳非零並記為 `blocked`。MOB-002 記錄 Android 真機的 Wi-Fi 和行動網路初驗。MOB-008/2 用最終簽名 APK 複驗。

## 在外接非 APFS 卷開發

在外接卷上的 checkout 使用以下設定。此例將建置目錄指向本機 APFS。

```sh
git config --local core.fileMode false
export COPYFILE_DISABLE=1
export YUKI_MOBILE_BUILD_ROOT="$HOME/Library/Caches/Yuki-Mobile"
mkdir -p "$YUKI_MOBILE_BUILD_ROOT"
```

Android 專案建好後，依 MOB-002 提供的設定入口，把各模組的 Gradle `buildDir` 指到本機 APFS 下的獨立目錄。
