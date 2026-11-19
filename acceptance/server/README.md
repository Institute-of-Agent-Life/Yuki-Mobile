# Android 驗收測試服務

QA-002 使用固定 Yuki `6cf15321575391f217d1c9129e6f56e0010b09b9` 的真實身份、SQLite、Work、Social 與成果儲存。依賴版本是 Client API rev14 和 NativeConnector protocol 5。模型與 OneBot 使用本機測試替身。

## 啟動

先依任務書匯出固定版 Yuki 到 APFS 目錄，在根目錄寫入 `.yuki-commit`。將 `YUKI_BACKEND_TEST_ROOT` 指到該目錄，將 `YUKI_BACKEND_TEST_PYTHON` 指到以固定依賴建立的 Python 3.12 虛擬環境。從 Yuki-Mobile 根目錄執行：

```sh
qa002_dir="$(mktemp -d -t yuki-mobile-fixture)"
PYTHONDONTWRITEBYTECODE=1 "$YUKI_BACKEND_TEST_PYTHON" acceptance/server/yuki_fixture_server.py \
  --root "$qa002_dir/data" --state-file "$qa002_dir/state.json" \
  --port 0 --base-path /role/test
```

以 state 檔的 `base_url` 找到 loopback 服務。state 檔含測試配對碼，留在本機。手機側連到 QA-001 的白名單代理。本機 runner 直連隔離後端操作 `/role/test/__test/*`。

## 測試控制

| 路由 | 用途 |
| --- | --- |
| `POST /__test/work-scenario-artifact` | 建立不可變成果快照，再 arm 下一封符合條件的 App 訊息 |
| `POST /__test/qq-group-work` | 經真實群訊息 ingress 建立等待補充的 Work。後續群送達由記錄型 OneBot 驗證 |
| `POST /__test/workspace-change/{artifact_id}` | 更改同名工作區檔案，供讀取原成果版本的測試 |
| `POST /__test/second-app-entry` | 先配對出第二個 App binding，再把來源 Person 歸並給 owner |
| `GET /__test/facts` | 讀 Work、原觸發事件與回執、成果版本與群送達摘要。以 `paired_device_id` 或 `code_id` 查配對碼簽發事實 |
| `POST /__test/superuser` | 切換 Person 的超管狀態 |
| `POST /__test/source-enabled` | 切換來源 Person 的可用狀態 |
| `POST /__test/model-script` | 設定下一段腳本模型工具呼叫 |

以 `{"person_id":"<fixture-person-id>","enabled":false}` 測來源禁用。`model-script` 接受 `{"tools":[{"name":"<tool>","arguments":{}}]}`，工具按序用於下一次符合條件的 App 訊息。可加 `person_id` 指定測試角色，預設為 owner。

## 驗證

```sh
PYTHONDONTWRITEBYTECODE=1 "$YUKI_BACKEND_TEST_PYTHON" -B acceptance/server/test_fixture_server.py
```

核對工作區同名檔變更後的原版本下載、第二個 App 入口、群 Work 補充後的投遞、精確配對回執、來源禁用和超管降權。整合報告放在 `acceptance/out/fixture-server.json`。
