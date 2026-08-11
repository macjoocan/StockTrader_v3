from dataclasses import dataclass

CORE_CODE = '069500'  # KODEX 200


@dataclass(frozen=True)
class Order:
    code: str
    side: str  # 'BUY' | 'SELL'
    qty: int


def core_rebalance(total: float, core_value: float, core_price: float,
                   target: float = 0.70, band: float = 0.05) -> Order | None:
    if total <= 0 or core_price <= 0:
        return None
    w = core_value / total
    if abs(w - target) <= band:
        return None
    diff = target * total - core_value
    qty = int(abs(diff) // core_price)
    if qty == 0:
        return None
    return Order(CORE_CODE, 'BUY' if diff > 0 else 'SELL', qty)


def slot_budget(total: float, ratio: float = 0.30, slots: int = 4) -> float:
    return total * ratio / slots


def size_buy(budget: float, price: float, cash: float) -> int:
    if price <= 0:
        return 0
    return int(min(budget, cash) // price)
