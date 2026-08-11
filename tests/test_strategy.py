import numpy as np
import pandas as pd
from signal_engine.strategy import Intent, evaluate_stock, scan


def make_closes(n=250, last_dip=True):
    # 완만한 상승(SMA200 위) 후 마지막 2일 급락 -> RSI2 극저
    base = pd.Series(np.linspace(100, 130, n))
    if last_dip:
        base.iloc[-2] = base.iloc[-3] * 0.97
        base.iloc[-1] = base.iloc[-2] * 0.97
    return base


def test_entry_signal():
    assert evaluate_stock(make_closes(), holding=False) == 'BUY'


def test_no_entry_below_sma200():
    closes = make_closes()
    closes.iloc[-1] = 50.0  # SMA200 아래로
    assert evaluate_stock(closes, holding=False) is None


def test_no_entry_short_history():
    assert evaluate_stock(make_closes(150), holding=False) is None


def test_exit_signal():
    closes = pd.Series(np.linspace(100, 130, 250))  # 상승 지속 -> close > SMA5
    assert evaluate_stock(closes, holding=True) == 'SELL'


def test_hold_no_exit():
    closes = make_closes()  # 급락 직후 -> close < SMA5
    assert evaluate_stock(closes, holding=True) is None


def test_scan_slot_cap_rsi_ascending():
    dip = make_closes()
    deeper = dip.copy()
    deeper.iloc[-1] = deeper.iloc[-2] * 0.96  # 더 깊은 딥 = 더 낮은 RSI (but not so deep as to drop below SMA)
    out = scan({'AAA': dip, 'BBB': deeper}, holdings=set(), free_slots=1)
    assert [i.code for i in out] == ['BBB']
    assert out[0].side == 'BUY'


def test_scan_sell_always_included():
    up = pd.Series(np.linspace(100, 130, 250))
    out = scan({'CCC': up}, holdings={'CCC'}, free_slots=0)
    assert [(i.code, i.side) for i in out] == [('CCC', 'SELL')]
