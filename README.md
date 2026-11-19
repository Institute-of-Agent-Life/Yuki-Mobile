# Yuki Mobile

Yuki 的 Android 與 iPhone 客戶端。第一階段的目標是在手機上接回桌面發起的同一項 Work，取回原版本成果。

目前可執行 Git 暫存區掃描與驗收證據去識別測試。需要 Git 和 Python 3.9 或以上版本。

```sh
python3 tools/check_repo.py
python3 tools/redact_evidence.py --self-test
```

掃描報告寫入 `acceptance/out/repo-check.json`。候選包、公開證據和外接卷的操作方式見[發布指南](docs/publishing.md)。

授權採用 [AGPL-3.0-only](LICENSE)。
