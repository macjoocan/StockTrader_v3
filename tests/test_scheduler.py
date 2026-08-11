from datetime import date, datetime, timezone, timedelta

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


def test_aware_kst_datetimes_work():
    """Test that tz-aware datetimes (KST) work correctly without TypeError."""
    KST = timezone(timedelta(hours=9))
    now = datetime(2026, 8, 10, 15, 20, tzinfo=KST)
    hb = datetime(2026, 8, 10, 15, 20, tzinfo=KST)
    # Both aware, same timezone: should work
    assert next_action(now, hb, None) == 'trade'
    assert next_action(now, hb, date(2026, 8, 10)) == 'sleep'


def test_trade_at_exact_1519():
    """Boundary: exactly 15:19:00 is >= TRADE_TIME."""
    mon_1519_exact = datetime(2026, 8, 10, 15, 19, 0)
    assert next_action(mon_1519_exact, mon_1519_exact, None) == 'trade'


def test_heartbeat_at_exact_300sec():
    """Boundary: exactly 300 seconds elapsed triggers heartbeat."""
    now = datetime(2026, 8, 10, 10, 5, 0)  # 10:05:00
    old_hb = datetime(2026, 8, 10, 10, 0, 0)  # 10:00:00 (300 sec ago)
    assert next_action(now, old_hb, None) == 'heartbeat'
