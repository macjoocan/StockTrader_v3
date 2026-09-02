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


# 미국 코어 슬리브 (코어 온리): USD 자산 내 자기완결 — 균등 목표, 밴드 리밸
# 실계좌 전환 시 전체 배분(국내35/미국35/새틀30)은 환전 금액으로 실현 (설계 문서 참조)
US_CORE = [('SPY', 'AMS'), ('QQQ', 'NAS')]
US_INVEST_RATIO = 0.95  # USD 자산 중 투자 비율 (잔여는 단수/수수료 버퍼)
US_BAND = 0.05


def us_core_orders(usd_cash: float, holdings: dict, prices: dict,
                   invest_ratio: float = US_INVEST_RATIO, band: float = US_BAND) -> list:
    """USD 총자산 기준 심볼별 균등 목표, |편차|>band(총자산 대비) 시 주문.
    holdings: {symb: qty}, prices: {symb: usd}. 반환: [Order] (SELL 먼저)."""
    symbols = [s for s, _ in US_CORE]
    if any((prices.get(s) or 0) <= 0 for s in symbols):
        return []
    total = usd_cash + sum(holdings.get(s, 0) * prices[s] for s in symbols)
    if total <= 0:
        return []
    target = total * invest_ratio / len(symbols)
    orders = []
    for s in symbols:
        val = holdings.get(s, 0) * prices[s]
        if abs(val - target) / total <= band:
            continue
        diff = target - val
        qty = int(abs(diff) // prices[s])
        if qty > 0:
            orders.append(Order(s, 'BUY' if diff > 0 else 'SELL', qty))
    orders.sort(key=lambda o: o.side != 'SELL')  # SELL 먼저 (현금 확보)
    return orders


def slot_budget(total: float, ratio: float = 0.30, slots: int = 4) -> float:
    return total * ratio / slots


def size_buy(budget: float, price: float, cash: float) -> int:
    if price <= 0:
        return 0
    return int(min(budget, cash) // price)
