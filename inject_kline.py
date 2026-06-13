#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日K線注入器 — 把互動式日K線圖（canvas + 內嵌資料 + 純 JS）注入報告 HTML
----------------------------------------------------------
用途：
    python inject_kline.py TICKER reports/TICKER_report_YYYY-MM-DD.html

做的事：
  1. 讀 history/{TICKER}_history.json 的 price_bars（近 2 年日 OHLCV）。
  2. 產出自包含的 K 線圖區塊：含蠟燭、MA20/MA50、成交量，與 [1月][半年][1年][2年] 切換鈕。
  3. 把報告 HTML 內的 <!-- KLINE --> 佔位符替換成該區塊（就地寫回）。

設計：
  - 全內嵌（資料 + JS 都在區塊內），不引用任何外部資源 → 下載到手機離線也能渲染。
  - 資料以緊湊 JSON 陣列嵌入，模型不需手抄上百根 K 棒，避免出錯。
  - canvas 繪製，數百根 K 棒仍清晰；視窗切換只重繪、不重載。

依賴：標準函式庫。
"""

import sys
import os
import json

# K 線圖區塊模板。{DATA} 會被換成 price_bars 的 JSON；其餘 { } 已用 {{ }} 跳脫。
BLOCK = """<div class="chartcard">
  <div class="chartttl">日K線（還原權值）　綠漲紅跌 ·
    <span style="color:#d97706;font-weight:700">MA20</span> ·
    <span style="color:#7c3aed;font-weight:700">MA50</span> · 下方為成交量</div>
  <div class="klbtns" style="display:flex;gap:6px;margin:2px 0 8px 2px">
    <button data-n="22">1月</button><button data-n="126">半年</button>
    <button data-n="252" class="on">1年</button><button data-n="0">2年</button>
  </div>
  <canvas id="klcv" style="width:100%;height:360px;display:block"></canvas>
  <style>
    .klbtns button{{font:13px/1 inherit;padding:5px 12px;border:1px solid #cbd5e1;
      background:#fff;color:#475569;border-radius:7px;cursor:pointer}}
    .klbtns button.on{{background:#4f46e5;border-color:#4f46e5;color:#fff;font-weight:700}}
  </style>
</div>
<script>
(function(){{
  const BARS = {DATA};
  if(!BARS || !BARS.length){{return;}}
  const close = BARS.map(b=>b[4]);
  function ma(p){{const r=[];let s=0;for(let i=0;i<close.length;i++){{s+=close[i];
    if(i>=p)s-=close[i-p];r.push(i>=p-1?s/p:null);}}return r;}}
  const MA20=ma(20), MA50=ma(50);
  const cv=document.getElementById('klcv'), cx=cv.getContext('2d');
  let N=252;
  function draw(){{
    const dpr=window.devicePixelRatio||1, W=cv.clientWidth, H=360;
    cv.width=W*dpr; cv.height=H*dpr; cx.setTransform(dpr,0,0,dpr,0,0);
    cx.clearRect(0,0,W,H);
    const n=N>0?N:BARS.length, start=Math.max(0,BARS.length-n);
    const view=BARS.slice(start), m20=MA20.slice(start), m50=MA50.slice(start);
    const padT=10,padB=22,padR=50,padL=8,gap=8,volH=64;
    const priceH=H-padT-padB-volH-gap, x0=padL, x1=W-padR;
    let hi=-1e9,lo=1e9;
    view.forEach(b=>{{hi=Math.max(hi,b[2]);lo=Math.min(lo,b[3]);}});
    m20.concat(m50).forEach(v=>{{if(v!=null){{hi=Math.max(hi,v);lo=Math.min(lo,v);}}}});
    const pd=(hi-lo)*0.06||1; hi+=pd; lo-=pd;
    const py=v=>padT+(hi-v)/(hi-lo)*priceH;
    let vmax=0; view.forEach(b=>vmax=Math.max(vmax,b[5]||0));
    const volTop=padT+priceH+gap, vy=v=>volTop+volH-(v/(vmax||1))*volH;
    const cw=(x1-x0)/view.length, bw=Math.max(1,Math.min(cw*0.7,13));
    cx.strokeStyle='#eef2f7'; cx.fillStyle='#94a3b8'; cx.font='10px sans-serif'; cx.textAlign='left';
    for(let g=0;g<=4;g++){{const v=lo+(hi-lo)*g/4,y=py(v);
      cx.beginPath();cx.moveTo(x0,y);cx.lineTo(x1,y);cx.stroke();
      cx.fillText(v.toFixed(v<10?2:0),x1+4,y+3);}}
    view.forEach((b,i)=>{{
      const xc=x0+cw*i+cw/2, up=b[4]>=b[1], col=up?'#16a34a':'#dc2626';
      cx.strokeStyle=col; cx.fillStyle=col;
      cx.beginPath();cx.moveTo(xc,py(b[2]));cx.lineTo(xc,py(b[3]));cx.stroke();
      const yo=py(b[1]),yc=py(b[4]),top=Math.min(yo,yc),hh=Math.max(1,Math.abs(yo-yc));
      cx.fillRect(xc-bw/2,top,bw,hh);
      cx.fillStyle=up?'rgba(22,163,74,.4)':'rgba(220,38,38,.4)';
      const vyy=vy(b[5]||0); cx.fillRect(xc-bw/2,vyy,bw,volTop+volH-vyy);
    }});
    function line(arr,col){{cx.strokeStyle=col;cx.lineWidth=1.4;cx.beginPath();let st=false;
      arr.forEach((v,i)=>{{if(v==null)return;const x=x0+cw*i+cw/2,y=py(v);
        if(!st){{cx.moveTo(x,y);st=true;}}else cx.lineTo(x,y);}});cx.stroke();cx.lineWidth=1;}}
    line(m20,'#d97706'); line(m50,'#7c3aed');
    cx.fillStyle='#94a3b8'; cx.textAlign='center';
    [0,Math.floor(view.length/2),view.length-1].forEach(i=>{{
      if(i<0||i>=view.length)return; cx.fillText(view[i][0].slice(2),x0+cw*i+cw/2,H-6);}});
  }}
  document.querySelectorAll('.klbtns button').forEach(btn=>{{
    btn.onclick=()=>{{N=+btn.dataset.n;
      document.querySelectorAll('.klbtns button').forEach(b=>b.classList.remove('on'));
      btn.classList.add('on'); draw();}};
  }});
  window.addEventListener('resize',draw);
  draw();
}})();
</script>"""

PLACEHOLDER = "<!-- KLINE -->"


def main():
    if len(sys.argv) < 3:
        print("用法: python inject_kline.py TICKER REPORT_HTML_PATH")
        sys.exit(1)
    ticker = sys.argv[1].upper().strip()
    report_path = sys.argv[2]

    here = os.path.dirname(os.path.abspath(__file__))
    hist_path = os.path.join(here, "history", f"{ticker}_history.json")
    if not os.path.exists(hist_path):
        print(f"找不到歷史檔：{hist_path}（請先跑 update_history.py {ticker}）")
        sys.exit(2)
    with open(hist_path, encoding="utf-8") as f:
        hist = json.load(f)
    bars = hist.get("price_bars") or []
    if not bars:
        print(f"歷史檔無 price_bars（可能用舊版 fetch_stock 產生）；請重跑 update_history.py {ticker}")
        sys.exit(2)

    if not os.path.exists(report_path):
        print(f"找不到報告檔：{report_path}")
        sys.exit(2)
    with open(report_path, encoding="utf-8") as f:
        html = f.read()
    if PLACEHOLDER not in html:
        print(f"報告中找不到佔位符 {PLACEHOLDER}，未注入（請在 M4 技術面放此佔位符）")
        sys.exit(3)

    block = BLOCK.replace("{DATA}", json.dumps(bars, ensure_ascii=False, separators=(",", ":")))
    html = html.replace(PLACEHOLDER, block)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已注入日K線（{len(bars)} 根）至 {report_path}")


if __name__ == "__main__":
    main()
