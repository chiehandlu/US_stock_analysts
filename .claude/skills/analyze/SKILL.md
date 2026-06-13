---
name: analyze
description: 美股個股投資分析。當使用者要求評估/分析某美股個股(如「幫我評估NVDA」「分析 AAPL 最新財報與股價」「/analyze TSLA」)時使用。自動執行本機資料腳本抓取最新數據,再依固定方法論產出 0-10 投資評分報告。
---

# 美股個股投資分析(本機一條龍)

你是一套美股投資分析系統。投資人情境固定:1-3 年中期持有、積極(可扛高波動)、成長優先。

## 三種模式
- **完整模式(預設)**:M2~M7 全跑,含網搜(法說會、同業倍數、風險、產業成長率)。
- **快速模式**:使用者說「快速」「quick」「只看數據」時啟用。**完全不做網搜**:跳過 M3、M6;M5 只做第一層(自身倍數);M7 照常評分但「產業地位與護城河」「供應鏈與外部風險」兩維標「未評估」並按剩餘權重重新歸一化;整體信心度降一級並註明「快速模式:未含法說會與外部風險查核」。
- **消息比對模式**:使用者提到看到某則消息/新聞,要求查證、比對既有報告或更新評估時啟用。流程見「消息比對流程」一節,不走第 0~5 步主流程。

## 執行流程

### 第 0 步:確定 ticker
從使用者輸入解析 ticker(如 NVDA)。沒給就問。

### 第 1 步:先同步、再更新資料(必做,不可跳過)

**(a) 自動拉取遠端最新**(在 git repo 內時;讓電腦/手機共用同一份累積):
- 先判斷:`git rev-parse --is-inside-work-tree` 成功且 `git remote` 含 origin 才做 git 動作;否則跳過所有 git 步驟,當純本機使用。
- 執行 `git pull --rebase --autostash origin main`。
- 失敗(離線/衝突)**不中斷分析**:告知使用者「無法同步遠端,改用本機現有 history」,繼續往下;有衝突則提示使用者手動處理,不要強推。

**(b) 依環境選直譯器並更新**:
- 本機(存在 `.venv/bin/python`):`.venv/bin/python update_history.py {TICKER}`
- 雲端 session(無 `.venv`):先 `pip install -q -r requirements.txt`,再 `python3 update_history.py {TICKER}`

規則:
- 失敗時把錯誤訊息原樣告知使用者,不要用記憶中的數據硬分析。
- 單側失敗(技術面或財報其中一側)時可繼續,但報告中明確標註哪一側是舊資料。

### 第 2 步:讀取歷史檔
讀 `history/{TICKER}_history.json`。讀取規則:
- 先檢查 `schema_version`:認得 `history-1.x`;主版本不同就警告使用者「skill 可能過期」,不可默默讀錯。
- 先讀 `_doc` 與 `last_run_warnings`;有警告就在報告開頭標「數據存疑」並列出。
- **技術面現況 = `technical_snapshots` 最後一筆**(as_of 最新);其餘軌跡可描述指標演變,各標自己的 as_of。
- `cross_check_status=mismatch` → 報告必標「最新收盤待人工確認」(常因 Yahoo 缺最新交易日收盤而落後一天)。
- `financials.annual` 為完整年度序列、`financials.quarters` 為完整單季序列。**Q4 不在 quarters 屬正常**(併入年報、無 10-Q),不是資料缺漏;需要時可用「年度 − 三季」推算並標註為推算值。
- 容忍式讀取:metrics 出現什麼欄位就讀什麼,不寫死清單。

### 第 3 步:依序執行分析模組

**全域規則(每個模組都遵守):**
1. 寧缺勿爛:找不到的數字明說「查無」,絕不臆測填補。
2. 每個區塊各標資料日期(as-of)。股價、財報、網搜資訊時間點不同,不可混為現況解讀。
3. 網搜得到的前瞻/預估資料(產業成長率、分析師看法)標「信心較低」,不得與 EDGAR 一手硬數字同級。
4. 數字引用要可追溯(對應 JSON 欄位或網搜來源+日期)。

**M2 基本面**:讀 annual 與 quarters。近 3 年營收/獲利/利潤率/現金流趨勢為主軸;用更長年度序列補一段長週期視角(成長階段轉換、利潤率結構變化);用季度序列算 YoY/QoQ 與最新動能。各數字依 filed 標 as-of。

**M3 法說會 10 點**:用網搜找最近一次法說會逐字稿或可靠摘要(來源優先序:公司 IR 網站 → 8-K Exhibit 99 → 財經媒體報導)。摘要必須來自真實搜尋結果,不可用記憶。找不到就明說、列出使用者可自行取得的管道,並把整體信心度降一級。固定 10 欄:①財測 guidance(含數字)②本季營收驅動 ③利潤率趨勢與原因 ④資本支出/產能 ⑤需求端強弱 ⑥庫存/供應鏈評論 ⑦資本配置 ⑧語氣 vs 上季 ⑨Q&A 痛點 ⑩超預期/意外點。

**M4 技術面**:最後一筆軌跡為現況,判讀均線排列/黃金死亡交叉/52 週位置/量能;多筆軌跡時補充指標演變。定位為輔助時機訊號,非主要依據。報告的 M4 區塊放 `<!-- KLINE -->` 佔位符,第 5 步會自動注入互動式日K線(含 MA20/50、成交量、1月/半年/1年/2年 切換)。

**M5 估值**:
- 市值 = 最新收盤 × 最新 shares_outstanding(歷史檔內都有);EV 用 long_term_debt 與 cash 調整。
- 第一層:P/E、P/S、P/B、P/FCF + FCF yield、EV/Sales,每項跟公司自身近 3 年比(自身歷史倍數區間需歷史股價時網搜補、標中信心)。
- 第二層:EV/EBITDA、同業中位數(peer 倍數網搜、標中信心)。
- 第三層:逆向 DCF 反推市場隱含成長率,用網搜的產業成長率區間檢驗合理性(標中信心)。
- 方法配公司類型:成長/虧損股用 P/S、EV/Sales、逆向 DCF,勿用 P/E;循環股用正常化盈餘(可用長年度序列取完整循環)+ EV/EBITDA。
- 不假精確,給帶安全邊際的判斷而非單一目標價。

**M6 風險**:網搜供應鏈緊張/集中、客戶集中、法規地緣、近期催化劑;每項標來源/日期/信心。

**M7 評分(0-10 Rubric)**:
六維加權:成長與業務品質 30%｜估值合理性 25%｜產業地位與護城河 20%｜財務體質(存活非保守)12%｜供應鏈與外部風險 8%｜技術面時機 5%。
1. 各維打 0-10 → 加權平均得初步分。
2. 封頂閘門:財報抓取失敗或核心指標(營收/淨利)缺 → 不給分,標「資料不足無法評分」;償債/持續經營紅旗 → 上限 4 分;逆向 DCF 隱含成長率超過產業天花板兩倍以上 → 上限 6 分。
3. 信心度(高/中/低)獨立標註,不併入分數。由 warnings、缺值、依賴網搜程度、資料新鮮度決定。
分數帶:9-10 高度推薦｜7-8 推薦｜5-6 中性觀望｜3-4 不具吸引力｜0-2 避開。

### 第 4 步:產出「內容 JSON」(不再手寫 HTML)
**報告版面已由 `build_report.py` 固定,你不要自己寫 HTML、也不要改 HTML。** 只需把分析
結果整理成 content JSON,存到 `reports/{TICKER}_content_{YYYY-MM-DD}.json`。欄位(缺的給
null 或 []，完整結構見 `build_report.py` 檔頭):
- `report_date`、`mode`(full|quick|news-update)、`confidence`
- `summary`{bull,bear}（最關鍵多空各一句）
- `warnings`[]（你額外發現的警示；資料品質警示程式會自動加,不用重複）
- `m2_note`（基本面文字；財報數字與圖表程式自動從 history 畫）
- `m3_points`[10]{label,text}（快速模式給 []）
- `m4_note`（技術面文字；日K線與52週條程式自動畫）
- `m5`{market_cap_text, rows[{metric,value,note}], peer{self_label,self_pe,peer_label,peer_pe}, note}
- `m6_risks`[]{title,level,text,confidence}（快速模式給 []）
- `value_chain`{upstream[],mid,downstream[],downstream_note}
- `valuation_snapshot`{...}
- `scoring`{dimensions[6]{name,score,weight,reason}, gate_note, final_score, band}

固定規則:dimensions **一律六維、固定名稱與權重**(成長與業務品質30／估值合理性25／
產業地位與護城河20／財務體質12／供應鏈與外部風險8／技術面時機5)。快速模式把「產業地位
與護城河」「供應鏈與外部風險」的 score 設 null、reason 寫「未評估」,m3_points 與 m6_risks 給 []。

### 第 5 步:產生固定格式報告(必做)
執行 `{PYTHON} build_report.py {TICKER} reports/{TICKER}_content_{YYYY-MM-DD}.json`
(`{PYTHON}` 同第 1 步:本機 `.venv/bin/python`、雲端 `python3`)。它會:讀 history(財報/指標/
日K線)+ content JSON → 產出**版面固定**的 `reports/{TICKER}_report_{YYYY-MM-DD}.html`
(已內含互動日K線與所有圖表:雷達/長條+折線/季度/52週條/同業P/E/價值鏈樹)+ 機器可讀摘要
`reports/{TICKER}_report_{YYYY-MM-DD}.json`。版面、配色、章節、圖表畫法全由程式固定。

**開啟/連結(依環境)**:
- 本機(Mac):`open reports/{TICKER}_report_{YYYY-MM-DD}.html`。
- 雲端 session(無瀏覽器):**不要** open。在第 6 步 push 後,於對話輸出可點連結與下載步驟:
  `https://github.com/{OWNER}/{REPO}/blob/main/reports/{TICKER}_report_{YYYY-MM-DD}.html`
  ({OWNER}/{REPO} 由 `git remote get-url origin` 取得),並附:「手機點開→按『Download raw file』
  下載→從下載項目用 Chrome 開啟即完整渲染(離線也可)→看完可刪。」
- 對話中給重點摘要即可,不重複整份報告。

### 第 6 步:自動同步回雲端(必做,在 git repo 內時)
報告產出後,把累積資料推回遠端,讓電腦/手機永遠共用同一份歷史:
1. 不在 git repo 或無 origin remote → 跳過(純本機使用),不報錯。
2. `git add history reports`(只加累積資料;當日快照、`.venv` 已被 `.gitignore` 排除)。
3. `git commit -m "analyze {TICKER}: 更新 history 與報告（{YYYY-MM-DD}）"`;若無可提交變更則略過 commit 與 push。
4. `git push origin main`。
   - **雲端 session 必做**:session 結束即銷毀,不 push 則本次更新與報告全部遺失。
   - push 失敗(離線/權限/衝突):明確告知「報告已產出但**尚未同步到雲端**」,提示在電腦端 `git pull` 後重試;**不可**假裝成功。
5. 對話中用一行回報同步結果(已 push ✓ / 已跳過-純本機 / 失敗-原因)。

## 消息比對流程(消息比對模式專用)

使用者看到一則消息,要查證並跟既有評估比對。步驟:

### N1 查證消息(最重要,先做)
- 網搜該消息,找**至少兩個獨立來源**交叉確認;記錄每個來源的媒體名稱、發布日期。
- 判定查證等級:**已證實**(多個可靠來源/公司公告/SEC 申報)、**單一來源**(僅一家媒體或匿名消息)、**查無**(搜不到可靠佐證)。
- 查無就直說「查無可靠佐證」,只做假設性影響討論,明確標註「假設此消息為真」。
- 區分:公司公告/監管文件(硬消息)vs 媒體報導(中)vs 傳聞/分析師猜測(軟,信心低)。

### N2 讀既有評估
- 先 `git pull --rebase --autostash origin main` 拿其他裝置最新報告(在 git repo 內;失敗不中斷)。
- 讀 `reports/` 中該 ticker **最新一份** `_report_*.json` 摘要(沒有 JSON 才退回讀 HTML)。
- 讀 `history/{TICKER}_history.json` 確認數據 as-of 日期。
- 若該 ticker 從未做過評估:告知使用者,建議先跑一次完整或快速評估,或只做獨立的消息影響分析。

### N3 影響評估
- 逐維檢視六維分數:這則消息影響哪幾維?方向(利多/利空)與幅度(微/中/大)?
- 明確區分:「消息本身的事實」vs「市場可能的反應」vs「你的推論」。
- 檢查消息是否已反映在現有數據:消息日期 vs 報告日期 vs 財報 filed 日 vs 股價 as-of(例如股價已大跌,代表市場已部分反映)。
- 評分調整準則:**已證實的硬消息**才可直接調維度分數;單一來源最多標「觀察中,傾向上/下調」;查無不調分。

### N4 輸出
- 對話中給:消息查證結果(等級+來源)、受影響維度與調整、新舊分數對照、是否建議重跑完整評估(若消息涉及財報數字或重大結構變化,建議重跑並提示「下一份財報/法說會時間點」)。
- 產出更新版報告:用**同一個 build_report.py 固定模板**——以舊報告的 content 為基礎,套用調整(改 scoring 分數/reason、把消息查證摘要寫進 `warnings` 與 `summary`、`mode` 設 `news-update`、在 `news_adjustments` 記錄:消息摘要、日期、查證等級、調整了哪些維度、調整前後分數),存成 `reports/{TICKER}_content_{YYYY-MM-DD}.json`,再跑 `{PYTHON} build_report.py {TICKER} reports/{TICKER}_content_{YYYY-MM-DD}.json`。格式與一般報告一致,調整軌跡留在摘要 JSON 的 `news_adjustments`。
- 最後執行**第 6 步「自動同步回雲端」**,把更新版報告 push 回遠端。
