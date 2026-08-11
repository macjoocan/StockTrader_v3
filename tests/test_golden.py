import json
from pathlib import Path

import pandas as pd
from signal_engine.strategy import evaluate_stock

FIX = Path(__file__).parent / 'fixtures'


def replay(closes: pd.Series):
    """라이브 신호 모듈로 bt_rsi2.trades_from_signals 루프 재현"""
    trades, holding, entry_px, entry_dt = [], False, 0.0, None
    for i in range(1, len(closes) + 1):
        window = closes.iloc[:i].dropna()
        if window.empty:
            continue
        dt, c = window.index[-1], window.iloc[-1]
        side = evaluate_stock(window, holding)
        if not holding and side == 'BUY':
            holding, entry_px, entry_dt = True, c, dt
        elif holding and side == 'SELL':
            trades.append({'entry': str(entry_dt.date()), 'exit': str(dt.date()),
                           'ret': round(c / entry_px - 1.0, 10)})
            holding = False
    return [t for t in trades if t['exit'] >= '2018-01-01']


def test_golden_trades_reproduced():
    closes = pd.read_csv(FIX / 'golden_closes.csv', index_col=0, parse_dates=True)
    expected = json.loads((FIX / 'golden_trades.json').read_text())
    for code in expected:
        assert replay(closes[code]) == expected[code], f'{code} 트레이드 불일치'
