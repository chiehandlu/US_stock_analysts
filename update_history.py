#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
歷史累積層腳本 — 在「快照」之上，為每檔股票維護一個只增不減的歷史檔
----------------------------------------------------------
用途：
    python update_history.py NVDA

每次執行做三件事：
  1. 跑既有兩支腳本的抓取邏輯，照常輸出當日快照 JSON（與分開跑兩支腳本行為一致）。
  2. 把關鍵數據合併進 history/{TICKER}_history.json：
     - technical_snapshots：每個交易日一筆技術面軌跡（同日重跑覆蓋，不重複）。
     - financials.annual  ：「全部」會計年度（非只 3 年），只增不減；同年重編取 filed 最新。
     - financials.quarters：「全部」單季序列，只增不減（skill 可算 YoY/QoQ、逐季利潤率）。
  3. 印出累積摘要。

設計原則（延續資料層）：
  - 不改變既有兩支腳本的行為；本檔只是「組合 + 累積」。
  - 冪等：同一天重跑不會堆出重複資料。
  - 寧缺勿爛：某一側抓取失敗時，另一側照常更新；失敗側沿用既有歷史並記入警告。
  - 歷史檔同樣自我描述（_doc + schema_version），可直接上傳 Project 給 skill 讀。

依賴：與兩支腳本相同（yfinance、pandas 在 .venv；本檔自身只用標準函式庫）。
"""

import sys
import os
import json
from datetime import datetime, timezone

import fetch_stock as fs
import fetch_financials as ff

HISTORY_SCHEMA_VERSION = "history-1.1"  # 1.1：新增 price_bars（日K線，滾動視窗覆蓋）
HISTORY_DIR = os.path.join(os.path.expanduser(fs.SAVE_DIR), "history")

DOC = {
    "schema_version": "歷史檔結構版本。skill 應先檢查，不認得就警告而非默默讀錯。",
    "meta.runs": "此歷史檔被更新過的次數",
    "technical_snapshots": "技術面軌跡，依交易日排序，每個交易日一筆（同日重跑覆蓋）。"
                           "欄位同 fetch_stock 輸出的 indicators；cross_check_status 為當次雙來源比對結果",
    "financials.annual": "全部會計年度（只增不減），鍵為期間結束日。metrics 缺某指標代表該年無資料；"
                         "tags 記錄各指標實際採用的 XBRL 標籤；filed 為該年資料最新申報日",
    "financials.quarters": "全部單季序列（只增不減），鍵為季末日。損益/現金流為單季值（80~100天），"
                           "資產負債為 10-Q 時點值",
    "price_bars": "近 2 年日線 [日期,開,高,低,收,量]，每次更新覆蓋（滾動視窗，非累積），供報告畫日K線",
    "financials.annual.<end>.derived": "該年衍生指標：毛利率%、淨利率%、自由現金流、EBITDA（輸入齊全才計算）",
    "last_run_warnings": "最近一次更新的警告（含兩支腳本的 warnings 與抓取失敗訊息）",
}


def _now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ====================================================================
# 歷史檔載入 / 合併（純邏輯，可單元測試）
# ====================================================================

def new_history(ticker):
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "_doc": DOC,
        "meta": {
            "ticker": ticker,
            "company_name": None,
            "entity_name": None,
            "cik": None,
            "created_at_utc": _now_utc(),
            "last_updated_utc": None,
            "runs": 0,
        },
        "technical_snapshots": [],
        "financials": {"annual": {}, "quarters": {}},
        "price_bars": [],
        "last_run_warnings": [],
    }


def load_history(path, ticker):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            hist = json.load(f)
        ver = hist.get("schema_version", "")
        # 同主版本（history-1.x）相容、可直接續用（欄位增補是向後相容的）；主版本不符才擋下
        if not ver.startswith("history-1."):
            raise RuntimeError(
                f"歷史檔 schema 為 {ver}，本腳本為 {HISTORY_SCHEMA_VERSION}（主版本不符），"
                f"請先處理版本差異（避免默默混寫壞檔）")
        hist.setdefault("price_bars", [])  # 由 1.0 升上來時補欄位
        hist["schema_version"] = HISTORY_SCHEMA_VERSION
        return hist
    return new_history(ticker)


def merge_periods(store, new):
    """把本次抓到的期間資料併入歷史。
    規則：本次重抓的是 SEC 完整最新資料，視為權威 → 同期間同指標直接覆蓋；
    歷史中有、但本次缺的指標保留（防 SEC 端偶發缺漏導致歷史倒退）。"""
    for end, rec in new.items():
        cur = store.get(end)
        if cur is None:
            store[end] = rec
            continue
        cur["metrics"].update(rec["metrics"])
        cur["tags"].update(rec["tags"])
        if rec.get("filed", "") > cur.get("filed", ""):
            cur["filed"] = rec["filed"]
        if rec.get("fy_label"):
            cur["fy_label"] = rec["fy_label"]
        if rec.get("derived"):
            cur["derived"] = rec["derived"]
    return dict(sorted(store.items()))


def merge_snapshot(snaps, snap):
    """同一交易日（as_of）重跑 → 覆蓋；否則 append。依日期排序。"""
    snaps = [s for s in snaps if s.get("as_of") != snap["as_of"]]
    snaps.append(snap)
    snaps.sort(key=lambda s: s.get("as_of") or "")
    return snaps


# ====================================================================
# 財報：全年度 / 全單季序列（直接從 companyfacts 萃取）
# ====================================================================

def build_annual_periods(facts):
    """全部會計年度。逐指標用合併候選標籤的年度序列，再按期間結束日聚合。

    期間軸防呆：先用營收/淨利（整年 duration）的結束日建立「有效年度軸」，
    其他指標只對齊到軸上。否則 dei 類標籤（如 EntityCommonStockSharesOutstanding）
    的 end 是申報封面日而非年度結束日，會長出一堆只有單一指標的碎片年度。"""
    axis = set()
    for key in ("revenue", "net_income"):
        by_end, _ = ff.merged_annual_series(facts, ff.METRICS[key])
        axis |= set(by_end.keys())

    periods = {}
    for name, mdef in ff.METRICS.items():
        by_end, _ = ff.merged_annual_series(facts, mdef)
        for end, rec in by_end.items():
            if end not in axis:
                continue
            p = periods.setdefault(end, {"fy_label": rec["fy_label"], "filed": "",
                                         "metrics": {}, "tags": {}})
            p["metrics"][name] = rec["val"]
            p["tags"][name] = rec["tag"]
            if rec["filed"] > p["filed"]:
                p["filed"] = rec["filed"]

    def pct(a, b):
        return round(a / b * 100, 2) if a is not None and b not in (None, 0) else None

    for p in periods.values():
        m = p["metrics"]
        ocf, capex = m.get("operating_cash_flow"), m.get("capex")
        oi, da = m.get("operating_income"), m.get("dep_amort")
        p["derived"] = {
            "gross_margin_pct": pct(m.get("gross_profit"), m.get("revenue")),
            "net_margin_pct": pct(m.get("net_income"), m.get("revenue")),
            "free_cash_flow": (ocf - capex) if ocf is not None and capex is not None else None,
            "ebitda": (oi + da) if oi is not None and da is not None else None,
        }
    return periods


def quarter_series(facts, metric):
    """單一指標的全部單季序列（跨候選標籤合併）。
    duration 只取 80~100 天的單季期間（排除 YTD 累計）；instant 取 10-Q 時點值。
    同一季末多次申報取 filed 最新。回傳 {end: {'val','filed','tag'}}。"""
    is_dur = metric["type"] == "duration"
    by_end = {}
    for tag, units in ff._candidate_unit_lists(facts, metric):
        for f in units:
            if f.get("form") != "10-Q":
                continue
            end = f.get("end")
            if not end:
                continue
            if is_dur:
                try:
                    s = datetime.fromisoformat(f["start"]).date()
                    e = datetime.fromisoformat(f["end"]).date()
                    if not (80 <= (e - s).days <= 100):
                        continue
                except Exception:
                    continue
            if end not in by_end or f.get("filed", "") > by_end[end]["filed"]:
                by_end[end] = {"val": f.get("val"), "filed": f.get("filed", ""), "tag": tag}
    return by_end


def build_quarter_periods(facts):
    """全部單季。期間軸防呆同 build_annual_periods：
    軸 = 各 duration 指標的單季結束日聯集（即真正的季末日）；
    instant 指標只在軸上才收，擋掉 dei 封面日造成的碎片季度。"""
    axis = set()
    for name, mdef in ff.METRICS.items():
        if mdef["type"] == "duration":
            axis |= set(quarter_series(facts, mdef).keys())

    periods = {}
    for name, mdef in ff.METRICS.items():
        for end, rec in quarter_series(facts, mdef).items():
            if end not in axis:
                continue
            p = periods.setdefault(end, {"filed": "", "metrics": {}, "tags": {}})
            p["metrics"][name] = rec["val"]
            p["tags"][name] = rec["tag"]
            if rec["filed"] > p["filed"]:
                p["filed"] = rec["filed"]
    return periods


# ====================================================================
# 兩側更新（需網路）
# ====================================================================

def update_technical(hist, ticker, run_warnings):
    df, name = fs.fetch_yfinance(ticker)
    indicators, warns = fs.compute_indicators(df)
    output = fs.build_output(ticker, df, name, indicators, warns)
    fs.emit(output, ticker, "stock")  # 照常輸出當日快照
    snap = {
        "as_of": output["meta"]["latest_trading_day"],
        "fetched_at_utc": output["meta"]["fetched_at_utc"],
        "data_quality": output["meta"]["data_quality"],
        "cross_check_status": output["cross_check"]["status"],
        "indicators": output["indicators"],
    }
    hist["technical_snapshots"] = merge_snapshot(hist["technical_snapshots"], snap)
    hist["price_bars"] = output.get("price_bars", [])  # 滾動視窗：每次以最新 2 年覆蓋
    if name:
        hist["meta"]["company_name"] = name
    run_warnings.extend(output["warnings"])


def update_financials(hist, ticker, run_warnings):
    output, data = ff.build_fin_output(ticker)
    ff.emit(output, ticker, "fin")  # 照常輸出當日快照
    facts = data.get("facts", {})
    hist["meta"]["entity_name"] = output["meta"]["entity_name"]
    hist["meta"]["cik"] = output["meta"]["cik"]
    fin = hist["financials"]
    fin["annual"] = merge_periods(fin["annual"], build_annual_periods(facts))
    fin["quarters"] = merge_periods(fin["quarters"], build_quarter_periods(facts))
    run_warnings.extend(output["warnings"])


# ====================================================================
# 主流程
# ====================================================================

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "用法: python update_history.py TICKER"}, ensure_ascii=False))
        sys.exit(1)
    ticker = sys.argv[1].upper().strip()

    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f"{ticker}_history.json")
    hist = load_history(path, ticker)

    run_warnings = []
    ok_tech = ok_fin = False
    try:
        update_technical(hist, ticker, run_warnings)
        ok_tech = True
    except Exception as e:
        run_warnings.append(f"本次技術面抓取失敗，沿用既有歷史：{e}")
    try:
        update_financials(hist, ticker, run_warnings)
        ok_fin = True
    except Exception as e:
        run_warnings.append(f"本次財報抓取失敗，沿用既有歷史：{e}")

    if not ok_tech and not ok_fin and hist["meta"]["runs"] == 0:
        print(json.dumps({"error": "兩側抓取皆失敗且無既有歷史，未建立歷史檔",
                          "warnings": run_warnings}, ensure_ascii=False, indent=2))
        sys.exit(2)

    hist["meta"]["runs"] += 1
    hist["meta"]["last_updated_utc"] = _now_utc()
    hist["last_run_warnings"] = run_warnings

    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(hist, ensure_ascii=False, indent=2))

    snaps = hist["technical_snapshots"]
    annual = hist["financials"]["annual"]
    quarters = hist["financials"]["quarters"]
    print(f"已更新歷史檔：{path}")
    print(f"  技術面軌跡：{len(snaps)} 筆（最新 as_of={snaps[-1]['as_of'] if snaps else '-'}）")
    print(f"  年度資料：{len(annual)} 年（{min(annual)[:4] if annual else '-'} ~ {max(annual)[:4] if annual else '-'}）")
    print(f"  單季資料：{len(quarters)} 季")
    if run_warnings:
        print(f"  ⚠ 本次警告 {len(run_warnings)} 則（詳見歷史檔 last_run_warnings）")


if __name__ == "__main__":
    main()
