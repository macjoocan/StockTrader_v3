# -*- coding: utf-8 -*-
"""Stock_trader 읽기전용 대시보드 (port 5030).

- 데이터원: DATA_DIR의 positions.json + events_*.jsonl (KIS API 호출 없음 — 봇과 완전 분리)
- 화면: 상태 카드 / 평가금액 추이(일일요약 시계열, 단일 시리즈 SVG 라인) / 보유 포지션 / 최근 이벤트
"""
import html
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

KST = timezone(timedelta(hours=9))
DATA_DIR = Path(os.environ.get('DATA_DIR') or './data-volume')

app = Flask(__name__)


# ---- 데이터 로딩 (순수 파트는 테스트 대상) ----

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
    """daily_summary 이벤트 -> [(날짜str, 총평가)] 날짜 오름차순, 같은 날은 마지막 값"""
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


def svg_line(series: list, w: int = 720, h: int = 200, pad: int = 36) -> str:
    """단일 시리즈 라인차트 SVG (2px 라인, 마지막 점 직접 라벨, 호버 툴팁용 데이터 포함)"""
    if len(series) < 2:
        return '<div class="empty">데이터 2일 이상 쌓이면 추이가 표시됩니다</div>'
    vals = [v for _, v in series]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(series)
    pts = []
    for i, (_, v) in enumerate(series):
        x = pad + (w - 2 * pad) * i / (n - 1)
        y = h - pad - (h - 2 * pad) * (v - lo) / span
        pts.append((round(x, 1), round(y, 1)))
    poly = ' '.join(f'{x},{y}' for x, y in pts)
    last_x, last_y = pts[-1]
    payload = html.escape(json.dumps(series, ensure_ascii=False), quote=True)
    grid_ys = [h - pad - (h - 2 * pad) * f for f in (0, 0.5, 1)]
    grid = ''.join(
        f'<line x1="{pad}" y1="{y:.1f}" x2="{w-pad}" y2="{y:.1f}" class="grid"/>'
        f'<text x="{pad-6}" y="{y:.1f}" class="axis" text-anchor="end" dominant-baseline="middle">'
        f'{lo + span * f:,.0f}</text>'
        for f, y in zip((0, 0.5, 1), grid_ys))
    return f'''<svg id="eq" viewBox="0 0 {w} {h}" data-series="{payload}"
     data-pad="{pad}" role="img" aria-label="평가금액 추이">
  {grid}
  <polyline points="{poly}" class="line"/>
  <circle cx="{last_x}" cy="{last_y}" r="4" class="dot"/>
  <text x="{last_x-8}" y="{last_y-10}" class="lastlabel" text-anchor="end">{vals[-1]:,.0f}원</text>
  <line id="xh" y1="{pad}" y2="{h-pad}" class="crosshair" visibility="hidden"/>
  <circle id="hp" r="5" class="dot" visibility="hidden"/>
</svg>
<div id="tip" class="tip" hidden></div>'''


KIND_BADGE = {
    'fill': ('체결', 'good'), 'signal': ('신호', 'info'), 'error': ('오류', 'crit'),
    'rebalance': ('리밸', 'info'), 'daily_summary': ('요약', 'muted'),
    'reconcile': ('대사', 'warn'),
}


def render_page() -> str:
    state = load_positions(DATA_DIR)
    events = load_events(DATA_DIR)
    eq = equity_series(events)
    last_summary = next((e for e in reversed(events) if e.get('kind') == 'daily_summary'), {})
    mode = os.environ.get('KIS_MODE') or 'paper'

    pos_rows = ''.join(
        f"<tr><td>{html.escape(c)}</td><td class='num'>{p['qty']}</td>"
        f"<td class='num'>{p['entry_price']:,.0f}</td><td>{html.escape(str(p['entry_date']))}</td></tr>"
        for c, p in sorted(state['positions'].items())) or \
        '<tr><td colspan="4" class="empty">보유 포지션 없음</td></tr>'

    ev_rows = []
    for e in list(reversed(events))[:60]:
        kind = e.get('kind', '?')
        label, cls = KIND_BADGE.get(kind, (kind, 'muted'))
        detail = {k: v for k, v in e.items() if k not in ('ts', 'kind')}
        ev_rows.append(
            f"<tr><td class='muted'>{html.escape(str(e.get('ts', ''))[:19])}</td>"
            f"<td><span class='badge {cls}'>{html.escape(label)}</span></td>"
            f"<td class='detail'>{html.escape(json.dumps(detail, ensure_ascii=False)[:160])}</td></tr>")
    ev_html = ''.join(ev_rows) or '<tr><td colspan="3" class="empty">이벤트 없음</td></tr>'

    total_txt = f"{last_summary.get('total', 0):,.0f}원" if last_summary else '—'
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60"><title>Stock Trader</title>
<style>
:root {{ --bg:#0e1420; --card:#161e2e; --ink:#e6ebf4; --ink2:#9aa7bd; --muted:#5f6b81;
        --line:#5ba3f5; --grid:#243048; --good:#3fb970; --warn:#d9a13b; --crit:#e0605e; --info:#5ba3f5; }}
* {{ box-sizing:border-box; margin:0; }}
body {{ background:var(--bg); color:var(--ink); font:14px/1.5 'Malgun Gothic',sans-serif; padding:20px; }}
h1 {{ font-size:18px; margin-bottom:4px; }} h2 {{ font-size:14px; color:var(--ink2); margin:0 0 10px; }}
.sub {{ color:var(--muted); font-size:12px; margin-bottom:16px; }}
.cards {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px; }}
.card {{ background:var(--card); border-radius:10px; padding:14px 18px; min-width:150px; }}
.card .k {{ color:var(--ink2); font-size:12px; }} .card .v {{ font-size:20px; font-weight:700; margin-top:2px; }}
.panel {{ background:var(--card); border-radius:10px; padding:16px; margin-bottom:16px; overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; }} th,td {{ padding:6px 10px; text-align:left; font-size:13px; }}
th {{ color:var(--ink2); border-bottom:1px solid var(--grid); font-weight:600; }}
td {{ border-bottom:1px solid #1d2739; }} .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.muted {{ color:var(--muted); }} .detail {{ color:var(--ink2); font-size:12px; }}
.empty {{ color:var(--muted); text-align:center; padding:14px; }}
.badge {{ padding:1px 8px; border-radius:8px; font-size:12px; }}
.badge.good {{ background:#173527; color:var(--good); }} .badge.crit {{ background:#3a1d1d; color:var(--crit); }}
.badge.warn {{ background:#37301a; color:var(--warn); }} .badge.info {{ background:#182c44; color:var(--info); }}
.badge.muted {{ background:#222b3d; color:var(--ink2); }}
svg {{ width:100%; height:auto; display:block; }}
.line {{ fill:none; stroke:var(--line); stroke-width:2; }} .dot {{ fill:var(--line); }}
.grid {{ stroke:var(--grid); stroke-width:1; }} .axis {{ fill:var(--muted); font-size:10px; }}
.lastlabel {{ fill:var(--ink); font-size:11px; font-weight:700; }}
.crosshair {{ stroke:var(--muted); stroke-dasharray:3 3; }}
.tip {{ position:fixed; background:#0b111c; border:1px solid var(--grid); border-radius:6px;
       padding:6px 10px; font-size:12px; pointer-events:none; z-index:9; }}
</style></head><body>
<h1>📈 Stock Trader <span class="badge {'warn' if mode == 'paper' else 'crit'}">{'모의투자' if mode == 'paper' else '실전'}</span></h1>
<div class="sub">코어 70% KODEX200 + 새틀 30% RSI2 · 갱신 {datetime.now(KST):%m-%d %H:%M} (60초 자동새로고침)</div>
<div class="cards">
  <div class="card"><div class="k">평가금액 (최근 요약)</div><div class="v">{total_txt}</div></div>
  <div class="card"><div class="k">보유 포지션</div><div class="v">{len(state['positions'])} / 4 슬롯</div></div>
  <div class="card"><div class="k">최근 거래일</div><div class="v">{state.get('last_trade_date') or '—'}</div></div>
  <div class="card"><div class="k">최근 리밸런싱</div><div class="v">{state.get('last_rebal_ym') or '—'}</div></div>
</div>
<div class="panel"><h2>평가금액 추이</h2>{svg_line(eq)}</div>
<div class="panel"><h2>보유 포지션 (새틀라이트)</h2>
<table><tr><th>종목</th><th class="num">수량</th><th class="num">진입가</th><th>진입일</th></tr>{pos_rows}</table></div>
<div class="panel"><h2>최근 이벤트</h2>
<table><tr><th>시각</th><th>구분</th><th>내용</th></tr>{ev_html}</table></div>
<script>
const svg = document.getElementById('eq');
if (svg) {{
  const series = JSON.parse(svg.dataset.series), pad = +svg.dataset.pad;
  const vb = svg.viewBox.baseVal, tip = document.getElementById('tip');
  const xh = document.getElementById('xh'), hp = document.getElementById('hp');
  const vals = series.map(d => d[1]), lo = Math.min(...vals), hi = Math.max(...vals);
  const span = (hi - lo) || 1;
  svg.addEventListener('mousemove', ev => {{
    const r = svg.getBoundingClientRect();
    const mx = (ev.clientX - r.left) * vb.width / r.width;
    const i = Math.max(0, Math.min(series.length - 1,
      Math.round((mx - pad) / ((vb.width - 2 * pad) / (series.length - 1)))));
    const x = pad + (vb.width - 2 * pad) * i / (series.length - 1);
    const y = vb.height - pad - (vb.height - 2 * pad) * (series[i][1] - lo) / span;
    xh.setAttribute('x1', x); xh.setAttribute('x2', x); xh.removeAttribute('visibility');
    hp.setAttribute('cx', x); hp.setAttribute('cy', y); hp.removeAttribute('visibility');
    tip.hidden = false; tip.textContent = series[i][0] + ' · ' + series[i][1].toLocaleString() + '원';
    tip.style.left = (ev.clientX + 14) + 'px'; tip.style.top = (ev.clientY - 10) + 'px';
  }});
  svg.addEventListener('mouseleave', () => {{
    tip.hidden = true; xh.setAttribute('visibility', 'hidden'); hp.setAttribute('visibility', 'hidden');
  }});
}}
</script>
</body></html>'''


@app.route('/')
def index():
    return render_page()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5030)
