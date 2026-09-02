from portfolio.allocator import Order, core_rebalance, size_buy, slot_budget


def test_core_within_band_no_order():
    assert core_rebalance(total=1_000_000, core_value=690_000, core_price=40_000) is None


def test_core_below_band_buys():
    o = core_rebalance(total=1_000_000, core_value=600_000, core_price=40_000)
    assert o == Order('069500', 'BUY', 2)  # (700k-600k)//40k


def test_core_above_band_sells():
    o = core_rebalance(total=1_000_000, core_value=800_000, core_price=40_000)
    assert o == Order('069500', 'SELL', 2)


def test_core_tiny_diff_no_zero_qty_order():
    assert core_rebalance(total=1_000_000, core_value=760_000, core_price=999_999) is None


def test_slot_budget():
    assert slot_budget(2_000_000) == 150_000.0  # 30% / 4슬롯


def test_size_buy_floor_and_cash_cap():
    assert size_buy(budget=150_000, price=70_000, cash=1_000_000) == 2
    assert size_buy(budget=150_000, price=70_000, cash=80_000) == 1
    assert size_buy(budget=150_000, price=200_000, cash=1_000_000) == 0


def test_us_core_orders_initial_buy_equal_split():
    from portfolio.allocator import us_core_orders
    orders = us_core_orders(usd_cash=10000.0, holdings={}, prices={'SPY': 765.0, 'QQQ': 708.0})
    assert [(o.code, o.side) for o in orders] == [('SPY', 'BUY'), ('QQQ', 'BUY')]
    spy = next(o for o in orders if o.code == 'SPY')
    assert spy.qty == int(10000 * 0.95 / 2 // 765.0)  # 균등 목표


def test_us_core_orders_band_and_sell_first():
    from portfolio.allocator import us_core_orders
    # SPY 과체중(스큐) -> SPY SELL 먼저, QQQ BUY
    orders = us_core_orders(usd_cash=0.0, holdings={'SPY': 10, 'QQQ': 2},
                            prices={'SPY': 700.0, 'QQQ': 700.0})
    assert orders[0].side == 'SELL' and orders[0].code == 'SPY'
    assert any(o.code == 'QQQ' and o.side == 'BUY' for o in orders)


def test_us_core_orders_within_band_noop_and_guards():
    from portfolio.allocator import us_core_orders
    # 완벽 균형 -> 주문 없음
    assert us_core_orders(500.0, {'SPY': 10, 'QQQ': 10},
                          {'SPY': 500.0, 'QQQ': 500.0}) == []
    assert us_core_orders(1000.0, {}, {'SPY': 0, 'QQQ': 700.0}) == []  # 가격 0 방어
    assert us_core_orders(0.0, {}, {'SPY': 700.0, 'QQQ': 700.0}) == []  # 자산 0
