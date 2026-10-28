---
# 硬性條件篩選 (通用設定)
keywords: [實習, 軟體, 數據]       # 範例關鍵字 (留空代表不限關鍵字)
job_type: internship          # 實習工作 (internship | fulltime | parttime)
regions: [台北市, 新北市, 桃園市, 新竹市, 新竹縣]
# 行政區（有些平台不支援行政區篩選，留空代表不限區）
districts: []
# 中正區, 中和區, 大安區, 新莊區, 信義區, 南港區, 中山區, 內湖區, 板橋區, 中壢區, 平鎮區, 楊梅區, 新屋區, 大園區
exclude_keywords: [電話行銷, 業務推廣, 保險經紀, 門市計時, 餐飲, 餐廳, 美髮, 沙龍, 剪髮, 理髮, 清潔, 風管, 機械加工, 廚房]
# max_age_days: 90             # 最大上架天數 (超過 90 天之舊職缺/歷史公告不抓取)
base_threshold: 65.0             # 基本門檻：高於此分數封存記錄至 DB 與 CSV，但不推播
notify_threshold: 75.0           # 通知門檻：滿分 100，高於此分數直接透過 Discord 推播
save_all_evaluated: false        # 是否強制留存所有通過硬篩並評分的職缺（方便校驗評分標準）
low_confidence_below: 0.4     # Jev 信心度低於此值時標示 ⚠️
interval: 3600               # 巡檢週期秒數 (預設: 3600 秒 / 1 小時)
export_csv: true              # 是否自動將評分結果存入 records.csv

# 各平台原生參數直通（選填，直接從瀏覽器網址列複製）
platforms:
  "104":
    params:
      area: "6001001001,6001001005,6001001007,6001001003,6001002003,6001002015,6001002021,6001001011,6001001010,6001005001,6001005002,6001005004,6001005005,6001005012"
      jobcat: "2007000000,2004000000,2003000000,2001000000,2008000000"
      rostatus: "1024"
      order: "16"
      asc: "0"
      mode: "s"
      jobsource: "joblist_search"
  yourator:
    enabled: true
  cake:
    enabled: true
  "1111":
    enabled: true
  yes123:
    enabled: true
---

## Corporate Authority / 企業權威性 (45%)
- 0: Tier 4: 高風險 / 空殼 / 無法驗證
  * 經濟部商業登記狀態非正常（停業、解散、廢止）。
  * 經公開查核具重大司法詐欺、嚴重勞資爭議或被列入採購黑名單。
  * 無任何官方商業登記或公開稅籍證明之地下實體。
- 15: Tier 3: 在地中小企業 / 單一市場型機構
  * 合法登記之中小企業或微型企業，未達公開發行標準。
  * 全球總員工人數 < 200 人，且無海外實體營運據點。
  * 母公司為未上市櫃實體，且未獲 Tier 2 認可之機構融資。
- 30: Tier 2: 中大型企業 / 跨國體系 / 上市櫃
  * 公開市場：TWSE / TPEx 上市上櫃，或國際主要交易所掛牌公司（含其正式分公司/子公司）。
  * 跨國規模：具跨國母子公司架構，且全球合併總人數 ≥ 200 人（以母集團全體 headcount 計，而非單看在地辦公室）。
  * 機構規模化新創：公開資料庫（如 Crunchbase / PitchBook / 經濟部公示登記）可佐證之 Series B（含）以上，或最新輪次估值達 1 億美元（約 30 億 TWD）以上之未上市新創。
- 45: Tier 1: 全球巨頭 / 國家級權威指標
  * Fortune Global 500 或 S&P 500 成分股企業（含其完全持有之在台子公司/分公司）。
  * 台灣五十成分股 (FTSE TWSE Taiwan 50 Index) 或國內全體金控公司。
  * 上市櫃企業市值達 1,000 億 TWD 以上，或單一實體實收資本額達 100 億 TWD 以上。

## Business & Technology / 商業與技術 (35%)
- 0: Edge Interface Level: Involves zero business analysis and requires no understanding of technical logic; limited solely to schedule tracking/follow-up or clerical document formatting.
- 15: Tool Operation & Maintenance Level:
  * Software track: Writes unit functions based on established specifications, executes test cases drafted by others, or handles routine maintenance.
  * Data track: Retrieves data solely via predetermined SQL/scripts to generate routine reports, maintains existing BI dashboards, or executes data annotation/hyperparameter grid searches defined by others, lacking independent hypothesis testing and insight generation.
- 20: Pure Business & Strategy Track:
  * Pure market research, qualitative customer interviews, social media copywriting, community operations, business development (BD), or routine business planning.
  * Completely lacks code implementation, automated pipeline/ETL, statistical validation, or quantitative modeling capability (technical/quantitative logic accounts for less than 20%).
- 25: Pure Engineering & Technology Track:
  * Standard software, full-stack, frontend/backend engineering, API development, data pipelines/ETL, or model tuning; primary deliverable is production code and system implementation.
  * Positive Anchor: Includes engineering roles collaborating with Product Owners under Agile/Scrum ceremonies or presenting at demo days; lack of commercial strategy responsibility anchors it at this level.
- 35: Skill Integration: Substantive dual-track roles requiring "translating business pain points into concrete technical solutions with practical implementation":
  * Software/Systems track: Defines system integration specifications, enterprise architecture, API logic, or solutions architecture solving core business needs.
  * Data/AI/ML track: Translates business goals into quantitative hypotheses, designs feature engineering/ML pipelines, evaluates algorithmic ROI, or converts model outputs into commercial decisions.
  * Negative Constraint: Standard SWE/web dev positions whose core deliverable is programming code (even when interacting with users or POs in Agile) MUST NOT be scored 35 points unless the role explicitly requires defining commercial revenue models or business strategies.

## Project Impact / 專案影響力 (20%)
- 0: Remote & Incidental Chores / Non-Project Edge Support (遙遠附帶支援 / 庶務粗活):
  * Operational Nature: Purely informational, recording, clerical, or incidental support that operates at a long distance from the organization's business outcomes.
  * Typical Tasks: Manual data entry, equipment operation logging, filing/scheduling, routine social media reposting/copying, questionnaire dispatching, billing/reimbursements, or frontline phone message forwarding.
  * Constraint: Tasks completely lack independent analytical, professional, or technical problem-solving.
- 10: Contributory & Secondary Support (協同支援 / 二線輔助職能):
  * Operational Nature: Providing preparatory, advisory, analytical, promotional, or testing support services to other core teams; deliverables do not directly constitute the enterprise's primary core value proposition or core operational backbone.
  * Typical Tasks: Auxiliary functional support (such as promotional channel distribution, exhibition/event logistics, or vendor liaison outside the enterprise's core product domain), internal departmental tooling, manual test script execution, auxiliary documentation, or localized competitive research.
- 20: Direct & Primary Core Contribution (核心價值直接綁定 / 主營業務直接貢獻):
  * Operational Nature: The role's core responsibilities are directly tied to the enterprise's core value proposition and primary operational engine—the foundational products, services, or mission-critical systems that define the organization's existence.
  * Typical Tasks: Integrated team contributor directly engineering the organization's core operating/financial platforms, developing core client-facing software/product lines, or driving foundational commercial offerings that directly constitute the enterprise's primary business deliverables.
