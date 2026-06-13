#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股價技術面數據抓取腳本（方案 A：本機執行）
----------------------------------------------------------
用途：抓取單一美股的日線資料，計算技術指標，輸出「自我描述」的固定 JSON，
      供 Claude project 的 skill 讀取分析。

設計原則：
  1. 數據層（抓取＋計算）與分析層（skill）分離。本檔只負責產出「確定的數字」。
  2. 自我描述：JSON 自帶 _doc 欄位說明與 schema_version，skill 可泛用讀取。
  3. 容錯：yfinance 主來源 + Nasdaq 官方 API 獨立交叉檢查；抓不到就明確報錯，絕不回半殘資料。
  4. auto_adjust 明寫為 True（用還原權值算均線，避免分割/配息造成假訊號）。

用法：
    python fetch_stock.py AAPL
    python fetch_stock.py AAPL > aapl.json      # 存檔後上傳給 project

依賴：
    pip install yfinance pandas
"""

import os
import sys
import json
import urllib.request
from datetime import datetime, timezone

import pandas as pd

SCHEMA_VERSION = "1.1"  # 1.1：第二來源由 Stooq（已被反爬蟲牆封）改為 Nasdaq 官方 API；cross_check 欄位改名

# 存檔資料夾。預設＝腳本所在資料夾（本機 Mac 與雲端 GitHub 簽出皆通用，勿寫死絕對路徑，
# 否則雲端 session 會把檔案寫到 repo 外、push 不回來，跨裝置累積即斷裂）。
# 留空字串 "" = 改印到畫面（可用 > 導向）。
SAVE_DIR = os.path.dirname(os.path.abspath(__file__))

# 交叉檢查的容許差異（百分比）。超過則標記警告。
CROSS_CHECK_TOLERANCE_PCT = 1.5
# 最新交易日距今超過幾天就警告（寬鬆涵蓋週末/假日）。
STALENESS_DAYS = 4
# 52 週約略交易日數。
TRADING_DAYS_52W = 252


# ====================================================================
# 純計算層（無網路，可單元測試）
# ====================================================================

def _round(x, n=4):
    return None if x is None or pd.isna(x) else round(float(x), n)


def detect_cross(ma_fast: pd.Series, ma_slow: pd.Series):
    """偵測最近一次黃金/死亡交叉。
    回傳 {type, date, days_ago}；type 為 'golden'/'death'/None。"""
    diff = (ma_fast - ma_slow).dropna()
    if len(diff) < 2:
        return {"type": None, "date": None, "trading_days_ago": None}
    sign = (diff > 0).astype(int)
    changes = sign.diff().fillna(0)
    cross_points = changes[changes != 0]
    if len(cross_points) == 0:
        return {"type": None, "date": None, "trading_days_ago": None}
    last_idx = cross_points.index[-1]
    ctype = "golden" if cross_points.loc[last_idx] > 0 else "death"
    pos = list(diff.index).index(last_idx)
    days_ago = len(diff) - 1 - pos
    date_str = str(last_idx.date()) if hasattr(last_idx, "date") else str(last_idx)
    return {"type": ctype, "date": date_str, "trading_days_ago": int(days_ago)}


def compute_indicators(df: pd.DataFrame):
    """df 需含 'Close' 與 'Volume'，依日期升序。
    回傳 (indicators_dict, warnings_list)。"""
    warnings = []
    close = df["Close"]
    n = len(close)

    # 缺值檢查
    if close.isna().any():
        warnings.append("價格序列含缺值（NaN），技術指標可能失真")

    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()

    if n < 200:
        warnings.append(f"歷史資料僅 {n} 個交易日（<200），MA200 與黃金/死亡交叉不可靠或為空")

    latest_close = float(close.iloc[-1])

    # 均線排列
    cur_ma20, cur_ma50, cur_ma200 = ma20.iloc[-1], ma50.iloc[-1], ma200.iloc[-1]
    alignment = None
    if not any(pd.isna(v) for v in [cur_ma20, cur_ma50, cur_ma200]):
        if cur_ma20 > cur_ma50 > cur_ma200:
            alignment = "多頭排列 (MA20>MA50>MA200)"
        elif cur_ma20 < cur_ma50 < cur_ma200:
            alignment = "空頭排列 (MA20<MA50<MA200)"
        else:
            alignment = "糾結（均線交錯，無明確趨勢）"

    # 趨勢格局（MA50 vs MA200）
    regime = None
    if not pd.isna(cur_ma50) and not pd.isna(cur_ma200):
        regime = "bullish (MA50>MA200)" if cur_ma50 > cur_ma200 else "bearish (MA50<MA200)"

    cross = detect_cross(ma50, ma200)

    # 52 週高低
    window_52w = close.tail(TRADING_DAYS_52W)
    hi = float(window_52w.max())
    lo = float(window_52w.min())
    if n < TRADING_DAYS_52W:
        warnings.append(f"資料不足 252 日，52 週高低以現有 {n} 日近似計算")

    # 量能
    vol_latest = float(df["Volume"].iloc[-1]) if "Volume" in df else None
    vol_avg20 = float(df["Volume"].tail(20).mean()) if "Volume" in df else None
    vol_ratio = _round(vol_latest / vol_avg20, 2) if vol_avg20 else None

    indicators = {
        "latest_close": _round(latest_close, 2),
        "prev_close": _round(close.iloc[-2], 2) if n >= 2 else None,
        "ma20": _round(cur_ma20, 2),
        "ma50": _round(cur_ma50, 2),
        "ma200": _round(cur_ma200, 2),
        "ma_alignment": alignment,
        "trend_regime": regime,
        "last_cross": cross,
        "high_52w": _round(hi, 2),
        "low_52w": _round(lo, 2),
        "pct_from_52w_high": _round((latest_close / hi - 1) * 100, 2),
        "pct_from_52w_low": _round((latest_close / lo - 1) * 100, 2),
        "volume_latest": int(vol_latest) if vol_latest else None,
        "volume_avg20": int(vol_avg20) if vol_avg20 else None,
        "volume_ratio_vs_avg20": vol_ratio,
    }
    return indicators, warnings


# ====================================================================
# 抓取層（需網路）
# ====================================================================

def fetch_yfinance(ticker: str):
    import yfinance as yf
    t = yf.Ticker(ticker)
    # 明寫 auto_adjust=True：用還原權值，避免分割/配息造成假突破/假跌破
    df = t.history(period="2y", auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError("yfinance 回傳空資料（可能 ticker 錯誤、下市或被限流）")
    # yfinance 常在最後一列塞「未完成/未結算交易日」或空白列：Close 為 NaN（可能仍帶 Volume）。
    # 這種半殘列會讓 latest_close 與所有均線連鎖變成 NaN，且 volume_latest 取到對不上的量。
    # 只保留有收盤價的完整交易日，才能算出可用的技術指標。
    df = df[df["Close"].notna()]
    if df.empty:
        raise RuntimeError("yfinance 資料剔除無收盤價的列後為空（資料異常）")
    name = None
    try:
        info = t.info
        name = info.get("longName") or info.get("shortName")
    except Exception:
        pass
    return df, name


def fetch_nasdaq_latest(ticker: str):
    """獨立第二來源：Nasdaq 官方報價 API。回傳 (last_price, date, is_realtime) 或 (None, None, None)。

    為何用 Nasdaq 而非 Stooq / yfinance 第二端點：
      - Stooq 的 CSV 下載端點已改成 JS 工作量證明反爬蟲牆，純 urllib 無法通過（永久失效）。
      - 用 yfinance 的另一個端點當交叉檢查是「同來源」，對 Yahoo 整體性的資料缺漏（例如
        某交易日 Close 回 NaN）視而不見 → 失去交叉驗證的意義。Nasdaq 是真正獨立來源，
        才抓得出「Yahoo 少了最新交易日收盤」這類問題。
    """
    url = f"https://api.nasdaq.com/api/quote/{ticker}/info?assetclass=stocks"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8"))
        pdata = (j.get("data") or {}).get("primaryData") or {}
        raw = pdata.get("lastSalePrice")  # 形如 "$200.42"
        if not raw or raw in ("N/A", ""):
            return None, None, None
        price = float(raw.replace("$", "").replace(",", "").strip())
        return price, pdata.get("lastTradeTimestamp"), pdata.get("isRealTime")
    except Exception:
        return None, None, None


# ====================================================================
# 組裝與輸出
# ====================================================================

DOC = {
    "schema_version": "JSON 結構版本。skill 應先檢查此值，不認得就警告而非默默讀錯。",
    "meta.ticker": "股票代號（請肉眼核對 company_name 是否為你要分析的公司）",
    "meta.company_name": "公司全名，用於驗證沒抓錯標的",
    "meta.fetched_at_utc": "本次抓取時間（UTC）。skill 應檢查距今是否過久",
    "meta.latest_trading_day": "資料中最新交易日（美東收盤）。台灣早上跑會是前一交易日，屬正常",
    "meta.sources": "實際使用的數據來源",
    "meta.data_quality": "ok=可直接分析；review=有警告，須在報告中標註存疑",
    "indicators": "技術指標。MA 均線、均線排列、趨勢格局、黃金/死亡交叉、52週位置、量能",
    "cross_check": "yfinance 收盤 與 Nasdaq 獨立來源最新成交價交叉比對。mismatch 常代表 Yahoo 缺最新交易日收盤（落後一天）或盤中時點差異，須人工確認",
    "warnings": "所有合理性檢查的警告。非空時 data_quality=review",
}


def build_output(ticker, df, name, indicators, warnings):
    latest_day = df.index[-1]
    latest_day_str = str(latest_day.date()) if hasattr(latest_day, "date") else str(latest_day)

    # 新鮮度檢查
    try:
        days_old = (datetime.now(timezone.utc).date() - latest_day.date()).days
        if days_old > STALENESS_DAYS:
            warnings.append(f"最新交易日為 {latest_day_str}，距今約 {days_old} 天，資料可能過舊，建議重跑")
    except Exception:
        pass

    # Nasdaq 交叉檢查（獨立第二來源）
    nasdaq_price, nasdaq_date, nasdaq_realtime = fetch_nasdaq_latest(ticker)
    yf_close = indicators["latest_close"]
    cross_check = {
        "yfinance_close": yf_close,
        "nasdaq_last_price": _round(nasdaq_price, 2) if nasdaq_price else None,
        "nasdaq_date": nasdaq_date,
        "nasdaq_is_realtime": nasdaq_realtime,
        "diff_pct": None,
        "status": None,
    }
    if nasdaq_price and yf_close:
        diff = abs(nasdaq_price / yf_close - 1) * 100
        cross_check["diff_pct"] = round(diff, 3)
        if diff > CROSS_CHECK_TOLERANCE_PCT:
            cross_check["status"] = "mismatch"
            warnings.append(
                f"yfinance 最新收盤 {yf_close} 與 Nasdaq 最新成交 {nasdaq_price}"
                f"（{nasdaq_date}）差異 {diff:.2f}%（>{CROSS_CHECK_TOLERANCE_PCT}%）。"
                f"常見原因：Yahoo 缺最新交易日收盤而落後一天、除權息日、或盤中即時報價"
                f"與收盤時點不同，建議人工確認最新收盤價"
            )
        else:
            cross_check["status"] = "consistent"
    else:
        cross_check["status"] = "nasdaq_unavailable"
        warnings.append("Nasdaq 交叉檢查不可用（無法取得第二來源），本次僅單一來源")

    return {
        "schema_version": SCHEMA_VERSION,
        "_doc": DOC,
        "meta": {
            "ticker": ticker,
            "company_name": name,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "latest_trading_day": latest_day_str,
            "sources": ["yfinance", "nasdaq"],
            "data_quality": "review" if warnings else "ok",
        },
        "indicators": indicators,
        "cross_check": cross_check,
        "warnings": warnings,
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


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "用法: python fetch_stock.py TICKER"}, ensure_ascii=False))
        sys.exit(1)

    ticker = sys.argv[1].upper().strip()

    try:
        df, name = fetch_yfinance(ticker)
    except Exception as e:
        # 優雅失敗：明確報錯，不回半殘資料
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "meta": {"ticker": ticker, "data_quality": "error"},
            "error": f"主來源 yfinance 抓取失敗：{e}",
            "hint": "請確認 ticker 正確、網路正常；若持續失敗請執行 pip install -U yfinance",
        }, ensure_ascii=False, indent=2))
        sys.exit(2)

    indicators, warnings = compute_indicators(df)
    output = build_output(ticker, df, name, indicators, warnings)
    emit(output, ticker, "stock")


if __name__ == "__main__":
    main()
