from types import SimpleNamespace

from broker.kis import Fill, KisBroker, extract_fill_price, snapshot_from_pykis


def test_snapshot_mapping():
    b = SimpleNamespace(
        deposits={'KRW': SimpleNamespace(amount=500000)},
        stocks=[SimpleNamespace(symbol='005930', qty=3, price=70000),
                SimpleNamespace(symbol='069500', qty=10, price=40000)],
    )
    s = snapshot_from_pykis(b)
    assert s.cash == 500000
    assert s.holdings == {'005930': (3, 70000.0), '069500': (10, 40000.0)}
    assert s.total == 500000 + 3 * 70000 + 10 * 40000


def test_fill_price_fallback():
    order_no_price = SimpleNamespace(price=None, executed_qty=3)
    assert extract_fill_price(order_no_price, current_price=71000.0) == 71000.0
    order_with_price = SimpleNamespace(price=70500, executed_qty=3)
    assert extract_fill_price(order_with_price, current_price=71000.0) == 70500.0
    order_zero = SimpleNamespace(price=0, executed_qty=3)
    assert extract_fill_price(order_zero, current_price=71000.0) == 71000.0


class _FakeQuote:
    def __init__(self, price):
        self.price = price


class _FakeStock:
    """order(=buy/sell)와 quote(=current_price)를 독립적으로 성공/실패시킬 수 있는 더블.

    quote_calls로 current_price()가 호출됐는지(=지연 호출 준수 여부) 검증한다.
    """

    def __init__(self, order=None, order_exc=None, quote_price=None, quote_exc=None):
        self._order = order
        self._order_exc = order_exc
        self._quote_price = quote_price
        self._quote_exc = quote_exc
        self.quote_calls = 0

    def buy(self, qty):
        if self._order_exc:
            raise self._order_exc
        return self._order

    def sell(self, qty):
        return self.buy(qty)

    def quote(self):
        self.quote_calls += 1
        if self._quote_exc:
            raise self._quote_exc
        return _FakeQuote(self._quote_price)


class _FakeKis:
    def __init__(self, stock):
        self._stock = stock

    def stock(self, code):
        return self._stock


def _make_broker(stock):
    b = KisBroker.__new__(KisBroker)  # __init__ 우회 — pykis 접속 없이 순수 로직만 검증
    b.kis = _FakeKis(stock)
    return b


def test_market_order_success_with_price_skips_current_price_lookup():
    order = SimpleNamespace(price=70500, executed_qty=3)
    stock = _FakeStock(order=order)
    b = _make_broker(stock)
    fill = b.market_order('005930', 'BUY', 3)
    assert fill == Fill('005930', 'BUY', 3, 70500.0, ok=True)
    assert stock.quote_calls == 0  # current_price는 지연 호출 — 가격 있으면 아예 안 부름


def test_market_order_success_price_missing_and_current_price_fails_stays_ok():
    order = SimpleNamespace(price=None, executed_qty=3)
    stock = _FakeStock(order=order, quote_exc=RuntimeError('quote timeout'))
    b = _make_broker(stock)
    fill = b.market_order('005930', 'BUY', 3)
    assert fill.ok is True  # 주문은 이미 성공 — 사후 가격조회 실패로 뒤집으면 안 됨
    assert fill.price == 0.0
    assert fill.reason == 'price_lookup_failed'
    assert stock.quote_calls == 1


def test_market_order_order_itself_fails_returns_not_ok():
    stock = _FakeStock(order_exc=RuntimeError('order rejected'))
    b = _make_broker(stock)
    fill = b.market_order('005930', 'BUY', 3)
    assert fill.ok is False
    assert fill.price == 0.0
    assert 'order rejected' in fill.reason
