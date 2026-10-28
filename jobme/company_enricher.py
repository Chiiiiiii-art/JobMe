from __future__ import annotations
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union
import requests

from .db import get_connection
from .rubric import RubricConfig, get_tier_definitions

logger = logging.getLogger("jobme.enricher")
TWSE_API_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
G0V_SEARCH_URL = "https://company.g0v.ronny.tw/api/search"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"

# Deterministic Tier 1 Authority sets (Taiwan 50 + Financial Holdings)
FINANCIAL_HOLDINGS = {
    "富邦金控", "富邦金", "富邦金融控股",
    "國泰金控", "國泰金", "國泰金融控股",
    "中信金控", "中信金", "中國信託金融控股", "中國信託金控",
    "兆豐金控", "兆豐金", "兆豐金融控股",
    "玉山金控", "玉山金", "玉山金融控股",
    "元大金控", "元大金", "元大金融控股",
    "第一金控", "第一金", "第一金融控股",
    "合庫金控", "合庫金", "合作金庫金融控股", "合作金庫金控",
    "華南金控", "華南金", "華南金融控股",
    "凱基金控", "凱基金", "凱基金融控股", "開發金控", "開發金", "中華開發金融控股",
    "台新金控", "台新金", "台新金融控股",
    "永豐金控", "永豐金", "永豐金融控股",
    "新光金控", "新光金", "新光金融控股",
    "國票金控", "國票金", "國票金融控股",
}

TAIWAN_50 = {
    "台積電", "台積", "台灣積體電路製造",
    "鴻海", "鴻海精密",
    "聯發科", "聯發科技",
    "台達電", "台達電子",
    "廣達", "廣達電腦",
    "中華電", "中華電信",
    "聯電", "聯華電子",
    "長榮", "長榮海運",
    "日月光", "日月光投控", "日月光半導體",
    "大立光", "大立光電",
    "聯詠", "聯詠科技",
    "上海商銀", "上海商業儲蓄銀行",
    "華碩", "華碩電腦",
    "和碩", "和碩聯合科技",
    "瑞昱", "瑞昱半導體",
    "緯穎", "緯穎科技",
    "緯創", "緯創資通",
    "研華", "研華科技",
    "台灣大", "台灣大哥大",
    "遠傳", "遠傳電信",
    "統一超", "統一超商",
    "統一", "統一企業",
    "中鋼", "中國鋼鐵",
    "台塑", "台灣塑膠",
    "南亞", "南亞塑膠",
    "台化", "台灣化學纖維",
    "台塑化", "台塑石化",
    "台泥", "台灣水泥",
    "和泰車", "和泰汽車",
    "遠東新", "遠東新世紀",
    "陽明", "陽明海運",
    "萬海", "萬海航運",
    "世芯", "世芯電子",
    "智邦", "智邦科技",
    "國巨",
    "彰銀", "彰化商業銀行",
    "中租", "中租控股",
    "矽力", "矽力杰",
    "奇鋐", "奇鋐科技",
    "川湖", "川湖科技",
    "藥華藥", "藥華醫藥",
    "健策", "健策精密",
    "嘉澤", "嘉澤端子",
    "技嘉", "技嘉科技",
}


@dataclass
class CompanyProfile:
    name: str
    official_name: str
    capital_twd: Optional[float]
    is_listed: bool
    tier: str  # "Tier 1", "Tier 2", "Tier 3", "Tier 4"
    market_context: str
    benchmark_summary: str
    basis: str = ""
    is_provisional: bool = False

    def to_english_state(self) -> str:
        usd_str = ""
        if self.capital_twd:
            usd_approx = self.capital_twd / 32.0 / 1_000_000.0  # M USD
            if usd_approx >= 1000:
                usd_str = f" (~{usd_approx / 1000.0:.2f} Billion USD)"
            else:
                usd_str = f" (~{usd_approx:.1f} Million USD)"

        lines = [
            f"- Company Name: {self.name} (Official: {self.official_name})",
            f"- Tier Classification: {self.tier}",
            f"- Publicly Listed (TWSE): {'Yes' if self.is_listed else 'No'}",
        ]
        if self.capital_twd:
            lines.append(f"- Paid-in Capital: NT$ {self.capital_twd:,.0f}{usd_str}")
        lines.append(f"- Market Standing: {self.market_context}")
        lines.append(f"- Jev Anchor: {self.benchmark_summary}")
        if self.basis:
            lines.append(f"- Tier Rationale (Basis): {self.basis}")
        return "\n".join(lines)


def extract_candidate_tokens(name: str) -> List[str]:
    """
    Split compound platform names into candidate brand and legal tokens.
    Example:
      'BOSCH_台灣羅伯特博世股份有限公司' -> ['台灣羅伯特博世股份有限公司', 'BOSCH', '台灣羅伯特博世', '羅伯特博世']
      '(遠東集團)鼎鼎企業管理顧問股份有限公司' -> ['鼎鼎企業管理顧問股份有限公司', '遠東集團', '鼎鼎企業管理顧問']
    """
    raw = (name or "").strip()
    if not raw:
        return []

    tokens: List[str] = []
    # 1. Delimiter splitting: _, /, |, ｜, newline
    parts = re.split(r"[_/|｜\n]+", raw)
    for p in parts:
        clean_p = p.strip()
        if clean_p and clean_p not in tokens:
            tokens.append(clean_p)

    # 2. Extract contents inside or outside parentheses
    paren_matches = re.findall(r"[\(（](.+?)[\)）]", raw)
    for pm in paren_matches:
        clean_pm = pm.strip()
        if clean_pm and clean_pm not in tokens:
            tokens.append(clean_pm)

    stripped_paren = re.sub(r"[\(（].+?[\)）]", "", raw).strip()
    if stripped_paren and stripped_paren not in tokens:
        tokens.append(stripped_paren)

    # 2b. Extract contiguous Chinese phrases (>= 2 chars) when mixed with English
    zh_blocks = re.findall(r"[\u4e00-\u9fff]{2,}", raw)
    for zb in zh_blocks:
        if zb not in tokens:
            tokens.append(zb)


    # 3. Strip legal suffixes to yield core brand names
    suffixes = [
        "股份有限公司台灣分公司",
        "企業股份有限公司",
        "實業股份有限公司",
        "管理顧問股份有限公司",
        "商業銀行股份有限公司",
        "股份有限公司",
        "有限公司台灣分公司",
        "有限公司",
        "台灣分公司",
        "辦事處",
    ]
    derived: List[str] = []
    for tok in list(tokens):
        for sfx in suffixes:
            if tok.endswith(sfx) and len(tok) > len(sfx):
                brand = tok[:-len(sfx)].strip()
                if brand and brand not in tokens and brand not in derived:
                    derived.append(brand)
                # Also strip leading "台灣" / "美商" / "新加坡商" from brand
                trimmed = re.sub(r"^(台灣|美商|日商|德商|法商|港商|英商|新加坡商)", "", brand).strip()
                if trimmed and len(trimmed) >= 2 and trimmed not in tokens and trimmed not in derived:
                    derived.append(trimmed)

    tokens.extend(derived)

    # 4. Common Taiwanese listed company abbreviation patterns (e.g. 台灣大哥大 -> 台灣大, 中華電信 -> 中華電)
    abbrs: List[str] = []
    for tok in list(tokens):
        if tok.endswith("大哥大") and len(tok) >= 4:
            abbrs.append(tok[:-3] + "大")
        if tok.endswith("電信") and len(tok) >= 3:
            abbrs.append(tok[:-1])
            abbrs.append(tok[:-2])
        if (tok.endswith("金融控股") or tok.endswith("金控")) and len(tok) >= 3:
            base = tok[:-4] if tok.endswith("金融控股") else tok[:-2]
            abbrs.append(base + "金")
        if tok.endswith("科技") and len(tok) >= 4:
            abbrs.append(tok[:-1])
    for a in abbrs:
        if a and a not in tokens:
            tokens.append(a)

    # Sort tokens with legal entities first, then longer tokens
    tokens.sort(key=lambda t: (0 if "公司" in t else 1, -len(t)))

    return tokens


class CompanyEnricher:
    def __init__(
        self,
        db_path: str = "jobs.db",
        deepseek_api_key: Optional[str] = None,
        deepseek_base_url: Optional[str] = None,
        deepseek_model: Optional[str] = None,
        rubric_path: str = "config/rubric.md",
        notifier: Optional[Any] = None,
    ):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        self.db_path = db_path
        candidates = [
            rubric_path,
            os.path.join("config", os.path.basename(rubric_path)),
            "config/rubric.md",
            "rubric.md",
            "config/rubric.example.md",
            "rubric.example.md",
        ]
        self.rubric_path = next((p for p in candidates if os.path.exists(p)), rubric_path)
        self.notifier = notifier
        self.has_degraded = False
        self._last_deepseek_error: Optional[str] = None
        if deepseek_api_key is not None:
            self.deepseek_api_key = deepseek_api_key.strip()
        else:
            self.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.deepseek_base_url = (deepseek_base_url or os.getenv("DEEPSEEK_API_URL") or DEFAULT_DEEPSEEK_BASE_URL).rstrip("/")
        self.deepseek_model = deepseek_model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL

        self._init_db_table()
        self._twse_cache: Optional[List[Dict[str, Any]]] = None

    def _init_db_table(self):
        with get_connection(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS company_profiles (
                    name TEXT PRIMARY KEY,
                    official_name TEXT,
                    capital_twd REAL,
                    is_listed INTEGER,
                    tier TEXT,
                    market_context TEXT,
                    benchmark_summary TEXT,
                    basis TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Schema migration: ensure basis column exists
            cur = conn.execute("PRAGMA table_info(company_profiles)")
            cols = [r[1] for r in cur.fetchall()]
            if "basis" not in cols:
                try:
                    conn.execute("ALTER TABLE company_profiles ADD COLUMN basis TEXT")
                except Exception:
                    pass
            conn.commit()

    def clear_profiles(self) -> int:
        """Clear cached company profiles for re-tiering."""
        with get_connection(self.db_path) as conn:
            cur = conn.execute("DELETE FROM company_profiles")
            conn.commit()
            return cur.rowcount

    def _get_from_db(self, name: str) -> Optional[CompanyProfile]:
        try:
            with get_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT * FROM company_profiles WHERE name = ?", (name,))
                row = cur.fetchone()
                if row:
                    cols = row.keys()
                    basis_val = row["basis"] if "basis" in cols and row["basis"] else ""
                    return CompanyProfile(
                        name=row["name"],
                        official_name=row["official_name"] or row["name"],
                        capital_twd=row["capital_twd"],
                        is_listed=bool(row["is_listed"]),
                        tier=row["tier"] or "Tier 3",
                        market_context=row["market_context"] or "",
                        benchmark_summary=row["benchmark_summary"] or "",
                        basis=basis_val,
                        is_provisional=False,
                    )
        except Exception:
            pass
        return None

    def _save_to_db(self, prof: CompanyProfile):
        if prof.is_provisional:
            return  # Never cache provisional fallback tiers
        try:
            with get_connection(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO company_profiles (
                        name, official_name, capital_twd, is_listed, tier, market_context, benchmark_summary, basis, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        prof.name,
                        prof.official_name,
                        prof.capital_twd,
                        1 if prof.is_listed else 0,
                        prof.tier,
                        prof.market_context,
                        prof.benchmark_summary,
                        prof.basis,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to cache company profile for {prof.name}: {e}")

    def _load_twse_data(self) -> List[Dict[str, Any]]:
        if self._twse_cache is not None:
            return self._twse_cache

        cache_file = "twse_cache.json"
        if os.path.exists(cache_file):
            try:
                mtime = os.path.getmtime(cache_file)
                if (time.time() - mtime) < 7 * 86400:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        self._twse_cache = json.load(f)
                        return self._twse_cache
            except Exception:
                pass

        try:
            resp = requests.get(TWSE_API_URL, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                self._twse_cache = data
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                return data
        except Exception as e:
            logger.warning(f"Could not fetch TWSE data: {e}")

        self._twse_cache = []
        return []

    def _match_twse(self, candidates: List[str]) -> Optional[Dict[str, Any]]:
        """Exact match only between candidates and TWSE full/short names (no substring pollution)."""
        twse_list = self._load_twse_data()
        cand_set = {c.strip().lower() for c in candidates if c.strip()}
        for item in twse_list:
            full_name = (item.get("公司名稱") or "").strip()
            short_name = (item.get("公司簡稱") or "").strip()
            # Exact equality match only!
            if full_name.lower() in cand_set or (short_name and short_name.lower() in cand_set):
                raw_cap = item.get("實收資本額") or "0"
                try:
                    capital = float(str(raw_cap).replace(",", ""))
                except ValueError:
                    capital = 0.0
                return {
                    "official_name": full_name,
                    "short_name": short_name,
                    "capital": capital,
                    "industry": item.get("產業別", "Listed Industry"),
                }
        return None

    def _match_g0v(self, candidates: List[str]) -> Optional[Dict[str, Any]]:
        """Search g0v commercial registry with candidate names."""
        for cand in candidates:
            if len(cand) < 2:
                continue
            try:
                resp = requests.get(G0V_SEARCH_URL, params={"q": cand}, timeout=4)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    if data:
                        first = data[0]
                        legal_names = first.get("公司名稱")
                        if isinstance(legal_names, list):
                            legal_name = str(legal_names[0]) if legal_names else cand
                        else:
                            legal_name = str(legal_names or cand)

                        raw_cap_str = first.get("實收資本額(元)") or first.get("資本總額(元)") or "0"
                        capital_val = float(re.sub(r"[^\d.]", "", str(raw_cap_str)) or "0")
                        status = first.get("公司狀況", "核准設立")

                        # If legal name matches one of candidates or has same core stem
                        cand_clean = re.sub(r"[\(（].+?[\)）]", "", cand).strip()
                        if cand_clean in legal_name or legal_name in cand_clean:
                            return {
                                "official_name": legal_name,
                                "capital": capital_val,
                                "status": status,
                            }
            except Exception:
                pass
        return None

    def _search_deepseek(self, company_name: str) -> str:
        """
        DeepSeek Search API (POST /v1/tools/search).
        Disabled per user request to conserve API quota.
        """
        # ponytail: toolsearch disabled to prevent quota drain; TWSE, g0v, and platform tags provide sufficient context.
        return ""

    def _classify_with_deepseek(
        self,
        company_name: str,
        candidates: List[str],
        evidence: List[str],
        search_snippets: str = "",
        rubric_config: Optional[RubricConfig] = None,
    ) -> Optional[Tuple[str, str]]:
        """Call DeepSeek Chat Completions in JSON mode to classify company tier (1-4)."""
        if not self.deepseek_api_key:
            return None

        tier_defs = get_tier_definitions(rubric_config, self.rubric_path)
        system_prompt = (
            "你是一個嚴謹的企業權威性分級專家。請根據給定的分級標準與客觀佐證，將目標企業歸類為 Tier 1, Tier 2, Tier 3 或 Tier 4。\n"
            "必須嚴格僅輸出 JSON 物件，格式如下：{\"tier\": 1, \"basis\": \"理由摘要\"}\n"
            "tier 必須為整數 (1, 2, 3, 4)，basis 為一句話精簡判定理由 (約 30 字以內)。嚴禁輸出任何 markdown、程式碼區塊或額外解釋文字。"
        )

        ev_lines = "\n".join(f"- {e}" for e in evidence) if evidence else "無公開上市或政府登記資本佐證"
        tiers_text = "\n".join(f"- {k}: {v}" for k, v in sorted(tier_defs.items()))

        prompt_blocks = [
            f"【目標企業】\n原始名稱：{company_name}\n候選詞：{', '.join(candidates[:4])}",
            f"【客觀佐證】\n{ev_lines}",
        ]
        if search_snippets:
            prompt_blocks.append(f"【網路搜尋摘要】\n{search_snippets}")

        prompt_blocks.extend([
            f"【分級標準 (Rubric)】\n{tiers_text}",
            "【核心判定原則（嚴格遵守）】\n"
            "1. 跨國規模判定 (Tier 2)：外商在台分公司/子公司，必須以母公司全球合併規模（全球總人數、跨國據點）為準！若具跨國母子公司架構且全球總人數 ≥ 200 人，一律屬於 Tier 2，嚴禁因在台分公司之局部登記資本額較小而降級為 Tier 3！\n"
            "2. 在地中小微企業 (Tier 3)：僅限未達公開發行、全球總人數 < 200 人且無海外實體據點、無機構融資之一般中小微企業。\n"
            "3. 高風險/空殼 (Tier 4)：商業登記異常（停業/廢止/解散）或無登記稅籍之地下實體。",
            "請輸出 JSON 分級結果。",
        ])
        user_prompt = "\n\n".join(prompt_blocks)

        url = f"{self.deepseek_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.deepseek_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 100,
            "temperature": 0.0,
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=12)
            if resp.status_code == 400 and "thinking" in resp.text:
                del payload["thinking"]
                resp = requests.post(url, json=payload, headers=headers, timeout=12)

            if resp.status_code == 200:
                res_data = resp.json()
                content = res_data["choices"][0]["message"]["content"].strip()
                # Remove possible markdown wrapper
                clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE).strip()
                parsed = json.loads(clean_json)
                tier_val = int(parsed.get("tier", 3))
                basis = str(parsed.get("basis", "")).strip()[:80]
                if 1 <= tier_val <= 4:
                    return f"Tier {tier_val}", basis
            else:
                err_msg = f"HTTP {resp.status_code}: {resp.text[:120]}"
                self._last_deepseek_error = err_msg
                logger.warning(f"DeepSeek Chat API {err_msg}")
        except Exception as e:
            self._last_deepseek_error = str(e)
            logger.warning(f"DeepSeek classification call failed for {company_name}: {e}")

        return None

    def _fallback_tier(
        self,
        twse_match: bool,
        capital_twd: Optional[float],
        is_foreign: bool,
        is_listed: bool,
    ) -> Tuple[str, str]:
        """
        Deterministic provisional fallback heuristic when DeepSeek is unconfigured or unavailable.
        Does not produce Tier 4 (only LLM with search can judge Tier 4 obscurity).
        Marked provisional to prevent polluting the persistent SQLite cache.
        """
        if twse_match and capital_twd and capital_twd >= 10_000_000_000:
            return "Tier 1", "TWSE百億上市巨頭 (臨時規則)"
        elif is_listed or is_foreign or (capital_twd and capital_twd >= 100_000_000):
            reason = "上市或外商體系 (臨時規則)" if (is_listed or is_foreign) else "資本額破億企業 (臨時規則)"
            return "Tier 2", reason
        else:
            return "Tier 3", "在地中小微企業 (臨時規則)"

    def enrich_company(
        self,
        target: Union[Any, str],
        force_refresh: bool = False,
        rubric_config: Optional[RubricConfig] = None,
    ) -> CompanyProfile:
        """
        Enrich company profile using:
        1. Exact-match TWSE and g0v registries
        2. Platform native metadata (Foreign / Listed tags)
        3. DeepSeek classification (toolsearch disabled for efficiency)
        4. Clean provisional rule fallback if DeepSeek is unreachable
        """
        # Extract metadata from Job or str
        if hasattr(target, "company"):
            company_name = (target.company or "").strip()
            tags = target.tags or []
        else:
            company_name = str(target or "").strip()
            tags = []

        if not company_name:
            return CompanyProfile(
                name="Unknown",
                official_name="Unknown",
                capital_twd=None,
                is_listed=False,
                tier="Tier 3",
                market_context="No company information provided",
                benchmark_summary="Matches Tier 3 (Unknown or small company)",
                basis="名稱缺失",
                is_provisional=True,
            )

        # 1. Local SQLite cache check
        if not force_refresh:
            cached = self._get_from_db(company_name)
            if cached:
                return cached

        # 2. Extract candidate brand/legal tokens
        candidates = extract_candidate_tokens(company_name)

        # 3. Collect objective evidence
        evidence: List[str] = []
        is_foreign = False
        is_listed = False
        official_name = company_name
        capital_twd: Optional[float] = None
        twse_matched = False

        # Platform native tags
        tags_str = json.dumps(tags, ensure_ascii=False) if isinstance(tags, (list, dict)) else str(tags)
        if "外商" in tags_str or "zoneForeign" in tags_str:
            is_foreign = True
            evidence.append("求職平台原生標籤：外商公司 (Foreign Entity)")
        if "上市" in tags_str or "上市上櫃" in tags_str:
            is_listed = True
            evidence.append("求職平台原生標籤：上市上櫃公司 (Publicly Listed)")

        # TWSE Open Data (Exact equality match)
        twse_info = self._match_twse(candidates)
        if twse_info:
            twse_matched = True
            is_listed = True
            official_name = twse_info["official_name"]
            capital_twd = twse_info["capital"]
            evidence.append(
                f"台灣證券交易所 (TWSE) 正式上市企業，產業別：{twse_info['industry']}，"
                f"實收資本額：NT$ {capital_twd:,.0f}"
            )

        # g0v Commercial Registry (Exact/stem match)
        if not twse_matched:
            g0v_info = self._match_g0v(candidates)
            if g0v_info:
                official_name = g0v_info["official_name"]
                capital_twd = g0v_info["capital"]
                evidence.append(
                    f"台灣商業登記在案，實收資本額：NT$ {capital_twd:,.0f}，狀況：{g0v_info['status']} "
                    f"（註：此僅為在台登記資本，跨國外商母公司規模不受此限）"
                )

        # Fast-path deterministic Tier 4: Government registry non-normal status (停業/解散/廢止)
        if not twse_matched and g0v_info:
            status = g0v_info.get("status", "")
            if any(s in status for s in ("停業", "解散", "廢止", "撤銷")):
                clean_status = re.sub(r"\s+", " ", status).strip()[:30]
                prof = CompanyProfile(
                    name=company_name,
                    official_name=official_name,
                    capital_twd=capital_twd,
                    is_listed=False,
                    tier="Tier 4",
                    market_context="; ".join(evidence),
                    benchmark_summary=f"Level 1 / Tier 4 (商業登記{clean_status})",
                    basis=f"商業登記{clean_status}",
                    is_provisional=False,
                )
                self._save_to_db(prof)
                return prof

        # Fast-path deterministic Tier 1: Taiwan 50, Financial Holdings, or TWSE Capital >= 10B TWD
        is_tw50_or_fin = any(c in TAIWAN_50 or c in FINANCIAL_HOLDINGS for c in candidates)
        is_mega_listed = twse_matched and capital_twd and capital_twd >= 10_000_000_000
        if is_tw50_or_fin or is_mega_listed:
            basis = "台灣50成分股或金控" if is_tw50_or_fin else "TWSE百億資本上市巨頭"
            prof = CompanyProfile(
                name=company_name,
                official_name=official_name,
                capital_twd=capital_twd,
                is_listed=is_listed,
                tier="Tier 1",
                market_context="; ".join(evidence) if evidence else "國家級權威指標企業",
                benchmark_summary=f"Level 4 / Tier 1 ({basis})",
                basis=basis,
                is_provisional=False,
            )
            self._save_to_db(prof)
            return prof

        # 4. DeepSeek Classification (toolsearch disabled to conserve quota)
        # ponytail: toolsearch disabled; objective evidence (TWSE/g0v/tags) is sufficient for classification.
        classified = self._classify_with_deepseek(
            company_name=company_name,
            candidates=candidates,
            evidence=evidence,
            rubric_config=rubric_config,
        )

        if classified:
            tier, basis = classified
            is_provisional = False
            ctx = "; ".join(evidence) if evidence else "標準商業實體"
            summary = f"Level {5 - int(tier[-1])} / {tier} ({basis})"
            prof = CompanyProfile(
                name=company_name,
                official_name=official_name,
                capital_twd=capital_twd,
                is_listed=is_listed,
                tier=tier,
                market_context=ctx,
                benchmark_summary=summary,
                basis=basis,
                is_provisional=False,
            )
            self._save_to_db(prof)
            return prof

        # 6. Fallback rule when DeepSeek is unconfigured or failed
        fallback_tier_str, fallback_basis = self._fallback_tier(
            twse_match=twse_matched,
            capital_twd=capital_twd,
            is_foreign=is_foreign,
            is_listed=is_listed,
        )
        self.has_degraded = True
        if not hasattr(self, "_fallback_warned") or not self._fallback_warned:
            logger.warning(
                f"[Enricher] DeepSeek 未配置或未連線，已啟用極簡規則暫定企業分級 (Provisional，不寫入持久快取)。"
            )
            if self.notifier:
                err_detail = self._last_deepseek_error or "未配置 DEEPSEEK_API_KEY 或連線失敗"
                self.notifier.notify_error(
                    "DeepSeek 企業分級異常 (已降級為資本額推估)",
                    f"原因: {err_detail}\n系統已退回依資本額/公開名單估算 Tier，可能導致企業權威性失準。",
                    platform=getattr(target, "platform", ""),
                )
            self._fallback_warned = True

        prof = CompanyProfile(
            name=company_name,
            official_name=official_name,
            capital_twd=capital_twd,
            is_listed=is_listed,
            tier=fallback_tier_str,
            market_context="; ".join(evidence) if evidence else "暫定中小企業",
            benchmark_summary=f"{fallback_tier_str} ({fallback_basis})",
            basis=fallback_basis,
            is_provisional=True,
        )
        return prof

    def warmup_companies(
        self,
        jobs: List[Any],
        rubric_config: Optional[RubricConfig] = None,
        max_workers: int = 8,
    ) -> None:
        """Parallel warm-up of company profiles cache for all passed jobs."""
        comp_jobs = {}
        for j in jobs:
            c = (getattr(j, "company", "") or "").strip()
            if c and c not in comp_jobs:
                comp_jobs[c] = j

        if not comp_jobs:
            return

        uncached_jobs = [j for c, j in comp_jobs.items() if not self._get_from_db(c)]
        if not uncached_jobs:
            logger.info("✅ 企業權威性與規模快取全數命中 (100% Cache Hit)。")
            return

        logger.info(f"正在背景平行加速擴充與分級 {len(uncached_jobs)} 家目標企業...")
        from concurrent.futures import ThreadPoolExecutor

        def _enrich(j):
            try:
                self.enrich_company(j, rubric_config=rubric_config)
            except Exception as e:
                logger.debug(f"平行企業擴充異常 ({getattr(j, 'company', '')}): {e}")

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                list(executor.map(_enrich, uncached_jobs))
            logger.info("✅ 企業分級資料庫預熱完成。")
        except Exception as e:
            logger.warning(f"平行企業預熱過程異常: {e}")

