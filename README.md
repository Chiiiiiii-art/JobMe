# JobMe: 智慧求職爬蟲與 Jev 雙階段決策推播系統

[![CI](https://github.com/WhitetapeOwO/JobMe/actions/workflows/ci.yml/badge.svg)](https://github.com/WhitetapeOwO/JobMe/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-green.svg)](https://playwright.dev/)

專為台灣求職市場打造的高效能、低成本自動化職缺監控與 AI 評估推播系統。整合主流求職平台、政府公開商工登記與雙階段決策漏斗，精準推送符合個人履歷與客觀標準的高價值職缺。

---

## 🌟 核心特色 (Core Highlights)

### 1. 雙階段漏斗評估管線 (Two-Stage Gating Pipeline)
為了兼顧評估深度與 API 成本，系統採用兩段式篩選機制：
- **本地硬性篩選 (Local Hard Filter)**：零成本即時排除不符關鍵字、指定行政區（如大安區、信義區）、實習/正職身份或負面黑名單詞彙之職缺。
- **Stage 1 履歷契合度把關 (Profile Fit Gatekeeper)**：對齊個人履歷背景 (`profile.md`)，由 Jev 模型判定方向吻合度（High / Medium / Low / Unrelated）。不相關職缺直接止步，**完全省下後續企業調查與翻譯成本**；判定歷程完整留存 `profile_fits.csv` 供稽核。
- **Stage 2 多維度量化深度評估 (Rubric Comprehensive Scoring)**：職缺完整內文標準化轉譯為英文 Fact Sheet，由 Jev 決策引擎依據雙語標準規準 (`rubric.md`) 計算各維度期望值分數。

### 2. 企業權威性客觀錨定與分級知識庫 (Corporate Authority Engine)
跳脫傳統僅由 LLM 自由發揮的主觀評估，引入多層次客觀市場數據交叉驗證：
- **確定性權威名單**：內建台灣 50 (0050)、14 大金融控股集團總部，以及 `companies.json` 一線外商與科技獨角獸白名單，自動錨定 Tier 1 / Tier 2。
- **官方商工資訊串接**：自動整合台灣證券交易所 (TWSE) 1,000+ 上市公司開放資料與 g0v 經濟部商工登記 API，動態查核實收資本額與登記現況。
- **DeepSeek 背景研究兜底**：針對中小型企業或特殊名稱，調用 DeepSeek 針對資本規模、市場聲譽與產業地位進行客觀分級。
- **企業檔案快取與動態校準 (`--retier`)**：所有公司背景快取於 SQLite，永不重複消耗額度；若調整評分權重，支援一鍵重算歷史全量職缺分數，無需重新抓取或重跑模型。

### 3. 多級容錯與自動降級決策鏈 (Resilient Multi-Provider Fallback)
確保 24/7 常駐監控不中斷，決策引擎支援即時無縫容錯：
- **優先決策鏈**：`OpenRouter (typesafe/jev-1.14)` ➔ `TypeSafe 官方原生` ➔ `DeepSeek 結構化決策` ➔ `本機啟發式保底`。
- **自動熔斷與告警**：遇到供應商額度超限 (429) 或網路異常時，毫秒級切換至備援鏈並發送 Discord 降級告警，保證巡檢任務順暢完成。

### 4. 五大求職平台全覆蓋與反爬防禦 (Full-Spectrum Scraping)
- **平台支援**：完整支援 104 人力銀行、Yourator、Cake (CakeResume)、1111 人力銀行、yes123。
- **跨平台職類映射 (`category_mapper.py`)**：自動對齊各平台原生職缺類別代碼與篩選參數。
- **公平交錯輪詢 (Fair Interleaving)**：跨平台職缺交錯進入評分佇列，杜絕單一平台筆數過多產生飢餓現象。
- **瀏覽器防護解鎖 (`--setup-browser`)**：針對 Cloudflare 或機器人驗證，提供互動式瀏覽器視窗供一次性手動驗證並保存工作階段 Cookie。

### 5. 跨裝置 Discord 互動控制台 (Remote Interactive Dashboard)
- **美觀富文本推播**：達標職缺即時推送 Discord Embed，條列維度得分雷達、企業 Tier、信心度指標與職缺直達連結。
- **雙向控制機器人**：於 Discord 頻道常駐互動中控台，支援按鈕與斜線指令（`/jobme`）：
  - ⚡ **立即巡檢**：不等待排程，背景即刻執行一輪抓取與評估。
  - 🟢 **啟動** / ⏸️ **暫停**：外出時一鍵調控背景常駐排程。
  - ⏱️ **調整頻率**：下拉即時變更巡檢週期（30分 / 1小時 / 2小時 / 4小時）。
  - 📁 **匯出報表**：機器人直接於頻道上傳最新 `records.csv` 與 `profile_fits.csv`。
  - 🗑️ **清除重置**：一鍵清空已讀紀錄，保留企業快取與認證 Cookie。

### 6. Chrome 瀏覽器外掛即時評測 (Real-Time Browser Extension)
- **當前頁面即時分析**：瀏覽 104、Cake、Yourator、1111、yes123 時，外掛自動從網頁 DOM 擷取職缺內文，零延遲繞過後端反爬阻擋。
- **歷史評分秒級快取**：若系統曾評測過該職缺，外掛彈窗直接顯示歷史得分、企業 Tier 與雷達各維度明細。
- **一鍵 Jev 決策與推播**：新職缺一鍵送測，自動執行 Stage 1 適配度與 Stage 2 深度規準評分，達標自動同步 Discord。

### 7. 雙重門檻與資料留存稽核 (Dual Threshold & Data Flow Audit)
- **封存門檻 (`base_threshold`)** vs **推播門檻 (`notify_threshold`)**：中等潛力職缺靜態封存於資料庫與 CSV，頂尖職缺才推播洗版，降低干擾。
- **全流程透明化**：內建 SQLite 追蹤、去重與狀態機；CSV 雙軌留存便於評估標準（Rubric）持續反思迭代。

---

## 🚀 快速開始

### 1. 安裝環境與相依套件
本專案建議使用 Python 3.10+：
```powershell
# 啟用虛擬環境
.\.venv\Scripts\Activate.ps1

# 安裝相依套件與 Playwright 瀏覽器核心
pip install -r requirements.txt
playwright install chromium
```

### 2. 設定環境變數 (`.env`)
複製 `.env.example` 為 `.env` 並填入金鑰：
```env
# Jev 決策模型金鑰 (優先使用 OpenRouter 或 TypeSafe)
OPENROUTER_API_KEY=your_openrouter_api_key_here
# TYPESAFE_API_KEY=your_typesafe_api_key_here

# DeepSeek 企業背景與規模分級金鑰 (必填)
DEEPSEEK_API_KEY=your_deepseek_api_key_here

# Discord 推播 Webhook (必填)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Discord 跨裝置互動機器人控制台 (選填，啟用遠端按鈕操控)
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_CHANNEL_ID=your_discord_channel_id_here
```

### 3. 設定個人履歷與評分標準 (`config/`)
複製範本檔案建立個人設定：
```powershell
Copy-Item config/rubric.example.md config/rubric.md
Copy-Item config/profile.example.md config/profile.md
```
- **`config/profile.md`**：填寫個人學經歷、核心技能與求職偏好，供 Stage 1 履歷契合度前置過濾。
- **`config/rubric.md`**：
  - Frontmatter：設定搜尋關鍵字、地點、行政區（如 `[信義區, 大安區]`）、排除詞、巡檢週期與雙門檻（`base_threshold` / `notify_threshold`）。
  - Markdown 本文：自訂各評分維度等級與權重（滿分 100 分）。

---

## 🏃 常用執行指令

### 1. 常駐自動巡檢模式 (Daemon Mode，推薦)
定時自動輪詢全平台新職缺；若設定了 `DISCORD_BOT_TOKEN`，會同步啟動 Discord 互動控制台：
```powershell
# 每小時自動巡檢一次 (預設同步啟動 API 伺服器，供 Chrome 外掛隨時連線)
.\.venv\Scripts\python.exe -m jobme --daemon

# 自訂輪詢間隔 (例如每 2 小時 / 7200 秒)
.\.venv\Scripts\python.exe -m jobme --daemon --interval 7200
```

### 2. 獨立啟動 API 伺服器 (供 Chrome 外掛使用)
若不進行背景自動輪詢巡檢，僅需提供瀏覽器外掛即時評估服務：
```powershell
.\.venv\Scripts\python.exe -m jobme --serve
# 預設監聽 http://127.0.0.1:8000
```

### 3. 單次完整執行
```powershell
.\.venv\Scripts\python.exe -m jobme
```

### 4. 指定爬取平台
平台選項：`104`, `yourator`, `cake`, `1111`, `yes123` 或 `all`：
```powershell
# 僅爬取 104 與 Yourator
.\.venv\Scripts\python.exe -m jobme --platforms 104,yourator
```

### 4. 測試與除錯模式
```powershell
# 限制評估數量 (例如僅評估最新 5 筆職缺)
.\.venv\Scripts\python.exe -m jobme --limit 5

# 模擬模式 (Dry-Run：不呼叫付費模型、不發送推播)
.\.venv\Scripts\python.exe -m jobme --dry-run

# 首次執行靜音模式 (將當前職缺入庫作為基底，不推播洗版)
.\.venv\Scripts\python.exe -m jobme --quiet-first-run
```

### 5. 企業分級重新校準 (`--retier`)
更新 `rubric.md` 中的企業權威性等級或分數後，一鍵重算歷史所有職缺得分並更新 CSV：
```powershell
.\.venv\Scripts\python.exe -m jobme --retier
```

### 6. 瀏覽器防護驗證解鎖 (`--setup-browser`)
若 Cake 或 1111 出現 Cloudflare 驗證阻擋，開啟真實視窗手動通過一次即可儲存 Cookie：
```powershell
.\.venv\Scripts\python.exe -m jobme --setup-browser
```

### 7. 清空歷史資料庫
清空 `jobs.db` 與 `records.csv`（完整保留已分析之企業資料庫與瀏覽器 Cookie）：
```powershell
.\.venv\Scripts\python.exe -m jobme --clear-history
```

---

## 📱 Discord 跨裝置互動面板

在常駐模式下，機器人會在指定的 Discord 頻道維持一個即時互動面板：

| 按鈕 | 功能說明 |
|---|---|
| `⚡ 立即巡檢` | 立即在背景非同步觸發一輪抓取與雙階段評估 |
| `🟢 啟動監控` | 恢復常駐定時輪詢排程 |
| `⏸️ 暫停監控` | 暫停常駐定時輪詢（外出時節省本機與 API 資源） |
| `⏱️ 調整頻率` | 下拉式選單切換巡檢週期（30 分 / 1 小時 / 2 小時 / 4 小時） |
| `📊 刷新狀態` | 檢視當前運作狀態、上次執行摘要與預計下次巡檢時間 |
| `📑 最新 Jev 紀錄` | 快速檢視近期職缺之 Stage 1 履歷吻合度判讀結果 |
| `📁 下載 CSV 報表` | 機器人直接在頻道上傳最新 `records.csv` 與 `profile_fits.csv` 檔案 |
| `🗑️ 清除歷史資料` | 一鍵重設職缺歷程與 CSV（保留企業資料庫與 Cookie） |

---

## 📊 資料稽核與分析留存

系統在根目錄輸出兩個主要稽核檔案：
- **`records.csv`**：所有通過基本門檻之職缺評估紀錄，包含總分、各維度子分、企業 Tier、公司資本額及職缺原始連結。
- **`profile_fits.csv`**：Stage 1 履歷適配度把關歷程，完整記錄被放行或過濾之職缺、契合度等級與模型信心度。

---

## 🧩 Chrome 擴充功能安裝與使用 (即時職缺評測)

在日常手動瀏覽求職平台時，可透過 Chrome 外掛即時評估當前職缺：

### 1. 安裝外掛 (只需一次)
1. 開啟 Google Chrome 瀏覽器，於網址列輸入 `chrome://extensions/`。
2. 開啟右上角 **「開發人員模式」 (Developer mode)**。
3. 點擊左上角 **「載入未打包項目」 (Load unpacked)**。
4. 選擇本專案目錄中的 **`extension`** 資料夾。
5. 安裝完成後，點擊瀏覽器右上角拼圖圖示，將 **JobMe** 外掛釘選至工具列。

### 2. 使用方式
1. 確保本機 API 伺服器正在運行（執行 `python -m jobme --serve` 或 `python -m jobme --daemon`）。
2. 在瀏覽器開啟任一支援平台的職缺頁面（104、Cake、Yourator、1111、yes123）。
3. 點擊瀏覽器右上角的 **JobMe** 圖示：
   - **已評測過**：即刻顯示歷史總分、達標徽章、企業 Tier 與各維度得分明細。
   - **尚未評測**：點擊 **「⚡ 立即使用 Jev AI 評測」**，系統將在 5~15 秒內完成雙階段決策評估、更新 SQLite 與 CSV，達標職缺同步推播至 Discord！

---

## 📁 專案結構

```text
JobMe/
├── .env.example              # 環境變數範本
├── .gitignore                # 排除機密金鑰、本地資料庫與瀏覽器快取
├── requirements.txt          # Python 相依套件清單
├── config/                   # 使用者自訂設定目錄
│   ├── rubric.md             # 雙語評分維度自訂檔 (含 Frontmatter 硬篩條件)
│   ├── rubric.example.md     # 雙語評分維度範本
│   ├── profile.md            # 求職者履歷背景自訂檔
│   └── profile.example.md    # 求職者履歷背景範本
├── extension/                # Chrome 擴充功能 (Manifest V3)
│   ├── manifest.json         # 外掛清單與權限設定
│   ├── content.js            # 求職平台 (104/Cake/Yourator) DOM 內文萃取
│   ├── popup.html            # 現代沉浸式暗色評測介面
│   ├── popup.css             # 樣式與動畫效果
│   ├── popup.js              # 彈窗互動、API 串接與快取呈現
│   └── icons/                # 外掛圖標
├── jobme/                    # 核心程式套件
│   ├── __init__.py
│   ├── __main__.py           # CLI 命令列進入點與管線調度
│   ├── api.py                # FastAPI 本機 REST API 伺服器
│   ├── evaluator.py          # 單一職缺評測服務與 URL 解析
│   ├── bot.py                # Discord 互動機器人控制台與狀態管理
│   ├── models.py             # 資料結構定義 (Job, ScoredJob, DimensionScore)
│   ├── db.py                 # SQLite 去重與評分歷程持久化
│   ├── rubric.py             # 評分規準解析器與本地硬篩邏輯
│   ├── category_mapper.py    # 跨平台職類代碼與標準名稱映射
│   ├── detail_fetcher.py     # 職缺完整內文抓取器與快取
│   ├── company_enricher.py   # 企業背景擴充 (TWSE / g0v / DeepSeek 分級)
│   ├── translator.py         # 英文 Fact Sheet 轉譯與快取
│   ├── jev.py                # Jev 多供應商容錯決策引擎
│   ├── notifier.py           # Discord Webhook 富文本推播與告警
│   ├── exporter.py           # CSV 評分紀錄與稽核檔匯出
│   ├── data/                 # 靜態資料庫
│   │   └── companies.json    # 一線跨國企業與科技巨頭白名單
│   └── scrapers/             # 求職平台爬蟲模組
│       ├── yourator.py       # Yourator JSON API 爬蟲
│       ├── yes123.py         # yes123 HTTP 爬蟲
│       └── browser_scrapers.py # Playwright 瀏覽器爬蟲 (104, Cake, 1111)
├── scripts/                  # 批次工具與基準測試腳本
└── tests/
    ├── test_api.py           # FastAPI 與評測服務測試
    └── test_self_check.py    # Assert-based 核心功能自我驗證測試
```

---

## 🧪 自我檢查驗證

專案提供完整的自我檢查測試套件，不依賴龐大測試框架，直接驗證解析器、資料庫、硬篩、數學計算、企業擴充與匯出功能：
```powershell
.\.venv\Scripts\python.exe tests/test_self_check.py
```
