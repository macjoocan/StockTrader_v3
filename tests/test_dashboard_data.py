from datetime import date, datetime, timezone, timedelta

import numpy as np
import pandas as pd

from dashboard_data import (KST, enrich_positions, gate_status, in_bot_window,
                            indexed_pair, ops_report, radar_rows,
                            realized_trades, token_file_fresh)


def series(vals, end='2026-08-21'):
    idx = pd.bdate_range(end=end, periods=len(vals))
    return pd.Series([float(v) for v in vals], index=idx)


def test_enrich_positions_pnl_and_exit_distance():
    positions = {'068270': {'qty': 19, 'entry_price': 195200.0, 'entry_date': '2026-08-18'}}
    closes = {'068270': series([200000, 198000, 196000, 194000, 192300])}
    rows = enrich_positions(positions, closes, today=date(2026, 8, 22))
    r = rows[0]
    assert r['cur'] == 192300.0
    assert round(r['pnl']) == round((192300 - 195200) * 19)
    assert r['pnl_pct'] < 0
    assert r['days'] == 4
    assert r['sma5_dist'] < 0  # 하락 추세라 SMA5 아래 = 청산조건 미충족


def test_radar_sorted_by_rsi_and_signal_flag():
    n = 250
    up = list(np.linspace(100, 130, n))
    dip = up[:-2] + [up[-3] * 0.97, up[-3] * 0.94]  # 이틀 급락 -> RSI2 극저, SMA200 위
    flat_down = list(np.linspace(130, 100, n - 1)) + [102.0]  # SMA200 아래, 막날 반등(RSI2 상승)
    closes = {'AAA': series(up), 'BBB': series(dip), 'CCC': series(flat_down)}
    rows = radar_rows(closes, holdings={'CCC'})
    assert rows[0]['code'] == 'BBB' and rows[0]['signal'] is True
    aaa = next(r for r in rows if r['code'] == 'AAA')
    assert aaa['signal'] is False and aaa['above_sma200'] is True
    ccc = next(r for r in rows if r['code'] == 'CCC')
    assert ccc['holding'] is True and ccc['above_sma200'] is False


def test_indexed_pair_base_100_and_live_override():
    eq = [('2026-08-18', 50000000.0), ('2026-08-19', 50500000.0)]
    kodex = series([108000, 109080], end='2026-08-19')  # +1%
    out = indexed_pair(eq, kodex, live_point=('2026-08-19', 51000000.0))
    assert out['acct'][0] == ('2026-08-18', 100.0)
    assert round(out['acct'][-1][1], 1) == 102.0  # live가 같은 날 요약을 덮음
    assert round(out['kodex'][-1][1], 1) == 101.0


def test_ops_report_counts():
    events = [
        {'kind': 'daily_summary', 'ts': '2026-08-18T15:23:00+09:00', 'signals': 1},
        {'kind': 'fill', 'ts': '2026-08-18T15:23:00+09:00', 'ok': True},
        {'kind': 'fill', 'ts': '2026-08-18T15:23:10+09:00', 'ok': False},
        {'kind': 'error', 'ts': '2026-08-20T15:21:00+09:00', 'msg': '일일작업 3회 실패'},
    ]
    r = ops_report(events)
    assert r['summary_days'] == 1 and r['skip_days'] == 1
    assert r['fills_ok'] == 1 and r['fills_fail'] == 1
    assert r['first_day'] == '2026-08-18' and r['last_day'] == '2026-08-20'
    assert '3회 실패' in r['last_error']


def test_realized_trades_fifo_excludes_core():
    events = [
        {'kind': 'fill', 'ts': '2026-08-18T15:23:00+09:00', 'code': '069500',
         'side': 'BUY', 'qty': 322, 'price': 108375.0, 'ok': True},
        {'kind': 'fill', 'ts': '2026-08-18T15:23:00+09:00', 'code': '068270',
         'side': 'BUY', 'qty': 19, 'price': 195200.0, 'ok': True},
        {'kind': 'fill', 'ts': '2026-08-25T15:23:00+09:00', 'code': '068270',
         'side': 'SELL', 'qty': 19, 'price': 200000.0, 'ok': True},
    ]
    trades = realized_trades(events)
    assert len(trades) == 1
    t = trades[0]
    assert t['code'] == '068270' and t['qty'] == 19
    assert round(t['pnl']) == round((200000 - 195200) * 19)
    assert t['buy_day'] == '2026-08-18' and t['sell_day'] == '2026-08-25'


def test_gate_status():
    g = gate_status(date(2026, 8, 22))
    assert g['d_left'] == 23 and g['over'] is False


def test_in_bot_window():
    assert in_bot_window(datetime(2026, 8, 21, 15, 20, tzinfo=KST)) is True   # 금 15:20
    assert in_bot_window(datetime(2026, 8, 21, 15, 36, tzinfo=KST)) is False
    assert in_bot_window(datetime(2026, 8, 22, 15, 20, tzinfo=KST)) is False  # 토


def test_token_file_fresh(tmp_path):
    import json as j
    import time as t
    assert token_file_fresh(tmp_path, 'paper', t.time()) is False  # 파일 없음
    (tmp_path / 'kis_token_paper.json').write_text(
        j.dumps({'token': 'X', 'expires_at': t.time() + 3600}), encoding='utf-8')
    assert token_file_fresh(tmp_path, 'paper', t.time()) is True
    assert token_file_fresh(tmp_path, 'paper', t.time() + 3000) is False  # 잔여 10분뿐