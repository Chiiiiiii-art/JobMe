from __future__ import annotations
import os
import re
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv

load_dotenv(override=True)

# 104 jobcat codes to Yes123 find_work_mode mapping
MAP_104_TO_YES123: Dict[str, List[str]] = {
    # 2007000000: 資訊軟體系統類 -> 軟體/工程全部, MIS/網管全部
    "2007000000": ["2_1011_0001_0000", "2_1011_0002_0000"],
    # 2004000000: 專案/產品管理類 -> 專案管理全部
    "2004000000": ["2_1006_0002_0000"],
    # 2003000000: 財務/金融/法務類 -> 財會全部, 金融全部
    "2003000000": ["2_1002_0001_0000", "2_1002_0002_0000"],
    # 2001000000: 經營/幕僚/人資類 -> 經營/幕僚全部, 人力資源全部
    "2001000000": ["2_1003_0002_0000", "2_1003_0001_0000"],
    # 2008000000: 研發/硬體工程類 -> 工程研發全部
    "2008000000": ["2_1009_0001_0000"],
    # 2006000000: 行銷/企劃類 -> 行銷/企劃全部
    "2006000000": ["2_1006_0001_0000"],
    # 2002000000: 行政/總務類 -> 行政/總務全部
    "2002000000": ["2_1001_0001_0000"],
    # 2005000000: 業務銷售類 -> 業務銷售全部
    "2005000000": ["2_1004_0003_0000"],
}

# 104 jobcat codes to 1111 d0 (job position) codes
MAP_104_TO_1111: Dict[str, List[str]] = {
    "2007000000": ["140100", "140200", "140400"],  # 軟體工程, 系統分析/數據/AI, MIS網管
    "2004000000": ["130300", "130200"],            # 專案管理, 產品企劃
    "2003000000": ["110100", "110200", "110300"],  # 金融保險, 財務會計, 稽核審計
    "2001000000": ["100100", "100200"],            # 經營管理, 人力資源
    "2008000000": ["150100", "150200", "160100"],  # 電子通訊, 半導體, 機械工程
    "2006000000": ["130100"],                      # 行銷廣告
    "2002000000": ["100300"],                      # 行政後勤/總務
    "2005000000": ["120100", "120200"],            # 國貿, 業務推廣
}

# 1111 c0 (region/city) codes mapping
MAP_REGION_TO_1111: Dict[str, str] = {
    "台北市": "100100",
    "新北市": "100200",
    "基隆市": "100300",
    "宜蘭縣": "100400",
    "桃園市": "100500",
    "新竹市": "100600",
    "新竹縣": "100700",
    "苗栗縣": "100800",
    "台中市": "100900",
    "彰化縣": "101000",
    "南投縣": "101100",
    "雲林縣": "101200",
    "嘉義市": "101300",
    "嘉義縣": "101400",
    "台南市": "101600",
    "高雄市": "101800",
    "屏東縣": "101900",
    "花蓮縣": "102000",
    "台東縣": "102100",
    "澎湖縣": "102200",
    "金門縣": "102300",
    "連江縣": "102400",
}

CATEGORY_TAXONOMY: List[Dict[str, Any]] = [
    {
        "id": "software_it",
        "aliases": ["資訊軟體", "軟體工程", "軟體", "資訊", "it", "mis", "網管", "全端", "後端", "前端", "資料庫", "演算法", "ai", "2007000000"],
        "104": ["2007000000"],
        "1111": ["140100", "140200", "140400"],
        "yes123": ["2_1011_0001_0000", "2_1011_0002_0000"],
        "keywords": ["軟體", "資訊", "系統開發", "工程師"],
    },
    {
        "id": "project_product",
        "aliases": ["專案管理", "產品管理", "專案/產品管理類", "產品企劃", "pm", "po", "product manager", "project manager", "2004000000"],
        "104": ["2004000000"],
        "1111": ["130300", "130200"],
        "yes123": ["2_1006_0002_0000"],
        "keywords": ["專案管理", "產品經理", "PM"],
    },
    {
        "id": "data_analytics",
        "aliases": ["數據分析", "商業分析", "資料分析", "資料工程", "商業智慧", "商業數據", "bi", "ba", "data", "data analyst"],
        "104": ["2007000000"],
        "1111": ["140200"],
        "yes123": ["2_1011_0001_0000", "2_1011_0002_0000"],
        "keywords": ["數據分析", "資料分析", "商業分析"],
    },
    {
        "id": "finance_legal",
        "aliases": ["財務金融", "財務", "金融", "法務", "會計", "fintech", "金融科技", "2003000000"],
        "104": ["2003000000"],
        "1111": ["110100", "110200", "110300"],
        "yes123": ["2_1002_0001_0000", "2_1002_0002_0000"],
        "keywords": ["金融科技", "財務", "會計"],
    },
    {
        "id": "hardware_rd",
        "aliases": ["研發硬體", "硬體工程", "研發", "硬體", "電子", "電機", "韌體", "2008000000"],
        "104": ["2008000000"],
        "1111": ["150100", "150200", "160100"],
        "yes123": ["2_1009_0001_0000"],
        "keywords": ["硬體", "韌體", "研發"],
    },
    {
        "id": "marketing_planning",
        "aliases": ["行銷企劃", "行銷", "企劃", "數位行銷", "社群", "marketing", "2006000000"],
        "104": ["2006000000"],
        "1111": ["130100"],
        "yes123": ["2_1006_0001_0000"],
        "keywords": ["行銷", "數位行銷"],
    },
    {
        "id": "management_hr",
        "aliases": ["經營幕僚", "人力資源", "人資", "hr", "招募", "2001000000"],
        "104": ["2001000000"],
        "1111": ["100100", "100200"],
        "yes123": ["2_1003_0002_0000", "2_1003_0001_0000"],
        "keywords": ["人資", "人力資源", "招募"],
    },
    {
        "id": "admin_general",
        "aliases": ["行政總務", "行政", "總務", "助理", "2002000000"],
        "104": ["2002000000"],
        "1111": ["100300"],
        "yes123": ["2_1001_0001_0000"],
        "keywords": ["行政", "助理"],
    },
    {
        "id": "sales_business",
        "aliases": ["業務銷售", "業務", "銷售", "bd", "商務開發", "2005000000"],
        "104": ["2005000000"],
        "1111": ["120100", "120200"],
        "yes123": ["2_1004_0003_0000"],
        "keywords": ["業務", "商務開發", "BD"],
    },
]



def get_raw_configured_categories(config: Optional[Any] = None) -> List[str]:
    """
    Extract raw category tokens from:
    1. os.getenv("JOB_CATEGORIES") (.env)
    2. config.job_categories (rubric.md)
    3. config.platforms.104.params.jobcat
    """
    if config is not None and getattr(config, "job_categories", None):
        raw = config.job_categories
        if isinstance(raw, list):
            return [str(c).strip() for c in raw if str(c).strip()]
        return [str(c).strip() for c in str(raw).split(",") if str(c).strip()]

    env_cats = os.getenv("JOB_CATEGORIES", "").strip()
    if env_cats:
        return [c.strip() for c in env_cats.split(",") if c.strip()]

    if config is not None:
        p104 = getattr(config, "platforms", {}).get("104", {}) if hasattr(config, "platforms") else {}
        params = p104.get("params", {})
        raw_jobcat = params.get("jobcat") or params.get("category")
        if raw_jobcat:
            if isinstance(raw_jobcat, list):
                return [str(c).strip() for c in raw_jobcat if str(c).strip()]
            return [str(c).strip() for c in str(raw_jobcat).split(",") if str(c).strip()]

    return []


def get_configured_104_jobcats(config: Optional[Any] = None) -> List[str]:
    """Map universal categories into 104 jobcat codes."""
    raw_cats = get_raw_configured_categories(config)
    codes: List[str] = []

    for item in raw_cats:
        item_clean = item.lower().strip()
        matched = False
        for tax in CATEGORY_TAXONOMY:
            if any(alias in item_clean or item_clean in alias for alias in tax["aliases"]):
                for c in tax["104"]:
                    if c not in codes:
                        codes.append(c)
                matched = True
                break

        if not matched and len(item_clean) == 10 and item_clean.isdigit():
            if item_clean not in codes:
                codes.append(item_clean)

    return codes


def get_1111_categories(config: Optional[Any] = None) -> List[str]:
    """Map universal categories into 1111 d0 (job position) codes."""
    raw_cats = get_raw_configured_categories(config)
    d0_list: List[str] = []

    for item in raw_cats:
        item_clean = item.lower().strip()
        matched = False
        for tax in CATEGORY_TAXONOMY:
            if any(alias in item_clean or item_clean in alias for alias in tax["aliases"]):
                for c in tax["1111"]:
                    if c not in d0_list:
                        d0_list.append(c)
                matched = True
                break

        # Check 104 code mapping
        if not matched and item_clean in MAP_104_TO_1111:
            for c in MAP_104_TO_1111[item_clean]:
                if c not in d0_list:
                    d0_list.append(c)
            matched = True

        # Direct 1111 code (6 digits)
        if not matched and len(item_clean) == 6 and item_clean.isdigit():
            if item_clean not in d0_list:
                d0_list.append(item_clean)

    return d0_list


def get_1111_regions(config: Optional[Any] = None) -> List[str]:
    """Map configured regions into 1111 c0 (region/city) codes."""
    if not config or not getattr(config, "regions", None):
        return []
    c0_list: List[str] = []
    for reg in config.regions:
        clean = reg.replace("臺", "台").strip()
        if clean in MAP_REGION_TO_1111:
            code = MAP_REGION_TO_1111[clean]
            if code not in c0_list:
                c0_list.append(code)
    return c0_list



def get_yes123_work_modes(config: Optional[Any] = None) -> List[str]:
    """Map universal categories into Yes123 find_work_mode codes."""
    raw_cats = get_raw_configured_categories(config)
    modes: List[str] = []

    for item in raw_cats:
        item_clean = item.lower().strip()
        matched = False
        for tax in CATEGORY_TAXONOMY:
            if any(alias in item_clean or item_clean in alias for alias in tax["aliases"]):
                for m in tax["yes123"]:
                    if m not in modes:
                        modes.append(m)
                matched = True
                break

        if not matched and item_clean in MAP_104_TO_YES123:
            for m in MAP_104_TO_YES123[item_clean]:
                if m not in modes:
                    modes.append(m)
            matched = True

    return modes[:10]


def get_category_keywords(config: Optional[Any] = None) -> List[str]:
    """
    Derive search keywords from user configured categories for text-based platforms (Yourator, Cake, 1111).
    Any custom user category (e.g. '生技', 'UIUX') automatically becomes a search keyword.
    """
    raw_cats = get_raw_configured_categories(config)
    keywords: List[str] = []

    for item in raw_cats:
        item_clean = item.strip()
        item_lower = item_clean.lower()
        matched = False
        for tax in CATEGORY_TAXONOMY:
            if any(alias in item_lower or item_lower in alias for alias in tax["aliases"]):
                for kw in tax["keywords"]:
                    if kw not in keywords:
                        keywords.append(kw)
                matched = True
                break

        # If user provided a completely custom category string not in taxonomy and not a pure numeric code:
        if not matched and not item_clean.isdigit():
            if item_clean not in keywords:
                keywords.append(item_clean)

    return keywords


def get_scraper_keywords(config: Any, default_keyword: str = "") -> List[str]:
    """
    Resolve keywords for scrapers:
    1. If user explicitly provided config.keywords in rubric.md, use them.
    2. Otherwise, return empty list [] so scrapers rely on native platform tags and filters
       without injecting arbitrary keywords.
    """
    if getattr(config, "keywords", None):
        return [k for k in config.keywords if k]

    return [default_keyword] if default_keyword else []
