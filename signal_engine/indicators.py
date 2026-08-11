import numpy as np
import pandas as pd


def sma(closes: pd.Series, n: int) -> pd.Series:
    return closes.rolling(n).mean()


def rsi(closes: pd.Series, n: int = 2) -> pd.Series:
    d = closes.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)
