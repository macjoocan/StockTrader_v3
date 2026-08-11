from datetime import date, datetime, time

TRADE_TIME = time(15, 19)
HEARTBEAT_SEC = 300


def next_action(now: datetime, last_hb: datetime | None,
                last_trade_date: date | None) -> str:
    """Determine next action based on time and trade history.

    Args:
        now: Current datetime. Must have consistent tz-awareness with last_hb.
             If now is aware, it must be in the same timezone as last_hb.
             If mixed naive/aware, raises TypeError on (now - last_hb) computation.
        last_hb: Last heartbeat time, or None if first call.
        last_trade_date: Date of last trade, or None if not yet traded.

    Returns:
        'trade' if weekday and now.time() >= 15:19 and haven't traded today
        'heartbeat' if last_hb is None or >= 300 seconds have elapsed
        'sleep' otherwise

    Note:
        If now is tz-aware, now.date() is in that timezone.
        Example: datetime(2026, 8, 10, 15, 20, tzinfo=KST).date() == date(2026, 8, 10) in KST.
    """
    if (now.weekday() < 5 and now.time() >= TRADE_TIME
            and last_trade_date != now.date()):
        return 'trade'
    if last_hb is None or (now - last_hb).total_seconds() >= HEARTBEAT_SEC:
        return 'heartbeat'
    return 'sleep'
