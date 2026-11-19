# SSE 與 HTTP 補充合同

本頁補齊固定 Yuki `6cf15321` 的 OpenAPI 未描述的串流事件、回應欄位和 HTTP 標頭。以下路徑均以 `/client/v1` 為前綴。完整的請求與回應模型見 [rev14 OpenAPI](client-api.openapi.json)。

## 前景串流

`GET /stream` 使用裝置憑據，可帶 `works=active`。成功回傳 `text/event-stream`，快取控制為 `no-cache`。每次連線從 `id: 1` 開始，`data:` 帶 JSON。後端等待提示 15 秒超時時寫出 `: keepalive` 註解，事件序號維持原值。[HTTP 路由][stream-http]、[事件產生器][stream]

| `event` | `data` | 觸發條件 |
| --- | --- | --- |
| `sync` | 當前的 `conversations`、`works`、`activity` 及可用的角色、電腦欄位 | 連線起始，或有項目從快照移除 |
| `refresh` | `{}` | 後端發出變更提示，快照內容相同 |
| `computer` | `{"revision": ...}` | 電腦 revision 變更 |
| `character` | 角色提示投影 | 角色提示變更 |
| `conversation` | 一筆帶 `conversation_id` 的對話摘要 | 該摘要新增或變更 |
| `work` | 一筆帶 `work_id` 的 Work 提示 | 該提示新增或變更 |
| `activity` | 一筆帶 `conversation_id` 的活動提示 | 該提示新增或變更 |

手機收到已知或未知事件時，以最新的 HTTP 投影和持久歷史游標補讀。SSE 序號的有效範圍是本次連線。斷線及回前景後先讀 `/me`，再重讀快照與歷史。若後端遇到 `ClientError`，串流直接結束並清除訂閱。讀取 `/me` 回 `unauthenticated` 時停止派發並引導重新配對。[快照與串流][stream]、[歷史分頁][history]、[本機收斂規則](source-map.md)

## 其他 HTTP 回應及請求

| 路徑 | 合同 |
| --- | --- |
| `DELETE /devices/{identity}` | 手機送 JSON `{"request_id":"<原 UUID>"}`，指定 `Content-Type: application/json`。HTTP 200 的 `data` 包含 `device_id`、`revoked: true` 和 `replay` 布林值。回應遺失時用原入口查 `/requests/{id}`。 |
| `GET /artifacts/{identity}?version=<sha256>` | `application/octet-stream`。回應帶強 `ETag`（雙引號的 SHA-256）、`X-Yuki-Sha256` 及 `Content-Disposition`。比對版本、兩個雜湊標頭和實收 bytes。 |
| `GET /conversations/{identity}/events/{event_id}/attachments/{attachment_index}` | `application/octet-stream`。雜湊來自強 `ETag`，另有 `Content-Disposition` 和可選的 `X-Yuki-Expires-At`。用強 `ETag` 核對實收 bytes。 |
| `POST /uploads` | `request_id`、`sha256`、`size`、`filename` 和 `kind` 在查詢參數。body 為串流讀取的原件 bytes。上傳回執可用原 UUID 查證。 |

撤銷的 `replay` 由後端專屬回執路徑決定。成果的 `version` 定位到不可變的檔案版本。事件附件的 `attachment_index` 對應訊息中的附件順序。[撤銷路由][revoke]、[撤銷回執][identity-revoke]、[上傳與下載路由][assets]

## 錯誤與寫入結果

`ClientError` 的實際 JSON 使用 `problem.code`，並可帶該錯誤的詳細欄位。用 HTTP 狀態辨識傳輸結果，以 `code` 決定客戶端轉移。[錯誤處理器][errors]

| HTTP 狀態 | 常用 `problem.code` |
| --- | --- |
| 401 | `unauthenticated` |
| 403 | `forbidden`、`owner_required` |
| 404 | `not_found` |
| 422 | `validation_error` |
| 503 | `unavailable` |
| 500 | 寫入結果不確定時的 `unknown` |
| 409 | 其他 `ClientError`，例如 `request_conflict`、`precondition_failed`、`receipt_conflict`、`observe_only`、`deny_only` |

FastAPI 產生的 OpenAPI 將 422 記為 `HTTPValidationError`。固定後端的 `/client/v1/`、`/agents/v1/` 路由會由驗證處理器回傳 `{"problem":{"code":"validation_error"}}`。故障 fixture 也會用 HTTP 503 回 `unknown`。回應遺失或收到 `unknown` 時，在原入口查同一 UUID 和 `payload_hash`。[實際驗證回應][errors]、[故障 fixture][faults]

[stream-http]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L539-L545
[stream]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/views.py#L343-L432
[history]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/views.py#L192-L261
[revoke]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L665-L671
[identity-revoke]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/identity.py#L523-L572
[assets]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L452-L515
[errors]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http_errors.py#L19-L63
[faults]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/support/desktop_fault_fixture.py#L194-L205
