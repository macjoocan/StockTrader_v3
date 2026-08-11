from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import rsi, sma

RSI_N = 2
RSI_BUY = 10.0
SMA_LONG = 200
SMA_EXIT = 5


@dataclass(frozen=True)
class Intent:
    code: str
    side: str  # 'BUY' | 'SELL'
    rsi: float


def evaluate_stock(closes: pd.Series, holding: bool) -> str | None:
    closes = closes.dropna()
    c = closes.iloc[-1] if len(closes) else np.nan
    if holding:
        if len(closes) >= SMA_EXIT and c > sma(closes, SMA_EXIT).iloc[-1]:
            return 'SELL'
        return None
    if len(closes) < SMA_LONG:
        return None
    r = rsi(closes, RSI_N).iloc[-1]
    if c > sma(closes, SMA_LONG).iloc[-1] and r < RSI_BUY:
        return 'BUY'
    return None


def scan(closes_by_code: dict[str, pd.Series], holdings: set[str],
         free_slots: int) -> list[Intent]:
    sells, buys = [], []
    for code, closes in closes_by_code.items():
        side = evaluate_stock(closes, holding=(code in holdings))
        if side is None:
            continue
        r = float(rsi(closes.dropna(), RSI_N).iloc[-1])
        (sells if side == 'SELL' else buys).append(Intent(code, side, r))
    buys.sort(key=lambda i: i.rsi)
    return sells + buys[:max(0, free_slots)]
