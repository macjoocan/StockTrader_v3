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

from dashboard_data import (CORE_CODE, diagnose_cards, enrich_positions,  # noqa: E402
                            factor_ranking, fin_rows, gate_status, indexed_pair,
                            ops_report, radar_rows, realized_trades, valuation_row)

KST = timezone(timedelta(hours=9))
DATA_DIR = Path(os.environ.get('DATA_DIR') or './data-volume')
# dataviz 검증 통과 팔레트 (dark surface #161e2e): 계좌=파랑, KODEX=주황
C_ACCT, C_KODEX = '#3f7fc9', '#bd8137'

app = Flask(__name__)
CACHE = None  # MarketCache — main에서만 기동 (테스트는 None)


def _load_names() -> dict:
    try:
        u = json.loads((Path(__file__).parent / 'data' / 'universe_2026.json')
                       .read_text(encoding='utf-8'))
        return u.get('names') or {}
    except (OSError, ValueError):
        return {}


NAMES = _load_names()


def nm(code: str) -> str:
    return NAMES.get(code) or code


def stk_link(code: str) -> str:
    return (f'<a class="stk" href="/stock/{html.escape(code)}">{html.escape(nm(code))}'
            f' <span class="muted small">{html.escape(code)}</span></a>')


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
    show_legend = len(series_map) >= 2  # 단일 시리즈는 제목이 정체를 말함 (범례 생략)
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
    legend_html = f'<div class="legendrow">{"".join(legend)}</div>' if show_legend else ''
    return f'''{legend_html}
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
        f"<tr><td>{stk_link(r['code'])}</td><td class='num'>{r['qty']}</td>"
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
        return (f"<tr><td>{stk_link(r['code'])}{pin}</td><td class='num'>{r['cur']:,.0f}</td>"
                f"<td class='num {cls_pnl(r['chg'])}'>{pct(r['chg'])}</td>"
                f"<td class='num'>{rsi_txt}{sig}</td><td>{sma_txt}</td></tr>")

    radar_html = ''.join(radar_row(r) for r in radar) or \
        '<tr><td colspan="5" class="empty">시장 데이터 수집 중 (기동 후 ~1분)</td></tr>'

    # -- 종목 진단 카드 (규칙 기반 서술) --
    uni_quotes_all = {c: q for c, q in (snap.get('quotes') or {}).items() if c != CORE_CODE}
    dcards = diagnose_cards(uni_quotes_all, snap.get('fin') or {},
                            {c: s for c, s in closes.items() if c != CORE_CODE},
                            holdings=set(state['positions']))

    def bar(label, v, suffix=''):
        if v is None:
            return f'<div class="brow"><span>{label}</span><i class="btrack"></i><b>—</b></div>'
        w = max(2, min(100, round(v)))
        return (f'<div class="brow"><span>{label}</span>'
                f'<i class="btrack"><i class="bfill" style="width:{w}%"></i></i>'
                f'<b>{v:.0f}{suffix}</b></div>')

    def dcard(c):
        badges = f'<span class="badge {c["grade_cls"]}">{c["grade"]}</span>'
        if c['signal']:
            badges += ' <span class="badge crit">신호권</span>'
        if c['holding']:
            badges += ' 📌'
        chg = c['chg_pct']
        chg_html = (f"<span class='{cls_pnl(chg)}'>{chg:+.2f}%</span>"
                    if chg is not None else '')
        tech = []
        if c['above200'] is not None:
            tech.append(f"SMA200 {'위' if c['above200'] else '아래'}")
        if c['rsi2'] is not None:
            tech.append(f"RSI2 {c['rsi2']:.0f}")
        if c['w52_band'] is not None:
            tech.append(f"52주 {c['w52_band']*100:.0f}%")
        return f'''<a class="dcard" href="/stock/{c['code']}">
<div class="dhead"><b>{html.escape(nm(c['code']))}</b> {badges}</div>
<div class="dprice">{won(c['cur'])} {chg_html} <span class="muted small">#{c['rank'] or '—'}</span></div>
{bar('가치', c['value'])}{bar('퀄리티', c['quality'])}{bar('외인', c['frgn_rate'], '%')}
<div class="dtech muted small">{' · '.join(tech) or '—'}</div>
<div class="dwhy small">{html.escape(' / '.join(c['reasons']))}</div></a>'''

    dcards_html = ''.join(dcard(c) for c in dcards) or \
        '<div class="empty">시장 데이터 수집 중</div>'

    # -- 팩터 랭킹 (Tier 2: 정보성) --
    uni_quotes = {c: q for c, q in (snap.get('quotes') or {}).items() if c != CORE_CODE}
    franks = factor_ranking(uni_quotes, snap.get('fin') or {})

    def fnum(v, fmt='{:.1f}'):
        return fmt.format(v) if v is not None else '—'

    def frow(r):
        pin = ' 📌' if r['code'] in state['positions'] else ''
        top = ' style="color:var(--good);font-weight:700"' if (r['rank'] or 99) <= 3 else ''
        return (f"<tr><td class='num'{top}>{r['rank'] or '—'}</td>"
                f"<td>{stk_link(r['code'])}{pin}</td>"
                f"<td class='num'>{fnum(r['per'])}</td><td class='num'>{fnum(r['pbr'], '{:.2f}')}</td>"
                f"<td class='num'>{fnum(r['roe'])}</td><td class='num'>{fnum(r['debt'], '{:.0f}')}</td>"
                f"<td class='num'>{r['value'] if r['value'] is not None else '—'}</td>"
                f"<td class='num'>{r['quality'] if r['quality'] is not None else '—'}</td>"
                f"<td class='num'><b>{r['total'] if r['total'] is not None else '—'}</b></td></tr>")

    factor_html = ''.join(frow(r) for r in franks) or \
        '<tr><td colspan="9" class="empty">시장 데이터 수집 중</td></tr>'

    # -- 실현손익 --
    trades = realized_trades(events)
    if trades:
        tr_sum = sum(t['pnl'] for t in trades)
        trades_html = ''.join(
            f"<tr><td>{stk_link(t['code'])}</td><td class='num'>{t['qty']}</td>"
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
{BASE_STYLE}{STYLE_EXTRA}
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
<div class="panel"><h2>종목 진단 카드 — 규칙 기반 서술
<span class="badge warn">참고용 · 추천 아님</span>
<span class="muted small">규칙: 부채≥200%→재무주의 | 종합≥70+추세위→저평가·우량 | 가치≤25→고평가 | SMA200 아래→추세약세</span></h2>
<div class="dgrid">{dcards_html}</div></div>
<div class="panel"><h2>팩터 랭킹 — 가치(PER·PBR)·퀄리티(ROE·부채비율) 순위점수
<span class="badge warn">참고용 · 백테 미검증 · 매매 미연결</span></h2>
<table><tr><th class="num">순위</th><th>종목 (📌보유)</th><th class="num">PER</th><th class="num">PBR</th>
<th class="num">ROE%</th><th class="num">부채비율%</th><th class="num">가치</th><th class="num">퀄리티</th>
<th class="num">종합</th></tr>{factor_html}</table></div>
<div class="panel"><h2>실현손익 (새틀라이트 청산 기록)</h2>
<table><tr><th>종목</th><th class="num">수량</th><th>기간</th><th class="num">매수→매도가</th>
<th class="num">손익</th></tr>{trades_html}</table></div>
<div class="panel"><h2>운영 리포트</h2>{ops_html}</div>
<div class="panel"><h2>최근 이벤트</h2>
<table><tr><th>시각</th><th>구분</th><th>내용</th></tr>{ev_html}</table></div>
<div id="tip" class="tip" hidden></div>
<script>{CHART_JS}</script>
</body></html>'''


BASE_STYLE = '''
:root { --bg:#0e1420; --card:#161e2e; --ink:#e6ebf4; --ink2:#9aa7bd; --muted:#5f6b81;
        --grid:#243048; --good:#3fb970; --warn:#d9a13b; --crit:#e0605e; --info:#5ba3f5; }
* { box-sizing:border-box; margin:0; }
body { background:var(--bg); color:var(--ink); font:14px/1.5 'Malgun Gothic',sans-serif; padding:20px; }
h1 { font-size:18px; margin-bottom:4px; } h2 { font-size:14px; color:var(--ink2); margin:0 0 10px; }
.sub { color:var(--muted); font-size:12px; margin-bottom:16px; }
.cards { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }
.card { background:var(--card); border-radius:10px; padding:12px 16px; min-width:135px; }
.card .k { color:var(--ink2); font-size:11px; } .card .v { font-size:18px; font-weight:700; margin-top:2px; }
.panel { background:var(--card); border-radius:10px; padding:16px; margin-bottom:16px; overflow-x:auto; }
table { border-collapse:collapse; width:100%; } th,td { padding:6px 10px; text-align:left; font-size:13px; }
th { color:var(--ink2); border-bottom:1px solid var(--grid); font-weight:600; }
td { border-bottom:1px solid #1d2739; } .num { text-align:right; font-variant-numeric:tabular-nums; }
.muted { color:var(--muted); } .detail { color:var(--ink2); font-size:12px; }
.empty { color:var(--muted); text-align:center; padding:14px; }
.pos { color:var(--good); } .neg { color:var(--crit); } .warn2 { color:var(--warn); }
.badge { padding:1px 8px; border-radius:8px; font-size:12px; }
.badge.good { background:#173527; color:var(--good); } .badge.crit { background:#3a1d1d; color:var(--crit); }
.badge.warn { background:#37301a; color:var(--warn); } .badge.info { background:#182c44; color:var(--info); }
.badge.muted { background:#222b3d; color:var(--ink2); }
svg.chart { width:100%; height:auto; display:block; }
.line { fill:none; stroke-width:2; } .grid { stroke:var(--grid); stroke-width:1; }
.axis { fill:var(--muted); font-size:10px; } .lastlabel { fill:var(--ink); font-size:11px; font-weight:700; }
.crosshair { stroke:var(--muted); stroke-dasharray:3 3; }
.legendrow { margin-bottom:6px; } .lg { margin-right:14px; font-size:12px; color:var(--ink2); }
.lg i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; }
.ops { color:var(--ink2); font-size:13px; }
.tip { position:fixed; background:#0b111c; border:1px solid var(--grid); border-radius:6px;
       padding:6px 10px; font-size:12px; pointer-events:none; z-index:9; white-space:pre; }
.two { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width:900px) { .two { grid-template-columns:1fr; } }
'''

STYLE_EXTRA = '''
.stk { color: var(--ink); text-decoration: none; border-bottom: 1px dotted var(--muted); }
.small { font-size: 11px; }
.back { color: var(--info); text-decoration: none; font-size: 13px; }
.dgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(225px, 1fr)); gap: 10px; }
.dcard { display: block; background: #121a29; border: 1px solid var(--grid); border-radius: 10px;
         padding: 12px 14px; color: var(--ink); text-decoration: none; }
.dcard:hover { border-color: var(--info); }
.dhead { margin-bottom: 4px; } .dprice { font-size: 15px; font-weight: 700; margin-bottom: 8px; }
.brow { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--ink2); margin: 3px 0; }
.brow span { width: 34px; } .brow b { width: 34px; text-align: right; font-variant-numeric: tabular-nums; }
.btrack { flex: 1; height: 6px; background: #1d2739; border-radius: 3px; display: block; overflow: hidden; }
.bfill { display: block; height: 100%; background: var(--info); border-radius: 3px; }
.dtech { margin-top: 6px; } .dwhy { color: var(--muted); margin-top: 3px; }
'''

CHART_JS = r'''
document.querySelectorAll('svg.chart').forEach(svg => {
  const cfg = JSON.parse(svg.dataset.chart), pad = +svg.dataset.pad;
  const vb = svg.viewBox.baseVal, tip = document.getElementById('tip');
  const xh = svg.querySelector('.xh'), days = cfg.days, nx = Math.max(days.length - 1, 1);
  svg.addEventListener('mousemove', ev => {
    const r = svg.getBoundingClientRect();
    const mx = (ev.clientX - r.left) * vb.width / r.width;
    const i = Math.max(0, Math.min(days.length - 1, Math.round((mx - pad) / ((vb.width - 2 * pad) / nx))));
    const x = pad + (vb.width - 2 * pad) * i / nx;
    xh.setAttribute('x1', x); xh.setAttribute('x2', x); xh.removeAttribute('visibility');
    let lines = [days[i]];
    for (const [name, byDay] of Object.entries(cfg.series)) {
      if (byDay[days[i]] !== undefined) lines.push(name + ': ' + byDay[days[i]].toLocaleString());
    }
    tip.hidden = false; tip.textContent = lines.join('\n');
    tip.style.left = (ev.clientX + 14) + 'px'; tip.style.top = (ev.clientY - 10) + 'px';
  });
  svg.addEventListener('mouseleave', () => { tip.hidden = true; xh.setAttribute('visibility', 'hidden'); });
});
'''


def render_stock(code: str, cache=None) -> str:
    """종목 상세: 밸류에이션 + 재무비율 + 기술 상태 + 60일 차트 (Tier 1 기업분석)"""
    cache = cache if cache is not None else CACHE
    snap = getattr(cache, 'snapshot', None) or {}
    name = nm(code)
    q = (snap.get('quotes') or {}).get(code)
    val = valuation_row(q) if q else None
    s = (snap.get('closes') or {}).get(code)
    state = load_positions(DATA_DIR)
    held = state['positions'].get(code)

    # 기술 상태
    tech = '—'
    if s is not None and len(s) >= 200:
        from signal_engine.indicators import rsi, sma
        r2 = float(rsi(s, 2).iloc[-1])
        above = float(s.iloc[-1]) > float(sma(s, 200).iloc[-1])
        sig = ' · <span class="badge crit">신호권</span>' if (above and r2 < 10) else ''
        tech = f"RSI2 {r2:.1f} · SMA200 {'위' if above else '아래'}{sig}"

    # 팩터 랭킹 내 위치 (Tier 2, 참고용)
    uni_q = {c: qq for c, qq in (snap.get('quotes') or {}).items() if c != CORE_CODE}
    franks = factor_ranking(uni_q, snap.get('fin') or {})
    mine = next((r for r in franks if r['code'] == code), None)
    n_ranked = sum(1 for r in franks if r['rank'])
    rank_txt = ''
    if mine and mine['rank']:
        rank_txt = (f" · 팩터랭킹 <b>{mine['rank']}/{n_ranked}위</b>"
                    f" (가치 {mine['value']} · 퀄리티 {mine['quality']})")

    def card(k, v, c=''):
        return f'<div class="card"><div class="k">{k}</div><div class="v {c}">{v}</div></div>'

    if val:
        band_txt = f"{val['w52_band']*100:.0f}%" if val['w52_band'] is not None else '—'
        cards = ''.join([
            card('현재가', won(val['cur']), ''),
            card('전일대비', pct(val['chg_pct'] / 100 if val['chg_pct'] is not None else None),
                 cls_pnl(val['chg_pct'])),
            card('PER', f"{val['per']:.1f}배" if val['per'] else '—'),
            card('PBR', f"{val['pbr']:.2f}배" if val['pbr'] else '—'),
            card('EPS', won(val['eps'])), card('BPS', won(val['bps'])),
            card('시가총액', f"{val['mcap_eok']:,.0f}억" if val['mcap_eok'] else '—'),
            card('52주 밴드 위치', band_txt),
            card('외국인 소진율', f"{val['frgn_rate']:.1f}%" if val['frgn_rate'] else '—'),
        ])
    else:
        cards = '<div class="empty">시세 캐시 수집 중 — 잠시 후 새로고침</div>'

    fin = (snap.get('fin') or {}).get(code)
    frows = fin_rows(fin) if fin else []
    if frows:
        heads = ''.join(f'<th class="num">{h}</th>' for h in frows[0])
        body = ''.join('<tr>' + ''.join(f'<td class="num">{v}</td>' for v in r.values())
                       + '</tr>' for r in frows)
        fin_html = f'<table><tr>{heads}</tr>{body}</table>'
    else:
        fin_html = ('<div class="empty">재무비율 데이터 없음 '
                    '(수집 전이거나 모의서버 미지원 — 실전 전환 시 확인)</div>')

    chart = svg_chart({name: [(f'{ts:%Y-%m-%d}', float(v)) for ts, v in s.iloc[-60:].items()]},
                      {name: C_ACCT}) if s is not None and len(s) else \
        '<div class="empty">차트 데이터 수집 중</div>'
    held_html = (f"<span class='badge good'>보유 {held['qty']}주 @{held['entry_price']:,.0f} "
                 f"({held['entry_date']}~)</span>" if held else '')

    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="300"><title>{html.escape(name)} — Stock Trader</title>
<style>{BASE_STYLE}{STYLE_EXTRA}</style></head><body>
<a class="back" href="/">← 대시보드</a>
<h1 style="margin-top:8px">{html.escape(name)} <span class="muted">{html.escape(code)}</span> {held_html}</h1>
<div class="sub">기술 상태: {tech}{rank_txt} · 데이터는 참고용 (투자 판단 아님)</div>
<div class="cards">{cards}</div>
<div class="panel"><h2>최근 60일 종가</h2>{chart}</div>
<div class="panel"><h2>재무비율 (연간, KIS 제공)</h2>{fin_html}</div>
<div id="tip" class="tip" hidden></div>
<script>{CHART_JS}</script>
</body></html>'''


@app.route('/')
def index():
    return render_page()


@app.route('/stock/<code>')
def stock_detail(code):
    if not (code.isdigit() and len(code) == 6):
        return '잘못된 종목코드', 404
    return render_stock(code)


if __name__ == '__main__':
    from config import load_config
    from dashboard_data import MarketCache
    cfg = load_config(dict(os.environ))
    universe = json.loads((Path(__file__).parent / 'data' / 'universe_2026.json')
                          .read_text(encoding='utf-8'))['codes']
    CACHE = MarketCache(cfg, universe)
    app.run(host='0.0.0.0', port=5030)
