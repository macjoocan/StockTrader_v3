from datetime import date, datetime, time

TRADE_TIME = time(15, 19)
TRADE_CUTOFF = time(15, 25)  # 이 이후엔 장외 시장가 발사 위험 -> trade 스킵 (finding #1)
# 240s: watchdog의 STALE 판정 창(5분 내 로그)과 heartbeat 간격이 같으면 경계 레이스
# -> 창보다 짧게 유지해 5분 창에 항상 로그 1개 이상 보장
HEARTBEAT_SEC = 240


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
        'trade' if weekday and 15:19 <= now.time() <= 15:25 and haven't traded today
        'heartbeat' if last_hb is None or >= 300 seconds have elapsed
        'sleep' otherwise (includes: past TRADE_CUTOFF on a weekday that hasn't
        traded yet — falls through to heartbeat/sleep and gets re-evaluated every
        tick until midnight rolls the date over; no special state needed)

    Note:
        If now is tz-aware, now.date() is in that timezone.
        Example: datetime(2026, 8, 10, 15, 20, tzinfo=KST).date() == date(2026, 8, 10) in KST.
    """
    if (now.weekday() < 5 and TRADE_TIME <= now.time() <= TRADE_CUTOFF
            and last_trade_date != now.date()):
        return 'trade'
    if last_hb is None or (now - last_hb).total_seconds() >= HEARTBEAT_SEC:
        return 'heartbeat'
    return 'sleep'
