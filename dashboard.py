# -*- coding: utf-8 -*-
"""Stock_trader 대시보드 v2 (port 5030).

- 이벤트/포지션: DATA_DIR 파일 (실시간)
- 시장/잔고: MarketCache (10분 캐시, 봇 유량 보호 — dashboard_data.py 참조)
- 패널: 상태카드 / 계좌vs KODEX 벤치마크 / 보유 포지션+ / 시그널 레이더 /
        실현손익 / 운영 리포트 / 이벤트 로그
"""
import html
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Flask

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent))

from dashboard_data import (CORE_CODE, enrich_positions, gate_status,  # noqa: E402
                            indexed_pair, ops_report, radar_rows, realized_trades)

KST = timezone(timedelta(hours=9))
DATA_DIR = Path(os.environ.get('DATA_DIR') or './data-volume')
# dataviz 검증 통과 팔레트 (dark surface #161e2e): 계좌=파랑, KODEX=주황
C_ACCT, C_KODEX = '#3f7fc9', '#bd8137'

app = Flask(__name__)
CACHE = None  # MarketCache — main에서만 기동 (테스트는 None)


# ---- 파일 데이터 (기존) ----

def load_events(data_dir: Path, limit_files: int = 60) -> list:
    events = []
    for p in sorted(Path(data_dir).glob('events_*.jsonl'))[-limit_files:]:
        try:
            lines = p.read_text(encoding='utf-8').splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    return events


def equity_series(events: list) -> list:
    by_day = {}
    for e in events:
        if e.get('kind') == 'daily_summary' and e.get('total'):
            by_day[str(e.get('ts', ''))[:10]] = float(e['total'])
    return sorted(by_day.items())


def load_positions(data_dir: Path) -> dict:
    try:
        state = json.loads((Path(data_dir) / 'positions.json').read_text(encoding='utf-8'))
        if isinstance(state, dict) and isinstance(state.get('positions'), dict):
            return state
    except (OSError, ValueError):
        pass
    return {'positions': {}, 'last_trade_date': None, 'last_rebal_ym': None}


# ---- SVG 차트 (멀티 시리즈 + 범례 + 호버) ----

def svg_chart(series_map: dict, colors: dict, w: int = 760, h: int = 230, pad: int = 42) -> str:
    """series_map: {라벨: [(날짜str, 값)]}. 공통 날짜축(합집합), 값축 공통 스케일."""
    series_map = {k: v for k, v in series_map.items() if v}
    if not series_map or all(len(v) < 2 for v in series_map.values()):
        return '<div class="empty">데이터 2일 이상 쌓이면 표시됩니다</div>'
    days = sorted({d for pts in series_map.values() for d, _ in pts})
    vals = [v for pts in series_map.values() for _, v in pts]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    nx = max(len(days) - 1, 1)

    def xy(d, v):
        x = pad + (w - 2 * pad) * days.index(d) / nx
        y = h - pad - (h - 2 * pad) * (v - lo) / span
        return round(x, 1), round(y, 1)

    body, legend = [], []
    for i, (label, pts) in enumerate(series_map.items()):
        color = colors.get(label, '#888')
        coords = [xy(d, v) for d, v in pts]
        poly = ' '.join(f'{x},{y}' for x, y in coords)
        lx, ly = coords[-1]
        body.append(f'<polyline points="{poly}" class="line" style="stroke:{color}"/>')
        body.append(f'<circle cx="{lx}" cy="{ly}" r="3.5" style="fill:{color}"/>')
        body.append(f'<text x="{lx-8}" y="{ly-9}" class="lastlabel" text-anchor="end">'
                    f'{pts[-1][1]:,.1f}</text>')
        legend.append(f'<span class="lg"><i style="background:{color}"></i>{html.escape(label)}</span>')
    grid = ''.join(
        f'<line x1="{pad}" y1="{h-pad-(h-2*pad)*f:.1f}" x2="{w-pad}" y2="{h-pad-(h-2*pad)*f:.1f}" class="grid"/>'
        f'<text x="{pad-6}" y="{h-pad-(h-2*pad)*f:.1f}" class="axis" text-anchor="end" '
        f'dominant-baseline="middle">{lo+span*f:,.1f}</text>'
        for f in (0, 0.5, 1))
    payload = html.escape(json.dumps({'days': days, 'series': {
        k: dict(v) for k, v in series_map.items()}}, ensure_ascii=False), quote=True)
    return f'''<div class="legendrow">{''.join(legend)}</div>
<svg class="chart" viewBox="0 0 {w} {h}" data-chart="{payload}" data-pad="{pad}"
     data-lo="{lo}" data-hi="{hi}" role="img" aria-label="추이 차트">
  {grid}{''.join(body)}
  <line class="crosshair xh" y1="{pad}" y2="{h-pad}" visibility="hidden"/>
</svg>'''


# ---- 포맷 헬퍼 ----

def won(v):
    return f'{v:,.0f}원' if v is not None else '—'


def pct(v, digits=2):
    return f'{v*100:+.{digits}f}%' if v is not None else '—'


def cls_pnl(v):
    return 'pos' if (v or 0) > 0 else ('neg' if (v or 0) < 0 else '')


# ---- 페이지 ----

def render_page(cache=None) -> str:
    cache = cache if cache is not None else CACHE
    snap = getattr(cache, 'snapshot', None) or {}
    cache_status = getattr(cache, 'status', '시장 캐시 미기동')
    closes = snap.get('closes') or {}
    state = load_positions(DATA_DIR)
    events = load_events(DATA_DIR)
    now = datetime.now(KST)
    mode = os.environ.get('KIS_MODE') or 'paper'

    # -- 벤치마크 지수화 --
    eq = equity_series(events)
    live = (f'{now:%Y-%m-%d}', snap['total']) if snap.get('total') else None
    idx = indexed_pair(eq, closes.get(CORE_CODE), live_point=live)
    acct_ret = (idx['acct'][-1][1] / 100 - 1) if idx['acct'] else None
    kodex_ret = (idx['kodex'][-1][1] / 100 - 1) if idx['kodex'] else None

    # -- 카드 --
    total = snap.get('total') or (eq[-1][1] if eq else None)
    core_w = None
    if snap.get('total') and snap.get('holdings', {}).get(CORE_CODE):
        q, p = snap['holdings'][CORE_CODE]
        core_w = q * p / snap['total']
    g = gate_status(now.date())
    rep = ops_report(events)
    cards = [
        ('평가금액', won(total), ''),
        ('개시 후 수익률', pct(acct_ret), cls_pnl(acct_ret)),
        ('KODEX 동기간', pct(kodex_ret), cls_pnl(kodex_ret)),
        ('코어 비중 (목표 70±5%)', f'{core_w*100:.1f}%' if core_w else '—',
         '' if core_w and abs(core_w - 0.70) <= 0.05 else 'warn2'),
        ('보유 슬롯', f"{len(state['positions'])} / 4", ''),
        ('모의 게이트', ('종료' if g['over'] else f"D-{g['d_left']}") + ' (~9/14)', ''),
        ('사이클 성공/스킵', f"{rep['summary_days']} / {rep['skip_days']}",
         'warn2' if rep['skip_days'] else ''),
    ]
    cards_html = ''.join(
        f'<div class="card"><div class="k">{k}</div><div class="v {c}">{v}</div></div>'
        for k, v, c in cards)

    # -- 포지션 --
    pos_rows = enrich_positions(state['positions'], closes, today=now.date())
    pos_html = ''.join(
        f"<tr><td>{r['code']}</td><td class='num'>{r['qty']}</td>"
        f"<td class='num'>{r['entry']:,.0f}</td><td class='num'>{won(r['cur'])}</td>"
        f"<td class='num {cls_pnl(r['pnl'])}'>{won(r['pnl'])}</td>"
        f"<td class='num {cls_pnl(r['pnl_pct'])}'>{pct(r['pnl_pct'])}</td>"
        f"<td class='num'>{r['days'] if r['days'] is not None else '—'}일</td>"
        f"<td class='num'>{pct(r['sma5_dist'])} {'🔔' if (r['sma5_dist'] or -1) > 0 else ''}</td></tr>"
        for r in pos_rows) or '<tr><td colspan="8" class="empty">보유 포지션 없음</td></tr>'

    # -- 레이더 --
    uni_closes = {c: s for c, s in closes.items() if c != CORE_CODE}
    radar = radar_rows(uni_closes, holdings=set(state['positions']))
    def radar_row(r):
        rsi_txt = f"{r['rsi2']:.1f}" if r['rsi2'] is not None else '—'
        sig = ' <span class="badge crit">신호권</span>' if r['signal'] else ''
        sma_txt = '위' if r['above_sma200'] else ('아래' if r['above_sma200'] is not None else '—')
        pin = ' 📌' if r['holding'] else ''
        return (f"<tr><td>{r['code']}{pin}</td><td class='num'>{r['cur']:,.0f}</td>"
                f"<td class='num {cls_pnl(r['chg'])}'>{pct(r['chg'])}</td>"
                f"<td class='num'>{rsi_txt}{sig}</td><td>{sma_txt}</td></tr>")

    radar_html = ''.join(radar_row(r) for r in radar) or \
        '<tr><td colspan="5" class="empty">시장 데이터 수집 중 (기동 후 ~1분)</td></tr>'

    # -- 실현손익 --
    trades = realized_trades(events)
    if trades:
        tr_sum = sum(t['pnl'] for t in trades)
        trades_html = ''.join(
            f"<tr><td>{t['code']}</td><td class='num'>{t['qty']}</td>"
            f"<td>{t['buy_day']} → {t['sell_day']}</td>"
            f"<td class='num'>{t['buy']:,.0f} → {t['sell']:,.0f}</td>"
            f"<td class='num {cls_pnl(t['pnl'])}'>{won(t['pnl'])} ({pct(t['pnl_pct'])})</td></tr>"
            for t in trades)
        trades_html += (f"<tr><th colspan='4'>합계 ({len(trades)}건)</th>"
                        f"<th class='num {cls_pnl(tr_sum)}'>{won(tr_sum)}</th></tr>")
    else:
        trades_html = '<tr><td colspan="5" class="empty">아직 매도 없음 — 첫 청산 발생 시 표시</td></tr>'

    # -- 운영 리포트 --
    ops_html = (
        f"<div class='ops'>운영 {rep['first_day'] or '—'} ~ {rep['last_day'] or '—'} · "
        f"요약 {rep['summary_days']}일 · 스킵 {rep['skip_days']}일 · "
        f"신호 {rep['signals']}건 · 체결 {rep['fills_ok']}건 (실패 {rep['fills_fail']}) · "
        f"에러 {rep['errors']}건"
        + (f"<br>최근 에러: <span class='neg'>{html.escape(rep['last_error'])}</span>"
           if rep['last_error'] else '') + '</div>')

    # -- 이벤트 --
    kind_badge = {'fill': ('체결', 'good'), 'signal': ('신호', 'info'), 'error': ('오류', 'crit'),
                  'rebalance': ('리밸', 'info'), 'daily_summary': ('요약', 'muted'),
                  'reconcile': ('대사', 'warn')}
    ev_rows = []
    for e in list(reversed(events))[:40]:
        label, cls = kind_badge.get(e.get('kind', '?'), (e.get('kind', '?'), 'muted'))
        detail = {k: v for k, v in e.items() if k not in ('ts', 'kind')}
        ev_rows.append(
            f"<tr><td class='muted'>{html.escape(str(e.get('ts', ''))[:19])}</td>"
            f"<td><span class='badge {cls}'>{html.escape(label)}</span></td>"
            f"<td class='detail'>{html.escape(json.dumps(detail, ensure_ascii=False)[:150])}</td></tr>")
    ev_html = ''.join(ev_rows) or '<tr><td colspan="3" class="empty">이벤트 없음</td></tr>'

    chart = svg_chart({'계좌': idx['acct'], 'KODEX 200': idx['kodex']},
                      {'계좌': C_ACCT, 'KODEX 200': C_KODEX})

    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="120"><title>Stock Trader</title>
<style>
:root {{ --bg:#0e1420; --card:#161e2e; --ink:#e6ebf4; --ink2:#9aa7bd; --muted:#5f6b81;
        --grid:#243048; --good:#3fb970; --warn:#d9a13b; --crit:#e0605e; --info:#5ba3f5; }}
* {{ box-sizing:border-box; margin:0; }}
body {{ background:var(--bg); color:var(--ink); font:14px/1.5 'Malgun Gothic',sans-serif; padding:20px; }}
h1 {{ font-size:18px; margin-bottom:4px; }} h2 {{ font-size:14px; color:var(--ink2); margin:0 0 10px; }}
.sub {{ color:var(--muted); font-size:12px; margin-bottom:16px; }}
.cards {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }}
.card {{ background:var(--card); border-radius:10px; padding:12px 16px; min-width:135px; }}
.card .k {{ color:var(--ink2); font-size:11px; }} .card .v {{ font-size:18px; font-weight:700; margin-top:2px; }}
.panel {{ background:var(--card); border-radius:10px; padding:16px; margin-bottom:16px; overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; }} th,td {{ padding:6px 10px; text-align:left; font-size:13px; }}
th {{ color:var(--ink2); border-bottom:1px solid var(--grid); font-weight:600; }}
td {{ border-bottom:1px solid #1d2739; }} .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.muted {{ color:var(--muted); }} .detail {{ color:var(--ink2); font-size:12px; }}
.empty {{ color:var(--muted); text-align:center; padding:14px; }}
.pos {{ color:var(--good); }} .neg {{ color:var(--crit); }} .warn2 {{ color:var(--warn); }}
.badge {{ padding:1px 8px; border-radius:8px; font-size:12px; }}
.badge.good {{ background:#173527; color:var(--good); }} .badge.crit {{ background:#3a1d1d; color:var(--crit); }}
.badge.warn {{ background:#37301a; color:var(--warn); }} .badge.info {{ background:#182c44; color:var(--info); }}
.badge.muted {{ background:#222b3d; color:var(--ink2); }}
svg.chart {{ width:100%; height:auto; display:block; }}
.line {{ fill:none; stroke-width:2; }} .grid {{ stroke:var(--grid); stroke-width:1; }}
.axis {{ fill:var(--muted); font-size:10px; }} .lastlabel {{ fill:var(--ink); font-size:11px; font-weight:700; }}
.crosshair {{ stroke:var(--muted); stroke-dasharray:3 3; }}
.legendrow {{ margin-bottom:6px; }} .lg {{ margin-right:14px; font-size:12px; color:var(--ink2); }}
.lg i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; }}
.ops {{ color:var(--ink2); font-size:13px; }}
.tip {{ position:fixed; background:#0b111c; border:1px solid var(--grid); border-radius:6px;
       padding:6px 10px; font-size:12px; pointer-events:none; z-index:9; white-space:pre; }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width:900px) {{ .two {{ grid-template-columns:1fr; }} }}
</style></head><body>
<h1>📈 Stock Trader <span class="badge {'warn' if mode == 'paper' else 'crit'}">{'모의투자' if mode == 'paper' else '실전'}</span></h1>
<div class="sub">코어 70% KODEX200 + 새틀 30% RSI2 딥바잉 · 페이지 {now:%m-%d %H:%M} ·
시장캐시: {html.escape(str(cache_status))}</div>
<div class="cards">{cards_html}</div>
<div class="panel"><h2>계좌 vs KODEX 200 (개시 = 100)</h2>{chart}</div>
<div class="two">
<div class="panel"><h2>보유 포지션 (🔔 = 종가가 SMA5 위 → 다음 사이클 청산 예정)</h2>
<table><tr><th>종목</th><th class="num">수량</th><th class="num">진입가</th><th class="num">현재가</th>
<th class="num">평가손익</th><th class="num">수익률</th><th class="num">보유</th><th class="num">SMA5 대비</th></tr>
{pos_html}</table></div>
<div class="panel"><h2>시그널 레이더 — RSI2 낮은 순 (진입: RSI2&lt;10 &amp; SMA200 위)</h2>
<table><tr><th>종목 (📌보유)</th><th class="num">현재가</th><th class="num">등락</th>
<th class="num">RSI2</th><th>SMA200</th></tr>{radar_html}</table></div>
</div>
<div class="panel"><h2>실현손익 (새틀라이트 청산 기록)</h2>
<table><tr><th>종목</th><th class="num">수량</th><th>기간</th><th class="num">매수→매도가</th>
<th class="num">손익</th></tr>{trades_html}</table></div>
<div class="panel"><h2>운영 리포트</h2>{ops_html}</div>
<div class="panel"><h2>최근 이벤트</h2>
<table><tr><th>시각</th><th>구분</th><th>내용</th></tr>{ev_html}</table></div>
<div id="tip" class="tip" hidden></div>
<script>
document.querySelectorAll('svg.chart').forEach(svg => {{
  const cfg = JSON.parse(svg.dataset.chart), pad = +svg.dataset.pad;
  const lo = +svg.dataset.lo, hi = +svg.dataset.hi, span = (hi - lo) || 1;
  const vb = svg.viewBox.baseVal, tip = document.getElementById('tip');
  const xh = svg.querySelector('.xh'), days = cfg.days, nx = Math.max(days.length - 1, 1);
  svg.addEventListener('mousemove', ev => {{
    const r = svg.getBoundingClientRect();
    const mx = (ev.clientX - r.left) * vb.width / r.width;
    const i = Math.max(0, Math.min(days.length - 1, Math.round((mx - pad) / ((vb.width - 2 * pad) / nx))));
    const x = pad + (vb.width - 2 * pad) * i / nx;
    xh.setAttribute('x1', x); xh.setAttribute('x2', x); xh.removeAttribute('visibility');
    let lines = [days[i]];
    for (const [name, byDay] of Object.entries(cfg.series)) {{
      if (byDay[days[i]] !== undefined) lines.push(name + ': ' + byDay[days[i]].toFixed(2));
    }}
    tip.hidden = false; tip.textContent = lines.join('\\n');
    tip.style.left = (ev.clientX + 14) + 'px'; tip.style.top = (ev.clientY - 10) + 'px';
  }});
  svg.addEventListener('mouseleave', () => {{ tip.hidden = true; xh.setAttribute('visibility', 'hidden'); }});
}});
</script>
</body></html>'''


@app.route('/')
def index():
    return render_page()


if __name__ == '__main__':
    from config import load_config
    from dashboard_data import MarketCache
    cfg = load_config(dict(os.environ))
    universe = json.loads((Path(__file__).parent / 'data' / 'universe_2026.json')
                          .read_text(encoding='utf-8'))['codes']
    CACHE = MarketCache(cfg, universe)
    app.run(host='0.0.0.0', port=5030)
