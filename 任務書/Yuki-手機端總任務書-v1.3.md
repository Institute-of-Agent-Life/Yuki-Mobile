# Yuki 手機端總任務書 v1.3

整理日期：2026-11-15（America/Los_Angeles）。本版取代 v1.2、v1.1 和 v1.0（v1.0 的原件在 `Yuki-手機端交付包-v1.0.zip`）。v1.3 依審閱意見修正 QA-003 的循環、A08／A09 的驗收條件與真機 HTTPS 測試入口，並把 Android A01 單列為里程碑 M1（見 §3）。

最近修訂：2026-11-17。補齊測試服務、桌面交接與正式候選包的驗收條件，並記錄 GitHub 倉庫的公開決定。

**範圍（自 v1.2 起）：只寫入 Yuki-Mobile。** Yuki 本體和 Yuki-Desktop 都以固定版本唯讀使用，不改程式、不改測試、不開分支。產品只裝在本人的設備上，原始碼上傳 GitHub。

本輪交付的是審核後的架構裁決與提交級任務書。它不包含手機程式、部署或真機通過證明。本輪實際執行過的驗證列在 §2。

## 1. 決策與第一個可用版本

**保留原 roadmap 的 S4／S5／S6。** 第一條驗收是：

> 桌面建立伺服器 Work → 關閉桌面 App → Android 配對到同一原 App 入口 → 接回原 Work → 補充要求 → 取回原 Work 成果的原版本檔 → 桌面重開看到同一結果。

iPhone 基本版消費同一份公開合同。Android 與 iPhone 基本版**各自**驗收，彼此不是前置；手機執行器有自己的交付門檻；需要改 Yuki 的系統能力（R-D1）與推播（R-P）暫緩。

推薦 Kotlin／Jetpack Compose 與 SwiftUI。兩端共用的是 MOB-001 鎖定的合同、golden vectors，以及 QA-001 的驗收；不先建跨平台狀態機框架。

Yuki 後端保有身份、原入口、事件、Main、Work 與回執的權威。手機持有自己的裝置憑據與本機狀態，不保存模型 API Key，不管理伺服器 Memory，也不重建 Work 引擎。設備執行器與聊天各自配對、各自撤銷。

首版（R-A、R-I）**不需要改 Yuki**，包括：安全配對與撤銷、同入口歷史、前景與回前景補讀、每入口持久 outbox、Work 詳情與定向補充／取消、聊天附件、不可變成果下載、錯誤與未知狀態。首版不含：推播、跨 App 操作、SOUL 編輯、電腦接手、完整記憶管理。這些另有後續卡或暫緩卡，不從 roadmap 刪除。

**第一個可演示的里程碑是 M1：Android A01 真機演示。** 以 debug 包在本人 Android 真機上證明：桌面關掉後，手機接回同一 Work，兩端最後看到同一結果、下載到同版本的檔案（MOB-008/1）。M1 不是發布；R-A 候選包再以 release 包跑完整的 A01～A12（MOB-008/2）。

**M1 的實作範圍是 REPO-01、MOB-001～007、QA-001～003 與 MOB-008/1。** 它用真實後端的原 Work、原回執與原檔案驗證 A01。QA-002 以腳本化模型控制流程，手機、桌面、HTTP、資料庫與檔案路徑都用實際組件。

## 2. 審閱邊界與本輪證據

| 對象 | 固定提交 | 本輪實際證據 |
| --- | --- | --- |
| Yuki | `08164f6732c14fca0396eb3411f8a82d6762aa8c` | 本機 HEAD 與遠端 main（`git ls-remote`）相同。讀了 Client API 全文、HTTP 路由與回應模型、identity／credentials／requests／works／views／errors、參考 client、隔離後端與故障 fixture、NativeConnector 的 `computer` 派發。**匯出 OpenAPI，sha256 `14fd970b…c101`。** **以倉庫外 venv 執行 31 個既有測試，全部通過，exit 0。** API revision 12（`client_api/version.py`） |
| Yuki-Desktop | `a5687a66cf74485c94394e1008ddfbc0ebd926a2` | 本機 HEAD 與遠端 main 相同。`contracts/source.json` 鎖定 Yuki `df133fbc`、rev12，sha 與上面的匯出**逐字節相同**。讀了 outbox、fingerprint、message 正規化、SSE、上傳查證、E2E 後端啟動方式。**未執行 Desktop 測試** |
| Mika | `10b5712e30d6d10ace588bac08a1aad0fc6057ce` | 唯讀淺取。讀了 Manifest、gradle、無障礙設定、虛擬顯示、接手、第三方清冊。**遠端 main 已前進到 `eb10b349`**，本輪沒有改追 |
| Poki | `2743a6b62bdb346a9bb1c1ee51c37ae0c5cd61d0` | 唯讀淺取，等於遠端 main。讀了在機架構、真機前提、簽名、驗證範圍、entitlements、第三方清冊 |

v1.2 是範圍調整，沒有新增執行；上表證據沿用 v1.1 本輪的結果。本輪**沒有**做的事：編譯 Android／iOS、執行 Desktop E2E、操作真機、重讀 Google／Apple 政策頁。

**更正（v1.3）：** v1.1 回報「沒有寫入 Yuki 工作樹」不正確。當時 venv 放在暫存目錄，但 `uv sync` 以 editable 方式安裝 Yuki，`hatch_build.py` 把 `native.o`、`vec1.dylib` 編進了 `src/yuki/_native/`（2026-11-15 20:59 建立）。這些檔被 `.gitignore` 遮住，所以 `git status` 沒有顯示。v1.3 審改時發現後已刪除；刪除後，Yuki 自本會話開始以來沒有新建或修改的檔案，HEAD 仍是 `08164f67`。Desktop 沒有寫入。之後的測試環境改用 `git archive` 匯出的副本（見提交級任務書的共用測試環境）。

## 3. 修正紀錄

### 2026-11-17 驗收與倉庫設定

- QA-003 以測試服務的原回執核對實際發送與簽發的桌面裝置，並以手機 `/me.binding_id` 核對交接入口。A08 與 A09 所需的群 Work、來源禁用和降權操作由 QA-002 提供。
- Android 與 iPhone 的骨架階段各自記錄真機 HTTPS 連通證據。M1 使用 debug 包，基本版候選驗收使用實際要交付的 release 包。
- Yuki-Mobile 的 GitHub 倉庫能見度定為公開。

### v1.2 → v1.3：審閱修正

| v1.2 的問題 | 實查 | v1.3 |
| --- | --- | --- |
| QA-003 的完成條件要等手機跑完 A01，而 MOB-008／013 又以 QA-003 為前置，形成循環 | QA-003 的門檻寫著「手機下載的雜湊」 | QA-003 分 `before`／`phone`／`after` 三段，`phone` 段由 `reference` 替身手機自驗；A01 只在 MOB-008／013 判定 |
| A01 寫成「桌面發訊息後才觸發劇本」 | `desktop_fault_fixture.py:317-334` 在 arm 時記下 `after_event_id`，只接之後的新事件 | 改為先 arm 再送訊息；QA-002 回傳 `armed_after_event_id` |
| A08 用 `__test/qq-entry` 驗群 Work | `seed_qq_entry`（`desktop_reference_server.py:76-110`）只建私聊事件；群 Work 只有進程內測試 `[group]` | QA-002 新增 `__test/qq-group-work`；A08 驗本人補充成功、nonowner 被拒、結果仍投遞原群；實際 QQ 群另跑 |
| A09 把降權也寫成 `401` | `/me` 以 `is_superuser` 投影目前權限（`client_api/identity.py:465-492`），裝置不失效 | 撤銷驗 `401`；降權驗 `/me` 回 `200`、`is_superuser:false`，並清受限快取 |
| 真機走區網代理與 debug 信任的測試 CA | 候選包是 release 信任設定；A03 要切到行動網路後連回同一網址，區網位址做不到 | QA-001 新增固定網址、公開可信憑證的 HTTPS 測試入口（§7）；MOB-002／009 在骨架階段就以真機在兩種網路下讀 `/meta` |
| 沒有早期可演示的成果 | — | MOB-008 拆為 /1（M1，debug 包 A01）與 /2（候選包） |

### v1.1 → v1.2：只動 Yuki-Mobile

| v1.1 | v1.2 |
| --- | --- |
| BE-001 改 Yuki `tests/support/` | 改為 **QA-002**（Yuki-Mobile）：在手機端組自己的測試服務，引用 Yuki 的 ApplicationContainer、`DesktopFaultFixture`、`publish_artifact`、`merge_persons`（已核可行，見提交級任務書 QA-002）。代價是 Yuki 升版時可能要跟著調整，但只影響測試 |
| DESK-01 在 Desktop 新增 Playwright spec | 改為 **QA-003**（Yuki-Mobile）：用既有桌面 App 照導引操作，runner 以 API 和三方雜湊核對；可選 `/2` 在 Yuki-Mobile 驅動已建好的 Desktop |
| DEV-008 是 Yuki 整合測試 | 改為 Yuki-Mobile 對真實後端的黑箱測試；發現後端缺口就記為 blocked，不繞過 |
| DEV-004（鬧鐘資源）、PUSH-01（推播）改 Yuki | **暫緩**；連同 DEV-001、DEV-005、PUSH-02、PUSH-03 移入暫緩卡，R-D1、R-P 暫不交付 |
| R-D2 的 connector 基座在 DEV-005 | 新增 **DEV-009**：Android 獨立 connector 加上本 App fixture 的 `computer` 操作 |
| 沒有建倉與發佈規則 | 新增 **REPO-01**：AGPL-3.0-only、`.gitignore`、機密掃描、證據去識別、CI |
| 三層門檻含「公開發布」 | 只裝在本人設備，改成兩層：測試環境就緒、候選包通過 |

### v1.0 → v1.1

| v1.0 的寫法 | 查到的事實 | v1.1 的修正 |
| --- | --- | --- |
| MOB-001 在 Yuki 新建 `tools/mobile_contract/export.py` | 已有 `client_openapi()`／`yuki client openapi`；Desktop 已鎖定同一份檔 | MOB-001 移到 Yuki-Mobile，鎖定現有匯出，不寫入 Yuki |
| MOB-001 的驗收是 A01～A12 | A 場景要等手機實作才能跑，形成循環 | 改為 contract-check；A01～A12 對既有測試只算「合同覆蓋」 |
| QA-001 同時寫 Yuki 與 Mobile，並自建後端 harness | Yuki 已有 `desktop_reference_server` 與大部分故障點 | QA-001 只寫 Yuki-Mobile，消費現有 fixture 並加 HTTP 故障代理；缺的兩個 fixture 另立 BE-001（v1.2 改為 QA-002） |
| DESK-01 依賴 MOB-001、QA-001 | Desktop 有自己的合同檢查與真後端 E2E | 只加交接 E2E（v1.2 改為 QA-003，不寫入 Desktop） |
| 「同一原入口」由配對來源決定 | 裝置不保存 binding；預設入口是碼所屬 Person 的 App binding。operator 碼可能為 root 新建入口 | A01 的碼必須在 B-A 下以 `/link-codes` 簽發；手機核 `/me.binding_id` |
| 丟應答時同號續送的條件不明 | 後端明示 messages／inputs／cancel／uploads 同號冪等；Desktop 與參考 client 在 `found:false` 時原號續送 | 寫成明確規則（矩陣 §4）；`/pair` 沒有可查回執 |
| SSE 只有 conversation／work／activity | 實際 7 種 event，含 `refresh`；`id:` 每連線從 1 起；串流佔用電腦裝置連線計數 | 未知 event 一律觸發重讀；背景時關閉串流 |
| payload_hash 只寫「同一 golden vector」 | 正文經 Python `str.strip()`，與 Kotlin／Swift 的 trim 空白集合不同 | golden vectors 由後端程式產生，涵蓋空白邊界 |
| cancel 衝突後「新請求」 | 失敗也以原 UUID 記成回執 | 原號只會重放失敗；新意圖用新 UUID 並再確認 |
| 第一個設備能力是 `clipboard.write/readback` | #299 §5.7 與 C40～C44 已訂下 S6 走「系統 Intent 優先」（以鬧鐘為例），結果分四態；剪貼簿沒有合同 | R-D1 回到鬧鐘 Intent 加讀回（DEV-001／004／005） |
| 界面操作要另建合同 | protocol 4 已有 `computer` 資源（截圖、元素、點擊，journal 與 unknown） | R-D2 沿用它；新增 DEV-008，以假手機 connector 的測試確認缺口 |
| DEV-004 前置含 MOB-001、QA-001 | 它是 Yuki 內部的工作 | 只保留條件前置：DEV-001 或 DEV-003 |
| DEV-005 依賴 MOB-008；DEV-007 依賴 MOB-013 | connector 用獨立憑據與 target，不需要基本版候選包 | 移除；發布順序與技術前置分開寫 |
| 各發布只有一個「門檻」 | 環境就緒、候選包通過、公開發布三件事被混在一起 | 分層判定（v1.2 只剩前兩層） |
| Mika「未證實使用無障礙」、Apache 授權 | Manifest 宣告無障礙服務並可執行手勢；第三方含 GPL-2.0、LGPL-3.0 | 平台調研 §5.2、§8 改寫 |
| Poki「新版在機 App」 | 在機路徑依賴 NE 隧道或外部 VPN、WDA／XCTest、Developer Mode；第三方含 PolyForm Noncommercial | 平台調研 §6、§8 改寫 |

另外，桌面舊 09 任務書有三處要修正：

- 它列的 `X-Yuki-Client: android/<ver>` 在後端沒有任何消費者（全庫搜尋為零），所以不送。
- 它寫的「C10 能力名稱為開放字串（`alarm.set`）」屬於 revision 7 已退役的 LocalExecutor，S6 改走 NativeConnector（§10）。
- 它對 MO-01 的完成條件要求「OpenAPI 含 SSE 提示 schema」，但目前 OpenAPI 仍沒有 SSE，所以由 MOB-001 從源碼補寫 `contract-lock/sse.md`；這不阻擋開工。

v1.0 中有證據支持的設計保留不變：原 ID 保留、Work 選擇與活動清單分離、四種事實分開、附件與成果分開、推播只是提示、操作要獨立授權、Rust 遷移只靠公開合同。

## 4. 承接 S4／S5／S6 與 MO-01～05

原 ROADMAP 工作樹不在附件中，這一節依三份文件承接：Yuki #299 任務書（`docs/architecture/Yuki-控制面重构与多客户端接入任务书-2026-10-14.md`）的 §5.7、§9.4，C17 與 C40～C44；以及 Desktop 的 `docs/09-手機接續與共用協定任務書.md`。三份本輪都已讀。

| 原項目 | 本輪歸宿 | 完成判準 |
| --- | --- | --- |
| S4 Android 個人 UI | R-A：MOB-002～008 | 真實後端上的跨端閉環與拒絕案例通過 |
| S5 桌面個人 UI | 既有消費者，不改；QA-003 在手機端做交接核對 | 同一事件、回執與檔案版本 |
| S6 Android 設備執行器 | R-D2：DEV-002／008／009／006；R-D1（DEV-001／004／005）暫緩 | 界面操作（C43）先交付；系統能力（C40～C42、C44）需改 Yuki，暫緩 |
| MO-01 共用合同 | MOB-001（鎖定現有匯出） | 首版 DTO、錯誤、SSE 與 golden vectors 出自同一來源 |
| MO-02 配對連結 | MOB-003、MOB-009 | 網址／貼上／QR 共用 parser；保留路徑前綴 |
| MO-03 推播 | PUSH-01～03（暫緩） | 需改 Yuki；v1.2 以回前景補讀代替 |
| MO-04 手機 App | R-A、R-I、EXT-01～04 | 分批交付 |
| MO-05 設備執行 | DEV-002／003／006～009；DEV-001／004／005 暫緩 | 聊天憑據與操作憑據分離 |

## 5. 倉庫與責任

| 倉庫 | 角色 | 寫入範圍 |
| --- | --- | --- |
| [Yuki-Mobile](https://github.com/Institute-of-Agent-Life/Yuki-Mobile)〔數字生命研究所，公開〕 | 全部現行卡：REPO-01、MOB-*、QA-001～003、DEV-002／003／006～009、EXT-* | `android/`、`ios/`、`contract-lock/`、`acceptance/`、`experiments/`、`research/`、`tools/`、`.github/` |
| Yuki | 固定 SHA 的唯讀依賴：OpenAPI 匯出、測試組件、參考 client | **無**（暫緩卡 DEV-004、PUSH-01 啟用時才會寫入） |
| Yuki-Desktop | 固定 SHA 的既有 App 與唯讀參考 | **無** |
| Mika／Poki | 唯讀研究 | 無；不 fork，也不複製 |

建議分支（只在 Yuki-Mobile）：`feat/contract-lock`、`feat/acceptance`、`feat/android-client`、`feat/ios-client`、`exp/android-operator`、`exp/ios-operator`。本輪沒有建立任何分支或 commit。

在 Yuki-Mobile 裡**不得**用以下方式繞過後端：
- 直連 Yuki 的 SQLite（QA-002 測試服務內部除外，而且只用於測試）；
- import Yuki 的模型到正式 App；
- 自己重算授權或完成狀態。

將來的 Rust 核心只要通過同一套公開 HTTP、事件、原回執合同、MOB-001 golden vectors 與 QA-001 驗收即可接入。

## 6. 架構與資料所有權

```text
Android UI / iPhone UI / Desktop UI
  └─ 個人 Client API /client/v1：身份、原入口、歷史、Work、回執、附件、成果
       └─ Yuki：Person / Binding / Conversation / Main / Work / Social / Memory
            └─ 本人設備 NativeConnector /agents/v1（另配對、另授權，protocol 4）
                 └─ computer 資源：觀察 → 動作（先 journal） → 讀回查證 → 停止／接手
```

手機只持有：裝置 token 的安全引用、每入口的草稿與 outbox、事件快取、持久游標、所選 Work ID、下載快取。這些都是伺服器事實的投影。

原始識別碼全部保留：`device_id`、`person_id`、`source_person_id`、`request_person_id`、`binding_id`、`conversation_id`、`request_id`、`event_id`、`input_id`、`work_id`、revision、`media_id`、`artifact_id`、artifact version。欄位是否存在、是否可空，依鎖定的 OpenAPI；不以這份清單增加 wire 欄位。

螢幕上所選的 Work 與「目前活動的 Work」分開。補充或取消一律指向按鈕所在卡片的原 Work。

## 7. 配對與安全

手機先讀 `/meta`，再讓使用者確認配對。伺服器位址保留路徑前綴。正式 build 只接受 HTTPS，只靠系統預設信任鏈。開發例外只在 debug 設定裡、只對 localhost 開，給模擬器和 `adb reverse` 用；真機（包括 debug 包）一律走下面的 HTTPS 測試入口，不安裝自訂 CA。

**真機 HTTPS 測試入口（QA-001 建，MOB-002／009 在骨架階段用真機驗）：** 隔離後端與故障代理只監聽本機，前面接一個固定主機名、憑證能被系統預設信任鏈驗證的 HTTPS 入口。手機以**同一個網址**從 Wi-Fi 與行動網路讀 `/meta`（A03）。測試控制路由 `__test/*` 與原始狀態檔只供本機 runner 使用。
- **建議預設：** 手機與開發機加入同一個 Tailscale tailnet，用 `tailscale serve` 把本機代理發佈為 `https://<機器>.<tailnet>.ts.net/role/test`。憑證由 Let's Encrypt 為 ts.net 名稱簽發，入口只對 tailnet 內的裝置可見。
- **替代：** 自有網域加公開 CA 憑證，再接反向隧道。入口會暴露在網際網路上，需要另加存取控制，也不能放真實資料。
- 兩者的可行性都要由 QA-001 在真機上實查，本輪沒有驗。另一個待查的限制：iOS 同一時間只能有一條 VPN 生效（DEV-003 實查）。如果 R-DI 的 A17 要走 Poki 的 packet tunnel，就會和 Tailscale 搶同一條 VPN；屆時 A17 改用自有網域方案。

**同一入口的條件：** 碼必須在目標入口下以 `POST /link-codes` 簽發。手機配對後讀 `/me`，核對 `binding_id`；不一致時明示，並以 `X-Yuki-Entry` 選回，選不回就停止派發。operator 對已歸並的 root 簽發的碼可能新建入口，這不屬於「同一入口」的流程。

`yuki://pair?server=…&code=…`、QR、整則貼上共用一個 parser。深層連結不能直接執行工作或控制手機。碼與 token 不進剪貼簿歷史、分析平台、崩潰報告或診斷附件。

Android 以 Keystore 保護的金鑰加密 token；iOS 用 Keychain，設 `ThisDeviceOnly`。正式版不得在備份中洩漏 token。

**配對回應丟失：** 後端的 pair 回執沒有客戶端可查的 request_id，所以原碼不能再兌換。畫面顯示「可能已建立裝置，憑據未保存」與本機標籤短碼；由已授權的裝置從 `/me.devices` 定位孤兒裝置、撤銷後再發新碼。新碼成功時，不自動刪除其他同名裝置。

所有操作資格都由伺服器依目前 Person 與原資源判定。401 停止派發；403 隱去受限功能並重讀身份；草稿保留，但不換帳號重放。

## 8. 連線與 outbox

連線狀態：`unpaired → connecting → syncing → ready`；暫時網路錯誤轉 `offline`；憑據失效轉 `auth_required`；版本不合轉 `incompatible`。

回前景的順序：
1. 核對實例與憑據；
2. `/me` 查實際入口；
3. 每個入口用自己的 outbox，帶原 `X-Yuki-Entry` 查 `/requests/{id}`；
4. 建立 `/stream` 並消費 sync；
5. 補讀 Conversation 與已開啟的 Work；
6. 更新畫面。

`refresh` 與未知 event 一律觸發重讀；串流結束後先 GET `/me`。

outbox 沿用四態：`draft`、`submitted`、`accepted`、`result`。首次送出前，在同一交易裡寫入 UUID、endpoint、target、正規化 payload 與 payload_hash；寫入失敗就不送。提交後不改 payload。

**丟應答的判斷**（完整版見矩陣 §4）：
- 先 GET 原請求。`found:true` 時核對操作、目標與 hash。
- `found:false` 且本機是 submitted 時，以原號原內容續送，這是後端明示的冪等路徑。
- 查詢失敗或 `unknown` 時保留 submitted。
- `request_conflict`／`precondition_failed` 是終態，原號不再使用。

cancel 帶使用者確認過的 `expected_revision`。衝突的回執會綁在原 UUID 上，新的取消意圖要用新 UUID，並再確認一次。

手機在背景不承諾持續 SSE。伺服器上的 Work 照常繼續；推播只是提示。

## 9. 附件、成果與交付

上傳以原始 bytes 送 `POST /uploads`，query 帶原 UUID、摘要、長度、檔名與 kind；成功取得 `media_id`，再由 `/messages.attachments` 引用，才算構成訊息。重新上傳前，先確認本機原件的摘要沒有變。

聊天原件走原 Conversation／event／attachment_index 的端點。`media_id` 不是 `artifact_id`；`segment_index` 也不是附件序號。

成果用 `/artifacts/{id}?version=<sha256>`，依原 Social 或 Work 結果的引用取精確版本。核對 `X-Yuki-Sha256`，並自行計算 SHA-256。工作目錄的檔案後來更新，也不改讀新版本。原件被清理後如實顯示。

## 10. 設備操作的最小閉環

依 #299 §5.7：Main 負責理解與選擇；手機只承接設備操作。結果分四態：已交給系統、等待用戶處理、已確認（有讀回）、未知。

**現行（不改 Yuki）：界面操作，沿用現有 protocol 4 的 `computer` 資源。** Main 的 `computer` 工具帶 `device`；輸入先寫 journal 再執行，中斷記為 unknown；能力用 `features` 宣告（`local_device.py:86-135`）。
- DEV-008 用假手機 connector 對真實後端做黑箱驗證。發現缺口就停止，記下源碼證據，R-D2 和 R-DI 隨之 blocked。
- DEV-009 在真機的本 App 計數器 fixture 上，跑「讀 → 點 → 讀回」與 crash gap。
- DEV-006 擴到指定的 fixture App，或 trusted 虛擬顯示（對應 #299 C43）。

**暫緩（需改 Yuki）：系統能力。** #299 的「系統 Intent 優先」與鬧鐘案例（C40～C42、C44）需要新增 connector 資源與 Main 工具的接法（DEV-004）。DEV-001 的手機端試作不改 Yuki，可以先做來累積證據。

不另建通用遠控服務、任意 shell 工具或第二套 Work 表，也不復活 LocalExecutor。設備憑據用 connector 用途，與聊天各自配對、各自撤銷。

接手時，先撤銷自動操作資格、阻止新的手勢，再交還控制。已經進入 OS 的動作不一定能撤回，畫面要保留這段未知窗口。交還之後必須重新觀察。

## 11. 獨立發布與門檻

| 發布 | 候選卡 | 必過場景 | 不作前置 |
| --- | --- | --- | --- |
| R-A Android 基本版 | MOB-008 | A01～A12（android） | iOS、操作器、暫緩卡 |
| R-I iPhone 基本版 | MOB-013 | A01～A12（ios） | Android 卡、Poki、Mac 中繼、暫緩卡 |
| R-D2 Android 界面操作 | DEV-006 | A16（C43） | 基本版候選包 |
| R-DI iPhone 界面能力 | DEV-007 | A17 | R-I |
| R-X 後續功能 | EXT-01～04 | A18～A20 | — |
| R-D1 系統能力〔暫緩〕 | DEV-005 | A14、A15（C40～C42、C44） | — |
| R-P 推播〔暫緩〕 | PUSH-02／03 | A13 | — |

R-A、R-I 的測試環境由各平台獨立確認。參考 client 的 smoke 通過後，`run.py env-check` 以該平台已裝在本人真機上的 release build，記錄 Wi-Fi 與行動網路各一次讀取同一 HTTPS `/meta` 的結果。

候選包必過場景全部為 `passed` 才算通過。R-A、R-I 使用實際要交付的 release 包跑 A01～A12。操作版按上表各自的場景與環境驗收。

只裝在本人設備，所以沒有商店送審這一層。Android APK 以本人金鑰簽名，可附在 GitHub Releases；iPhone 只用 Xcode 安裝，不發佈二進位檔。完整的前置閉包見[提交級任務書](手機端提交級任務書.md#必要前置閉包從修訂後表格逐層展開)。

每個候選包的證據要包含：後端、桌面、手機的 commit，OS build，機型，測試入口，腳本模型版本，以及去識別的原 ID 對照。附到 GitHub 之前，先經 REPO-01 的 `redact_evidence.py`。

## 12. 後端究竟改什麼

**v1.2 不改後端。** 原本需要後端配合的三件事，處理如下：

| 原需求 | v1.2 處理 | 對 Yuki 的依賴 |
| --- | --- | --- |
| Work 成果帶檔、第二原 App 入口（原 BE-001） | QA-002 在手機端組測試服務 | 唯讀引用 Yuki 的測試組件；固定 SHA |
| 手機界面操作的後端路徑（DEV-008） | 手機端黑箱驗證現行 `computer` 路徑 | 只呼叫公開路由；有缺口就 blocked |
| 系統能力資源（DEV-004）、推播（PUSH-01） | 暫緩 | 需新增 Yuki 產品能力，與 v1.2 範圍衝突 |

`revision=12` 是基線事實。未實作的能力不在 `/meta` 宣告，手機端也不預設它存在。

## 13. 後續功能保留

- **EXT-01：** 記憶列表、本人更正（SELF 唯讀）、安排列表與建立者可用的操作。
- **EXT-02：** 角色公開資料；SOUL／頭像編輯只對目前的超管開放，並沿用 CAS。
- **EXT-03：** 角色電腦的觀看與接手，沿用現有的 ticket、租約與撤權。
- **EXT-04：** 系統分享、相機、按住錄音，沿用原附件與轉寫語義。

每張 EXT 卡的 `/a`、`/i` 子提交分別建立在 R-A／R-I 之上。

## 14. 首批並行與需要你決定的事

**開工順序：**
1. **先合入 REPO-01**（倉庫第一個 commit：授權、`.gitignore`、機密掃描、CI 骨架）。
2. **之後立即可並行：** MOB-001、QA-001、QA-002、DEV-002、DEV-003。Android 骨架也可以同時開工。MOB-002 在 QA-001 的 `env-check` 就緒後完成真機檢查並合入。
3. MOB-001、QA-001 和 QA-002 合入後，兩端驗配對與 A09。Android 還需要 MOB-002。QA-003 在 QA-001、QA-002 合入後，用參考 client 完成桌面交接自驗。
4. **第一個演示目標是 M1**（MOB-008/1）：MOB-006、MOB-007、QA-003 合入後，用 debug 包在 Android 真機上跑 A01。之後才做 MOB-008/2 的候選包。
5. DEV-001（鬧鐘試作，暫緩卡）不改 Yuki，可以隨時做，但只是累積證據。

**暫待真機或環境：**
- A01～A12 的手機一側，需要本人的 Android 與 iPhone 真機。
- HTTPS 測試入口要在真機上以 Wi-Fi 和行動網路各驗一次（MOB-002／009）。
- A08 的 fixture 不需要 QQ；實際 QQ 群投遞需要本人授權的測試群，記為獨立證據。
- A16 需要支援 trusted 虛擬顯示的 Android 14+ 機型。
- A17 需要 Developer Mode 與簽名。

**需要你決定、會影響開工的選擇：**

1. **Yuki-Mobile 的授權。** 預設 `AGPL-3.0-only`，與 Yuki、Desktop 一致；倉庫會收錄 Yuki 匯出的 OpenAPI 與 golden vectors，測試服務也引用 Yuki 的組件。若要改用別的授權，REPO-01 就得改成不提交這些衍生檔，改在 CI 生成。這會影響 REPO-01，以下皆屬工程建議，不是法律意見。
2. **Apple 開發者會員。** 免費 Personal Team 需要定期重新 provision（Poki README 記為 7 天）；付費會員可以減少重簽。只影響 iPhone 的安裝維護，不影響開工。
3. **Android 操作版是否另裝獨立 APK。** 建議接受，這樣普通版不帶任何操作權限。
4. **真機 HTTPS 測試入口。** 建議用 Tailscale Serve。也可使用自有網域加受限的反向隧道。選好後由 QA-001 檢查入口，再由 MOB-002／009 各自完成真機連通驗證。

最低系統：Android API 30、iOS 18 只是**候選工程目標**，由 MOB-002／009 依真機凍結。

## 15. 配套文件與來源

- [手機端提交級任務書](手機端提交級任務書.md)：每張卡的倉庫、前置、路徑、失敗情境、命令與產物。
- [跨端介面與驗收矩陣](跨端介面與驗收矩陣.md)：實際端點、重試判斷、場景、合同覆蓋與發布映射。
- [平台調研與分發裁決](平台調研與分發裁決.md)：Mika／Poki 的實讀源碼、授權、政策與發布邊界。
- `文件校驗.py`：可重跑的靜態驗證。它解析四份 Markdown，核對任務 ID、「現行卡全屬 Yuki-Mobile」、直接與條件依賴、無環、各發布的前置閉包（不含暫緩卡）、場景映射、合同列、相對連結與錨點。`python3 文件校驗.py --write` 會重寫 `文件校驗報告.json` 與 `SHA256SUMS.txt`；報告同時記錄本輪實際執行的證據。

[Y1]: https://github.com/YuanYeYouTao/Yuki/blob/08164f6732c14fca0396eb3411f8a82d6762aa8c/docs/architecture/client-api.md
[Y2]: https://github.com/YuanYeYouTao/Yuki/blob/08164f6732c14fca0396eb3411f8a82d6762aa8c/src/yuki/client_api/http.py
[R1]: https://github.com/YuanYeYouTao/Yuki/issues/299
[R2]: https://github.com/YuanYeYouTao/Yuki/issues/301
