# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Python CLIs that fetch US-stock data locally and emit **self-describing JSON**, plus a Claude Code skill (`.claude/skills/analyze/`) that consumes the JSON to produce investment analysis. Data layer produces verified numbers; interpretation happens in the skill. Comments and docs are in Traditional Chinese.

**一行指令分析**:在 Claude Code / Cowork 中說「幫我評估NVDA」(或 `/analyze NVDA`),analyze skill 會自動跑 `update_history.py` 抓最新數據 → 讀 `history/{TICKER}_history.json` → 依 M2~M7 方法論與 0-10 評分 Rubric 產出報告,並存成 HTML 到 `reports/` 自動開啟(同步存機器可讀的 `_report_*.json` 摘要)。說「快速評估」走快速模式(不網搜,只用本機數據);提供一或多則新聞/連結要求查證時走**消息事件分析模式(M8)**(分群 → 逐事件查證 → 先讀既有報告 → 逐維影響評估 → 跨事件彙總 + 觀察點 → 跑 `build_news_report.py` 產出**獨立**消息報告)。方法論同步維護於 claude.ai Project 的自訂指令(網頁版仍需手動上傳歷史檔);**新增 M8 後 Project 自訂指令需手動同步**。

- `fetch_stock.py TICKER` — daily price/technical indicators (MAs, alignment, golden/death cross, 52w range, volume) via yfinance, cross-checked against Nasdaq's official quote API (獨立第二來源).
- `fetch_financials.py TICKER` — XBRL financial facts (3 fiscal years + latest quarter) from SEC EDGAR companyfacts.
- `update_history.py TICKER` — 累積層（建議日常入口）：跑上面兩支的抓取邏輯、照常輸出當日快照，並把數據合併進 `history/{TICKER}_history.json`：技術面軌跡（每交易日一筆，同日重跑覆蓋）、全部會計年度與全部單季序列（只增不減）。冪等；單側抓取失敗時另一側照常更新。歷史檔自我描述，可直接上傳 Project。
  - 期間軸防呆：年度/季度軸由營收/淨利（duration）的結束日建立，其他指標只對齊到軸上——否則 dei 標籤（端點日=申報封面日）會長出單指標碎片期間。
- `inject_kline.py TICKER REPORT_HTML` — 把 history 的 `price_bars` 畫成自包含互動日K線（canvas + 內嵌資料 + 純 JS，含 MA20/50/200、成交量、十字游標即時 OHLC、1月/半年/1年/2年 切換），注入報告 HTML 的 `<!-- KLINE -->` 佔位符。離線可渲染。
- `build_report.py TICKER content.json` — **固定格式報告產生器**（解決報告版面浮動）：版面/CSS/章節/圖表畫法全寫死在程式裡，模型只產出「內容 JSON」（分數、各段文字、風險清單…），程式讀 history（財報/指標/日K線）+ 內容 → 輸出版面 100% 一致的 HTML（內含所有圖表與日K線）+ 摘要 JSON。重用 `inject_kline` 的 K 線區塊。analyze skill 第 4–5 步即走此流程。
- `build_news_report.py TICKER content.json` — **M8 消息事件分析報告產生器**：同 `build_report.py` 哲學（版面/章節寫死、模型只產內容 JSON），重用其 CSS（暗色視覺單一來源）。輸入消息事件 content JSON（新聞清單、各事件查證/影響/對六維評分影響、跨事件彙總、關鍵觀察點）→ 輸出**獨立** `reports/{TICKER}_news_{date}.html`（同日重跑自動加序號不覆蓋）+ 機器可讀摘要 `reports/{TICKER}_news_{date}.json`（供後續追蹤）。analyze skill 消息事件分析模式（M8）即走此流程。

## 互動入口（每個 session 都遵守）

當使用者的輸入**只有一個股票代碼**（1~5 個英文字母、無其他文字，如「NVDA」「aapl」「tsm」）時，不要直接開始分析，先用 AskUserQuestion 工具跳出選單讓使用者選擇：

1. **快速評估** — 不網搜，只用本機數據，最快（analyze skill 快速模式）
2. **完整評估** — M2~M7 全跑，含法說會/同業/風險網搜（analyze skill 完整模式）
3. **消息事件分析（M8）** — 查證一或多則新聞/連結、跟既有報告比對並產出獨立消息報告（analyze skill 消息事件分析模式；選此項後先追問「請貼上或描述你看到的消息／連結（可多則）」）

（AskUserQuestion 會自動附「Other」選項，即為「其他」，使用者可自行輸入需求。）

使用者選定後，依 analyze skill 的對應模式執行。若輸入是「代碼＋明確指令」（如「快速評估NVDA」「NVDA 我看到一則消息…」）則不用跳選單，直接執行對應模式。

## 報告連結（Pages）— 跨裝置開啟（每個 session 都遵守）

雲端 session(手機)是 push `claude/*` → `auto-merge.yml` 併入 `main` → GitHub Pages 重新部署,三段非同步(整段約 1~3 分鐘)網址才會活。**規則:先確認 Pages 部署成功(curl 輪詢網址回 `200`,或查 `pages build and deployment` workflow=success),才把連結給使用者;確認成功前不要請使用者開啟網址**——提早開會拿到 404 並被瀏覽器/CDN 快取,之後檔案上線了仍回舊 404(這就是「merge 回 main 了還是打不開」的元兇)。給連結時**一律附快取破解參數** `?t={UNIX秒}`,且 **REPO 保持原大小寫**(github.io 路徑大小寫敏感,如 `US_stock_analysts`)。救急:無痕視窗或在網址後加 `?t=任意數字` 即可繞開快取。詳見 analyze skill 第 5–6 步。

## 執行環境（重要）

系統 Python（Homebrew）沒有裝 yfinance/pandas，且 PEP 668 禁止全域安裝。已建立專案虛擬環境 `.venv`，依賴裝在裡面。**一律用 venv 直譯器執行**：

```
.venv/bin/python update_history.py NVDA      # 日常用這個：快照 + 累積歷史一次完成
.venv/bin/python fetch_stock.py NVDA         # 只要單次技術面快照時
.venv/bin/python fetch_financials.py NVDA    # 只要單次財報快照時
```

直接用 `python3` 會 `ModuleNotFoundError`。`fetch_financials.py` 只用標準函式庫，但統一走 venv 較省事。

**雲端 session（手機/方案 A）**：無 `.venv`，改 `pip install -q -r requirements.txt` 後用 `python3 ...`。analyze skill 第 1 步已內建此分支判斷。

## 跨裝置同步（方案 A：手機也能跑）

程式碼與累積資料放 GitHub，手機透過 claude.ai 的 Claude Code 雲端 session 即可執行整套（雲端沙箱簽出 repo → 跑腳本 → 分析 → push 回 repo）。要點：

- 存檔路徑用 `os.path.dirname(os.path.abspath(__file__))`（腳本所在資料夾），本機與雲端皆通用，**勿改回絕對路徑**。
- 版控範圍（見 `.gitignore`）：`history/`、`reports/` 要進 repo（累積大腦＋報告，跨裝置共享）；根目錄當日快照 `*_stock_*.json`/`*_fin_*.json`、`.venv/`、`__pycache__/` 不進。
- 累積靠 git：每次跑前 `git pull`、跑後 push `history/`＋`reports/`。雲端 session 用完即毀，不 push 則更新遺失。兩裝置避免同時跑同一檔以免衝突。
- 報告連結要等 Pages 部署確認成功才給（帶 `?t=` 破快取），規則見上方「報告連結（Pages）」一節。

## 目前狀態與待辦（先讀這段）

兩支腳本都已在 **NVDA 真實資料上實跑並逐項驗證通過**（2026-06-11）。資料層本身可用，尚未接上 claude.ai 的 Project。

**已驗證 / 已修的項目：**

- `fetch_stock.py`（schema 1.1）
  - 修掉 yfinance「未結算交易日」半殘列（Close=NaN 仍帶量）導致 `latest_close` 與全部均線連鎖變 null 的 bug → 抓取後 `df = df[df["Close"].notna()]` 剔除。
  - 第二來源 **Stooq 改成 Nasdaq 官方 API**：Stooq 的 CSV 下載端點已改成 JS 工作量證明反爬蟲牆，純 urllib 永久過不了；且用 yfinance 第二端點屬「同來源」，看不到 Yahoo 整體資料缺漏。Nasdaq 是真正獨立來源，NVDA 實跑時即抓到「Yahoo 缺最新交易日收盤、落後一天」並標 `mismatch`。`cross_check` 欄位改名為 `nasdaq_last_price/nasdaq_date/nasdaq_is_realtime`。
- `fetch_financials.py`（schema 1.3）
  - **重要**：先前 CLAUDE.md 記載「已修到 1.3」，但程式碼實際還停在 1.2 的 first-match 邏輯（那次修改沒存進檔）。本次已真正實作：`find_metric_units`（只取第一個有資料的標籤）→ `merged_annual_series`／`merged_latest_quarter`，**合併所有候選標籤**。NVDA 因 FY2022 後營收標籤由 `RevenueFromContractWithCustomerExcludingAssessedTax` 改用 `Revenues`，舊邏輯年度卡在 FY2020–22；修後已接到 **FY2024/2025/2026（營收 60.9B／130.5B／215.9B）**。
  - `tag_used`（單一）→ `tags_used`（清單，只列實際貢獻視窗內數值的標籤）。
  - 新增「最新年度結束日距今 >400 天」防呆警告，杜絕再次靜默回陳舊資料。

**已完成（2026-06-11 後續）：** 新增累積層 `update_history.py`（schema `history-1.0`），NVDA 實跑驗證：19 個年度（FY2008–FY2026）、54 個單季、技術面軌跡，連跑兩次確認冪等（只動時間戳與 runs）。經 `fetch_financials.py` 小幅重構：抓取+組裝抽成 `build_fin_output(ticker)` 供共用。

**下一步任務（claude.ai Project，Claude Code 不執行，只列步驟）：**

1. 進 claude.ai 建立／開啟 Project，把分析用的自訂指令（system prompt）貼進 Project 設定。
2. 上傳分析藍圖／skill 說明文件到 Project 知識庫。
3. 本機跑 `update_history.py TICKER`，把 `history/{TICKER}_history.json` 上傳給 Project 由 skill 分析（一檔在手，現在＋歷史都有）。

**驗證清單（保留供日後換股票或改版後重新核對）：**

- 股價：`latest_close`／各均線是否為數值（非 null）？`cross_check.status` 是 consistent 還是 mismatch（mismatch 多半是 Yahoo 缺最新交易日收盤，須人工確認）？
- 財報 `fiscal_years` / `period_end_dates` 是否為最近年份（非卡舊）？
- 財報 `revenue` 最新一年量級是否合理？`revenue.tags_used` 用了哪些標籤（換標籤的公司應能看到正確的新標籤）？
- 財報 `capex`、`free_cash_flow`、`ebitda` 等衍生指標是否有值而非全 null？
- `data_quality` 為 `ok` 或 `review`？`warnings` 內容是否都已理解？