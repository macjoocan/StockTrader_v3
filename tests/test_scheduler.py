from datetime import date, datetime

from scheduler import next_action

MON_1520 = datetime(2026, 8, 10, 15, 20)  # 월요일
MON_1000 = datetime(2026, 8, 10, 10, 0)
SAT_1520 = datetime(2026, 8, 15, 15, 20)  # 토요일


def test_trade_after_1519_weekday():
    assert next_action(MON_1520, last_hb=MON_1520, last_trade_date=None) == 'trade'


def test_no_trade_twice_same_day():
    assert next_action(MON_1520, MON_1520, last_trade_date=date(2026, 8, 10)) == 'sleep'


def test_no_trade_weekend():
    assert next_action(SAT_1520, SAT_1520, None) == 'sleep'


def test_no_trade_before_1519():
    assert next_action(MON_1000, MON_1000, None) == 'sleep'


def test_heartbeat_every_5min():
    assert next_action(MON_1000, last_hb=None, last_trade_date=None) == 'heartbeat'
    early = datetime(2026, 8, 10, 9, 56)
    assert next_action(MON_1000, last_hb=early, last_trade_date=None) == 'sleep'
    old = datetime(2026, 8, 10, 9, 54)
    assert next_action(MON_1000, last_hb=old, last_trade_date=None) == 'heartbeat'


def test_trade_priority_over_heartbeat():
    assert next_action(MON_1520, last_hb=None, last_trade_date=None) == 'trade'
