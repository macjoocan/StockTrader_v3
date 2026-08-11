import numpy as np
import pandas as pd

from broker.kis import Fill, Snapshot
from executor import run_daily


def dip_series(deep=0.97):
    base = pd.Series(np.linspace(100, 130, 250) * 500)  # 주가 5만~6.5만
    base.iloc[-2] = base.iloc[-3] * deep
    base.iloc[-1] = base.iloc[-2] * deep
    idx = pd.bdate_range(end='2026-08-12', periods=250)
    return pd.Series(base.values, index=idx)


def up_series():
    idx = pd.bdate_range(end='2026-08-12', periods=250)
    return pd.Series(np.linspace(100, 130, 250) * 500, index=idx)


class FakeBroker:
    def __init__(self, closes_map, snapshot):
        self.closes_map, self.snap, self.orders = closes_map, snapshot, []

    def balance(self):
        return self.snap

    def daily_closes(self, code, days=260):
        return self.closes_map[code]

    def current_price(self, code):
        return float(self.closes_map[code].iloc[-1])

    def market_order(self, code, side, qty):
        self.orders.append((code, side, qty))
        return Fill(code, side, qty, self.current_price(code), ok=True)


class FakeLog:
    def __init__(self):
        self.events = []

    def write(self, kind, **payload):
        self.events.append((kind, payload))


class FakeNotifier:
    def __init__(self):
        self.msgs = []

    def send(self, text):
        self.msgs.append(text)
        return True


def base_state():
    return {'positions': {}, 'last_trade_date': None, 'last_rebal_ym': None}


def test_buy_on_dip_signal():
    broker = FakeBroker({'AAA': dip_series()},
                        Snapshot(total=1_000_000, cash=1_000_000, holdings={}))
    log, notif, state = FakeLog(), FakeNotifier(), base_state()
    run_daily(broker, ['AAA'], state, '2026-08-12', log, notif, do_rebalance=False)
    assert any(o[0] == 'AAA' and o[1] == 'BUY' for o in broker.orders)
    assert 'AAA' in state['positions']
    assert any(k == 'fill' for k, _ in log.events)


def test_sell_exits_position():
    snap = Snapshot(total=1_000_000, cash=800_000,
                    holdings={'AAA': (3, 65000)})
    broker = FakeBroker({'AAA': up_series()}, snap)
    state = {'positions': {'AAA': {'qty': 3, 'entry_price': 60000, 'entry_date': 'x'}},
             'last_trade_date': None, 'last_rebal_ym': None}
    run_daily(broker, ['AAA'], state, '2026-08-12', FakeLog(), FakeNotifier(),
              do_rebalance=False)
    assert ('AAA', 'SELL', 3) in broker.orders
    assert state['positions'] == {}


def test_rebalance_only_when_flag():
    snap = Snapshot(total=1_000_000, cash=1_000_000, holdings={})
    closes = {'AAA': up_series(), '069500': up_series()}
    broker = FakeBroker(closes, snap)
    state = base_state()
    run_daily(broker, ['AAA'], state, '2026-08-12', FakeLog(), FakeNotifier(),
              do_rebalance=True)
    assert any(o[0] == '069500' and o[1] == 'BUY' for o in broker.orders)
    assert state['last_rebal_ym'] == '2026-08'


def test_slot_cap_respected():
    # 이미 4슬롯 보유 -> 신규 매수 없음
    holdings = {c: (1, 60000) for c in ['P1', 'P2', 'P3', 'P4']}
    snap = Snapshot(total=1_000_000, cash=700_000, holdings=holdings)
    closes = {c: dip_series() for c in ['P1', 'P2', 'P3', 'P4', 'NEW']}
    broker = FakeBroker(closes, snap)
    state = {'positions': {c: {'qty': 1, 'entry_price': 60000, 'entry_date': 'x'}
                           for c in holdings},
             'last_trade_date': None, 'last_rebal_ym': None}
    run_daily(broker, list(closes), state, '2026-08-12', FakeLog(), FakeNotifier(),
              do_rebalance=False)
    assert not any(o[0] == 'NEW' for o in broker.orders)


def test_failed_order_does_not_update_state():
    class RejectBroker(FakeBroker):
        def market_order(self, code, side, qty):
            return Fill(code, side, qty, 0.0, ok=False, reason='rejected')

    broker = RejectBroker({'AAA': dip_series()},
                          Snapshot(total=1_000_000, cash=1_000_000, holdings={}))
    state = base_state()
    notif = FakeNotifier()
    run_daily(broker, ['AAA'], state, '2026-08-12', FakeLog(), notif,
              do_rebalance=False)
    assert state['positions'] == {}
    assert any('실패' in m for m in notif.msgs)


def test_buy_fill_price_zero_falls_back_to_close():
    class ZeroPriceFillBroker(FakeBroker):
        def market_order(self, code, side, qty):
            self.orders.append((code, side, qty))
            return Fill(code, side, qty, 0.0, ok=True, reason='price_lookup_failed')

    broker = ZeroPriceFillBroker({'AAA': dip_series()},
                                 Snapshot(total=1_000_000, cash=1_000_000, holdings={}))
    state = base_state()
    run_daily(broker, ['AAA'], state, '2026-08-12', FakeLog(), FakeNotifier(), do_rebalance=False)
    assert state['positions']['AAA']['entry_price'] > 0
