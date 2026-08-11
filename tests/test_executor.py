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
    # 동어반복 방지: 일일요약("실패N")이 아니라 전용 주문실패 알림을 확인
    assert any('❌ 주문 실패' in m for m in notif.msgs)


def test_buy_fill_price_zero_falls_back_to_close():
    class ZeroPriceFillBroker(FakeBroker):
        def market_order(self, code, side, qty):
            self.orders.append((code, side, qty))
            return Fill(code, side, qty, 0.0, ok=True, reason='price_lookup_failed')

    broker = ZeroPriceFillBroker({'AAA': dip_series()},
                                 Snapshot(total=1_000_000, cash=1_000_000, holdings={}))
    state = base_state()
    run_daily(broker, ['AAA'], state, '2026-08-12', FakeLog(), FakeNotifier(), do_rebalance=False)
    # 실제 종가 값과 정확히 일치해야 함 (0.0을 그냥 방치하지 않았는지)
    assert state['positions']['AAA']['entry_price'] == float(dip_series().iloc[-1])


def test_sell_submitted_before_buy():
    # 같은 실행에서 SELL 신호(보유중)와 BUY 신호(미보유)가 동시에 나올 때
    # 실제 주문 제출 순서가 SELL -> BUY 인지 확인 (현금 확보 순서 불변)
    snap = Snapshot(total=1_000_000, cash=500_000, holdings={'SELL_ME': (3, 60000)})
    closes = {'SELL_ME': up_series(), 'BUY_ME': dip_series()}
    broker = FakeBroker(closes, snap)
    state = {'positions': {'SELL_ME': {'qty': 3, 'entry_price': 55000, 'entry_date': 'x'}},
             'last_trade_date': None, 'last_rebal_ym': None}
    run_daily(broker, ['SELL_ME', 'BUY_ME'], state, '2026-08-12', FakeLog(), FakeNotifier(),
              do_rebalance=False)
    sides = [o[1] for o in broker.orders]
    assert 'SELL' in sides and 'BUY' in sides
    assert sides.index('SELL') < sides.index('BUY')


def test_core_rebalance_cost_reduces_satellite_buy_cash():
    # 코어 리밸 BUY가 현금 대부분을 소모하면, 이후 종목 BUY 사이징에
    # 그 소모가 반영되어야 함 (반영 안 되면 잔고 초과 주문 위험)
    snap = Snapshot(total=1_000_000, cash=660_000, holdings={})
    closes = {'BUY_ME': dip_series(), '069500': up_series()}
    broker = FakeBroker(closes, snap)
    state = base_state()
    run_daily(broker, ['BUY_ME'], state, '2026-08-12', FakeLog(), FakeNotifier(),
              do_rebalance=True)
    # 코어 리밸(700,000원 목표매수, 65,000원 -> 10주)이 실행되어 현금이 10,000원만 남음
    assert ('069500', 'BUY', 10) in broker.orders
    # 남은 현금(10,000)으로는 BUY_ME(6만원대)를 살 수 없어야 함
    assert not any(o[0] == 'BUY_ME' and o[1] == 'BUY' for o in broker.orders)


def test_failed_rebalance_does_not_update_last_rebal_ym():
    class RejectRebalBroker(FakeBroker):
        def market_order(self, code, side, qty):
            self.orders.append((code, side, qty))
            return Fill(code, side, qty, 0.0, ok=False, reason='rejected')

    snap = Snapshot(total=1_000_000, cash=1_000_000, holdings={})
    broker = RejectRebalBroker({'069500': up_series()}, snap)
    state = base_state()
    run_daily(broker, [], state, '2026-08-12', FakeLog(), FakeNotifier(), do_rebalance=True)
    # 리밸 주문이 거부됐으면 이번 달을 "처리됨"으로 마킹하면 안 됨 (재시도 가능해야 함)
    assert state['last_rebal_ym'] is None


def test_sell_fill_price_zero_normalized_for_available_cash():
    # finding #2: SELL 체결가 0을 정규화하지 않으면 available_cash가 매도대금을
    # 못 반영해 이후 BUY 사이징이 과소평가됨 (cash가 부족해 BUY가 스킵됨)
    class ZeroPriceBroker(FakeBroker):
        def market_order(self, code, side, qty):
            self.orders.append((code, side, qty))
            return Fill(code, side, qty, 0.0, ok=True, reason='price_lookup_failed')

    snap = Snapshot(total=1_000_000, cash=10_000, holdings={'SELL_ME': (3, 60000)})
    closes = {'SELL_ME': up_series(), 'BUY_ME': dip_series()}
    broker = ZeroPriceBroker(closes, snap)
    state = {'positions': {'SELL_ME': {'qty': 3, 'entry_price': 55000, 'entry_date': 'x'}},
             'last_trade_date': None, 'last_rebal_ym': None}
    run_daily(broker, ['SELL_ME', 'BUY_ME'], state, '2026-08-12', FakeLog(), FakeNotifier(),
              do_rebalance=False)
    assert ('SELL_ME', 'SELL', 3) in broker.orders
    # 정규화 없으면 cash=10,000뿐이라 BUY_ME(6만원대)를 못 사서 주문 자체가 안 나감
    assert any(o[0] == 'BUY_ME' and o[1] == 'BUY' for o in broker.orders)
    assert state['positions']['BUY_ME']['entry_price'] == float(dip_series().iloc[-1])


def test_holding_outside_universe_can_be_sold():
    # finding #3: 유니버스 밖 보유종목(대사로 adopt됨)도 SMA5 위로 오르면 매도돼야 함
    # (안 그러면 영구 슬롯 점유 + 청산 불가)
    snap = Snapshot(total=1_000_000, cash=500_000, holdings={'ORPHAN': (2, 60000)})
    broker = FakeBroker({'ORPHAN': up_series()}, snap)
    state = {'positions': {'ORPHAN': {'qty': 2, 'entry_price': 55000, 'entry_date': 'x'}},
             'last_trade_date': None, 'last_rebal_ym': None}
    run_daily(broker, [], state, '2026-08-12', FakeLog(), FakeNotifier(), do_rebalance=False)
    assert ('ORPHAN', 'SELL', 2) in broker.orders
    assert state['positions'] == {}


def test_broker_exception_does_not_crash_run_daily():
    class ExplodingBroker(FakeBroker):
        def market_order(self, code, side, qty):
            raise RuntimeError('network timeout')

    broker = ExplodingBroker({'AAA': dip_series()},
                             Snapshot(total=1_000_000, cash=1_000_000, holdings={}))
    state = base_state()
    notif = FakeNotifier()
    run_daily(broker, ['AAA'], state, '2026-08-12', FakeLog(), notif, do_rebalance=False)
    assert state['positions'] == {}
    assert any('❌ 주문 실패' in m for m in notif.msgs)
