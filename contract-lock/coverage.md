# A01～A12 合同覆蓋

合同編號沿用 v1.4 跨端介面與驗收矩陣。下表對照固定 Yuki `6cf15321` 與 Desktop `a5687a66` 已有的測試。A01～A12 的手機驗收由各平台任務卡執行。[合同來源](source-map.md)、[HTTP 補充合同](sse.md)

| 場景 | 合同 | 既有 Yuki 測試 | 既有 Desktop E2E |
| --- | --- | --- | --- |
| A01 接回原 Work 及原成果 | C01–C08、C11 | [兩次冷啟動續接原 Work][work]，涵蓋 App、QQ 私聊和群入口 | [Work 補充][work-ui]、[連線恢復][connection-ui] |
| A02 同入口配對與精確撤銷 | C01、C02、C04 | [一次性配對碼與獨立裝置撤銷][pair] | [QR 配對、撤銷與遺失回應][device-ui] |
| A03 換網後補回最後提示 | C01、C05、C06、C07 | [最後提示遺失後由真實 SSE 補讀][sse] | [連線恢復][connection-ui] |
| A04 回前景或程序重啟後收斂 | C02、C05、C06、C07 | [後端程序重啟後讀到已提交結果][restart] | [重啟][restart-ui] |
| A05 原補充回應遺失 | C03、C04、C08 | [兩台裝置與持久 outbox][message]、[Work 回應遺失的精確故障點][work-loss] | [Work 補充及原回執][work-ui] |
| A06 同一 Person 的兩個原入口 | C02、C03、C04、C07、C08 | [保留兩個 App 原入口][entry]、[原 UUID 作用域][uuid]、[歸並後續接原 Work][merged] | [入口選擇][entry-ui] |
| A07 版本衝突和指定 Work 取消 | C04、C07、C08 | [同一原入口查到取消回執][cancel]、[取消回應遺失][work-loss] | [Work 原請求][work-ui] |
| A08 QQ 群 Work 投影與原群投遞 | C02、C04、C07、C08 | [私聊與群入口的程序內續接樣本][work]。QA-002 的記錄型群投遞另驗。 | [入口選擇][entry-ui] |
| A09 裝置撤銷、人物停用及降權 | C01、C02、C04、C07 | [來源停用、已撤銷裝置][revoke]、[App 入口停用時保留人物及原 Work][disabled-app] | [管理權限][authority-ui]、[登出][logout-ui] |
| A10 取回原件和原版本 bytes | C04、C09、C10、C11 | [原檔案讀取權限][foreign-file]、[清理後回執重放][expired-upload]、[附件與成果的故障測試][artifact-fault] | [成果][artifact-ui]、[附件][media-ui] |
| A11 安裝、TLS 與路徑前綴 | C01、C02 | [固定 API revision 與 `/meta`][meta]。真機信任鏈、雙網連通與代理白名單在手機卡驗收。 | [伺服器設定][server-ui] |
| A12 歷史修訂、刪除與快取清理 | C05、C06、C07、C10 | [刪除提示遺失後從真實資料收斂][delete-hint] | [後端歷史][backend-ui]、[連線恢復][connection-ui] |

C12 角色、電腦與委派屬擴充卡，C13 設備執行屬設備卡。C14 推播暫緩，A04 使用 C05、C06 補讀。此區分沿用 v1.4 的發布門檻。

## 本機重跑

在固定 Yuki 的倉外 APFS 匯出副本執行下列測試，觀察到 **35 passed、exit 0**，其中四項是 protocol 5 測試。SQLAlchemy 報出三次循環外鍵排序警告。表中的 Desktop E2E 為來源對照，本卡未執行。

```sh
cd "$YUKI_BACKEND_TEST_ROOT"
export COPYFILE_DISABLE=1 PYTHONDONTWRITEBYTECODE=1
"$YUKI_BACKEND_TEST_PYTHON" -m pytest -p no:cacheprovider \
  --basetemp="$YUKI_BACKEND_TEST_ROOT/../pytest-tmp" -q \
  -m 'not native_worker and not real_agent' \
  tests/integration/test_client_continuation.py \
  tests/integration/test_client_person_identity.py \
  tests/integration/test_desktop_work_scenario_fixture.py \
  tests/unit/test_desktop_fault_fixture.py \
  tests/integration/test_external_agent_protocol5.py
```

在 Yuki-Mobile 執行合同查核。CI 使用固定上游檢出與 `YUKI_BACKEND_TEST_ROOT`。本機可用相同鎖定環境再匯出並比對 golden vectors。

```sh
python3 tools/contract_lock.py export --yuki-python "$YUKI_BACKEND_TEST_PYTHON"
python3 tools/contract_lock.py check --desktop-root "$YUKI_DESKTOP_ROOT"
python3 tools/contract_lock.py golden --yuki-python "$YUKI_BACKEND_TEST_PYTHON" --check
```

報告寫入 `acceptance/out/contract-check.json`。它記錄上游提交、revision、OpenAPI SHA-256、相容模式及檢查結果。

固定 Desktop `a5687a66` 的 rev12 通過相容超集檢查。開工時另對遠端 main `d947c3c1` 作唯讀檢查，其 rev14 合同與固定 Yuki 的匯出逐位元組相同，通過同 revision 檢查。[遠端 Desktop 合同來源][desktop-main-lock]

[work]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_continuation.py#L236
[pair]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/unit/test_client_identity.py#L34
[sse]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_continuation.py#L190
[restart]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_continuation.py#L527
[message]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_continuation.py#L89
[work-loss]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/unit/test_desktop_fault_fixture.py#L214
[entry]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_person_identity.py#L59
[uuid]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_person_identity.py#L136
[merged]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_person_identity.py#L350
[cancel]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_person_identity.py#L442
[revoke]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_person_identity.py#L389
[disabled-app]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_person_identity.py#L558
[foreign-file]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_person_identity.py#L463
[expired-upload]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/integration/test_client_person_identity.py#L302
[artifact-fault]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/unit/test_desktop_fault_fixture.py#L95-L190
[meta]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/src/yuki/client_api/http.py#L206-L222
[delete-hint]: https://github.com/YuanYeYouTao/Yuki/blob/6cf15321575391f217d1c9129e6f56e0010b09b9/tests/unit/test_desktop_fault_fixture.py#L234
[work-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/work-inputs.spec.ts
[connection-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/connection-recovery.spec.ts
[device-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/device-security.spec.ts
[restart-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/restart.spec.ts
[entry-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/entry.spec.ts
[authority-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/management-authority.spec.ts
[logout-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/logout.spec.ts
[artifact-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/artifacts.spec.ts
[media-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/media.spec.ts
[server-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/server-settings.spec.ts
[backend-ui]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/a5687a66cf74485c94394e1008ddfbc0ebd926a2/tests/e2e/backend.spec.ts
[desktop-main-lock]: https://github.com/Institute-of-Agent-Life/Yuki-Desktop/blob/d947c3c1d67477073574af1c45cb7daed4a42864/contracts/source.json
