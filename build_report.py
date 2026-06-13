#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
固定格式報告產生器 — 把「結構化內容 JSON」+ history 數據 → 固定版面 HTML 報告
----------------------------------------------------------
用途：
    python build_report.py TICKER content.json

設計目的（解決「報告格式每次浮動」）：
  - 版面、CSS、章節順序、所有圖表畫法都「寫死」在本程式裡 → 每份報告格式 100% 一致。
  - 模型只負責產出 content JSON（分數、各段文字、風險清單…），不再手寫 HTML。
  - 確定性數據（財報序列、技術指標、日K線）直接從 history 讀，模型不需重抄。

輸入 content JSON 結構（缺欄位以 null/空陣列，程式自動略過對應段落）：
  {
    "report_date":"YYYY-MM-DD", "mode":"full|quick|news-update", "confidence":"中（偏高）",
    "summary":{"bull":"一句","bear":"一句"},
    "warnings":["額外警告…"],
    "m2_note":"基本面文字",
    "m3_points":[{"label":"① 財測","text":"…"}, …10],   // quick 模式可為 []
    "m4_note":"技術面文字",
    "m5":{"market_cap_text":"$4.99 兆","rows":[{"metric":"P/S","value":"≈19.7x","note":"…"}],
          "peer":{"self_label":"NVDA","self_pe":23.5,"peer_label":"AMD","peer_pe":28},"note":"…"},
    "m6_risks":[{"title":"客戶集中","level":"高·核心","text":"…","confidence":"中"}],  // quick 可為 []
    "value_chain":{"upstream":[{"name":"台積電","desc":"代工"}],
                   "mid":{"name":"NVIDIA","desc":"GPU+CUDA"},
                   "downstream":[{"name":"Microsoft"}],"downstream_note":"約85%營收集中於6家"},
    "scoring":{"dimensions":[{"name":"成長與業務品質","score":9,"weight":30,"reason":"…"}, …6],
               "gate_note":"封頂閘門：均未觸發","final_score":8.0,"band":"推薦"}
  }

輸出：reports/{TICKER}_report_{date}.html（含自包含日K線）＋ reports/{TICKER}_report_{date}.json（摘要）。
依賴：標準函式庫；重用 inject_kline 的 K 線區塊。
"""

import sys
import os
import json
import math

from inject_kline import BLOCK as KLINE_BLOCK

HERE = os.path.dirname(os.path.abspath(__file__))

CSS = """
  :root{--ink:#e5e7eb;--mute:#94a3b8;--line:#243044;--bg:#0f172a;--page:#020617;
    --accent:#818cf8;--pos:#22c55e;--neg:#f87171;--warnbg:#2a2211;--warnbd:#f59e0b;--card:#111c2e;--ink2:#cbd5e1}
  *{box-sizing:border-box}
  body{font-family:"PingFang TC","Noto Sans TC",sans-serif;color:var(--ink);background:var(--page);
    margin:0;line-height:1.75;font-size:17.5px;-webkit-text-size-adjust:100%}
  .wrap{max-width:1240px;margin:0 auto;background:var(--bg)}
  .hero{background:linear-gradient(135deg,#1e1b4b 0%,#4f46e5 60%,#0ea5e9 100%);color:#fff;padding:32px 32px 26px}
  .hero h1{font-size:28px;margin:0 0 2px;font-weight:800}
  .hero .tic{font-size:13px;opacity:.85;letter-spacing:1px}
  .hero .sub{font-size:12.5px;opacity:.8;margin-top:10px}
  .scorewrap{display:flex;gap:24px;align-items:center;flex-wrap:wrap;margin-top:18px}
  .scorebox{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.25);border-radius:14px;
    padding:14px 22px;text-align:center}
  .scorebox .n{font-size:46px;font-weight:800;line-height:1}
  .scorebox .n small{font-size:16px;opacity:.7;font-weight:600}
  .scorebox .b{font-size:14px;font-weight:700;margin-top:2px}
  .conf{font-size:12px;opacity:.85;margin-top:2px}
  .radarwrap{flex:1;min-width:300px;display:flex;justify-content:center}
  .body{padding:8px 32px 40px}
  h2{font-size:21px;margin:32px 0 6px;display:flex;align-items:center;gap:9px}
  h2 .tag{font-size:11px;background:var(--accent);color:#fff;border-radius:6px;padding:2px 8px;font-weight:700}
  h2::after{content:"";flex:1;height:2px;background:linear-gradient(90deg,var(--accent),transparent)}
  h3{font-size:16.5px;margin:18px 0 4px;color:var(--ink2)}
  p{margin:8px 0}
  table{width:100%;border-collapse:collapse;margin:10px 0;font-size:15px}
  th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
  th{background:var(--card);font-weight:700;color:var(--ink2)}
  td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
  .pos{color:var(--pos);font-weight:700}.neg{color:var(--neg);font-weight:700}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:14px 0}
  .chartcard{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:14px;margin:14px 0;overflow-x:auto}
  .chartttl{font-size:14.5px;font-weight:700;color:var(--ink2);margin:2px 0 8px 4px}
  .warn{background:var(--warnbg);border-left:4px solid var(--warnbd);border-radius:8px;padding:12px 16px;margin:12px 0;font-size:14.5px}
  .factor{margin:8px 0;font-size:15.5px}
  .factor b.bull{color:var(--pos)}.factor b.bear{color:var(--neg)}
  ul{margin:6px 0;padding-left:22px}li{margin:4px 0;font-size:15.5px}
  svg text{font-family:"PingFang TC","Noto Sans TC",sans-serif}
  .tree{display:flex;flex-direction:column;align-items:center;margin:8px 0}
  .tlevel{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
  .tnode{background:var(--card);border:1.5px solid var(--accent);border-radius:9px;padding:7px 12px;font-size:12.5px;text-align:center;min-width:96px;color:var(--ink2)}
  .tnode.mid{background:var(--accent);color:#0b1020;font-weight:700}
  .tnode small{display:block;font-size:10.5px;opacity:.75;font-weight:400}
  .tconn{width:2px;height:16px;background:var(--accent);opacity:.5}
  .tlabel{font-size:11px;color:var(--mute);margin:9px 0 3px}
  .rangebar{position:relative;height:34px;background:linear-gradient(90deg,#fecaca,#fde68a,#bbf7d0);border-radius:8px;margin:10px 0 22px}
  .rangebar .mk{position:absolute;top:-4px;width:3px;height:42px;background:var(--ink);border-radius:2px}
  .rangebar .lbl{position:absolute;top:38px;font-size:11px;color:var(--mute);transform:translateX(-50%)}
  .foot{margin-top:30px;padding:16px 32px 26px;border-top:1px solid var(--line);color:var(--mute);font-size:13px;background:var(--card)}
  @media(max-width:520px){.body{padding:8px 16px 32px}.hero{padding:24px 18px}.foot{padding:16px 18px}}
"""


def b(x, suffix="B"):
    return "-" if x is None else f"{x/1e9:.1f}{suffix}"


def pct(a, c):
    return None if a is None or not c else (a / c - 1) * 100


# ---------- 圖表產生器（畫法寫死，確保每份報告一致） ----------

def radar_svg(dims):
    cx = cy = 160; R = 120; n = len(dims)

    def pt(i, r):
        a = math.radians(i * 360 / n)
        return cx + r * math.sin(a), cy - r * math.cos(a)

    def poly(r_of):
        return " ".join(f"{x:.1f},{y:.1f}" for i in range(n) for x, y in [pt(i, r_of(i))])

    outer = poly(lambda i: R)
    mid = poly(lambda i: R / 2)
    sc = lambda d: R * (d.get("score") or 0) / 10
    data = " ".join(f"{x:.1f},{y:.1f}" for i, d in enumerate(dims) for x, y in [pt(i, sc(d))])
    axes = "".join(f'<line x1="160" y1="160" x2="{pt(i,R)[0]:.1f}" y2="{pt(i,R)[1]:.1f}"/>' for i in range(n))
    dots = "".join(f'<circle cx="{pt(i,sc(d))[0]:.1f}" cy="{pt(i,sc(d))[1]:.1f}" r="3"/>' for i, d in enumerate(dims))
    labels = ""
    for i, d in enumerate(dims):
        lx, ly = pt(i, R + 22)
        s = math.sin(math.radians(i * 360 / n))
        anchor = "middle" if abs(s) < 0.34 else ("start" if s > 0 else "end")
        sv = d.get("score")
        sv = f"{sv:g}" if isinstance(sv, (int, float)) else "—"
        labels += f'<text x="{lx:.1f}" y="{ly+4:.1f}" text-anchor="{anchor}">{d["name"][:2]} {sv}</text>'
    return (f'<svg width="380" height="348" viewBox="-30 -14 380 348" style="max-width:100%;height:auto">'
            f'<polygon points="{outer}" fill="rgba(255,255,255,.07)" stroke="rgba(255,255,255,.35)"/>'
            f'<polygon points="{mid}" fill="none" stroke="rgba(255,255,255,.22)" stroke-dasharray="3 3"/>'
            f'<g stroke="rgba(255,255,255,.25)">{axes}</g>'
            f'<polygon points="{data}" fill="rgba(56,189,248,.45)" stroke="#7dd3fc" stroke-width="2"/>'
            f'<g fill="#fff">{dots}</g>'
            f'<g fill="#e0e7ff" font-size="11.5" font-weight="600">{labels}</g></svg>')


def annual_chart_svg(items):
    """items: [{'label','rev'(B float),'fcf'(B float or None)}]，由近到遠的時間升序。"""
    W, Hc, top, base, left, right = 600, 290, 28, 242, 60, 585
    maxv = max([it["rev"] for it in items if it["rev"] is not None] + [1])
    scl = (base - top) / maxv
    slot = (right - left) / len(items)
    barw = min(56, slot * 0.55)
    grid = "".join(
        f'<line x1="{left}" y1="{base-scl*v:.1f}" x2="{right}" y2="{base-scl*v:.1f}" stroke="#2b3a52" stroke-dasharray="3 3"/>'
        f'<text x="{left-6}" y="{base-scl*v+4:.1f}" fill="#94a3b8" font-size="10" text-anchor="end">{v:.0f}</text>'
        for v in (0, maxv / 2, maxv))
    bars = vals = ""
    line_pts = []
    for i, it in enumerate(items):
        xc = left + slot * i + slot / 2
        if it["rev"] is not None:
            h = it["rev"] * scl
            bars += f'<rect x="{xc-barw/2:.1f}" y="{base-h:.1f}" width="{barw:.1f}" height="{h:.1f}" rx="3" fill="#6366f1"/>'
            vals += f'<text x="{xc:.1f}" y="{base-h-6:.1f}" fill="#c7d2fe" font-size="11" font-weight="700" text-anchor="middle">{it["rev"]:.1f}</text>'
        if it.get("fcf") is not None:
            line_pts.append(f'{xc:.1f},{base-it["fcf"]*scl:.1f}')
    line = ""
    if len(line_pts) >= 2:
        line = (f'<polyline points="{" ".join(line_pts)}" fill="none" stroke="#0ea5e9" stroke-width="2.5"/>'
                + "".join(f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="3.5" fill="#0ea5e9"/>' for p in line_pts))
    xlbl = "".join(f'<text x="{left+slot*i+slot/2:.1f}" y="{base+18}" fill="#94a3b8" font-size="11" font-weight="600" text-anchor="middle">{it["label"]}</text>' for i, it in enumerate(items))
    return f'<svg viewBox="0 0 {W} {Hc}" width="100%" style="height:auto;display:block;max-width:840px;margin:0 auto">{grid}{bars}{line}{vals}{xlbl}</svg>'


def bar_chart_svg(items, color="#0ea5e9"):
    """items: [{'label','val'(B float)}] 單純長條。"""
    W, Hc, top, base, left, right = 600, 210, 20, 168, 50, 585
    maxv = max([it["val"] for it in items if it["val"] is not None] + [1])
    scl = (base - top) / maxv
    slot = (right - left) / len(items)
    barw = min(58, slot * 0.62)
    out = f'<line x1="{left}" y1="{base}" x2="{right}" y2="{base}" stroke="#2b3a52"/>'
    for i, it in enumerate(items):
        xc = left + slot * i + slot / 2
        if it["val"] is not None:
            h = it["val"] * scl
            out += f'<rect x="{xc-barw/2:.1f}" y="{base-h:.1f}" width="{barw:.1f}" height="{h:.1f}" rx="3" fill="{color}"/>'
            out += f'<text x="{xc:.1f}" y="{base-h-5:.1f}" fill="#7dd3fc" font-size="11" font-weight="700" text-anchor="middle">{it["val"]:.1f}</text>'
        out += f'<text x="{xc:.1f}" y="{base+16}" fill="#94a3b8" font-size="10" text-anchor="middle">{it["label"]}</text>'
    return f'<svg viewBox="0 0 {W} {Hc}" width="100%" style="height:auto;display:block;max-width:840px;margin:0 auto">{out}</svg>'


def peer_bar_svg(peer):
    if not peer:
        return ""
    sp, pp = peer.get("self_pe"), peer.get("peer_pe")
    mx = max(sp or 0, pp or 0, 1)
    w = lambda v: (v or 0) / mx * 280
    return (f'<svg viewBox="0 0 400 120" width="100%" style="height:auto;display:block;max-width:540px;margin:0 auto">'
            f'<line x1="90" y1="92" x2="380" y2="92" stroke="#2b3a52"/>'
            f'<rect x="90" y="28" width="{w(sp):.1f}" height="22" rx="4" fill="#6366f1"/>'
            f'<rect x="90" y="62" width="{w(pp):.1f}" height="22" rx="4" fill="#94a3b8"/>'
            f'<g font-size="12" font-weight="700" fill="#fff" text-anchor="end">'
            f'<text x="{86+w(sp):.1f}" y="44">{sp:g}x</text><text x="{86+w(pp):.1f}" y="78">{pp:g}x</text></g>'
            f'<g font-size="12" fill="#cbd5e1" font-weight="600" text-anchor="end">'
            f'<text x="84" y="44">{peer["self_label"]}</text><text x="84" y="78">{peer["peer_label"]}</text></g></svg>')


def range_bar_html(ind):
    lo, hi, cur = ind.get("low_52w"), ind.get("high_52w"), ind.get("latest_close")
    if None in (lo, hi, cur) or hi == lo:
        return ""
    p = (cur - lo) / (hi - lo) * 100
    return (f'<div class="card"><div class="chartttl">52 週區間位置　現價 ${cur} 位於區間 {p:.1f}%</div>'
            f'<div class="rangebar"><div class="mk" style="left:{p:.1f}%"></div>'
            f'<div class="lbl" style="left:0%">低 {lo}</div>'
            f'<div class="lbl" style="left:{p:.1f}%;font-weight:700;color:var(--ink)">現價 {cur}</div>'
            f'<div class="lbl" style="left:100%">高 {hi}</div></div></div>')


def value_chain_html(vc):
    if not vc:
        return ""
    up = "".join(f'<div class="tnode">{u["name"]}<small>{u.get("desc","")}</small></div>' for u in vc.get("upstream", []))
    mid = vc.get("mid", {})
    down = "".join(f'<div class="tnode">{d["name"]}</div>' for d in vc.get("downstream", []))
    return (f'<div class="chartcard"><div class="chartttl">價值鏈定位</div><div class="tree">'
            f'<div class="tlabel">上游（依賴）</div><div class="tlevel">{up}</div><div class="tconn"></div>'
            f'<div class="tlevel"><div class="tnode mid">{mid.get("name","")}<small>{mid.get("desc","")}</small></div></div>'
            f'<div class="tconn"></div><div class="tlabel">下游客戶（{vc.get("downstream_note","")}）</div>'
            f'<div class="tlevel">{down}</div></div></div>')


# ---------- 主組裝 ----------

def build(ticker, content):
    with open(os.path.join(HERE, "history", f"{ticker}_history.json"), encoding="utf-8") as f:
        hist = json.load(f)
    meta = hist["meta"]
    snaps = hist.get("technical_snapshots", [])
    snap = snaps[-1] if snaps else {}
    ind = snap.get("indicators", {})
    annual = hist["financials"]["annual"]
    quarters = hist["financials"]["quarters"]
    bars = hist.get("price_bars", [])

    date = content.get("report_date", "")
    mode = content.get("mode", "full")
    conf = content.get("confidence", "—")
    sc = content["scoring"]
    dims = sc["dimensions"]
    weighted = sum((d.get("score") or 0) * d["weight"] for d in dims) / sum(d["weight"] for d in dims)

    # 年度資料（近 5 年圖、近 3 年表）
    akeys = sorted(annual.keys())
    chart_items = []
    for e in akeys[-5:]:
        m, d = annual[e]["metrics"], annual[e].get("derived", {})
        chart_items.append({"label": annual[e]["fy_label"],
                            "rev": m.get("revenue") / 1e9 if m.get("revenue") else None,
                            "fcf": d.get("free_cash_flow") / 1e9 if d.get("free_cash_flow") else None})
    # 由期間結束日推算財年起訖（財年≈結束月往前 12 個月；公司財年未必對齊曆年）
    def fy_period(end):
        ey, em = int(end[:4]), int(end[5:7])
        sm, sy = em + 1, ey - 1
        if sm == 13:
            sm, sy = 1, ey
        return f"{sy}/{sm:02d}–{ey}/{em:02d}"

    # 表格近 3 年（含 YoY、實際期間）
    rows = ""
    for idx in range(max(0, len(akeys) - 3), len(akeys)):
        e = akeys[idx]; m = annual[e]["metrics"]; d = annual[e].get("derived", {})
        prev = annual[akeys[idx - 1]]["metrics"].get("revenue") if idx > 0 else None
        yoy = pct(m.get("revenue"), prev)
        yoy_s = "—" if yoy is None else f'<span class="{"pos" if yoy>=0 else "neg"}">{yoy:+.0f}%</span>'
        gm = d.get("gross_margin_pct")
        rows += (f'<tr><td>{annual[e]["fy_label"]}<br><span style="font-size:11px;color:var(--mute)">{fy_period(e)}</span></td>'
                 f'<td class="num">{b(m.get("revenue"))}</td>'
                 f'<td class="num">{yoy_s}</td><td class="num">{gm if gm is not None else "—"}%</td>'
                 f'<td class="num">{b(m.get("net_income"))}</td><td class="num">{b(d.get("free_cash_flow"))}</td>'
                 f'<td class="num">{m.get("eps_diluted") if m.get("eps_diluted") is not None else "—"}</td></tr>')

    # 季度近 6
    qkeys = sorted(quarters.keys())[-6:]
    q_items = [{"label": e[2:7], "val": quarters[e]["metrics"].get("revenue") / 1e9
                if quarters[e]["metrics"].get("revenue") else None} for e in qkeys]

    # 自動數據品質警示
    fin_filed = max([annual[e].get("filed", "") for e in akeys] + [quarters[e].get("filed", "") for e in qkeys] + [""])
    auto_warn = (f'<div class="warn"><b>數據品質</b>　technical=<b>{snap.get("data_quality","?")}</b>、'
                 f'cross_check=<b>{snap.get("cross_check_status","?")}</b>。股價 as-of <b>{snap.get("as_of","?")}</b>'
                 f'（${ind.get("latest_close","?")}）；財報最新申報日 {fin_filed}。</div>')
    if snap.get("cross_check_status") == "mismatch":
        auto_warn += '<div class="warn"><b>注意</b>　兩來源最新收盤不一致（常因 Yahoo 缺最新交易日收盤），最新收盤待人工確認。</div>'
    extra_warn = "".join(f'<div class="warn">{w}</div>' for w in content.get("warnings", []))

    # K 線
    kline = KLINE_BLOCK.replace("{DATA}", json.dumps(bars, ensure_ascii=False, separators=(",", ":"))) if bars else ""

    # M3 / M6 段落（quick 模式略過）
    m3 = ""
    if content.get("m3_points"):
        tr = "".join(f'<tr><td>{p["label"]}</td><td>{p["text"]}</td></tr>' for p in content["m3_points"])
        m3 = f'<h2><span class="tag">M3</span> 法說會 10 點</h2><table><tr><th>項目</th><th>重點</th></tr>{tr}</table>'
    m6 = ""
    if content.get("m6_risks"):
        li = "".join(f'<li><b>{r["title"]}（{r.get("level","")}）</b>：{r["text"]}〔{r.get("confidence","")}信心〕</li>' for r in content["m6_risks"])
        m6 = (f'<h2><span class="tag">M6</span> 風險 + 價值鏈定位</h2>{value_chain_html(content.get("value_chain"))}<ul>{li}</ul>')

    # M5
    m5 = content.get("m5", {})
    m5rows = "".join(f'<tr><td>{r["metric"]}</td><td class="num">{r["value"]}</td><td>{r.get("note","")}</td></tr>' for r in m5.get("rows", []))
    peer = peer_bar_svg(m5.get("peer"))
    peer_block = f'<div class="chartcard"><div class="chartttl">前瞻 P/E 對同業（中信心）</div>{peer}</div>' if peer else ""

    # 評分表
    drows = "".join(f'<tr><td>{d["name"]}</td><td class="num"><b>{d.get("score","—")}</b></td>'
                    f'<td class="num">{d["weight"]}%</td><td>{d.get("reason","")}</td></tr>' for d in dims)
    formula = " + ".join(f'{d.get("score") or 0:g}×.{d["weight"]:02d}' for d in dims)

    parts = []
    parts.append(f'<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">'
                 f'<meta name="viewport" content="width=device-width, initial-scale=1">'
                 f'<title>{ticker} 投資分析報告 {date}</title><style>{CSS}</style></head><body><div class="wrap">')
    parts.append(f'<div class="hero"><div class="tic">{ticker}</div>'
                 f'<h1>{meta.get("company_name") or meta.get("entity_name") or ticker} 投資分析報告</h1>'
                 f'<div class="sub">{date} 產出 · {"完整" if mode=="full" else ("快速" if mode=="quick" else "消息更新")}模式 · 情境：1–3 年中期、積極、成長優先</div>'
                 f'<div class="scorewrap"><div class="scorebox"><div class="n">{sc["final_score"]:g}<small>/10</small></div>'
                 f'<div class="b">{sc["band"]}</div><div class="conf">信心度 {conf}</div></div>'
                 f'<div class="radarwrap">{radar_svg(dims)}</div></div></div>')
    parts.append('<div class="body">')
    parts.append(f'<div class="card"><div class="factor"><b class="bull">多方核心 ▲</b>　{content.get("summary",{}).get("bull","")}</div>'
                 f'<div class="factor"><b class="bear">空方核心 ▼</b>　{content.get("summary",{}).get("bear","")}</div></div>')
    parts.append(auto_warn + extra_warn)
    # M2
    parts.append('<h2><span class="tag">M2</span> 基本面</h2>')
    parts.append(f'<div class="chartcard"><div class="chartttl">年度營收（長條）與自由現金流（折線）　單位：十億美元</div>{annual_chart_svg(chart_items)}</div>')
    parts.append(f'<table><tr><th>會計年度<br><span style="font-size:11px;color:var(--mute)">(實際期間)</span></th>'
                 f'<th class="num">營收</th><th class="num">YoY</th><th class="num">毛利率</th>'
                 f'<th class="num">淨利</th><th class="num">FCF</th><th class="num">EPS</th></tr>{rows}</table>')
    parts.append('<p style="font-size:12px;color:var(--mute);margin-top:2px">財年依「期間結束日」命名，未必對齊曆年（如財年結束於 1 月底者，FY 標籤年份＝結束年，該年實際多落在前一曆年）。</p>')
    parts.append(f'<p>{content.get("m2_note","")}</p>')
    parts.append(f'<div class="chartcard"><div class="chartttl">近 6 季單季營收　單位：十億美元（季末）</div>{bar_chart_svg(q_items)}</div>')
    # M3
    parts.append(m3)
    # M4
    parts.append('<h2><span class="tag">M4</span> 技術面（輔助時機）</h2>')
    parts.append(f'<p>{content.get("m4_note","")}</p>')
    parts.append(kline)
    parts.append(range_bar_html(ind))
    # M5
    parts.append('<h2><span class="tag">M5</span> 估值</h2>')
    if m5.get("market_cap_text"):
        parts.append(f'<p>市值 ≈ <b>{m5["market_cap_text"]}</b></p>')
    parts.append(f'<table><tr><th>指標</th><th class="num">水準</th><th>對照</th></tr>{m5rows}</table>')
    parts.append(peer_block)
    if m5.get("note"):
        parts.append(f'<p>{m5["note"]}</p>')
    # M6
    parts.append(m6)
    # M7
    parts.append('<h2><span class="tag">M7</span> 評分</h2>')
    parts.append(f'<table><tr><th>維度</th><th class="num">分數</th><th class="num">權重</th><th>理由</th></tr>{drows}'
                 f'<tr><td colspan="4"><b>加權 = {formula} = {weighted:.2f} → {sc["final_score"]:g}</b>　·　{sc.get("gate_note","")}</td></tr></table>')
    parts.append(f'<div class="card" style="text-align:center"><div style="font-size:20px;font-weight:800">'
                 f'最終 {sc["final_score"]:g} / 10　·　{sc["band"]}　·　信心度 {conf}</div></div>')
    parts.append('</div>')  # body
    parts.append(f'<div class="foot">本內容為數據分析，<b>非投資顧問建議</b>，不構成買賣要約。投資涉及風險，請自行判斷。<br>'
                 f'報告產出 {date} · 股價 as-of {snap.get("as_of","?")} · 財報 SEC EDGAR（最新申報 {fin_filed}）· '
                 f'本機 {hist.get("schema_version","")} · 法說會/同業/風險數字經網路查證（中信心）。</div>')
    parts.append('</div></body></html>')
    return "".join(parts), weighted


def main():
    if len(sys.argv) < 3:
        print("用法: python build_report.py TICKER content.json")
        sys.exit(1)
    ticker = sys.argv[1].upper().strip()
    with open(sys.argv[2], encoding="utf-8") as f:
        content = json.load(f)
    html, weighted = build(ticker, content)

    date = content.get("report_date", "report")
    reports = os.path.join(HERE, "reports")
    os.makedirs(reports, exist_ok=True)
    html_path = os.path.join(reports, f"{ticker}_report_{date}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # 摘要 JSON（供消息比對模式讀取）
    sc = content["scoring"]
    summary = {
        "ticker": ticker, "report_date": date, "mode": content.get("mode"),
        "score": sc["final_score"], "band": sc["band"], "confidence": content.get("confidence"),
        "weighted_raw": round(weighted, 2),
        "dimensions": {d["name"]: {"score": d.get("score"), "weight": d["weight"], "reason": d.get("reason")} for d in sc["dimensions"]},
        "key_factors": {"bull": [content.get("summary", {}).get("bull")], "bear": [content.get("summary", {}).get("bear")]},
        "valuation_snapshot": content.get("valuation_snapshot"),
        "warnings": content.get("warnings", []),
        "news_adjustments": content.get("news_adjustments", []),
    }
    json_path = os.path.join(reports, f"{ticker}_report_{date}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"已產生固定格式報告：{html_path}")
    print(f"已產生摘要：{json_path}")


if __name__ == "__main__":
    main()
