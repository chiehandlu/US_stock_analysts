#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日K線注入器 — 把互動式日K線圖（canvas + 內嵌資料 + 純 JS）注入報告 HTML
----------------------------------------------------------
用途：
    python inject_kline.py TICKER reports/TICKER_report_YYYY-MM-DD.html

做的事：
  1. 讀 history/{TICKER}_history.json 的 price_bars（近 2 年日 OHLCV）。
  2. 產出自包含的 K 線圖區塊：蠟燭、MA20/MA50/MA200、成交量、十字游標即時顯示當日
     OHLC/漲跌/量/均線，與 [1月][半年][1年][2年] 切換鈕。
  3. 把報告 HTML 內的 <!-- KLINE --> 佔位符替換成該區塊；若報告已含先前注入的區塊
     （以 START/END 標記辨識）則就地更新——故本腳本可重複執行。

設計：
  - 全內嵌（資料 + JS 都在區塊內），不引用任何外部資源 → 下載到手機離線也能渲染。
  - 用 str.replace（非 str.format），故 JS/CSS 大括號維持單一、不需跳脫；唯一佔位符是 {DATA}。
  - canvas 繪製，數百根 K 棒仍清晰；視窗切換與十字游標只重繪、不重載。

依賴：標準函式庫。
"""

import sys
import os
import re
import json

START = "<!--KLINE:START-->"
END = "<!--KLINE:END-->"
PLACEHOLDER = "<!-- KLINE -->"

# K 線圖區塊模板。{DATA} 會被 price_bars 的 JSON 取代；其餘大括號皆為正常 JS/CSS。
BLOCK = START + """
<div class="chartcard">
  <div class="chartttl">日K線（還原權值）　綠漲紅跌 ·
    <span style="color:#f59e0b;font-weight:700">MA20</span> ·
    <span style="color:#a78bfa;font-weight:700">MA50</span> ·
    <span style="color:#60a5fa;font-weight:700">MA200</span> · 下方為成交量。移動游標／觸控看當日 OHLC</div>
  <div class="klbtns" style="display:flex;gap:6px;margin:2px auto 8px;max-width:1000px">
    <button data-n="22">1月</button><button data-n="126">半年</button>
    <button data-n="252" class="on">1年</button><button data-n="0">2年</button>
  </div>
  <canvas id="klcv" style="width:100%;max-width:1000px;height:420px;display:block;margin:0 auto;touch-action:none"></canvas>
  <style>
    .klbtns button{font:13px/1 inherit;padding:5px 12px;border:1px solid #cbd5e1;
      background:#fff;color:#475569;border-radius:7px;cursor:pointer}
    .klbtns button.on{background:#4f46e5;border-color:#4f46e5;color:#fff;font-weight:700}
  </style>
</div>
<script>
(function(){
  const BARS = {DATA};
  if(!BARS || !BARS.length){return;}
  const close = BARS.map(b=>b[4]);
  function ma(p){const r=[];let s=0;for(let i=0;i<close.length;i++){s+=close[i];
    if(i>=p)s-=close[i-p];r.push(i>=p-1?s/p:null);}return r;}
  const MA20=ma(20), MA50=ma(50), MA200=ma(200);
  const cv=document.getElementById('klcv'), cx=cv.getContext('2d'), H=420;
  let N=252, G=null, hx=null, hy=null;

  function draw(){
    const dpr=window.devicePixelRatio||1, W=cv.clientWidth;
    cv.width=W*dpr; cv.height=H*dpr; cx.setTransform(dpr,0,0,dpr,0,0);
    cx.clearRect(0,0,W,H);
    const n=N>0?N:BARS.length, start=Math.max(0,BARS.length-n);
    const view=BARS.slice(start), m20=MA20.slice(start), m50=MA50.slice(start), m200=MA200.slice(start);
    const padT=10,padB=22,padR=52,padL=8,gap=8,volH=60;
    const priceH=H-padT-padB-volH-gap, x0=padL, x1=W-padR;
    let hi=-1e9,lo=1e9;
    view.forEach(b=>{hi=Math.max(hi,b[2]);lo=Math.min(lo,b[3]);});
    m20.concat(m50,m200).forEach(v=>{if(v!=null){hi=Math.max(hi,v);lo=Math.min(lo,v);}});
    const pd=(hi-lo)*0.06||1; hi+=pd; lo-=pd;
    const py=v=>padT+(hi-v)/(hi-lo)*priceH;
    let vmax=0; view.forEach(b=>vmax=Math.max(vmax,b[5]||0));
    const volTop=padT+priceH+gap, vy=v=>volTop+volH-(v/(vmax||1))*volH;
    const cw=(x1-x0)/view.length, bw=Math.max(1,Math.min(cw*0.7,13));
    cx.strokeStyle='rgba(148,163,184,0.16)'; cx.fillStyle='#94a3b8'; cx.font='10px sans-serif'; cx.textAlign='left';
    for(let g=0;g<=4;g++){const v=lo+(hi-lo)*g/4,y=py(v);
      cx.beginPath();cx.moveTo(x0,y);cx.lineTo(x1,y);cx.stroke();
      cx.fillText(v.toFixed(v<10?2:0),x1+4,y+3);}
    view.forEach((b,i)=>{
      const xc=x0+cw*i+cw/2, up=b[4]>=b[1], col=up?'#22c55e':'#ef4444';
      cx.strokeStyle=col; cx.fillStyle=col;
      cx.beginPath();cx.moveTo(xc,py(b[2]));cx.lineTo(xc,py(b[3]));cx.stroke();
      const yo=py(b[1]),yc=py(b[4]),top=Math.min(yo,yc),hh=Math.max(1,Math.abs(yo-yc));
      cx.fillRect(xc-bw/2,top,bw,hh);
      cx.fillStyle=up?'rgba(34,197,94,.4)':'rgba(239,68,68,.4)';
      const vyy=vy(b[5]||0); cx.fillRect(xc-bw/2,vyy,bw,volTop+volH-vyy);
    });
    function line(arr,col){cx.strokeStyle=col;cx.lineWidth=1.4;cx.beginPath();let st=false;
      arr.forEach((v,i)=>{if(v==null)return;const x=x0+cw*i+cw/2,y=py(v);
        if(!st){cx.moveTo(x,y);st=true;}else cx.lineTo(x,y);});cx.stroke();cx.lineWidth=1;}
    line(m20,'#f59e0b'); line(m50,'#a78bfa'); line(m200,'#60a5fa');
    cx.fillStyle='#94a3b8'; cx.textAlign='center';
    [0,Math.floor(view.length/2),view.length-1].forEach(i=>{
      if(i<0||i>=view.length)return; cx.fillText(view[i][0].slice(2),x0+cw*i+cw/2,H-6);});
    G={view,m20,m50,m200,x0,cw,py,W,padT,priceH,volTop,volH,x1};
  }

  function fmtVol(v){if(v==null)return '-';return v>=1e8?(v/1e8).toFixed(2)+'億':(v/1e4).toFixed(0)+'萬';}

  function overlay(){
    if(!G||hx==null)return;
    const g=G;
    let i=Math.round((hx-g.x0-g.cw/2)/g.cw);
    i=Math.max(0,Math.min(g.view.length-1,i));
    const b=g.view[i], xc=g.x0+g.cw*i+g.cw/2;
    cx.save();cx.strokeStyle='#94a3b8';cx.setLineDash([4,3]);cx.lineWidth=1;
    cx.beginPath();cx.moveTo(xc,g.padT);cx.lineTo(xc,g.volTop+g.volH);cx.stroke();
    const yy=Math.max(g.padT,Math.min(g.padT+g.priceH,hy));
    cx.beginPath();cx.moveTo(g.x0,yy);cx.lineTo(g.x1,yy);cx.stroke();
    cx.restore();
    const prev=i>0?g.view[i-1][4]:b[1], chg=(b[4]-prev)/prev*100;
    const m20=g.m20[i], m50=g.m50[i], m200=g.m200[i];
    const lines=[
      b[0],
      '開 '+b[1].toFixed(2)+'   高 '+b[2].toFixed(2),
      '低 '+b[3].toFixed(2)+'   收 '+b[4].toFixed(2),
      '漲跌 '+(chg>=0?'+':'')+chg.toFixed(2)+'%   量 '+fmtVol(b[5]),
      'MA20 '+(m20!=null?m20.toFixed(1):'-')+'  MA50 '+(m50!=null?m50.toFixed(1):'-')+'  MA200 '+(m200!=null?m200.toFixed(1):'-')
    ];
    cx.font='11px sans-serif';
    let tw=0; lines.forEach(t=>tw=Math.max(tw,cx.measureText(t).width));
    const bw2=tw+16, bh=lines.length*16+10;
    let tx=xc+12; if(tx+bw2>g.W) tx=xc-12-bw2; if(tx<2)tx=2;
    const ty=g.padT+4;
    cx.fillStyle='rgba(15,23,42,.95)';cx.strokeStyle='#334155';cx.lineWidth=1;
    cx.beginPath();cx.rect(tx,ty,bw2,bh);cx.fill();cx.stroke();
    cx.textAlign='left';cx.textBaseline='top';
    lines.forEach((t,k)=>{
      cx.fillStyle = k===0?'#f1f5f9':(k===3?(chg>=0?'#22c55e':'#ef4444'):'#cbd5e1');
      cx.font = k===0?'700 11px sans-serif':'11px sans-serif';
      cx.fillText(t,tx+8,ty+6+k*16);
    });
    cx.textBaseline='alphabetic';
  }

  function render(){draw();overlay();}
  cv.addEventListener('pointermove',e=>{const r=cv.getBoundingClientRect();
    hx=e.clientX-r.left; hy=e.clientY-r.top; render();});
  cv.addEventListener('pointerdown',e=>{const r=cv.getBoundingClientRect();
    hx=e.clientX-r.left; hy=e.clientY-r.top; render();});
  cv.addEventListener('pointerleave',()=>{hx=null;draw();});
  document.querySelectorAll('.klbtns button').forEach(btn=>{
    btn.onclick=()=>{N=+btn.dataset.n;hx=null;
      document.querySelectorAll('.klbtns button').forEach(b=>b.classList.remove('on'));
      btn.classList.add('on');draw();};
  });
  window.addEventListener('resize',()=>{hx=null;draw();});
  draw();
})();
</script>
""" + END


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

    block = BLOCK.replace("{DATA}", json.dumps(bars, ensure_ascii=False, separators=(",", ":")))

    if START in html and END in html:
        html = re.sub(re.escape(START) + ".*?" + re.escape(END), lambda m: block, html, flags=re.S)
        where = "更新既有 K 線區塊"
    elif PLACEHOLDER in html:
        html = html.replace(PLACEHOLDER, block)
        where = "替換佔位符"
    else:
        print(f"報告中找不到 {PLACEHOLDER} 佔位符，也無既有 K 線區塊，未注入")
        sys.exit(3)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已注入日K線（{len(bars)} 根，含 MA20/50/200 + 十字游標）→ {where}：{report_path}")


if __name__ == "__main__":
    main()
