import numpy as np
import pandas as pd
from signal_engine.indicators import rsi, sma


def test_sma_basic():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 5)
    assert np.isnan(out.iloc[3])
    assert out.iloc[4] == 3.0


def test_rsi2_matches_backtest_formula():
    # bt_rsi2.py와 동일 공식(Wilder EWM) 검증: 독립 계산값과 대조
    s = pd.Series([100.0, 101.0, 99.0, 98.0, 102.0, 103.0, 101.0])
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    expected = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    out = rsi(s, 2)
    pd.testing.assert_series_equal(out, expected)


def test_rsi_all_up_is_nan():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert np.isnan(rsi(s, 2).iloc[-1])  # dn=0 -> rs=nan (bt_rsi2와 동일 처리)
