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


def gate_status(today: date) -> dict:
    d_left = (GATE_END - today).days
    return {'end': GATE_END.isoformat(), 'd_left': d_left, 'over': d_left < 0}


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

    def __init__(self, env: dict, universe: list, interval: int = 600):
        self.env, self.universe, self.interval = env, list(universe), interval
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
        closes, quotes = {}, {}
        for c in codes:
            try:
                closes[c] = b.daily_closes(c, 260)
                d = b._request('GET', '/uapi/domestic-stock/v1/quotations/inquire-price',
                               'FHKST01010100',
                               params={'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': c})
                quotes[c] = d.get('output') or {}
            except Exception:
                pass  # 종목 단위 실패는 스킵 (다음 주기 재시도)
        fin = self._refresh_fin(b, codes, now)
        self.snapshot = {'ts': now.isoformat(), 'total': snap.total, 'cash': snap.cash,
                         'holdings': snap.holdings, 'closes': closes, 'quotes': quotes,
                         'fin': fin}
        self.status = f'{now:%H:%M} 갱신 ({len(closes)}종목)'

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
