from datetime import date, datetime, time

TRADE_TIME = time(15, 19)
HEARTBEAT_SEC = 300


def next_action(now: datetime, last_hb: datetime | None,
                last_trade_date: date | None) -> str:
    if (now.weekday() < 5 and now.time() >= TRADE_TIME
            and last_trade_date != now.date()):
        return 'trade'
    if last_hb is None or (now - last_hb).total_seconds() >= HEARTBEAT_SEC:
        return 'heartbeat'
    return 'sleep'
