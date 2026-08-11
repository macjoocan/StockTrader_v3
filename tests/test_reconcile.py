import json

from broker.kis import Snapshot
from reconcile import load_state, reconcile, save_state


def snap(**holdings):
    h = {k: v for k, v in holdings.items()}
    return Snapshot(total=1000000, cash=500000, holdings=h)


def test_adopt_unknown_holding():
    state = {'positions': {}, 'last_trade_date': None, 'last_rebal_ym': None}
    new, warns = reconcile(state, snap(**{'005930': (3, 70000)}), core_code='069500')
    assert new['positions']['005930']['qty'] == 3
    assert new['positions']['005930']['entry_price'] == 70000
    assert len(warns) == 1


def test_drop_phantom_position():
    state = {'positions': {'005930': {'qty': 3, 'entry_price': 65000, 'entry_date': 'x'}},
             'last_trade_date': None, 'last_rebal_ym': None}
    new, warns = reconcile(state, snap(), core_code='069500')
    assert new['positions'] == {}
    assert len(warns) == 1


def test_core_excluded_and_match_silent():
    state = {'positions': {'005930': {'qty': 3, 'entry_price': 65000, 'entry_date': 'x'}},
             'last_trade_date': None, 'last_rebal_ym': None}
    new, warns = reconcile(
        state, snap(**{'005930': (3, 70000), '069500': (10, 40000)}), core_code='069500')
    assert new['positions']['005930']['entry_price'] == 65000  # 일치 시 유지
    assert '069500' not in new['positions']
    assert warns == []


def test_state_roundtrip_atomic(tmp_path):
    p = tmp_path / 'positions.json'
    state = {'positions': {'005930': {'qty': 1, 'entry_price': 1.0, 'entry_date': 'd'}},
             'last_trade_date': '2026-08-12', 'last_rebal_ym': '2026-08'}
    save_state(p, state)
    assert load_state(p) == state
    assert not list(tmp_path.glob('*.tmp'))


def test_load_missing_returns_default(tmp_path):
    s = load_state(tmp_path / 'nope.json')
    assert s == {'positions': {}, 'last_trade_date': None, 'last_rebal_ym': None}
