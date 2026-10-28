# 貢獻指南 (Contributing Guide)

感謝您對 **JobMe** 專案的關注與貢獻！我們歡迎各種類型的貢獻，包括但不限於：回報 Bug、提出新功能建議、補充求職平台爬蟲、改善測試覆蓋率或完善文件。

---

## 🛠️ 開發環境設置 (Development Setup)

1. **Fork 並 Clone 專案庫**：
   ```bash
   git clone https://github.com/<your-username>/JobMe.git
   cd JobMe
   ```

2. **建立並啟用 Python 虛擬環境 (Python 3.10+)**：
   ```bash
   # Windows (PowerShell)
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

   # Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **安裝依賴套件與 Playwright Chromium 核心**：
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

4. **建立個人測試環境變數與設定**：
   ```bash
   cp .env.example .env
   cp config/rubric.example.md config/rubric.md
   cp config/profile.example.md config/profile.md
   ```
   > ⚠️ **注意**：請切勿將包含真實 API Key、私人 Discord Token 或個人履歷的檔案（`.env`, `config/profile.md` 等）提交至 Git。專案 `.gitignore` 已預設排除這些敏感檔案。

---

## 🧪 執行測試 (Running Tests)

在提交任何修改前，請確保執行內建自我驗證測試套件並全數通過：

```bash
python tests/test_self_check.py
```

測試驗證包含：
- 雙語評分規準解析器 (Rubric Parser)
- 雙門檻過濾與封存狀態機 (Dual Threshold Logic)
- 本地硬性篩選與黑名單 (Hard Filter)
- SQLite 去重與歷程追蹤 (SQLite Deduplication)
- 企業背景擴充與 TWSE 匹配 (Company Enricher)
- Fact Sheet 轉譯與格式化 (Translator)
- Jev 決策期望值計算 (Jev Evaluator)
- CSV 報表匯出 (Exporter)

---

## 🚀 提交 Pull Request (PR 規範)

1. **建立專題分支 (Topic Branch)**：
   ```bash
   git checkout -b feat/your-feature-name
   # 或
   git checkout -b fix/your-bug-fix
   ```
2. **遵循清晰的 Commit 訊息規範 (Conventional Commits)**：
   - `feat: 新增 Cake 平台薪資欄位萃取`
   - `fix: 修復 104 地區篩選代碼重疊問題`
   - `docs: 更新 README 中的 Discord 部署說明`
3. **保持最小改動與清晰邊界**：避免在單一 PR 中混入無關的重構或格式調整。
4. **提交 PR**：發送 Pull Request 至 `main` 分支，並在 PR 描述中清晰說明變更動機與驗證方式。CI 將自動執行全平台相容性測試。

---

## 🐛 回報問題與建議 (Issues)

歡迎直接至 GitHub Issues 提出回報或建議：
- 回報 Bug 請附上重現步驟、作業系統環境與錯誤訊息日誌。
- 提出功能建議請簡述需求痛點與預期解決方案。

謝謝您的參與，讓台灣求職者的自動化工具更加完善！
