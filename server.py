"""Monitoring dashboard: GET / (UI) and /api/state (JSON)."""
import json
import logging

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

import config
from data_engine import alpaca_client
from evaluation.evaluator import dashboard_payload as evaluation_payload
from execution import position_manager
from execution.ledger import load as load_ledger
from data_engine.stock_data import get_daily_bars, get_hourly_bars

log = logging.getLogger(__name__)
app = FastAPI(title="Options Agent Dashboard")

_cache = {"t": 0.0, "account": None}


def _account_cached() -> dict:
    import time

    if _cache["account"] is None or time.time() - _cache["t"] > 15:
        try:
            acct = alpaca_client.safe(
                "get_account", alpaca_client.trading_client().get_account
            )
            clock = alpaca_client.safe("get_clock", alpaca_client.trading_client().get_clock)
            _cache["account"] = {
                "equity": float(acct.equity or 0),
                "buying_power": float(acct.buying_power or 0),
                "status": str(acct.status),
                "market_open": bool(clock.is_open),
                "next_open": str(clock.next_open),
            }
            _cache["t"] = time.time()
        except Exception as exc:  # noqa: BLE001
            _cache["account"] = {"error": str(exc)}
    return _cache["account"]


def _cycle_files() -> list[str]:
    return sorted(p.name for p in config.REPORTS_DIR.glob("*_cycle_report.json"))


def _latest_cycle() -> dict | None:
    files = _cycle_files()
    if not files:
        return None
    try:
        with open(config.REPORTS_DIR / files[-1], encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return None


@app.get("/api/state")
def api_state() -> JSONResponse:
    data = load_ledger()
    open_pos = [
        p for p in data["positions"]
        if p["status"] in {"OPEN", "PENDING_ENTRY", "CLOSING", "RECOVERY_REQUIRED"}
    ]
    closed_pos = [p for p in data["positions"] if p["status"] == "CLOSED"][-25:]
    state = {
        "account": _account_cached(),
        "open_positions": open_pos,
        "closed_positions": closed_pos,
        "daily": data.get("daily", {}),
        "latest_cycle": _latest_cycle(),
        "cycle_history": list(reversed(_cycle_files()))[:30],
        "exposure_pct": round(position_manager.exposure_pct(data), 2) if open_pos else 0,
        "evaluation": evaluation_payload(),
    }
    return JSONResponse(state, headers={"Cache-Control": "no-store"})


@app.get("/api/bars/{symbol}")
def api_bars(symbol: str, timeframe: str = "1H", days: int = 10) -> JSONResponse:
    """Return normalized historical OHLCV bars for charting."""
    timeframe = str(timeframe).upper()
    if timeframe not in {"1D", "1H"} or not 1 <= int(days) <= 400:
        return JSONResponse({"error": "timeframe must be 1D or 1H and days must be 1-400"}, status_code=400)
    frame = (get_hourly_bars if timeframe == "1H" else get_daily_bars)(symbol.upper(), days=int(days))
    bars = []
    for timestamp, row in frame.iterrows():
        item = {"timestamp": timestamp.isoformat()}
        for field in ("open", "high", "low", "close", "volume", "trade_count", "vwap"):
            if field in row and pd.notna(row[field]):
                item[field] = float(row[field])
        bars.append(item)
    return JSONResponse({"symbol": symbol.upper(), "timeframe": timeframe, "bars": bars})


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


HTML_PAGE = """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<title>Robot Trading Options — Pantauan</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 :root{--bg:#0f1420;--card:#1a2130;--card2:#202a3d;--border:#2c3850;--text:#eaeefb;--dim:#93a0bd;
 --green:#4ade80;--red:#f87171;--yellow:#fbbf24;--blue:#60a5fa;--purple:#c084fc}
 *{box-sizing:border-box;margin:0;padding:0}
 body{background:var(--bg);color:var(--text);font:15px/1.55 'Segoe UI',system-ui,sans-serif;padding:24px;max-width:1280px;margin:0 auto}
 h1{font-size:22px;font-weight:700} h2{font-size:14px;text-transform:none;font-weight:700;color:var(--text);margin-bottom:10px}
 .sub{color:var(--dim);font-size:13px;margin-top:2px}
 .banner{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0;padding:12px 16px;background:var(--card);border:1px solid var(--border);border-radius:12px;align-items:center}
 .banner .item{display:flex;align-items:center;gap:7px;font-size:13.5px}
 .grid{display:grid;gap:12px}
 .cards{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
 .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px}
 .kpi .label{color:var(--dim);font-size:12.5px;display:flex;justify-content:space-between;align-items:center}
 .kpi .v{font-size:23px;font-weight:800;margin-top:6px;letter-spacing:-.02em}
 .kpi .hint{color:var(--dim);font-size:11.5px;margin-top:3px}
 .row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}
 @media(max-width:980px){.row{grid-template-columns:1fr}}
 table{width:100%;border-collapse:collapse;font-size:13.5px}
 th{color:var(--dim);text-align:left;font-weight:600;padding:7px 9px;border-bottom:1px solid var(--border);font-size:12.5px}
 td{padding:8px 9px;border-bottom:1px solid #232d42;vertical-align:top}
 tr:last-child td{border-bottom:none}
 .pill{display:inline-block;padding:3px 11px;border-radius:99px;font-size:12px;font-weight:700;white-space:nowrap}
 .g{background:#0e3a22;color:var(--green)} .r{background:#45191d;color:var(--red)}
 .y{background:#40300a;color:var(--yellow)} .b{background:#152c4d;color:var(--blue)} .p{background:#33184a;color:var(--purple)}
 .muted{color:var(--dim)} .mono{font-family:Consolas,monospace;font-size:12.5px}
 details{margin-top:6px;border:1px solid var(--border);border-radius:10px;background:var(--card2)}
 details summary{cursor:pointer;padding:9px 12px;font-weight:600;font-size:13.5px;list-style:none;display:flex;align-items:center;gap:8px}
 details summary::before{content:'▸';color:var(--dim);transition:transform .15s}
 details[open] summary::before{transform:rotate(90deg)}
 details .body{padding:4px 12px 12px}
 .dot{width:10px;height:10px;border-radius:50%;display:inline-block}
 .ok{background:var(--green);box-shadow:0 0 8px var(--green)} .off{background:var(--red)}
 .ts{font-size:11.5px;color:var(--dim)}
  .action-line{padding:10px 12px;border-radius:10px;background:var(--card2);border-left:3px solid var(--blue);margin-top:8px;font-size:14px}
  .chart-controls{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
  .chart-controls select{background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:7px;padding:6px 8px}
  .chart{width:100%;height:240px;background:var(--card2);border:1px solid var(--border);border-radius:10px}
  .chart polyline{fill:none;stroke:var(--blue);stroke-width:2.5;vector-effect:non-scaling-stroke}
  .chart line{stroke:var(--border);stroke-width:1;vector-effect:non-scaling-stroke}
  :focus-visible{outline:2px solid var(--blue);outline-offset:2px}
 .help ol{margin-left:20px;color:var(--dim);font-size:13px;line-height:1.9}
 .pos-neg{font-weight:700}
 #errbar{display:none;background:#45191d;color:var(--red);padding:10px 14px;border-radius:10px;margin-top:12px;font-size:13.5px}
</style>
</head>
<body>
<main id="main">
<h1>🤖 Robot Trading Options <span class="sub">mode kertas (paper) — uang simulasi, bukan uang asli</span></h1>
<p class="sub" style="margin-top:6px">Halaman ini memperbarui sendiri setiap 12 detik. Robot bekerja otomatis:
menganalisa pasar → menimbang risiko → pasang order bila aman.</p>

<div class="banner">
 <span class="item"><span class="dot off" id="dot_market"></span><b id="market_txt">memuat…</b></span>
 <span class="item muted" id="next_open_txt"></span>
 <span class="item"><span class="dot off" id="dot_llm"></span>Otak AI (Ollama): <b id="llm_txt">?</b></span>
 <span class="item muted" style="margin-left:auto">Terakhir cek: <span class="ts" id="upd_at"></span></span>
</div>

<div id="errbar" role="alert" aria-live="assertive"></div>

<div class="grid cards">
 <div class="card kpi"><div class="label">Nilai Akun <span title="Total nilai seluruh aset di akun simulasi">ⓘ</span></div><div class="v" id="equity">—</div><div class="hint">dana kertas, bukan uang nyata</div></div>
 <div class="card kpi"><div class="label">Dana Siap Pakai</div><div class="v" id="bp">—</div><div class="hint">daya beli untuk order baru</div></div>
 <div class="card kpi"><div class="label">Posisi Aktif</div><div class="v" id="npos">—</div><div class="hint">strategi yang sedang berjalan</div></div>
 <div class="card kpi"><div class="label">Risiko Terpakai</div><div class="v" id="exp">—</div><div class="hint">% akun, total kemungkinan rugi maksimal</div></div>
 <div class="card kpi"><div class="label">Untung/Rugi Hari Ini</div><div class="v" id="pnl">—</div><div class="hint">dari posisi yang sudah ditutup</div></div>
</div>

<section class="card" style="margin-top:14px">
 <h2>📈 Trend Historical</h2>
 <div class="chart-controls">
  <label>Symbol <select id="chart_symbol" aria-label="Pilih symbol grafik"><option>SPY</option><option>QQQ</option><option>AAPL</option><option>NVDA</option><option>MSFT</option></select></label>
  <label>Timeframe <select id="chart_tf" aria-label="Pilih timeframe grafik"><option value="1H">Hourly</option><option value="1D">Daily</option></select></label>
 </div>
 <svg id="trend_chart" class="chart" viewBox="0 0 800 240" role="img" aria-label="Grafik close historical"></svg>
 <div id="chart_status" class="sub" style="margin-top:7px" role="status" aria-live="polite">memuat historical bars...</div>
 <div class="sub" style="margin-top:5px">GREEN_PROXY = sinyal saham berbasis histori; SHADOW_ONLY = LLM menganalisis tanpa order.</div>
</section>

<div class="row">
 <section class="card">
  <h2>📡 Kegiatan Siklus Terakhir <span class="ts" id="cyc_ts"></span></h2>
  <div id="cyc_summary"></div>
 </section>
 <section class="card">
  <h2>💼 Posisi Sedang Berjalan</h2>
  <table><thead><tr><th>Saham</th><th>Strategi</th><th>Kontrak</th><th>Credit / Debit</th><th>Rugi Maksimal</th></tr></thead>
  <tbody id="pos_rows"><tr><td colspan=5 class=muted>belum ada posisi</td></tr></tbody></table>
 </section>
</div>

<div class="row">
 <section class="card">
  <h2>🧠 Cara Sistem Berpikir <span class="ts">(siklus terakhir)</span></h2>
  <details open><summary>1️⃣ Arah harga saham</summary><div class="body"><div id="s_trend_short" class="sub"></div><pre class="mono muted" id="a_trend"></pre></div></details>
  <details><summary>2️⃣ Mahal-murahnya premi opsi</summary><div class="body"><div id="s_vol_short" class="sub"></div><pre class="mono muted" id="a_vol"></pre></div></details>
  <details><summary>3️⃣ Berita &amp; jadwal earnings</summary><div class="body"><div id="s_news_short" class="sub"></div><pre class="mono muted" id="a_news"></pre></div></details>
  <details><summary>4️⃣ Kesimpulan gabungan</summary><div class="body"><div id="s_tech_short" class="sub"></div><pre class="mono muted" id="a_tech"></pre></div></details>
  <details><summary>5️⃣ Usulan strategi (otak utama)</summary><div class="body"><div id="s_prop_short" class="sub"></div><pre class="mono muted" id="a_prop"></pre></div></details>
  <details open><summary>6️⃣ Pemeriksaan risiko (gerak terakhir)</summary><div class="body"><div id="s_risk_short" class="sub"></div><pre class="mono muted" id="a_risk"></pre></div></details>
 </section>
 <section class="card">
  <h2>📕 Riwayat Posisi Selesai</h2>
  <table><thead><tr><th>Saham</th><th>Strategi</th><th>Hasil</th><th>Alasan Tutup</th></tr></thead>
  <tbody id="closed_rows"><tr><td colspan=4 class=muted>belum ada</td></tr></tbody></table>
  <h2 style="margin-top:18px">🗂️ Laporan Siklus Tersimpan</h2>
  <div id="hist" class="muted mono" style="font-size:11px;max-height:110px;overflow:auto"></div>
 </section>
</div>

<section class="card" style="margin-top:14px">
 <h2>🎓 Evaluasi &amp; Pembelajaran Otonom</h2>
 <div id="eval_summary" class="sub"></div>
 <div id="eval_stats" style="margin-top:10px"></div>
 <details style="margin-top:8px"><summary>📚 Pelajaran yang AI tulis untuk dirinya sendiri</summary><div class="body"><ul id="eval_lessons" class="sub" style="margin-left:18px"></ul></div></details>
 <details><summary>🧷 Post-mortem terakhir (memori semantik LanceDB)</summary><div class="body"><div id="eval_pm" class="mono muted" style="font-size:11.5px"></div></div></details>
</section>

<section class="card help" style="margin-top:14px">
 <h2>❓ Bagaimana robot ini bekerja?</h2>
 <ol>
  <li><b>Kumpulkan data</b> — harga saham, rantai opsi, dan berita dari Alpaca.</li>
  <li><b>Hitung angka penting</b> — volatilitas, arah tren, perkiraan gerak harga (tanpa AI, murni matematika).</li>
  <li><b>AI menafsirkan</b> — beberapa agen AI membahas arah pasar, mahal/murahnya premi, dan risiko berita.</li>
  <li><b>AI memilih strategi</b> — misal beli call, atau pasangan sell/buy (spread), lengkap dengan alasannya.</li>
  <li><b>Bendungan risiko</b> — semua angka dihitung ulang; jika potensi rugi melebihi batas, order dibatalkan.</li>
  <li><b>Order dikirim</b> ke akun <b>kertas Alpaca</b>, lalu dipantau sampai ditutup (ambil untung / batasi rugi / antisipasi jatuh tempo).</li>
 </ol>
</section>
 </main>

<script>
const $=id=>document.getElementById(id);
const fmt=n=>n==null?'—':Number(n).toLocaleString('id-ID',{maximumFractionDigits:2});
const money=n=>'$'+fmt(n);
function pill(t,c){return `<span class="pill ${c}">${t}</span>`;}
const STRAT={LONG_CALL:'Long Call (beli call)',LONG_PUT:'Long Put (beli put)',
 BULL_PUT_SPREAD:'Bull Put Spread',BEAR_CALL_SPREAD:'Bear Call Spread',
 DEBIT_SPREAD:'Debit Spread',IRON_CONDOR:'Iron Condor',WAIT:'Menunggu'};
const stratName=s=>STRAT[s]||s||'—';
const EXIT={take_profit:'Target untung tercapai ✔',stop_loss:'Batas rugi tersentuh ✋',anti_assignment:'Ditutup lebih awal — antisipasi jatuh tempo'};
const exitName=r=>{if(!r)return'';for(const k in EXIT)if(r.startsWith(k))return EXIT[k];return r;};
const ACT={
 EXECUTED:(r)=>({cls:'g',txt:`✅ Order DIPASANG — ${stratName(r.reports?.proposal?.strategy_type)}`}),
   ORDER_SUBMITTED:(r)=>({cls:'b',txt:`⏳ Menunggu fill broker — ${stratName(r.reports?.proposal?.strategy_type)}`}),
   DRY_RUN:(r)=>({cls:'b',txt:`🧪 DRY-RUN — ${stratName(r.reports?.proposal?.strategy_type)}`}),
  SHADOW_ONLY:()=>({cls:'b',txt:'🔭 SHADOW_ONLY — LLM analisis, tanpa order'}),
  REJECTED:()=>({cls:'r',txt:'🚫 DITOLAK oleh pemeriksa risiko — dana tetap aman'}),
  WAIT:()=>({cls:'y',txt:'⏸️ Sistem memilih MENUNGGU — kondisi belum jelas'})};

async function loadChart(){
 try{
  const symbol=$('chart_symbol').value, timeframe=$('chart_tf').value;
  const days=timeframe==='1H'?10:120;
  const payload=await (await fetch(`/api/bars/${symbol}?timeframe=${timeframe}&days=${days}`)).json();
  if(payload.error) throw new Error(payload.error);
  const bars=payload.bars||[], closes=bars.map(b=>Number(b.close)).filter(Number.isFinite);
  if(closes.length<2) throw new Error('historical bars tidak cukup');
  const w=800,h=240,p=22,min=Math.min(...closes),max=Math.max(...closes),span=max-min||1;
  const points=closes.map((v,i)=>`${p+i*(w-2*p)/(closes.length-1)},${h-p-(v-min)*(h-2*p)/span}`).join(' ');
  $('trend_chart').innerHTML=`<line x1="${p}" y1="${h-p}" x2="${w-p}" y2="${h-p}"/><polyline points="${points}"/>`;
  $('chart_status').textContent=`${symbol} · ${timeframe} · ${bars.length} bars · ${new Date(bars[0].timestamp).toLocaleString('id-ID')} sampai ${new Date(bars[bars.length-1].timestamp).toLocaleString('id-ID')} · close terakhir ${fmt(closes[closes.length-1])}`;
 }catch(e){$('chart_status').textContent='Grafik gagal dimuat: '+e;}
}

async function tick(){
 try{
  const st=await (await fetch('/api/state')).json();
  $('errbar').style.display='none';
   const a=st.account||{};
   if(a.error){$('errbar').textContent='Gagal menghubungi Alpaca: '+a.error;$('errbar').style.display='block';}
   const llmOk=st.latest_cycle?st.latest_cycle.llm_available:null;
   $('llm_txt').textContent=llmOk==null?'belum ada siklus':(llmOk?'aktif':'nonaktif — pakai fallback');
   $('dot_llm').className='dot '+(llmOk?'ok':'off');

  // banner
  const open=a.market_open;
  $('dot_market').className='dot '+(open?'ok':'off');
  $('market_txt').textContent=open?'Pasar BUKA':'Pasar TUTUP';
  $('next_open_txt').textContent=a.next_open?('buka lagi '+new Date(a.next_open).toLocaleString('id-ID',{weekday:'short',hour:'2-digit',minute:'2-digit'})):'';
  $('upd_at').textContent=new Date().toLocaleTimeString('id-ID');

  // KPI
  $('equity').textContent=money(a.equity);
  $('bp').textContent=money(a.buying_power);
  $('npos').textContent=(st.open_positions||[]).length;
  $('exp').textContent=(st.exposure_pct??'—')+'%';
  const pl=((st.daily&&Object.values(st.daily))||[]).reduce((x,d)=>x+(d.realized_pl||0),0);
  const pnlEl=$('pnl'); pnlEl.textContent=(pl>0?'+':'')+money(pl);
  pnlEl.style.color=pl>0?'var(--green)':(pl<0?'var(--red)':'var(--text)');

  // cycle
  const c=st.latest_cycle;
  if(c){
   $('cyc_ts').textContent=c.timestamp?('· '+new Date(c.timestamp).toLocaleString('id-ID')):'';
   let html='';
   if(c.skipped) html+=`<div class="action-line" style="border-color:var(--yellow)">⏳ ${pill('LEWATI','y')} siklus tidak dijalankan — ${c.skipped}</div>`;
   if(c.new_entries_blocked) html+=`<div class="action-line" style="border-color:var(--red)">${pill('REM DARURAT','r')} entry baru dihentikan sementara: ${c.new_entries_blocked}</div>`;
   (c.position_exits||[]).forEach(e=>{
     html+=`<div class="action-line" style="border-color:var(--purple)">🔒 ${pill('POSISI DITUTUP','p')} ${exitName(e.reason)} · hasil ${e.estimated_realized_pl>=0?'+':''}$${fmt(e.estimated_realized_pl)}</div>`;});
   (c.results||[]).forEach(r=>{
     let inner;
     if(r.error) inner=`<div class="action-line" style="border-color:var(--red)">⚠️ ${r.symbol}: gagal dianalisa (${r.error})</div>`;
     else{
       const actFn=ACT[r.action];
       const act=(actFn||((x)=>({cls:'y',txt:x.action})))(r);
       const det=[];
       if(r.quant_summary){const q=r.quant_summary;det.push(`harga $${fmt(q.price)}`);}
       if(r.execution) det.push(`order ${r.execution.status}, credit ±$${fmt(Math.abs(r.execution.limit_net))}/kontrak`);
       if(r.reports?.risk_decision?.decision==='REJECTED') det.push('alasan: '+(r.reports.risk_decision.notes||'melewati batas'));
       inner=`<div class="action-line" style="border-color:${act.cls==='g'?'var(--green)':act.cls==='r'?'var(--red)':'var(--yellow)'}">
         <b>${r.symbol}</b> ${pill(act.txt,act.cls)}
         <div class="sub">${det.join(' · ')}</div></div>`;
     }
     html+=inner;
   });
   $('cyc_summary').innerHTML=html||'<div class="muted">belum ada siklus — jalankan <code>python main.py --once --force</code></div>';

   const rep=((c.results||[]).find(x=>x.reports)||{}).reports||{};
   const show=(id,obj)=>{$(id).textContent=obj?JSON.stringify(obj,null,1):'(belum ada data)';};
   show('a_trend',rep.trend);show('a_vol',rep.volatility);show('a_news',rep.news);
   show('a_tech',rep.technical);show('a_prop',rep.proposal);show('a_risk',rep.risk_decision);

   const one=(o,k)=>o&&o[k]!=null?o[k]:null;
   const t=rep.trend,v=rep.volatility,n=rep.news,tc=rep.technical,p=rep.proposal,rk=rep.risk_decision;
   $('s_trend_short').textContent=t?`AI menilai saham condong ${({BULLISH:'NAIK 📈',BEARISH:'TURUN 📉',NEUTRAL:'mendatar ➡️'})[t.bias]||t.bias} (kekuatan: ${({STRONG:'kuat',MODERATE:'sedang',WEAK:'lemah'})[t.trend_strength]||t.trend_strength})`:'';
   $('s_vol_short').textContent=v?`Premi opsi saat ini ${({HIGH_IV:'MAHAL — bagus untuk menjual premium 💰',LOW_IV:'MURAH — bagus untuk membeli premium 🎯',NEUTRAL:'sedang — netral'})[v.volatility_regime]||v.volatility_regime}`:'';
   $('s_news_short').textContent=n?`Risiko berita: ${n.event_risk}${n.earnings_warning?' ⚠️ earnings dekat (bahaya IV crush)':''}`:'';
   $('s_tech_short').textContent=tc?tc.summary||'':'';
   $('s_prop_short').textContent=p?(p.strategy_type==='WAIT'?`Sistem menunggu. Alasan: ${p.rationale||''}`:`Usulan: ${stratName(p.strategy_type)}. Alasan: ${p.rationale||''}`):'';
   $('s_risk_short').textContent=rk?(rk.decision==='APPROVED'?'✅ LOLOS semua pemeriksaan — order boleh dikirim.':rk.decision==='REJECTED'?`🚫 TIDAK LOLOS — ${(rk.notes||'ada pemeriksaan gagal')}.`:'—'):'';
  }

  // positions
  const rows=(st.open_positions||[]).map(p=>`<tr>
   <td><b>${p.underlying}</b></td><td>${stratName(p.strategy_type)}</td>
   <td class="mono">${p.legs.map(l=>`${l.action==='SELL'?'Jual':'Beli'} ${l.strike}${l.opt_type?(l.opt_type==='call'?'C':'P'):''}`).join('<br>')}</td>
   <td>${p.net_credit_or_debit_per_unit>0?'+':''}$${fmt(p.net_credit_or_debit_per_unit)}</td>
   <td style="color:var(--red)">$${fmt(Math.abs(p.max_loss_usd))}</td></tr>`).join('');
  $('pos_rows').innerHTML=rows||'<tr><td colspan=5 class=muted>belum ada posisi aktif</td></tr>';

  // closed history
  const crows=(st.closed_positions||[]).slice().reverse().map(p=>`<tr>
   <td><b>${p.underlying}</b></td><td>${stratName(p.strategy_type)}</td>
   <td class="pos-neg" style="color:${(p.realized_pl>=0)?'var(--green)':'var(--red)'}">${(p.realized_pl>=0?'+':'')}$${fmt(p.realized_pl)}</td>
   <td class="muted">${exitName(p.exit_reason)||''}</td></tr>`).join('');
  $('closed_rows').innerHTML=crows||'<tr><td colspan=4 class=muted>belum ada</td></tr>';

   $('hist').innerHTML=(st.cycle_history||[]).join('<br>')||'(kosong)';

  // evaluation panel
  const ev=st.evaluation||{};
  if(ev.stats){
   const s=ev.stats;
   $('eval_summary').textContent=`Posisi selesai: ${s.total_closed} · Win rate: ${s.win_rate!=null?(s.win_rate*100).toFixed(0)+'%':'—'} · Total P/L: ${(s.total_pl>=0?'+':'')}$${fmt(s.total_pl)} · Memori: ${ev.memory_rows??0} post-mortem`;
   let sh='';
   for(const [k,v] of Object.entries(s.by_strategy||{})){
    sh+=`<tr><td>${stratName(k)}</td><td>${v.n}x</td><td class="pos-neg" style="color:${v.total_pl>=0?'var(--green)':'var(--red)'}">${v.total_pl>=0?'+':''}$${fmt(v.total_pl)}</td><td>${v.win_rate!=null?(v.win_rate*100).toFixed(0)+'%':'—'}</td></tr>`;
   }
   $('eval_stats').innerHTML=sh?`<table><thead><tr><th>Strategi</th><th>Jumlah</th><th>Total P/L</th><th>Win rate</th></tr></thead><tbody>${sh}</tbody></table>`:'<div class="muted">belum ada data strategi</div>';
  }
  const lessons=ev.lessons||[];
  $('eval_lessons').innerHTML=lessons.length?lessons.map(l=>`<li>${l}</li>`).join(''):'<li class="muted">belum ada pelajaran — muncul setelah ada posisi selesai</li>';
  const pms=ev.postmortems||[];
  $('eval_pm').innerHTML=pms.length?pms.map(p=>`<div>${p.text||''}</div>`).join('<br>'):'belum ada post-mortem';
 }catch(e){
  $('errbar').style.display='block';
  $('errbar').textContent='Tidak bisa mengambil data: '+e;
 }
}
$('chart_symbol').addEventListener('change',loadChart); $('chart_tf').addEventListener('change',loadChart);
loadChart(); tick(); setInterval(tick,12000); setInterval(loadChart,60000);
</script>
</body>
</html>
"""
