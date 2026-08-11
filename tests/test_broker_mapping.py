from types import SimpleNamespace

from broker.kis import Fill, extract_fill_price, snapshot_from_pykis


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
