# -*- coding: utf-8 -*-
"""대시보드 데이터 레이어: 순수 분석함수(테스트 대상) + 시장 캐시.

시장 캐시 안전규칙 (봇 우선):
- 토큰은 봇이 heartbeat로 관리하는 파일캐시만 읽음 — 만료 임박이면 수집 스킵 (절대 발급 안 함)
- 15:15~15:35 KST(봇 일일작업 창)에는 수집 중단 (VTS 유량 2건/초 공유)
- 10분 TTL, 실패 시 이전 스냅샷 유지
"""
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from signal_engine.indicators import rsi, sma

KST = timezone(timedelta(hours=9))
CORE_CODE = '069500'
GATE_END = date(2026, 9, 14)  # 모의 4주 게이트 종료 목표
RSI_BUY, SMA_LONG, SMA_EXIT = 10.0, 200, 5


# ---- 순수 분석 (테스트 대상) ----

def enrich_positions(positions: dict, closes_map: dict, today: date | None = None) -> list:
    """보유 포지션 + 현재가/평손/수익률/보유일/청산선(SMA5) 거리"""
    rows = []
    for code, p in sorted(positions.items()):
        row = {'code': code, 'qty': p['qty'], 'entry': float(p['entry_price']),
               'entry_date': str(p['entry_date']), 'cur': None, 'pnl': None,
               'pnl_pct': None, 'sma5_dist': None, 'days': None}
        s = closes_map.get(code)
        if s is not None and len(s) >= 1:
            cur = float(s.iloc[-1])
            row['cur'] = cur
            if row['entry'] > 0:
                row['pnl'] = (cur - row['entry']) * row['qty']
                row['pnl_pct'] = cur / row['entry'] - 1.0
            if len(s) >= SMA_EXIT:
                s5 = float(sma(s, SMA_EXIT).iloc[-1])
                row['sma5_dist'] = cur / s5 - 1.0 if s5 else None  # 양수 = 청산조건 충족권
        if today is not None:
            try:
                row['days'] = (today - date.fromisoformat(row['entry_date'])).days
            except ValueError:
                pass
        rows.append(row)
    return rows


def radar_rows(closes_map: dict, holdings: set) -> list:
    """유니버스 시그널 레이더: RSI2 오름차순. signal = 진입조건(>SMA200 & RSI2<10) 충족."""
    rows = []
    for code, s in closes_map.items():
        if s is None or len(s) < 2:
            continue
        cur, prev = float(s.iloc[-1]), float(s.iloc[-2])
        row = {'code': code, 'cur': cur, 'chg': cur / prev - 1.0 if prev else 0.0,
               'rsi2': None, 'above_sma200': None, 'signal': False,
               'holding': code in holdings}
        r = rsi(s, 2).iloc[-1]
        if pd.notna(r):
            row['rsi2'] = float(r)
        if len(s) >= SMA_LONG:
            row['above_sma200'] = cur > float(sma(s, SMA_LONG).iloc[-1])
            row['signal'] = bool(row['above_sma200'] and row['rsi2'] is not None
                                 and row['rsi2'] < RSI_BUY)
        rows.append(row)
    rows.sort(key=lambda x: (x['rsi2'] is None, x['rsi2']))
    return rows


def indexed_pair(eq_points: list, kodex: pd.Series, live_point: tuple | None = None) -> dict:
    """계좌 vs KODEX 지수화(개시=100). eq_points=[(날짜str, total)], live_point=(날짜str, total).
    같은 날짜는 마지막 값. KODEX는 계좌 시계열의 날짜 구간으로 필터."""
    by_day = dict(eq_points)
    if live_point:
        by_day[live_point[0]] = live_point[1]
    days = sorted(by_day)
    if not days or by_day[days[0]] <= 0:
        return {'acct': [], 'kodex': []}
    base = by_day[days[0]]
    acct = [(d, by_day[d] / base * 100.0) for d in days]
    kx = []
    if kodex is not None and len(kodex):
        k = kodex[(kodex.index >= days[0]) & (kodex.index <= days[-1])]
        if len(k) and float(k.iloc[0]) > 0:
            kb = float(k.iloc[0])
            kx = [(f'{ts:%Y-%m-%d}', float(v) / kb * 100.0) for ts, v in k.items()]
    return {'acct': acct, 'kodex': kx}


def ops_report(events: list) -> dict:
    """이벤트 로그 -> 운영 통계"""
    r = {'summary_days': 0, 'skip_days': 0, 'signals': 0,
         'fills_ok': 0, 'fills_fail': 0, 'errors': 0, 'last_error': None,
         'first_day': None, 'last_day': None}
    for e in events:
        k, day = e.get('kind'), str(e.get('ts', ''))[:10]
        if day:
            r['first_day'] = min(r['first_day'] or day, day)
            r['last_day'] = max(r['last_day'] or day, day)
        if k == 'daily_summary':
            r['summary_days'] += 1
            r['signals'] += int(e.get('signals') or 0)
        elif k == 'fill':
            r['fills_ok' if e.get('ok') else 'fills_fail'] += 1
        elif k == 'error':
            r['errors'] += 1
            r['last_error'] = f"{day} {str(e.get('msg', ''))[:80]}"
            if '3회 실패' in str(e.get('msg', '')):
                r['skip_days'] += 1
    return r


def realized_trades(events: list) -> list:
    """fill 이벤트에서 BUY→SELL FIFO 매칭 실현손익 (코어 제외)"""
    open_lots, closed = {}, []
    for e in events:
        if e.get('kind') != 'fill' or not e.get('ok') or e.get('code') == CORE_CODE:
            continue
        code, qty, px = e['code'], int(e['qty']), float(e['price'])
        day = str(e.get('ts', ''))[:10]
        if e['side'] == 'BUY':
            open_lots.setdefault(code, []).append({'qty': qty, 'px': px, 'day': day})
        else:  # SELL
            remain = qty
            lots = open_lots.get(code, [])
            while remain > 0 and lots:
                lot = lots[0]
                take = min(remain, lot['qty'])
                if lot['px'] > 0:
                    closed.append({'code': code, 'qty': take, 'buy': lot['px'],
                                   'sell': px, 'buy_day': lot['day'], 'sell_day': day,
                                   'pnl': (px - lot['px']) * take,
                                   'pnl_pct': px / lot['px'] - 1.0})
                lot['qty'] -= take
                remain -= take
                if lot['qty'] == 0:
                    lots.pop(0)
    return closed


def _f(v):
    """KIS 문자열 숫자 -> float | None (빈값/'0'/파싱불가 방어)"""
    try:
        x = float(str(v).replace(',', ''))
        return x if x != 0 else None
    except (TypeError, ValueError):
        return None


def valuation_row(quote: dict) -> dict:
    """inquire_price full output -> 밸류에이션 스냅샷 (필드 없거나 0이면 None)"""
    q = quote or {}
    cur, hi, lo = _f(q.get('stck_prpr')), _f(q.get('w52_hgpr')), _f(q.get('w52_lwpr'))
    band = None
    if cur and hi and lo and hi > lo:
        band = (cur - lo) / (hi - lo)  # 52주 밴드 내 위치 0~1
    return {
        'cur': cur, 'chg_pct': _f(q.get('prdy_ctrt')),
        'per': _f(q.get('per')), 'pbr': _f(q.get('pbr')),
        'eps': _f(q.get('eps')), 'bps': _f(q.get('bps')),
        'mcap_eok': _f(q.get('hts_avls')),          # 시가총액(억원)
        'w52_hi': hi, 'w52_lo': lo, 'w52_band': band,
        'frgn_rate': _f(q.get('hts_frgn_ehrt')),    # 외국인 소진율(%)
        'turnover': _f(q.get('vol_tnrt')),          # 거래회전율(%)
    }


FIN_FIELDS = [('stac_yymm', '결산'), ('roe_val', 'ROE%'), ('lblt_rate', '부채비율%'),
              ('rsrv_rate', '유보율%'), ('grs', '매출증가율%'),
              ('bsop_prfi_inrt', '영업이익증가율%'), ('eps', 'EPS'), ('bps', 'BPS')]


def fin_rows(fin_list: list, limit: int = 4) -> list:
    """financial-ratio output -> 최근 결산기 rows [{결산, ROE%, ...}] (문자값 그대로, 없으면 '—')"""
    rows = []
    for r in (fin_list or [])[:limit]:
        rows.append({label: (str(r.get(key)) if r.get(key) not in (None, '') else '—')
                     for key, label in FIN_FIELDS})
    return rows


# ---- Tier 2: 팩터 랭킹 (정보성 — 매매 미연결, 백테 미검증) ----

FACTOR_METRICS = [  # (키, 낮을수록좋음, 축)
    ('per', True, 'value'), ('pbr', True, 'value'),
    ('roe', False, 'quality'), ('debt', True, 'quality'),
]


def factor_ranking(quotes: dict, fin: dict) -> list:
    """유니버스 가치(PER/PBR)·퀄리티(ROE/부채비율) 순위 백분위 점수 (0~100, 높을수록 우위).
    적자 PER(<=0)·비정상 PBR(<=0)은 결측 처리. 지표별 유효표본 3개 미만이면 그 지표 제외.
    ROE는 음수 허용(낮은 점수로 랭크)."""
    rows = []
    for code, q in (quotes or {}).items():
        v = valuation_row(q)
        latest = ((fin or {}).get(code) or [{}])[0]
        per = v['per'] if (v['per'] or 0) > 0 else None
        pbr = v['pbr'] if (v['pbr'] or 0) > 0 else None
        debt = _f(latest.get('lblt_rate'))
        debt = debt if (debt or 0) > 0 else None
        rows.append({'code': code, 'per': per, 'pbr': pbr,
                     'roe': _f(latest.get('roe_val')), 'debt': debt, '_s': {}})
    for key, lower_better, _axis in FACTOR_METRICS:
        present = sorted([r for r in rows if r[key] is not None], key=lambda r: r[key])
        n = len(present)
        if n < 3:
            continue
        for i, r in enumerate(present):
            s = 1 - i / (n - 1) if lower_better else i / (n - 1)
            r['_s'][key] = s
    for r in rows:
        axis_scores = {'value': [], 'quality': []}
        for key, _lb, axis in FACTOR_METRICS:
            if key in r['_s']:
                axis_scores[axis].append(r['_s'][key])
        r['value'] = round(100 * sum(axis_scores['value']) / len(axis_scores['value'])) \
            if axis_scores['value'] else None
        r['quality'] = round(100 * sum(axis_scores['quality']) / len(axis_scores['quality'])) \
            if axis_scores['quality'] else None
        both = [x for x in (r['value'], r['quality']) if x is not None]
        r['total'] = round(sum(both) / len(both)) if both else None
        del r['_s']
    rows.sort(key=lambda r: (r['total'] is None, -(r['total'] or 0)))
    for i, r in enumerate(rows):
        r['rank'] = i + 1 if r['total'] is not None else None
    return rows


# ---- 캔들차트 페이로드 (상세 페이지 — 서버가 지표 계산, JS가 렌더) ----

def _nan_list(s, digits=1):
    return [None if pd.isna(x) else round(float(x), digits) for x in s]


def chart_payload(ohlcv: pd.DataFrame, news: list = None, dart: list = None) -> dict:
    """OHLCV + 지표(SMA5/20/200, BB20, RSI2) + 이벤트(뉴스/공시) -> JSON 직렬화용 dict"""
    if ohlcv is None or ohlcv.empty:
        return {}
    c = ohlcv['close']
    sma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    dates = [f'{d:%Y-%m-%d}' for d in ohlcv.index]
    dset = set(dates)
    events = []
    for r in (news or []):
        if r.get('iso') in dset:
            events.append({'d': r['iso'], 'k': 'news', 't': r['title'], 'u': r.get('url', '')})
    for r in (dart or []):
        d = str(r.get('date', ''))
        iso = f'{d[:4]}-{d[4:6]}-{d[6:8]}' if len(d) == 8 else d
        if iso in dset:
            events.append({'d': iso, 'k': 'dart', 't': r.get('title', ''), 'u': r.get('url', '')})
    return {
        'd': dates,
        'o': _nan_list(ohlcv['open']), 'h': _nan_list(ohlcv['high']),
        'l': _nan_list(ohlcv['low']), 'c': _nan_list(c),
        'v': _nan_list(ohlcv['volume'], 0),
        'sma5': _nan_list(sma(c, 5)), 'sma20': _nan_list(sma20),
        'sma200': _nan_list(sma(c, 200)),
        'bbu': _nan_list(sma20 + 2 * std20), 'bbd': _nan_list(sma20 - 2 * std20),
        'rsi': _nan_list(rsi(c, 2)),
        'ev': events,
    }


# ---- 종목 진단 카드 (규칙 기반 서술 — "추천" 아님, 근거 동시 표시) ----

DEBT_ALERT = 200.0   # 부채비율 이상 -> 재무 주의
TOTAL_GOOD = 70      # 팩터 종합 이 이상 & 추세 위 -> 저평가·우량
VALUE_EXPENSIVE = 25  # 가치점수 이하(=비쌈) -> 고평가


def diagnose_cards(quotes: dict, fin: dict, closes_map: dict, holdings: set) -> list:
    """유니버스 진단 카드. 등급은 명시 규칙으로만 산출, reasons에 근거 문자열."""
    franks = {r['code']: r for r in factor_ranking(quotes, fin)}
    cards = []
    for code, q in (quotes or {}).items():
        v = valuation_row(q)
        f = franks.get(code, {})
        s = closes_map.get(code)
        rsi2, above200, sma5_dist, signal = None, None, None, False
        if s is not None and len(s) >= SMA_LONG:
            rsi2 = float(rsi(s, 2).iloc[-1])
            above200 = float(s.iloc[-1]) > float(sma(s, SMA_LONG).iloc[-1])
            s5 = float(sma(s, SMA_EXIT).iloc[-1])
            sma5_dist = float(s.iloc[-1]) / s5 - 1.0 if s5 else None
            signal = bool(above200 and rsi2 < RSI_BUY)

        reasons, grade = [], ('중립', 'muted')
        if (f.get('debt') or 0) >= DEBT_ALERT:
            grade = ('재무 주의', 'crit')
            reasons.append(f"부채비율 {f['debt']:.0f}% ≥ {DEBT_ALERT:.0f}%")
        elif f.get('total') is not None and f['total'] >= TOTAL_GOOD and above200:
            grade = ('저평가·우량', 'good')
            reasons.append(f"팩터 종합 {f['total']} ≥ {TOTAL_GOOD} + SMA200 위")
        elif f.get('value') is not None and f['value'] <= VALUE_EXPENSIVE:
            grade = ('고평가', 'warn')
            reasons.append(f"가치점수 {f['value']} ≤ {VALUE_EXPENSIVE} (유니버스 내 비쌈)")
        elif above200 is False:
            grade = ('추세 약세', 'warn')
            reasons.append('SMA200 아래')
        else:
            reasons.append('특이 규칙 미해당')

        cards.append({
            'code': code, 'cur': v['cur'], 'chg_pct': v['chg_pct'],
            'value': f.get('value'), 'quality': f.get('quality'),
            'total': f.get('total'), 'rank': f.get('rank'),
            'frgn_rate': v['frgn_rate'], 'w52_band': v['w52_band'],
            'rsi2': rsi2, 'above200': above200, 'sma5_dist': sma5_dist,
            'signal': signal, 'holding': code in holdings,
            'grade': grade[0], 'grade_cls': grade[1], 'reasons': reasons,
        })
    cards.sort(key=lambda c: (c['total'] is None, -(c['total'] or 0)))
    return cards


# ---- 투자 보고서 (규칙 기반 서술 생성 — LLM 아님, 전 문장 데이터 역추적 가능) ----

def _pct(v, digits=2):
    return f'{v*100:+.{digits}f}%' if v is not None else '—'


def _window_ret(points: list, days: int) -> float | None:
    """[(날짜str, 값)] 시계열의 최근 days일 수익률 (days=0이면 전체)"""
    if len(points) < 2:
        return None
    pts = points if days <= 0 else [p for p in points if
                                    (date.fromisoformat(points[-1][0])
                                     - date.fromisoformat(p[0])).days <= days]
    if len(pts) < 2 or pts[0][1] <= 0:
        return None
    return pts[-1][1] / pts[0][1] - 1.0


def build_report(*, eq: list, kodex, live: tuple | None, state: dict, events: list,
                 radar: list, pos_rows: list, news: dict, names: dict,
                 days: int = 7) -> dict:
    """대시보드 데이터 -> 보고서 dict (문단 서술 + 표 데이터). 순수함수."""
    nm = lambda c: names.get(c) or c  # noqa: E731
    by_day = dict(eq)
    if live:
        by_day[live[0]] = live[1]
    acct_pts = sorted(by_day.items())
    kodex_pts = ([(f'{ts:%Y-%m-%d}', float(v)) for ts, v in kodex.items()]
                 if kodex is not None and len(kodex) else [])
    # 기간을 계좌 시계열 구간과 정렬
    if acct_pts and kodex_pts:
        kodex_pts = [p for p in kodex_pts if p[0] >= acct_pts[0][0]]
    acct_ret = _window_ret(acct_pts, days)
    kodex_ret = _window_ret(kodex_pts, days)
    diff = (acct_ret - kodex_ret) if None not in (acct_ret, kodex_ret) else None

    # 거래/실현손익 (기간)
    all_trades = realized_trades(events)
    cutoff = (date.fromisoformat(acct_pts[-1][0]) - timedelta(days=days)).isoformat() \
        if (days > 0 and acct_pts) else ''
    trades = [t for t in all_trades if t['sell_day'] >= cutoff]
    realized = sum(t['pnl'] for t in trades)
    unrealized = sum(r['pnl'] or 0 for r in pos_rows)

    label = '전체 기간' if days <= 0 else f'최근 {days}일'
    total_txt = f'{acct_pts[-1][1]:,.0f}원' if acct_pts else '—'
    perf_p = (f'{label} 계좌 수익률은 {_pct(acct_ret)}'
              + (f' (KODEX 200 {_pct(kodex_ret)}, 상대 {_pct(diff)}p)' if diff is not None else '')
              + f'. 현재 평가금액 {total_txt}.'
              + f' 기간 실현손익 {realized:+,.0f}원({len(trades)}건),'
              + f' 새틀라이트 평가손익 {unrealized:+,.0f}원.')

    # 레짐 판정 (명시 규칙)
    judged = [r for r in radar if r['above_sma200'] is not None]
    above = sum(1 for r in judged if r['above_sma200'])
    n_sig = sum(1 for r in radar if r['signal'])
    deep = [r for r in radar if (r['rsi2'] or 100) < 10]
    ratio = above / len(judged) if judged else None
    if ratio is None:
        regime, regime_p = '판정 불가', '시장 데이터 수집 전.'
    elif ratio >= 0.7:
        regime = '상승 추세'
        regime_p = (f'유니버스 {len(judged)}종목 중 {above}개({ratio*100:.0f}%)가 SMA200 위 — '
                    f'추세 건재. 딥 발생 시 진입 가능 상태.')
    elif ratio >= 0.4:
        regime = '혼조'
        regime_p = (f'SMA200 위 {above}/{len(judged)}({ratio*100:.0f}%) — 종목별 차별화 구간.')
    else:
        regime = '약세 조정'
        regime_p = (f'유니버스 {len(judged)}종목 중 {len(judged)-above}개가 SMA200 아래 — '
                    f'조정 레짐. 과매도(RSI2<10) {len(deep)}종목이 있어도 추세 필터가 '
                    f'진입을 차단하는 방어 모드.')
    regime_p += f' 현재 신호권 {n_sig}종목.'

    # 포지션 서술
    pos_list = []
    for r in pos_rows:
        line = (f"{nm(r['code'])}({r['code']}): {r['qty']}주 @{r['entry']:,.0f} "
                f"({r['entry_date']}~, {r['days']}일차), 수익률 {_pct(r['pnl_pct'])}")
        if r['sma5_dist'] is not None:
            line += (' — 종가가 SMA5 위, 다음 사이클 청산 예정' if r['sma5_dist'] > 0
                     else f" — 청산선(SMA5)까지 {_pct(r['sma5_dist'])}, 반등 대기")
        n0 = (news.get(r['code']) or [None])[0]
        if n0:
            line += f" · 최근 기사: {n0['title'][:40]}"
        pos_list.append(line)

    # 관찰 종목: SMA200 위 + RSI2 낮은 순 (신호권 근접, 미보유)
    watch = [r for r in radar
             if r['above_sma200'] and not r['holding'] and r['rsi2'] is not None][:5]
    watch_list = [f"{nm(r['code'])} RSI2 {r['rsi2']:.1f}"
                  + (' ⚡신호권' if r['signal'] else '') for r in watch]

    ops = ops_report(events)
    ops_p = (f"운영 {ops['first_day']}~{ops['last_day']}: 사이클 성공 {ops['summary_days']}일 / "
             f"스킵 {ops['skip_days']}일, 누적 신호 {ops['signals']}건·체결 {ops['fills_ok']}건.")
    if ops['last_error']:
        ops_p += f" 최근 에러: {ops['last_error']}"

    return {'asof': acct_pts[-1][0] if acct_pts else '', 'label': label,
            'perf_p': perf_p, 'regime': regime, 'regime_p': regime_p,
            'pos_list': pos_list, 'trades': trades, 'realized': realized,
            'watch_list': watch_list, 'ops_p': ops_p,
            'acct_ret': acct_ret, 'kodex_ret': kodex_ret}


def gate_status(today: date) -> dict:
    d_left = (GATE_END - today).days
    return {'end': GATE_END.isoformat(), 'd_left': d_left, 'over': d_left < 0}


# ---- DART 공시 (금감원 오픈API, 키 필요 — 일 20,000건 제한이라 여유) ----

DART_LIST_URL = 'https://opendart.fss.or.kr/api/list.json'
DART_CORP_URL = 'https://opendart.fss.or.kr/api/corpCode.xml'


def parse_dart_corp_xml(xml_bytes: bytes, wanted: set) -> dict:
    """corpCode.xml -> {종목코드: dart고유번호} (wanted만)"""
    import xml.etree.ElementTree as ET
    out = {}
    for el in ET.fromstring(xml_bytes).iter('list'):
        stock = (el.findtext('stock_code') or '').strip()
        if stock in wanted:
            out[stock] = (el.findtext('corp_code') or '').strip()
    return out


def parse_dart_list(data: dict, limit: int = 5) -> list:
    """list.json -> [{'date','title','submitter','url'}] (최신순 그대로)"""
    rows = []
    for it in (data.get('list') or [])[:limit]:
        rcp = it.get('rcept_no', '')
        rows.append({'date': it.get('rcept_dt', ''),
                     'title': it.get('report_nm', ''),
                     'submitter': it.get('flr_nm', ''),
                     'url': f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp}'})
    return rows


# ---- 네이버 뉴스 검색 API (공식 — 크롤링 아님) ----
# 2026 개편: 신규 발급은 네이버클라우드 "NAVER API HUB" (기존 개발자센터 키는 2027-06-30까지)
# HUB: naverapihub.apigw.ntruss.com/search/v1/news + X-NCP-APIGW-API-KEY(-ID)
# 구형: openapi.naver.com/v1/search/news.json + X-Naver-Client-Id/Secret

NAVER_NEWS_URL = 'https://openapi.naver.com/v1/search/news.json'
NAVER_HUB_NEWS_URL = 'https://naverapihub.apigw.ntruss.com/search/v1/news'


def naver_news_endpoint(env: dict):
    """(url, headers) — HUB 키 우선, 구형 키 폴백, 없으면 None"""
    hub_id, hub_key = env.get('NAVER_HUB_KEY_ID'), env.get('NAVER_HUB_KEY')
    if hub_id and hub_key:
        return NAVER_HUB_NEWS_URL, {'X-NCP-APIGW-API-KEY-ID': hub_id,
                                    'X-NCP-APIGW-API-KEY': hub_key}
    cid, csec = env.get('NAVER_CLIENT_ID'), env.get('NAVER_CLIENT_SECRET')
    if cid and csec:
        return NAVER_NEWS_URL, {'X-Naver-Client-Id': cid, 'X-Naver-Client-Secret': csec}
    return None
_TAG_RE = None


def _strip_tags(s: str) -> str:
    global _TAG_RE
    import html as _html
    import re
    if _TAG_RE is None:
        _TAG_RE = re.compile(r'<[^>]+>')
    return _html.unescape(_TAG_RE.sub('', s or '')).strip()


def parse_naver_news(data: dict, limit: int = 5) -> list:
    """news.json -> [{'title','url','date'}] (title 태그/엔티티 제거, date=MM-DD HH:MM)"""
    from email.utils import parsedate_to_datetime
    rows = []
    for it in (data.get('items') or [])[:limit]:
        try:
            dt = parsedate_to_datetime(it.get('pubDate', ''))
            date, iso = f'{dt:%m-%d %H:%M}', f'{dt:%Y-%m-%d}'
        except (TypeError, ValueError):
            date, iso = '', ''
        rows.append({'title': _strip_tags(it.get('title')),
                     'url': it.get('originallink') or it.get('link') or '',
                     'date': date, 'iso': iso})
    return rows


# ---- 시장 캐시 (I/O — 유닛테스트 제외, 안전규칙만 순수함수로 분리) ----

def token_file_fresh(data_dir: Path, mode: str, now_ts: float, min_left: int = 900) -> bool:
    """봇이 관리하는 토큰 파일이 충분히 유효한가 (대시보드는 절대 발급하지 않음)"""
    try:
        saved = json.loads((Path(data_dir) / f'kis_token_{mode}.json').read_text(encoding='utf-8'))
        return saved['expires_at'] > now_ts + min_left
    except (OSError, ValueError, KeyError):
        return False


def in_bot_window(now: datetime) -> bool:
    """봇 일일작업 창(평일 15:15~15:35 KST) — 이 시간엔 수집 금지"""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 15 * 60 + 15 <= hm <= 15 * 60 + 35


class MarketCache:
    """백그라운드 시장 데이터 수집 (10분 주기). snapshot dict:
    {ts, total, cash, holdings, closes: {code: Series}, error}"""

    def __init__(self, env: dict, universe: list, interval: int = 600, names: dict | None = None):
        self.env, self.universe, self.interval = env, list(universe), interval
        self.names = names or {}
        self.data_dir = Path(env.get('DATA_DIR') or '.')
        self.mode = (env.get('KIS_MODE') or 'paper').lower()
        self.snapshot = None
        self.status = '초기 수집 대기'
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while True:
            try:
                self._refresh()
            except Exception as e:
                self.status = f'수집 실패: {str(e)[:80]}'
            time.sleep(self.interval)

    def _positions_codes(self):
        try:
            state = json.loads((self.data_dir / 'positions.json').read_text(encoding='utf-8'))
            return list(state.get('positions') or {})
        except (OSError, ValueError):
            return []

    def _refresh(self):
        now = datetime.now(KST)
        if in_bot_window(now):
            self.status = '봇 일일작업 창 — 수집 일시중지'
            return
        if not token_file_fresh(self.data_dir, self.mode, time.time()):
            self.status = '토큰 대기 (봇이 갱신 담당)'
            return
        from broker.kis import KisBroker  # 지연 import (테스트 격리)
        b = KisBroker(self.env)
        snap = b.balance()
        codes = list(dict.fromkeys([*self.universe, CORE_CODE, *self._positions_codes()]))
        closes, quotes, ohlcv = {}, {}, {}
        for c in codes:
            try:
                df = b.daily_ohlcv(c, 260)
                ohlcv[c] = df
                closes[c] = df['close']  # 기존 소비자(레이더/포지션/벤치마크) 호환
                d = b._request('GET', '/uapi/domestic-stock/v1/quotations/inquire-price',
                               'FHKST01010100',
                               params={'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': c})
                quotes[c] = d.get('output') or {}
            except Exception:
                pass  # 종목 단위 실패는 스킵 (다음 주기 재시도)
        fin = self._refresh_fin(b, codes, now)
        dart = self._refresh_dart(codes, now)
        news = self._refresh_news(codes)
        self.snapshot = {'ts': now.isoformat(), 'total': snap.total, 'cash': snap.cash,
                         'holdings': snap.holdings, 'closes': closes, 'quotes': quotes,
                         'ohlcv': ohlcv, 'fin': fin, 'dart': dart, 'news': news}
        self.status = f'{now:%H:%M} 갱신 ({len(closes)}종목)'

    def _refresh_news(self, codes):
        """네이버 뉴스 검색 (종목명 쿼리, 최신순 5건). 키 없으면 빈 dict."""
        import requests
        ep = naver_news_endpoint(self.env)
        if ep is None:
            return {}
        NAVER_URL, headers = ep
        news = {}
        for code in codes:
            if code == CORE_CODE:
                continue
            name = self.names.get(code) or code
            # 2글자 이하 종목명(기아 등)은 동음이의 노이즈(야구단...) -> '주가' 붙여 문맥 고정
            q = f'{name} 주가' if len(name) <= 2 else name
            try:
                r = requests.get(NAVER_URL, headers=headers,
                                 params={'query': q, 'display': 5, 'sort': 'date'},
                                 timeout=10)
                r.raise_for_status()
                news[code] = parse_naver_news(r.json())
            except Exception:
                pass  # 종목 단위 실패 스킵
            time.sleep(0.12)
        return news

    def _corp_map(self, key, codes) -> dict:
        """종목코드->DART 고유번호. 파일캐시(사실상 영구), 부족하면 zip 재다운로드."""
        import io
        import zipfile
        import requests
        cache = self.data_dir / 'dart_corp_map.json'
        try:
            m = json.loads(cache.read_text(encoding='utf-8'))
            if all(c in m for c in codes if c != CORE_CODE):
                return m
        except (OSError, ValueError):
            m = {}
        r = requests.get(DART_CORP_URL, params={'crtfc_key': key}, timeout=30)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml_bytes = z.read(z.namelist()[0])
        m = parse_dart_corp_xml(xml_bytes, set(codes))
        try:
            cache.write_text(json.dumps(m), encoding='utf-8')
        except OSError:
            pass
        return m

    def _refresh_dart(self, codes, now):
        """최근 30일 공시 — 하루 1회. 키 없으면 빈 dict (페이지에서 안내)."""
        import requests
        key = self.env.get('DART_API_KEY')
        prev = (self.snapshot or {}).get('dart') or {}
        if not key:
            return {}
        if prev.get('_date') == f'{now:%F}':
            return prev
        dart = {'_date': f'{now:%F}'}
        try:
            corp = self._corp_map(key, [c for c in codes if c != CORE_CODE])
        except Exception:
            return prev  # 맵 실패 시 이전값 유지, 다음 주기 재시도
        bgn = f'{(now - timedelta(days=30)):%Y%m%d}'
        for code, corp_code in corp.items():
            try:
                r = requests.get(DART_LIST_URL, params={
                    'crtfc_key': key, 'corp_code': corp_code,
                    'bgn_de': bgn, 'end_de': f'{now:%Y%m%d}',
                    'page_count': '10'}, timeout=15)
                r.raise_for_status()
                data = r.json()
                if data.get('status') in ('000', '013'):  # 013 = 데이터 없음(정상)
                    dart[code] = parse_dart_list(data)
            except Exception:
                pass  # 종목 단위 실패 스킵
            time.sleep(0.1)
        return dart

    def _refresh_fin(self, b, codes, now):
        """재무비율 — 하루 1회만 (거의 안 변함). 미지원(VTS 가능성)이면 빈 dict 유지."""
        prev = (self.snapshot or {}).get('fin') or {}
        if prev.get('_date') == f'{now:%F}':
            return prev
        fin = {'_date': f'{now:%F}'}
        for c in codes:
            if c == CORE_CODE:
                continue  # ETF는 재무비율 없음
            try:
                d = b._request('GET', '/uapi/domestic-stock/v1/finance/financial-ratio',
                               'FHKST66430300',
                               params={'fid_div_cls_code': '0',
                                       'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': c})
                fin[c] = d.get('output') or []
            except Exception:
                pass  # 미지원/일시 실패 -> 해당 종목 생략
        return fin
