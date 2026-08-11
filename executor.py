import dataclasses

from broker.kis import Fill
from portfolio.allocator import CORE_CODE, core_rebalance, size_buy, slot_budget
from reconcile import reconcile
from signal_engine.strategy import scan

SLOTS = 4


def run_daily(broker, universe, state, today, log, notifier, do_rebalance):
    universe = [c for c in universe if c != CORE_CODE]  # 코어는 리밸런싱 경로로만 매매

    snap = broker.balance()
    new_state, warns = reconcile(state, snap, CORE_CODE)
    state['positions'] = new_state['positions']
    for w in warns:
        log.write('reconcile', msg=w)
        notifier.send(f'⚠️ {w}')

    closes = {}
    # universe ∪ 보유종목 (finding #3): 대사로 adopt된 유니버스 밖 보유종목도 시세를
    # 조회해야 scan()이 SELL 평가를 할 수 있음 — 안 그러면 영구 슬롯 점유+청산 불가
    for code in list(dict.fromkeys([*universe, *state['positions']])):
        try:
            closes[code] = broker.daily_closes(code, 260)
        except Exception as e:
            log.write('error', code=code, msg=f'시세조회 실패: {e}')

    holdings = set(state['positions'])
    free = SLOTS - len(holdings)
    intents = scan(closes, holdings, free)
    for i in intents:
        log.write('signal', code=i.code, side=i.side, rsi=round(i.rsi, 2))

    fills, fails = [], []

    def submit(code, side, qty, fallback_px) -> bool | None:
        """주문 제출. 반환: True=체결, False=거부/예외, None=주문 안 함(qty<=0).

        fallback_px: f.ok인데 f.price<=0(체결가 조회 실패)일 때 대체할 가격
        (finding #2 — 정규화 안 하면 SELL은 현금 과소계상, BUY는 과대계상 위험).
        """
        if qty <= 0:
            return None
        try:
            f = broker.market_order(code, side, qty)
        except Exception as e:
            log.write('error', code=code, side=side, qty=qty, reason=str(e))
            notifier.send(f'❌ 주문 실패: {code} {side} {qty} — {e}')
            fails.append(Fill(code, side, qty, 0.0, ok=False, reason=str(e)))
            return False
        if f.ok and f.price <= 0:
            f = dataclasses.replace(f, price=float(fallback_px))
        log.write('fill' if f.ok else 'error', code=code, side=side, qty=qty,
                  price=f.price, ok=f.ok, reason=f.reason)
        if not f.ok:
            fails.append(f)
            notifier.send(f'❌ 주문 실패: {code} {side} {qty} — {f.reason}')
            return False
        fills.append(f)
        if side == 'SELL' and code in state['positions']:
            del state['positions'][code]
        elif side == 'BUY' and code != CORE_CODE:
            state['positions'][code] = {'qty': qty, 'entry_price': f.price,
                                        'entry_date': today}
        return True

    # 1) SELL 먼저 (현금 확보)
    for i in [x for x in intents if x.side == 'SELL']:
        submit(i.code, 'SELL', state['positions'][i.code]['qty'],
              fallback_px=closes[i.code].iloc[-1])

    # 2) 월초 코어 리밸런싱
    if do_rebalance:
        try:
            core_qty, _ = snap.holdings.get(CORE_CODE, (0, 0.0))
            px = broker.current_price(CORE_CODE)
            order = core_rebalance(snap.total, core_qty * px, px)
            ok = submit(order.code, order.side, order.qty, fallback_px=px) if order else True
            if ok:
                state['last_rebal_ym'] = today[:7]  # 실패 시 미갱신 -> 다음 실행에서 재시도
            log.write('rebalance', ym=today[:7], order=bool(order), ok=bool(ok))
        except Exception as e:
            log.write('error', msg=f'리밸런싱 실패: {e}')
            notifier.send(f'❌ 리밸런싱 실패: {e}')

    # 3) BUY — 슬롯/현금은 SELL·코어리밸 체결분을 매 반복 재계산해 반영 (이중차감 없음)
    budget = slot_budget(snap.total)

    def available_cash():
        return snap.cash + sum(
            f.price * f.qty if f.side == 'SELL' else -f.price * f.qty
            for f in fills
        )

    for i in [x for x in intents if x.side == 'BUY']:
        if len(state['positions']) >= SLOTS:
            break
        px = closes[i.code].iloc[-1]
        qty = size_buy(budget, px, available_cash())
        submit(i.code, 'BUY', qty, fallback_px=px)

    state['last_trade_date'] = today
    summary = {'signals': len(intents), 'fills': len(fills), 'fails': len(fails),
               'positions': len(state['positions']), 'total': snap.total}
    notifier.send(f"📊 {today} 신호{summary['signals']} 체결{summary['fills']} "
                  f"실패{summary['fails']} 보유{summary['positions']} "
                  f"평가 {snap.total:,.0f}원")
    log.write('daily_summary', **summary)
    return summary
