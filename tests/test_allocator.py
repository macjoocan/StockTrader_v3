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
