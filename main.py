# -*- coding: utf-8 -*-
import json
import os
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent))

from broker.kis import KisBroker          # noqa: E402
from config import load_config            # noqa: E402
from events.log import EventLog           # noqa: E402
from executor import run_daily, run_us_core  # noqa: E402
from notify.telegram import Notifier      # noqa: E402
from reconcile import load_state, save_state  # noqa: E402
from scheduler import next_action         # noqa: E402

KST = timezone(timedelta(hours=9))


def main():
    cfg = load_config(dict(os.environ))
    data_dir = Path(cfg['DATA_DIR'])
    data_dir.mkdir(parents=True, exist_ok=True)
    universe = json.loads((Path(__file__).parent / 'data' / 'universe_2026.json')
                          .read_text(encoding='utf-8'))['codes']
    log = EventLog(data_dir)
    notifier = Notifier(cfg['TELEGRAM_TOKEN'], cfg['TELEGRAM_CHAT_ID'])
    broker = KisBroker(cfg)
    state_path = data_dir / 'positions.json'
    print(f'[{datetime.now(KST):%F %T}] stock-trader 시작 (universe {len(universe)}종목)')
    notifier.send('🟢 stock-trader 시작')

    last_hb = None
    last_us_check = None  # 메모리만 (재시작 시 재체크 무해 — 조회 2콜)
    while True:
        try:
            now = datetime.now(KST)
            state = load_state(state_path)
            try:
                ltd = (date.fromisoformat(state['last_trade_date'])
                       if state['last_trade_date'] else None)
            except ValueError as e:
                print(f'[{now:%F %T}] last_trade_date 파싱 오류: {e}', flush=True)
                log.write('error', msg=f'last_trade_date parse: {e}')
                ltd = None
            action = next_action(now, last_hb, ltd, last_us_date=last_us_check)
            if action == 'us_core':
                last_us_check = now.date()
                # 월 1회만 실제 리밸 (미실행 사유: 이미 이번 달 완료)
                if state.get('last_us_rebal_ym') != f'{now:%Y-%m}':
                    try:
                        run_us_core(broker, state, f'{now:%Y-%m}', log, notifier)
                    except Exception as e:
                        print(f'[{now:%F %T}] 미국 코어 오류: {e}', flush=True)
                        log.write('error', msg=f'us_core: {e}')
                    finally:
                        save_state(state_path, state)
            elif action == 'heartbeat':
                print(f'[{now:%F %T}] heartbeat 보유 {len(state["positions"])}')
                last_hb = now
                # 토큰 선제 갱신 — 일일작업 직전(15:19~)에 발급할 일이 없도록 (VTS 그 시간대 지연 실측)
                if not broker.ensure_token():
                    print(f'[{now:%F %T}] 토큰 선제갱신 실패 (다음 heartbeat에 재시도)', flush=True)
            elif action == 'trade':
                # 스펙 §2: 동시호가(15:20~30) 제출 — 15:20:30까지 대기 후 실행
                target = now.replace(hour=15, minute=20, second=30, microsecond=0)
                wait = (target - datetime.now(KST)).total_seconds()
                if wait > 0:
                    time.sleep(wait)
                try:
                    for attempt in range(3):
                        do_rebal = state.get('last_rebal_ym') != f'{now:%Y-%m}'
                        try:
                            run_daily(broker, universe, state, f'{now:%F}', log,
                                      notifier, do_rebalance=do_rebal)
                            break
                        except Exception as e:
                            print(f'[{datetime.now(KST):%F %T}] 일일작업 재시도 {attempt+1}/3: {e}',
                                  flush=True)
                            time.sleep(10)
                    else:
                        notifier.send('❌ 일일작업 3회 실패 — 오늘 스킵')
                        log.write('error', msg='일일작업 3회 실패')
                        state['last_trade_date'] = f'{now:%F}'  # 오늘 재시도 안 함
                finally:
                    save_state(state_path, state)
            time.sleep(5)
        except Exception as e:
            now = datetime.now(KST)
            print(f'[{now:%F %T}] 루프 오류: {e}', flush=True)
            log.write('error', msg=f'main loop: {e}')
            time.sleep(60)


if __name__ == '__main__':
    main()
