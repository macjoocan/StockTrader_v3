from portfolio.allocator import CORE_CODE, core_rebalance, size_buy, slot_budget
from reconcile import reconcile
from signal_engine.strategy import scan

SLOTS = 4


def run_daily(broker, universe, state, today, log, notifier, do_rebalance):
    snap = broker.balance()
    new_state, warns = reconcile(state, snap, CORE_CODE)
    state['positions'] = new_state['positions']
    for w in warns:
        log.write('reconcile', msg=w)
        notifier.send(f'⚠️ {w}')

    closes = {}
    for code in universe:
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

    def submit(code, side, qty, entry_price=None):
        if qty <= 0:
            return
        f = broker.market_order(code, side, qty)
        log.write('fill' if f.ok else 'error', code=code, side=side, qty=qty,
                  price=f.price, ok=f.ok, reason=f.reason)
        if not f.ok:
            fails.append(f)
            notifier.send(f'❌ 주문 실패: {code} {side} {qty} — {f.reason}')
            return
        fills.append(f)
        if side == 'SELL' and code in state['positions']:
            del state['positions'][code]
        elif side == 'BUY' and code != CORE_CODE:
            price = f.price if f.price > 0 else float(closes[code].iloc[-1])
            state['positions'][code] = {'qty': qty, 'entry_price': price,
                                        'entry_date': today}

    # 1) SELL 먼저 (현금 확보)
    for i in [x for x in intents if x.side == 'SELL']:
        submit(i.code, 'SELL', state['positions'][i.code]['qty'])

    # 2) 월초 코어 리밸런싱
    if do_rebalance:
        try:
            core_qty, core_px = snap.holdings.get(CORE_CODE, (0, 0.0))
            px = broker.current_price(CORE_CODE)
            order = core_rebalance(snap.total, core_qty * px, px)
            if order:
                submit(order.code, order.side, order.qty)
            state['last_rebal_ym'] = today[:7]
            log.write('rebalance', ym=today[:7], order=bool(order))
        except Exception as e:
            log.write('error', msg=f'리밸런싱 실패: {e}')
            notifier.send(f'❌ 리밸런싱 실패: {e}')

    # 3) BUY (슬롯 재계산)
    budget = slot_budget(snap.total)
    cash = snap.cash + sum(f.price * f.qty for f in fills if f.side == 'SELL')
    for i in [x for x in intents if x.side == 'BUY']:
        if len(state['positions']) >= SLOTS:
            break
        px = closes[i.code].iloc[-1]
        qty = size_buy(budget, px, cash)
        submit(i.code, 'BUY', qty)
        if qty > 0:
            cash -= px * qty

    state['last_trade_date'] = today
    summary = {'signals': len(intents), 'fills': len(fills), 'fails': len(fails),
               'positions': len(state['positions']), 'total': snap.total}
    notifier.send(f"📊 {today} 신호{summary['signals']} 체결{summary['fills']} "
                  f"실패{summary['fails']} 보유{summary['positions']} "
                  f"평가 {snap.total:,.0f}원")
    log.write('daily_summary', **summary)
    return summary
