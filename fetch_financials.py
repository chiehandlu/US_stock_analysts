#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
財報數據抓取腳本（方案 A：本機執行）— SEC EDGAR companyfacts
----------------------------------------------------------
用途：抓取單一美股的 XBRL 財報事實，整理近 3 個會計年度 + 最新一季的核心財務
      數字，輸出「自我描述」的固定 JSON，供 Claude project 的 skill 讀取分析。

設計原則（與 fetch_stock.py 一致）：
  1. 數據層只產出「確定的數字 + 來源 + 申報日」，分析交給 skill。
  2. 自我描述：JSON 自帶 _doc + schema_version，skill 容忍式讀取。
  3. 候選標籤 fallback：同一指標備多個 us-gaap/dei 標籤，逐一嘗試。
  4. 寧缺勿爛：找不到的指標回 None 並寫進 warnings，絕不亂填。
  5. 每個數字都附「申報日(filed)」，支援報告的 as-of 日期標註（缺口 4）。

⚠️ 使用前必改：把下方 USER_AGENT 換成你的 email。
   SEC 要求請求帶可識別的 User-Agent，否則一律 403 擋掉。

用法：
    python fetch_financials.py AAPL
    python fetch_financials.py AAPL > aapl_fin.json   # 存檔後上傳

依賴：標準函式庫即可（urllib, json）。
"""

import os
import sys
import json
import urllib.request
from datetime import datetime, timezone

SCHEMA_VERSION = "1.3"  # 1.3: 合併所有候選標籤（修公司換標籤導致年度卡舊，如 NVDA 營收）；tag_used→tags_used；新增年度過舊防呆

# 存檔資料夾。預設＝腳本所在資料夾（本機 Mac 與雲端 GitHub 簽出皆通用，勿寫死絕對路徑）。
# 留空字串 "" = 改印到畫面（可用 > 導向）。
SAVE_DIR = os.path.dirname(os.path.abspath(__file__))

# ⚠️ 必改：SEC 規定要帶可識別聯絡資訊，否則 403。請換成你的 email。
USER_AGENT = "Jay Investment Research chiehandlu@gmail.com"

N_YEARS = 3  # 取最近幾個會計年度

# 每個指標的候選標籤（依偏好排序）、命名空間、單位、期間型別
# duration = 有起訖（損益表/現金流）；instant = 單一時點（資產負債表）
METRICS = {
    "revenue": {"tags": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                          "Revenues", "SalesRevenueNet",
                          "RevenueFromContractWithCustomerIncludingAssessedTax"],
                "unit": "USD", "type": "duration"},
    "gross_profit": {"tags": ["GrossProfit"], "unit": "USD", "type": "duration"},
    "operating_income": {"tags": ["OperatingIncomeLoss"], "unit": "USD", "type": "duration"},
    "net_income": {"tags": ["NetIncomeLoss", "ProfitLoss"], "unit": "USD", "type": "duration"},
    "eps_diluted": {"tags": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
                    "unit": "USD/shares", "type": "duration"},
    "operating_cash_flow": {"tags": ["NetCashProvidedByUsedInOperatingActivities",
                                     "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
                            "unit": "USD", "type": "duration"},
    "capex": {"tags": ["PaymentsToAcquirePropertyPlantAndEquipment",
                       "PaymentsToAcquireProductiveAssets"],
              "unit": "USD", "type": "duration"},
    "dep_amort": {"tags": ["DepreciationDepletionAndAmortization",
                           "DepreciationAmortizationAndAccretionNet",
                           "DepreciationAndAmortization", "Depreciation"],
                  "unit": "USD", "type": "duration"},
    "total_assets": {"tags": ["Assets"], "unit": "USD", "type": "instant"},
    "total_liabilities": {"tags": ["Liabilities"], "unit": "USD", "type": "instant"},
    "stockholders_equity": {"tags": ["StockholdersEquity",
                                     "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
                            "unit": "USD", "type": "instant"},
    "cash": {"tags": ["CashAndCashEquivalentsAtCarryingValue"], "unit": "USD", "type": "instant"},
    "long_term_debt": {"tags": ["LongTermDebtNoncurrent", "LongTermDebt"], "unit": "USD", "type": "instant"},
    "shares_outstanding": {"tags": ["CommonStockSharesOutstanding",
                                    "EntityCommonStockSharesOutstanding"],
                           "unit": "shares", "type": "instant"},
}

ANNUAL_FORMS = {"10-K", "20-F"}


# ====================================================================
# 純解析層（無網路，可單元測試）
# ====================================================================

def _candidate_unit_lists(facts: dict, metric: dict):
    """產生此指標所有『存在且有資料』的候選標籤 (tag, units_list)，依候選優先序。"""
    u = metric["unit"]
    for ns in ("us-gaap", "dei"):
        block = facts.get(ns, {})
        for tag in metric["tags"]:
            units = block.get(tag, {}).get("units", {})
            if u in units and units[u]:
                yield tag, units[u]


def metric_forms(facts: dict, metric: dict):
    """此指標所有候選標籤出現過的申報表單集合（供外國發行人判斷）。"""
    forms = set()
    for _tag, units in _candidate_unit_lists(facts, metric):
        forms |= {f.get("form") for f in units}
    return forms


def annual_series(units_list, is_duration):
    """取年度值，依『期間結束日(end)』選取與去重。
    不靠 XBRL 的 fy 欄位——因 fy 是『申報文件的財年』，非曆年公司會錯位。
    duration 類只取涵蓋整年（350~380 天）的期間，排除季度/累計。
    回傳 {end: {'val':, 'end':, 'filed':, 'fy_label':}}。"""
    by_end = {}
    for f in units_list:
        if f.get("form") not in ANNUAL_FORMS:
            continue
        end = f.get("end")
        if not end:
            continue
        if is_duration:
            try:
                s = datetime.fromisoformat(f["start"]).date()
                e = datetime.fromisoformat(f["end"]).date()
                if not (350 <= (e - s).days <= 380):
                    continue
            except Exception:
                continue
        # 同一結束日多次申報（重編）→ 取 filed 最新者
        if end not in by_end or f.get("filed", "") > by_end[end]["filed"]:
            by_end[end] = {"val": f.get("val"), "end": end,
                           "filed": f.get("filed", ""), "fy_label": f"FY{end[:4]}"}
    return by_end


def latest_quarter_value(units_list, is_duration):
    """最新一季值。duration 優先取 ~3 個月期間（80~100 天），避免抓到 YTD 累計。
    回傳 {'val':, 'end':, 'filed':, 'note':} 或 None。"""
    rows = [f for f in units_list if f.get("form") == "10-Q"]
    if not rows:
        return None
    note = None
    if is_duration:
        def span_days(f):
            try:
                s = datetime.fromisoformat(f["start"]).date()
                e = datetime.fromisoformat(f["end"]).date()
                return (e - s).days
            except Exception:
                return None
        three_mo = [f for f in rows if (span_days(f) or 0) and 80 <= span_days(f) <= 100]
        if three_mo:
            rows = three_mo
        else:
            note = "僅找到累計期間值（非單季），請留意"
    rows.sort(key=lambda f: (f.get("end", ""), f.get("filed", "")))
    last = rows[-1]
    return {"val": last.get("val"), "end": last.get("end"),
            "filed": last.get("filed", ""), "note": note}


def merged_annual_series(facts: dict, metric: dict):
    """合併『所有』候選標籤的年度序列，逐年保留實際採用的標籤。

    為何要合併而非只取第一個有資料的標籤：公司可能在某年改換 XBRL 標籤
    （例：NVDA 在 FY2022 後營收標籤從 RevenueFromContractWithCustomerExcludingAssessedTax
    改用 Revenues）。只取第一個有資料的標籤會讓年度永遠卡在舊標籤涵蓋的舊年份。
    合併後新舊年份各自由對應標籤補齊，年度軸才能接到最近年度。

    回傳 (by_end, tags_used)：
      by_end   : {end: {'val','end','filed','fy_label','tag'}}（同一結束日取 filed 最新者）
      tags_used: 實際貢獻了某一年數值的標籤清單（依候選優先序）
    """
    is_dur = metric["type"] == "duration"
    by_end = {}
    for tag, units in _candidate_unit_lists(facts, metric):
        for end, rec in annual_series(units, is_dur).items():
            if end not in by_end or rec["filed"] > by_end[end]["filed"]:
                rec = dict(rec)
                rec["tag"] = tag
                by_end[end] = rec
    contributing = {rec["tag"] for rec in by_end.values()}
    tags_used = [t for t in metric["tags"] if t in contributing]
    return by_end, (tags_used or None)


def merged_latest_quarter(facts: dict, metric: dict):
    """跨所有候選標籤找最新一季（10-Q）。回傳 {'val','end','filed','note','tag'} 或 None。"""
    is_dur = metric["type"] == "duration"
    best = None
    for tag, units in _candidate_unit_lists(facts, metric):
        q = latest_quarter_value(units, is_dur)
        if not q:
            continue
        q = dict(q)
        q["tag"] = tag
        key = (q.get("end") or "", q.get("filed") or "")
        if best is None or key > (best.get("end") or "", best.get("filed") or ""):
            best = q
    return best


def _safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def parse_companyfacts(data: dict, n_years=N_YEARS):
    """主解析：回傳 (annual_block, latest_quarter_block, warnings, is_foreign)。"""
    warnings = []
    facts = data.get("facts", {})

    # 判斷是否外國發行人（無 10-K 但有 20-F）
    is_foreign = False
    rev_forms = metric_forms(facts, METRICS["revenue"])
    if rev_forms and "10-K" not in rev_forms and "20-F" in rev_forms:
        is_foreign = True
        warnings.append("此為外國發行人（申報 20-F），財報顆粒度可能較粗")

    # 先用 revenue 決定目標年度（以期間結束日為軸；fallback net_income）
    # 用合併後的年度序列：跨候選標籤取最近 N 年，避免換標籤造成年度卡死
    target_ends = []
    for key in ("revenue", "net_income"):
        by_end, _ = merged_annual_series(facts, METRICS[key])
        ends = sorted(by_end.keys())
        if ends:
            target_ends = ends[-n_years:]
            break
    if not target_ends:
        warnings.append("找不到可用的年度營收/淨利，無法建立會計年度軸")

    # 防呆：最新年度若距今過久，多半代表最新年報尚未抓到或標籤抓取不全，須提醒
    if target_ends:
        try:
            latest_end = datetime.fromisoformat(target_ends[-1]).date()
            age_days = (datetime.now(timezone.utc).date() - latest_end).days
            if age_days > 400:
                warnings.append(
                    f"最新會計年度結束日為 {target_ends[-1]}，距今約 {age_days} 天（>400 天）。"
                    f"年度資料可能過舊或候選標籤抓取不全，請人工確認最新年報是否已發布、"
                    f"或該指標是否又換了 XBRL 標籤"
                )
        except Exception:
            pass

    fiscal_labels = [f"FY{e[:4]}" for e in target_ends]

    # 逐指標取值並對齊到 target_ends
    metrics_out = {}
    as_of_filed = {}  # fy_label -> 最新 filed（取各指標中最晚者）
    quarter_out = {}
    for name, mdef in METRICS.items():
        by_end, tags_used = merged_annual_series(facts, mdef)
        if not tags_used:
            metrics_out[name] = {"values": [None] * len(target_ends),
                                 "tags_used": None, "unit": mdef["unit"]}
            warnings.append(f"指標「{name}」找不到任何候選標籤，已留空")
            continue
        vals = []
        win_tags = set()  # 只記錄真正貢獻了「視窗內年度」數值的標籤
        for end, label in zip(target_ends, fiscal_labels):
            rec = by_end.get(end)
            vals.append(rec["val"] if rec else None)
            if rec:
                win_tags.add(rec["tag"])
                if rec["filed"] > as_of_filed.get(label, ""):
                    as_of_filed[label] = rec["filed"]
        # 依候選優先序列出；視窗內無任何值時退回完整序列的標籤（值會是全 None）
        out_tags = [t for t in mdef["tags"] if t in win_tags] or tags_used
        metrics_out[name] = {"values": vals, "tags_used": out_tags, "unit": mdef["unit"]}

        q = merged_latest_quarter(facts, mdef)
        if q:
            quarter_out[name] = {"value": q["val"], "tags_used": [q["tag"]], "unit": mdef["unit"]}
            if q.get("note"):
                warnings.append(f"最新季度「{name}」：{q['note']}")

    # 衍生指標（僅在輸入齊全時計算）
    rev = metrics_out["revenue"]["values"]
    gp = metrics_out["gross_profit"]["values"]
    ni = metrics_out["net_income"]["values"]
    ocf = metrics_out["operating_cash_flow"]["values"]
    capex = metrics_out["capex"]["values"]
    oi = metrics_out["operating_income"]["values"]
    da = metrics_out["dep_amort"]["values"]

    def pct(a, b):
        r = _safe_div(a, b)
        return round(r * 100, 2) if r is not None else None

    derived = {
        "gross_margin_pct": [pct(gp[i], rev[i]) for i in range(len(target_ends))],
        "net_margin_pct": [pct(ni[i], rev[i]) for i in range(len(target_ends))],
        "revenue_yoy_pct": [None] + [pct(rev[i] - rev[i - 1], rev[i - 1])
                                     if rev[i] is not None and rev[i - 1] not in (None, 0) else None
                                     for i in range(1, len(target_ends))],
        # FCF = 營業現金流 - 資本支出（capex 為正值的支出）
        "free_cash_flow": [(ocf[i] - capex[i]) if ocf[i] is not None and capex[i] is not None else None
                           for i in range(len(target_ends))],
        # EBITDA ≈ 營業利益 + 折舊攤銷（供 skill 計算 EV/EBITDA）
        "ebitda": [(oi[i] + da[i]) if oi[i] is not None and da[i] is not None else None
                   for i in range(len(target_ends))],
    }

    annual_block = {
        "fiscal_years": fiscal_labels,
        "period_end_dates": target_ends,
        "as_of_filed": {label: as_of_filed.get(label) for label in fiscal_labels},
        "metrics": metrics_out,
        "derived": derived,
    }
    return annual_block, quarter_out, warnings, is_foreign


# ====================================================================
# 抓取層（需網路）
# ====================================================================

def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.info().get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def ticker_to_cik(ticker):
    data = _get_json("https://www.sec.gov/files/company_tickers.json")
    for row in data.values():
        if row.get("ticker", "").upper() == ticker.upper():
            return str(row["cik_str"]).zfill(10), row.get("title")
    return None, None


def fetch_companyfacts(cik10):
    return _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json")


# ====================================================================
# 組裝與輸出
# ====================================================================

DOC = {
    "schema_version": "結構版本。skill 應先檢查，不認得就警告而非默默讀錯。",
    "meta.entity_name": "EDGAR 登記的公司全名，用於核對沒抓錯標的",
    "meta.is_foreign_filer": "true 表示申報 20-F（外國發行人），數字顆粒度可能較粗",
    "annual.fiscal_years": "近 N 個會計年度標籤（由期間結束日推得，未必對齊曆年）",
    "annual.period_end_dates": "各會計年度的實際期間結束日，年度選取與對齊以此為準",
    "annual.as_of_filed": "各年度資料的申報日，供報告標註 as-of 日期",
    "annual.metrics.<name>.tags_used": "實際貢獻數值的 XBRL 標籤清單（可能跨年換標籤而有多個），便於追溯",
    "annual.metrics.<name>.values": "對齊 fiscal_years 的數值陣列，缺值為 null",
    "annual.derived": "衍生指標：毛利率%、淨利率%、營收年增率%、自由現金流、EBITDA（僅在輸入齊全時計算）",
    "latest_quarter": "最新一季值（10-Q），損益/現金流項已盡量取單季而非累計",
    "warnings": "所有缺值與品質警告。非空時 data_quality=review",
}


def emit(output, ticker, kind):
    """SAVE_DIR 為空 → 印到 stdout（可用 > 導向）；有值 → 自動存檔到該資料夾。"""
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if SAVE_DIR:
        import os
        folder = os.path.expanduser(SAVE_DIR)
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(folder, f"{ticker}_{kind}_{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已存檔：{path}")
    else:
        print(text)


def build_fin_output(ticker):
    """抓取 + 解析 + 組裝完整輸出（供 main 與 update_history.py 共用）。
    回傳 (output, data)；data 為原始 companyfacts。失敗直接丟例外，由呼叫端決定呈現方式。"""
    cik10, title = ticker_to_cik(ticker)
    if not cik10:
        raise RuntimeError(f"找不到 ticker {ticker} 對應的 CIK（可能非美股或代號錯誤）")
    data = fetch_companyfacts(cik10)
    annual_block, quarter_out, warnings, is_foreign = parse_companyfacts(data)
    output = {
        "schema_version": SCHEMA_VERSION,
        "_doc": DOC,
        "meta": {
            "ticker": ticker,
            "cik": cik10,
            "entity_name": data.get("entityName") or title,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "SEC EDGAR companyfacts (XBRL)",
            "is_foreign_filer": is_foreign,
            "data_quality": "review" if warnings else "ok",
        },
        "annual": annual_block,
        "latest_quarter": quarter_out,
        "warnings": warnings,
    }
    return output, data


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "用法: python fetch_financials.py TICKER"}, ensure_ascii=False))
        sys.exit(1)
    if "example.com" in USER_AGENT:
        print(json.dumps({"error": "請先把腳本內的 USER_AGENT 改成你的 email，否則 SEC 會 403"},
                         ensure_ascii=False))
        sys.exit(1)

    ticker = sys.argv[1].upper().strip()
    try:
        output, _ = build_fin_output(ticker)
    except Exception as e:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "meta": {"ticker": ticker, "data_quality": "error"},
            "error": f"EDGAR 抓取失敗：{e}",
            "hint": "確認 USER_AGENT 已設 email、ticker 正確、為美國申報公司",
        }, ensure_ascii=False, indent=2))
        sys.exit(2)

    emit(output, ticker, "fin")


if __name__ == "__main__":
    main()
