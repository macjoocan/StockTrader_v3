from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Snapshot:
    total: float
    cash: float
    holdings: dict = field(default_factory=dict)  # code -> (qty, price)


@dataclass(frozen=True)
class Fill:
    code: str
    side: str
    qty: int
    price: float
    ok: bool
    reason: str = ''


def snapshot_from_pykis(balance_obj) -> Snapshot:
    cash = float(balance_obj.deposits['KRW'].amount)
    holdings = {s.symbol: (int(s.qty), float(s.price)) for s in balance_obj.stocks}
    total = cash + sum(q * p for q, p in holdings.values())
    return Snapshot(total=total, cash=cash, holdings=holdings)


def extract_fill_price(order_obj, current_price: float) -> float:
    price = getattr(order_obj, 'price', None)
    if price:
        return float(price)
    return float(current_price)  # v3 교훈: 가격 누락 시 현재가 폴백


class KisBroker:
    """얇은 I/O 어댑터. pykis 콜사이트는 Task 12 모의투자 스모크로 검증."""

    def __init__(self, env: dict):
        from pykis import PyKis
        self.kis = PyKis(
            id=env['KIS_ID'], account=env['KIS_ACCOUNT'],
            appkey=env['KIS_APPKEY'], secretkey=env['KIS_SECRET'],
            virtual_id=env.get('KIS_VIRTUAL_ID') or None,
            virtual_appkey=env.get('KIS_VIRTUAL_APPKEY') or None,
            virtual_secretkey=env.get('KIS_VIRTUAL_SECRET') or None,
            keep_token=True,
        )

    def balance(self) -> Snapshot:
        return snapshot_from_pykis(self.kis.account().balance())

    def daily_closes(self, code: str, days: int = 260) -> pd.Series:
        chart = self.kis.stock(code).chart('day')
        bars = list(chart.bars)[-days:]
        return pd.Series([float(b.close) for b in bars],
                         index=pd.DatetimeIndex([b.time for b in bars]))

    def current_price(self, code: str) -> float:
        return float(self.kis.stock(code).quote().price)

    def market_order(self, code: str, side: str, qty: int) -> Fill:
        try:
            stock = self.kis.stock(code)
            order = stock.buy(qty=qty) if side == 'BUY' else stock.sell(qty=qty)
        except Exception as e:
            return Fill(code, side, qty, 0.0, ok=False, reason=str(e))
        # 주문은 성공 — 이후 어떤 실패도 ok=False로 바꾸면 안 됨
        price = getattr(order, 'price', None)
        if not price:
            try:
                price = self.current_price(code)
            except Exception:
                return Fill(code, side, qty, 0.0, ok=True, reason='price_lookup_failed')
        return Fill(code, side, qty, float(price), ok=True)
