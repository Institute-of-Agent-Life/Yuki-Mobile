# 手機端合同來源

[source.json](source.json) 鎖定 Yuki `6cf15321` 匯出的 rev14 合同。固定 Desktop `a5687a66` 的 `contracts/source.json` 鎖定 Yuki `df133fbc` 的 rev12 合同。Desktop 在 `/meta` 比對的執行門檻取自 `REQUIRED_API_REVISION`，該常數在固定提交為 **11**。匯出合同版本與執行門檻各有自己的來源。[Yuki 匯出器][export]、[Desktop 合同來源][desktop-lock]、[Desktop 執行門檻][desktop-rev]、[連線檢查][connection-meta]

欄位型別、必填、可空值及 HTTP 參數以 [rev14 OpenAPI](client-api.openapi.json) 為準。[SSE 與 HTTP 補充合同](sse.md) 記錄 OpenAPI 未表達的事件、標頭及錯誤。手機 DTO 應容許未知欄位。後端同時有允許額外欄位的 `PublicProjection`，以及直接繼承 `BaseModel` 的 `Meta`、`PublicStep` 等模型。[回應模型][projections]、[Meta][meta]、[PublicStep][steps]

## Desktop 對照

| 合同 | Desktop 使用點 | Yuki 路由、模型與測試 |
| --- | --- | --- |
| C01 公開資訊與配對 | [Pairing.tsx][pairing]、[shared/pairing.ts][pairing-parser]、[connections.ts][connection-meta] | [`/meta`][meta-http]、[`/pair`][pair-http]、[一次性碼與獨立裝置測試][pair-test]。保留 URL 的服務路徑前綴。手機 parser 拒絕重複的 `server` 或 `code`。 |
| C02 身份、簽發與撤銷 | [DeviceSecurity.tsx][device-view]、[outbox/link-codes.ts][link-code] | [`/me`、`/link-codes`、`/devices/{identity}`][identity-http]、[`Me`][projections]、[Desktop 裝置安全測試][device-test]。桌面簽發連結帶 `server` 和 `code`，配對後由 `/me.binding_id` 核對所在入口。 |
| C03、C04 普通訊息與原請求查證 | [outbox/index.ts][outbox]、[outbox/fingerprint.ts][desktop-hash]、[outbox/work-commands.ts][work-outbox] | [`/messages`、`/requests/{id}`][message-http]、[原入口回執][receipt]、[兩裝置 outbox 測試][continuation]。以原入口、原 UUID、operation、target 及 hash 核對；`found:true` 只收斂本機狀態。 |
| C05、C06 歷史與串流 | [connections.ts][connection-stream] | [`/home`、事件頁及 `/stream`][history-http]、[快照、歷史與 revision][views]、[斷線補讀測試][sse-test]。事件頁保留 `generation`、`revision`、`next_after`、`next_before`、`latest_event_id` 及 `has_more`。 |
| C07、C08 Work 詳情、補充及取消 | [Works.tsx][works-view]、[outbox/work-commands.ts][work-outbox] | [Work 路由][work-http]、[`Work`][work-model]、[`PublicStep`、`StepDetail`][steps]、[原 Work 續做測試][work-test]。保留舊 `category`、`label`，並辨識新增的委派與子任務欄位。取消以 `expected_revision` 和原 UUID 綁定原 Work。 |
| C09 原件上傳 | [media/uploads.ts][uploads] | [`/uploads`][asset-http]、[分塊寫入及回執][upload-source]、[遺失上傳回執測試][upload-test]。以原 UUID 查回 `media_id` 及可用性。 |
| C10、C11 附件和原版本成果 | [media/content.ts][media-content]、[Files.tsx][files-view]、[connections.ts 的內容請求][connection-content] | [附件與成果路由][asset-http]、[不變版本測試][artifacts-test]。附件核對強 `ETag`，成果另外核對 `X-Yuki-Sha256`。 |

C12 的委派清單與直接回答，以及 C13 的原生設備公開 HTTP 路由，都在鎖定的 rev14 合同中。C13 的 WebSocket 握手以 protocol 5 接入，對應後續設備卡。C14 推播暫緩，基本版以回前景補讀驗收。[委派路由][work-http]、[設備 HTTP 路由][external-http]、[protocol 5 測試][external-test]

## 首版六種回執

`identity.fingerprint` 對 `[operation, target, payload]` 作排序鍵、緊湊分隔及 Python ASCII 跳脫的 JSON，接著計算 SHA-256。以 [payload vectors](golden/payload-hash.json) 與 [文字正規化 vectors](golden/text-normalize.json) 驗證跨平台實作。文字欄位使用 Python `str.strip()` 的範圍。JSON 中 U+007F 生成 `\u007f`，非 BMP 字元使用代理對。[後端 fingerprint][identity-hash]、[Desktop 編碼][desktop-hash]

| operation | target | 正規化 payload | 權威來源 |
| --- | --- | --- | --- |
| `message` | 原 `conversation_id` | `text`、`attachments`，選填 `agent_target` | [訊息 payload][message-payload]、[查證的 operation][receipt] |
| `client.work_input` | 原 `work_id` | `text`，選填去除 null 欄位的 `agent_target` | [補充回執][input-hash]、[payload helper][agent-target] |
| `client.work_cancel` | 原 `work_id` | `expected_revision` | [取消回執][cancel-hash] |
| `client.upload` | `principal.request_scope`。App 入口取原 `request_person_id`，ACP 入口取原 `conversation_id` | `filename`、`kind`、`sha256`、`size` | [上傳回執][upload-source]、[原入口範圍][request-scope] |
| `client.revoke` | 被撤銷的 `device_id` | `{}` | [撤銷回執][identity-revoke] |
| `client.link_code` | 原 `request_person_id` | `source_binding_id`。簽發 connector 碼加 `purpose: "connector"` | [簽發回執][issue-code]、[裝置簽發入口][issue-device] |

[export]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/openapi.py#L16-L37
[desktop-lock]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/contracts/source.json
[desktop-rev]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/shared/api.ts#L8
[connection-meta]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/main/connections.ts#L948-L964
[projections]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/responses.py#L9-L55
[meta]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/models.py#L30-L36
[steps]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/management_models.py#L163-L211
[pairing]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/renderer/views/Pairing.tsx
[pairing-parser]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/shared/pairing.ts
[meta-http]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L206-L222
[pair-http]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L641-L647
[pair-test]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/unit/test_client_identity.py#L34
[device-view]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/renderer/views/DeviceSecurity.tsx#L88-L99
[link-code]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/main/outbox/link-codes.ts
[identity-http]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L649-L671
[device-test]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/device-security.spec.ts
[outbox]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/main/outbox/index.ts#L175-L200
[desktop-hash]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/main/outbox/fingerprint.ts#L3-L21
[work-outbox]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/main/outbox/work-commands.ts
[message-http]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L615-L639
[receipt]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/requests.py#L239-L257
[continuation]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_continuation.py#L89
[connection-stream]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/main/connections.ts#L1280-L1326
[history-http]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L492-L545
[views]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/views.py#L192-L432
[sse-test]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_continuation.py#L190
[works-view]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/renderer/views/Works.tsx
[work-http]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L547-L614
[work-model]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/responses.py#L144-L177
[work-test]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_continuation.py#L236
[uploads]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/main/media/uploads.ts#L146-L176
[asset-http]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L452-L515
[upload-source]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/conversation/uploads.py#L56-L82
[upload-test]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/unit/test_desktop_fault_fixture.py#L95
[media-content]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/main/media/content.ts
[files-view]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/renderer/views/Files.tsx
[connection-content]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/src/main/connections.ts#L872-L912
[artifacts-test]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/unit/test_desktop_fault_fixture.py#L155
[external-http]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/external_agents/http.py
[external-test]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_external_agent_protocol5.py
[identity-hash]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/identity.py#L61-L64
[message-payload]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/prompts.py#L47-L56
[input-hash]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/works.py#L787-L801
[agent-target]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/agent_target.py#L89-L90
[cancel-hash]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/works.py#L984-L1008
[request-scope]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/identity.py#L97-L103
[identity-revoke]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/identity.py#L523-L572
[issue-code]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/identity.py#L318-L322
[issue-device]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/identity.py#L364-L382
