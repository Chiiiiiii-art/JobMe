# JobMe 資料流審核標準規範書 (Data Flow Audit Standard)

> **版本**: 1.0.0  
> **適用範圍**: JobMe 職缺雷達全生命週期資料流（包含爬蟲原始資料、過濾清洗、履歷適配、公司徵信、Prompt 構建、Jev 評分及終端輸出呈現）。  
> **目標**: 確立黑箱測試（Black-Box Audit）之驗收標準，確保職缺資訊呈現無缺失、無污染、格式標準化且數值邏輯嚴謹。

---

## 1. 核心稽核原則 (Core Audit Principles)

1. **端到端保真性 (End-to-End Fidelity)**：
   原始關鍵資訊（平台 ID、職稱、公司全稱、原始 URL、薪資區間）在各節點流轉時不可無故丟失、截斷或被非預期替換。
2. **零內容污染 (Zero Contamination)**：
   終端呈現與輸入給 LLM 的文本中，嚴格禁止殘留 HTML 標籤、CSS 代碼片段、未跳脫之跳行符號、前端模板語法或亂碼（Mojibake）。
3. **數值與決策邏輯一致性 (Mathematical & Threshold Integrity)**：
   所有子維度分數加總必須嚴格等於總分（允許浮點小數容差 ±0.1）；推播門檻觸發與標註狀態（合格、低信心）必須 100% 符合設定檔定義。
4. **邊界容錯與防禦 (Boundary Robustness)**：
   對缺失資料（如薪資面議、公司無資本額、無標籤）必須具備一致的預設值呈現，嚴禁出現 `None`、`null`、`undefined` 或引發拋錯。

---

## 2. 缺陷分級體系 (Defect Severity Matrix)

黑箱稽核採用三級缺陷體系，評定基準為 **100 分制**：

| 缺陷等級 | 權重扣分 | 容許上限 | 定義與影響範疇 | 範例場景 |
| :--- | :---: | :---: | :--- | :--- |
| **Blocker (致命錯誤)** | **一票否決 (FAIL)** | **0 處** | 核心資料遺失、數值運算失真、決策行為相反或致命性格式損壞。 | • 職缺 URL 為空或無效協定<br>• 分數加總不吻合或破表 (>100)<br>• 合格推播判斷與門檻相反<br>• 大量 HTML 標籤滲入 LLM Prompt<br>• 資料庫主鍵破裂 |
| **Major (重大缺陷)** | **-5 分 / 處** | ≤ 2 處 | 次要欄位缺失、可讀性嚴重受損、格式非標準化或徵信依據缺失。 | • 薪資無法解析為法定時薪/月薪對照<br>• 內頁爬取字數不足 (< 50 字) 卻未報錯<br>• 公司 Tier 分級缺少合理佐證依據 (basis 為空)<br>• CSV 輸出欄位移位或破格<br>• Discord 卡片配色與級距不一致 |
| **Minor (輕微瑕疵)** | **-1 分 / 處** | ≤ 5 處 | 純視覺小瑕疵、非關鍵元數據未補齊，不影響核心決策。 | • 前後空白未清除 (strip)<br>• 標籤陣列包含重複值<br>• 小數點位數呈現非統一 1 位<br>• 低信心警示圖標位置微偏 |

### 合格判定標準 (Acceptance Gate)
- **判定合格 (PASS)** 必須同時滿足：
  1. `Blocker 總數 == 0`
  2. `綜合評分 >= 95.0 分`
- 若任何一筆或整批資料出現 Blocker，稽核結果直接標定為 **FAILED**。

---

## 3. 全生命週期檢核點清單 (Lifecycle Audit Checklist)

### 📍 Stage 0: 跨平台職類映射與動態參數轉譯 (Taxonomy Translation)
檢驗由通用設定檔 `rubric.md` 到各大求職平台原生查詢參數的動態映射能力，確保非單一平台硬編碼：
- [ ] **[Blocker] 跨平台動態轉譯完整性**：通用職類（如資訊軟體、專案管理、財務金融）必須能動態映射為各平台對應原生代碼：
  - 104: `jobcat` (如 `2007000000`)
  - 1111: `d0` (如 `140100`, `140200`)
  - yes123: `find_work_mode` (如 `2_1011_0001_0000`)
  - Yourator / Cake: 衍生搜尋詞 (Keywords) 與標籤
  嚴禁非 104 平台退化為無篩選條件的全量抓取。
- [ ] **[Major] 自訂類別動態外推 (Extensibility)**：若使用者在 `rubric.md` 新增未見於內建字典之客製類別（例如「生技研發」、「UIUX」），系統必須自動將其轉譯為各平台搜尋關鍵詞，不得拋錯或遺失條件。

---

### 📍 Stage 1: 原始卡片爬取與異質產業滲透率 (Raw List Card & Intrusion Rate)
檢驗爬蟲各平台首頁列表卡片擷取之基礎完整度與領域精確度：
- [ ] **[Blocker] 必填欄位完整性**：`platform`, `job_id`, `title`, `company`, `url` 必須皆為非空字串。
- [ ] **[Blocker] URL 有效性**：必須以 `http://` 或 `https://` 開頭，為合法之職缺外部連結。
- [ ] **[Blocker] 異質產業防護洩漏**：若 Stage 1 爬取之職缺標題含有嚴重偏離之黑名單產業（如「餐飲外場」、「清潔工」、「門市計時」、「美髮理髮」），且未能在 Stage 2 硬篩及時熔斷淘汰者，判定為 Blocker。
- [ ] **[Major] 爬蟲入口異質產業滲透率 (Intrusion Rate)**：Stage 1 原始爬取的卡片中，若含有黑名單產業關鍵字之比率 > 0%，記為 Major 缺陷（代表該平台爬蟲端之 `jobcat` 或 `work_mode` 篩選參數未正確限制範疇，造成無效流量與額度浪費）。
- [ ] **[Major] 平台識別合法性**：`platform` 必須屬於已定義平台集合 (`104`, `yourator`, `cake`, `1111`, `yes123`)。
- [ ] **[Major] 地區可讀性**：`location` 必須具備基本縣市或行政區名稱（若無則標記為「未提供」）。
- [ ] **[Minor] 薪資欄位保真**：若有抓取到薪資字串，應保留原始文字（如「時薪 200」、「面議」），不可為 null。

---

### 📍 Stage 2: 硬性規則檢查 (Hard Filter Rule Check)
檢驗針對關鍵字、地區、工作類型與黑名單之初步過濾決策：
- [ ] **[Blocker] 地區判定正確性**：若職缺地點不屬於設定檔允許清單（如未在北北桃竹），且未勾選遠端工作，必須判定淘汰。
- [ ] **[Blocker] 黑名單排除防線**：標題或公司命中 `exclude_keywords`（如電銷、餐飲、清潔）者，必須 100% 攔截淘汰。
- [ ] **[Major] 淘汰原因可溯性**：`passed == false` 時，`reject_reason` 不可為空，必須清晰載明具體淘汰條件（例如：`地區不符: 苗栗縣` 或 `排除關鍵字: 餐飲`）。
- [ ] **[Minor] 容錯對齊**：地區名詞需相容全形/半形與別名（例如「臺北市」與「台北市」）。

---

### 📍 Stage 3: 內頁詳情爬取與清洗 (Detail Fetcher & DB Cache)
檢驗進入內頁抓取 JD 內文、工作要求及資料庫持久化：
- [ ] **[Blocker] 零 HTML 標籤殘留**：`description` 與 `requirements` 內文不得含有 `<div...>`, `<p>`, `<br>`, `&nbsp;`, `\u00a0` 等未清洗標籤與實體。
- [ ] **[Blocker] 內文有效長度**：內文長度必須 ≥ 50 個有效字元，避免空白頁或反爬蟲驗證頁面直接被認定為抓取成功。
- [ ] **[Major] 結構化排版**：清洗後的內文需保留合理分段（換行符 `\n`），不可全部黏合成單一無空格字串。
- [ ] **[Minor] 雜訊過濾**：移除各平台頁尾廣告、版權宣告（如「104人力銀行版權所有」）與導流標籤。

---

### 📍 Stage 4: Stage 1 履歷適配度把關 (Profile Fit Gatekeeper)
檢驗第一階段基於 Candidate Profile 與 JD 的快速語意適配度分流：
- [ ] **[Blocker] 分級枚舉合法**：`fit_level` 必須且僅能為 `['high', 'medium', 'low', 'unrelated']` 其中之一。
- [ ] **[Blocker] 門檻把關嚴謹度**：
  - `fit_level == 'unrelated'` 或 `'low'`：必須攔截淘汰，不可消耗額外 API 進入 Stage 2 算分。
  - `fit_level == 'high'` 或 `'medium'`：必須順利放行進入 Stage 2。
- [ ] **[Major] 機率分佈總和校驗**：`probabilities` 四個等級機率值總和必須為 `1.0` (浮點容差 ±0.03)。
- [ ] **[Minor] 信心度邊界值**：`confidence` 數值需介於 `0.0` 至 `1.0` 之間。

---

### 📍 Stage 5: 企業背景徵信與分級錨定 (Company Fact Enrichment)
檢驗經濟部商業司登記、TWSE 上市櫃與企業 Tier 等級判定：
- [ ] **[Blocker] Tier 等級合法性**：`tier` 必須屬於 `['Tier 1', 'Tier 2', 'Tier 3', 'Tier 4']` 規範等級。
- [ ] **[Major] 資本額與上市一致性**：若 `is_twse_listed == true`，則 `tier` 不得判定為 `Tier 4` 或 `Tier 3`；資本額大於 1,000 億或台 50 成分股須錨定為 `Tier 1`。
- [ ] **[Major] 判定依據可解釋性**：`basis` 欄位必須提供至少 1 句具體驗證依據（如公司資本額、員工人數或代表性業務架構），不可為空字串。
- [ ] **[Minor] 官方正式名稱**：`official_name` 應提供經統編或商工查詢校驗後的公司登記名稱。

---

### 📍 Stage 6: 標準化事實清單構建 (State Prompt Sent to Jev)
檢驗組合送往 Jev (Stage 2) 評估之結構化 Markdown 內容：
- [ ] **[Blocker] 四大核心區塊完整性**：
  1. `## 1. COMPANY REPUTATION & MARKET BENCHMARK`
  2. `## 2. POSITION BASICS`
  3. `## 3. CORE RESPONSIBILITIES & TASKS`
  4. `## 4. CANDIDATE PROFILE & QUALIFICATIONS`
- [ ] **[Blocker] 資訊防洩漏與注入**：JD 內文中的使用者輸入或引言不得中斷 Prompt 結構，不得出現代碼區塊語法破壞（未閉合之 triple backticks）。
- [ ] **[Major] 長度健康區間**：Prompt 總長度應合理落於 2,000 至 25,000 字元之間，避免過度稀疏或超出 LLM Context Window。
- [ ] **[Minor] 幣值標準化呈現**：薪資應同步提供 TWD 與 USD 換算參考對照。

---

### 📍 Stage 7: Stage 2 Jev 決策評分 (Rubric Scoring)
檢驗根據 `rubric.md` 三大維度進行評分的嚴謹性：
- [ ] **[Blocker] 數學加總保證**：
  $$\text{total\_score} = \sum (\text{dimension\_score})$$
  三維度分數之和與 `total_score` 誤差不得超過 `0.1`。
- [ ] **[Blocker] 滿分與邊界限制**：
  - 企業權威性: `0.0 ~ 45.0`
  - 商業與技術: `0.0 ~ 35.0`
  - 專案影響力: `0.0 ~ 20.0`
  - 總分區間: `0.0 ~ 100.0`
- [ ] **[Blocker] 推播資格連動**：
  - `is_qualified == true` 當且僅當 `total_score >= notify_threshold` (預設 75.0 分)。
- [ ] **[Major] 低信心標示聯動**：
  - 當任意維度之 `confidence < 0.4` 時，`has_low_confidence` 必須為 `true`，並標註警示圖示 `⚠️`。

---

## 4. 終端輸出呈現檢核點 (Terminal Delivery Checklist)

### 📱 A. Discord 推播卡片 (Discord Embed)
- [ ] **[Blocker] 卡片標題與連結**：標題格式必須為 `[{分數}分] {職缺職稱}`，且點擊必須能正確導向原始職缺網址。
- [ ] **[Major] 顏色代碼依分數分級**：
  - 總分 $\ge 85$：`0x2ECC71` (高分綠色)
  - $70 \le \text{總分} < 85$：`0xF1C40F` (中高金色)
  - 觸發低信心 (`has_low_confidence == true`)：`0xE67E22` (警戒橙色)
- [ ] **[Major] 必備 Inline 欄位**：包含「🏢 公司」、「📍 地點」、「💰 薪資」、「🌐 平台」四宮格呈現。
- [ ] **[Minor] 適配度徽章**：若有 Stage 1 契合度，應呈現 `🎯 高度契合 (High)` 或 `⚖️ 中度契合 (Medium)`。

### 📊 B. CSV 報表匯出 (`records.csv` & `profile_fits.csv`)
- [ ] **[Blocker] 編碼與防破格**：必須採用 `utf-8-sig` 編碼（支援 Excel 直接開啟不亂碼），所有逗號與引號必須依法則逸出。
- [ ] **[Major] 表頭與欄位對齊**：每一列的欄位數量必須嚴格等於表頭數量，絕不可因內容含逗號產生資料錯行。
- [ ] **[Major] 數值精度統一**：分數與機率應統一四捨五入至小數點後 1 位或 2 位。

### 💾 C. SQLite 資料庫 (`jobs.db`)
- [ ] **[Blocker] 主鍵不重複**：`(platform, job_id)` 為 Primary Key，不可產生衝突寫入失敗。
- [ ] **[Major] JSON 欄位可解析性**：`details_json` 必須為可被 `json.loads()` 還原的合法字串。
- [ ] **[Minor] 時間戳記格式**：`evaluated_at` 必須符合 ISO 8601 或 SQLite 標準 `YYYY-MM-DD HH:MM:SS` 格式。

---

## 5. 負向注入與邊界防禦測試標準 (Negative Injection Specifications)

為防止測試「偽陽性 (False Positive)」，黑箱稽核工具必須具備主動注入異常缺陷樣本的自我檢驗能力。若以下負向樣本未被攔截判定為預期嚴重度，稽核工具本身判定為校驗失效：

| 測試案例編號 | 注入缺陷特徵 (Synthetic Defect Payload) | 預期稽核判定 | 驗證目標 |
| :--- | :--- | :---: | :--- |
| **NEG-01 (空值 JD)** | `char_count == 0` 且 `snippet == ""` | `BLOCKER` | 確保空 JD 100% 於 Stage 3 被阻斷，絕不進入 Prompt 生成或 LLM 呼叫。 |
| **NEG-02 (殘缺截斷)** | `char_count == 25` (如「誠徵工讀生，意者內洽」) | `BLOCKER` | 確保未達 50 字之殘缺頁面無法矇混過關。 |
| **NEG-03 (HTML 污染)** | JD 殘留 `<div class="job"><script>alert(1)</script></div>` | `BLOCKER` | 確保前端標籤清洗漏洞被即刻告警。 |
| **NEG-04 (數學加總失真)** | 維度子分和為 60.0，但 `total_score` 標註為 80.0 | `BLOCKER` | 確保分數作弊或浮點加總失真被 100% 抓出。 |
| **NEG-05 (異質產業滲漏)** | Stage 1 標題包含「餐飲外場門市服務生」，但 Stage 2 標註 `passed: true` | `BLOCKER` | 確保硬篩未排除之餐飲職缺被抓出為嚴重外洩。 |
| **NEG-06 (非枚舉適配度)** | `fit_level` 賦值為 `"unknown_level"` | `BLOCKER` | 確保列舉型別嚴格對齊規範定義。 |

---

## 6. 稽核執行指南 (Auditor Runbook)

可透過隨附之黑箱稽核診斷腳本 `scripts/audit_data_flow.py` 進行自動化驗證：

```powershell
# 1. 稽核特定 Pipeline Snapshot 報表 (50 筆新職缺全流程，含 Stage 1 異質產業滲透率)
python scripts/audit_data_flow.py --file reports/pipeline_50_jobs_data.json

# 2. 跨平台職類映射與動態參數轉譯驗證 (驗證非 104 單一硬編碼)
python scripts/audit_data_flow.py --taxonomy

# 3. 執行負向邊界注入測試 (驗證空 JD、殘缺頁面、分數失真之防禦攔截率)
python scripts/audit_data_flow.py --negative

# 4. 稽核本地終端產出 (CSV 與 SQLite DB)
python scripts/audit_data_flow.py --csv records.csv --db jobs.db

# 5. 執行全域自動大盤點 (整合：流程報表 + CSV + DB + 分類動態映射 + 負向注入驗證)
python scripts/audit_data_flow.py --all
```

