#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M8 消息事件分析報告產生器 — 把「結構化內容 JSON」→ 固定版面獨立 HTML 報告
----------------------------------------------------------
用途：
    python build_news_report.py TICKER content.json

設計目的（與 build_report.py 同哲學）：
  - 版面、CSS、章節順序全「寫死」在本程式裡 → 每份消息報告格式 100% 一致。
  - 重用 build_report 的 CSS（暗色視覺單一來源，不走樣）。
  - 模型只負責產 content JSON（查證、影響、對六維評分的影響、彙總、觀察點），不手寫 HTML。

這是一份**獨立**報告（不接在財報報告後），每批新聞產一份。同一天重跑不覆蓋（檔名自動加序號）。

輸入 content JSON 結構（缺欄位以 null/空陣列，程式自動略過對應段落）：
  {
    "ticker":"NVDA", "report_date":"YYYY-MM-DD", "confidence":"中",
    "existing_report":{                        // N2：既有報告脈絡（要先讀；無則設 null）
        "report_date":"2026-06-11","final_score":8.0,"band":"推薦","source_file":"NVDA_report_2026-06-11.json"},
    "no_existing_note":null,                    // 若無既有報告，這裡放提示文字（程式會顯示警告卡）
    "news_items":[                              // 本批新聞清單
        {"title":"…","source":"…","url":"…","published":"2026-06-12","verification":"已證實"}],
    "events":[                                  // 各事件主題（步驟 1–3）
      {"title":"事件主題名","related_news":[0,1],
       "verification":{"level":"已證實|多源未證實|單一來源|傳言|查無",
                       "source_tier":"一手|二手|匿名","published":"…",
                       "cross_check":"互相佐證|互相矛盾|—","note":"…"},
       "credible":true,                         // 步驟 2 分流：可信走 impact.*，不可信走 doubt_*
       "impact":{"reasoning":"回推依據（標來源）","fundamental":"對基本面影響",
                 "direction":{"short":"…","mid":"…","long":"…"},
                 "doubt_reason":null,"if_market_believes":null,"key_watch":null},
       "scoring_impact":[                        // 步驟 3：對 canonical 六維的影響（不重給買賣價）
         {"dimension":"成長與業務品質","direction":"↑|↓|—","magnitude":"微|中|大","confidence":"…","note":"…"}]}],
    "summary":{"stance":"偏多|偏空|中性","key_events":"最關鍵的1-2個事件",
               "confidence":"整體信心度","text":"跨事件總結文字"},   // 步驟 4
    "watch_points":[{"item":"要追蹤什麼","trigger":"證實/證偽的觸發點","horizon":"短/中/長期"}]  // 步驟 5
  }

輸出：reports/{TICKER}_news_{date}.html ＋ reports/{TICKER}_news_{date}.json（機器可讀摘要，供後續追蹤/比對）。
依賴：標準函式庫 + build_report.CSS。
"""

import sys
import os
import json

from build_report import CSS

HERE = os.path.dirname(os.path.abspath(__file__))

# canonical 六維（與 M7／build_report 一致；步驟 3 對評分影響只能落在這六維上）
DIMENSIONS = ["成長與業務品質", "估值合理性", "產業地位與護城河", "財務體質", "供應鏈與外部風險", "技術面時機"]

# 查證等級 → 視覺
VERIF_CLASS = {"已證實": "pos", "多源未證實": "warn", "單一來源": "warn", "傳言": "neg", "查無": "mute"}
STANCE_CLASS = {"偏多": "pos", "偏空": "neg", "中性": "mute"}


def _vbadge(level):
    cls = VERIF_CLASS.get(level, "mute")
    color = {"pos": "var(--pos)", "neg": "var(--neg)", "warn": "var(--warnbd)", "mute": "var(--mute)"}[cls]
    return (f'<span style="display:inline-block;padding:2px 10px;border-radius:6px;font-size:16px;font-weight:700;'
            f'color:#0b1020;background:{color}">{level or "—"}</span>')


def _dir_span(direction):
    if direction == "↑":
        return '<span class="pos">↑ 利多</span>'
    if direction == "↓":
        return '<span class="neg">↓ 利空</span>'
    return '<span style="color:var(--mute)">— 中性</span>'


def build(ticker, content):
    # 公司名 / 數據 as-of（有 history 才補；沒有不致命）
    company, as_of = ticker, None
    hist_path = os.path.join(HERE, "history", f"{ticker}_history.json")
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding="utf-8") as f:
                hist = json.load(f)
            company = hist.get("meta", {}).get("company_name") or hist.get("meta", {}).get("entity_name") or ticker
            snaps = hist.get("technical_snapshots", [])
            as_of = snaps[-1].get("as_of") if snaps else None
        except Exception:
            pass

    date = content.get("report_date", "")
    conf = content.get("confidence", "—")
    summ = content.get("summary", {})
    stance = summ.get("stance", "中性")
    stance_cls = STANCE_CLASS.get(stance, "mute")
    ex = content.get("existing_report") or {}

    parts = []
    parts.append(f'<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">'
                 f'<meta name="viewport" content="width=device-width, initial-scale=1">'
                 f'<title>{ticker} 消息事件分析 {date}</title><style>{CSS}</style></head><body><div class="wrap">')

    # 1. Hero：標題 + 日期 + 整體傾向 + 既有報告脈絡
    score_box = ""
    if ex.get("final_score") is not None:
        score_box = (f'<div class="scorebox"><div class="conf">既有評分（{ex.get("report_date","?")}）</div>'
                     f'<div class="n">{ex["final_score"]:g}<small>/10</small></div>'
                     f'<div class="b">{ex.get("band","")}</div></div>')
    stance_color = {"pos": "var(--pos)", "neg": "var(--neg)", "mute": "var(--mute)"}[stance_cls]
    stance_box = (f'<div class="scorebox" style="border-color:{stance_color}"><div class="conf">本批消息整體</div>'
                  f'<div class="b" style="font-size:34px;color:{stance_color}">{stance}</div>'
                  f'<div class="conf">信心度 {conf}</div></div>')
    parts.append(f'<div class="hero"><div class="tic">{ticker} · 消息事件分析模組（M8）</div>'
                 f'<h1>{company} 消息事件分析</h1>'
                 f'<div class="sub">{date} 產出 · 情境：1–3 年中期、積極、成長優先 · '
                 f'<b>本內容為分析，非投資顧問建議</b>，不構成買賣要約</div>'
                 f'<div class="scorewrap">{stance_box}{score_box}</div></div>')

    parts.append('<div class="body">')

    # 無既有報告提示
    if content.get("no_existing_note"):
        parts.append(f'<div class="warn"><b>無既有評估</b>　{content["no_existing_note"]}</div>')

    # 彙總（放前面當 TL;DR）
    if summ.get("text"):
        parts.append(f'<div class="card"><div class="factor"><b class="{("bull" if stance_cls=="pos" else "bear")}">'
                     f'整體 {stance} ●</b>　{summ.get("text","")}</div>'
                     f'<p style="color:var(--mute);font-size:18px">最關鍵：{summ.get("key_events","—")}</p></div>')

    # 2. 本批新聞清單
    items = content.get("news_items", [])
    if items:
        parts.append('<h2><span class="tag">清單</span> 本批新聞</h2>')
        nrows = ""
        for i, it in enumerate(items):
            title = it.get("title", "")
            url = it.get("url")
            title_html = f'<a href="{url}" style="color:var(--accent)">{title}</a>' if url else title
            nrows += (f'<tr><td class="num">{i+1}</td><td>{title_html}</td>'
                      f'<td>{it.get("source","—")}</td><td>{it.get("published","—")}</td>'
                      f'<td>{_vbadge(it.get("verification"))}</td></tr>')
        parts.append(f'<table><tr><th class="num">#</th><th>標題</th><th>來源</th><th>發布日</th>'
                     f'<th>證實程度</th></tr>{nrows}</table>')

    # 3. 各事件主題分析（步驟 1–3）
    events = content.get("events", [])
    for ei, ev in enumerate(events):
        v = ev.get("verification", {})
        parts.append(f'<h2><span class="tag">事件 {ei+1}</span> {ev.get("title","")}</h2>')

        # 步驟 1：查證
        rel = ev.get("related_news", [])
        rel_s = "、".join(f"#{r+1}" for r in rel) if rel else "—"
        parts.append(f'<div class="card"><h3 style="margin-top:0">① 查證</h3>'
                     f'<p>證實程度：{_vbadge(v.get("level"))}　·　來源層級：<b>{v.get("source_tier","—")}</b>'
                     f'　·　發布時間：{v.get("published","—")}　·　關聯新聞：{rel_s}</p>'
                     f'<p>交叉驗證：<b>{v.get("cross_check","—")}</b>　{v.get("note","")}</p></div>')

        # 步驟 2：影響分析（依可信度分流）
        imp = ev.get("impact", {})
        if ev.get("credible"):
            d = imp.get("direction", {})
            parts.append('<div class="card"><h3 style="margin-top:0">② 影響分析（偏可信）</h3>')
            if imp.get("reasoning"):
                parts.append(f'<p><b>回推依據</b>：{imp["reasoning"]}</p>')
            if imp.get("fundamental"):
                parts.append(f'<p><b>對基本面</b>：{imp["fundamental"]}</p>')
            parts.append(f'<table><tr><th>期間</th><th>可能方向與理由（非價位）</th></tr>'
                         f'<tr><td>短期</td><td>{d.get("short","—")}</td></tr>'
                         f'<tr><td>中期</td><td>{d.get("mid","—")}</td></tr>'
                         f'<tr><td>長期</td><td>{d.get("long","—")}</td></tr></table></div>')
        else:
            parts.append('<div class="card"><h3 style="margin-top:0">② 影響分析（偏不可信／未證實）</h3>')
            if imp.get("doubt_reason"):
                parts.append(f'<p><b>為何存疑</b>：{imp["doubt_reason"]}</p>')
            if imp.get("if_market_believes"):
                parts.append(f'<p><b>若市場誤信</b>：{imp["if_market_believes"]}</p>')
            if imp.get("key_watch"):
                parts.append(f'<p><b>證實／證偽關鍵觀察點</b>：{imp["key_watch"]}</p>')
            parts.append('</div>')

        # 步驟 3：對評分（六維）的影響
        si = ev.get("scoring_impact", [])
        if si:
            srows = "".join(f'<tr><td>{s.get("dimension","")}</td><td>{_dir_span(s.get("direction"))}</td>'
                            f'<td>{s.get("magnitude","—")}</td><td>{s.get("confidence","—")}</td>'
                            f'<td>{s.get("note","")}</td></tr>' for s in si)
            parts.append(f'<h3>③ 對評分維度的影響</h3>'
                         f'<table><tr><th>維度</th><th>方向</th><th>幅度</th><th>信心變化</th><th>說明</th></tr>{srows}</table>')

    # 4. 跨事件彙總（已放前面 TL;DR；此處放完整版補充已含於 summary.text，不重複）

    # 5. 關鍵觀察點清單
    wp = content.get("watch_points", [])
    if wp:
        parts.append('<h2><span class="tag">追蹤</span> 關鍵觀察點清單</h2>')
        wrows = "".join(f'<tr><td>{w.get("item","")}</td><td>{w.get("trigger","")}</td>'
                        f'<td>{w.get("horizon","—")}</td></tr>' for w in wp)
        parts.append(f'<table><tr><th>觀察項目</th><th>證實／證偽觸發點</th><th>時間軸</th></tr>{wrows}</table>')

    parts.append('</div>')  # body
    parts.append(f'<div class="foot">本內容為消息事件分析，<b>非投資顧問建議</b>，不構成買賣要約。'
                 f'未做真／假二元判定，僅給可信度等級；查不到可靠來源即標「無法查證」，不臆測。<br>'
                 f'報告產出 {date}'
                 + (f' · 數據 as-of {as_of}' if as_of else '')
                 + (f' · 既有評分基準 {ex.get("report_date")}（{ex.get("final_score"):g}/10）' if ex.get("final_score") is not None else '')
                 + '。</div>')
    parts.append('</div></body></html>')
    return "".join(parts)


def _unique_path(reports, base):
    """同日重跑不覆蓋：base 已存在就加 _2、_3…"""
    p = os.path.join(reports, base + ".html")
    if not os.path.exists(p):
        return base
    n = 2
    while os.path.exists(os.path.join(reports, f"{base}_{n}.html")):
        n += 1
    return f"{base}_{n}"


def main():
    if len(sys.argv) < 3:
        print("用法: python build_news_report.py TICKER content.json")
        sys.exit(1)
    ticker = sys.argv[1].upper().strip()
    with open(sys.argv[2], encoding="utf-8") as f:
        content = json.load(f)
    html = build(ticker, content)

    date = content.get("report_date", "news")
    reports = os.path.join(HERE, "reports")
    os.makedirs(reports, exist_ok=True)
    base = _unique_path(reports, f"{ticker}_news_{date}")
    html_path = os.path.join(reports, base + ".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # 機器可讀摘要（供後續追蹤/比對讀取）
    summary = {
        "ticker": ticker, "report_date": date, "type": "news-event",
        "stance": content.get("summary", {}).get("stance"),
        "confidence": content.get("confidence"),
        "existing_report": content.get("existing_report"),
        "news_items": content.get("news_items", []),
        "events": [
            {"title": e.get("title"), "verification": e.get("verification", {}).get("level"),
             "credible": e.get("credible"),
             "scoring_impact": e.get("scoring_impact", [])}
            for e in content.get("events", [])
        ],
        "watch_points": content.get("watch_points", []),
        "news_adjustments": content.get("news_adjustments", []),
    }
    json_path = os.path.join(reports, base + ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"已產生消息事件分析報告：{html_path}")
    print(f"已產生摘要：{json_path}")


if __name__ == "__main__":
    main()
