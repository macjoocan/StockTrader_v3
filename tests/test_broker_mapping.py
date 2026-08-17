"""KIS 공식 REST 응답 매핑 순수함수 + market_order 제어흐름 테스트.

응답 필드명은 공식 예제 리포(koreainvestment/open-trading-api) 기준:
- 잔고: output1[].pdno/hldg_qty/prpr, output2[0].dnca_tot_amt/tot_evlu_amt
- 일봉: output2[].stck_bsop_date/stck_clpr
실제 서버 응답 재확인은 tools/smoke_kis.py (모의투자).
"""
from broker.kis import Fill, KisBroker, closes_from_chart, snapshot_from_balance


def test_snapshot_from_balance_real_fields():
    output1 = [
        {'pdno': '005930', 'hldg_qty': '3', 'prpr': '70000', 'prdt_name': '삼성전자'},
        {'pdno': '069500', 'hldg_qty': '10', 'prpr': '40000', 'prdt_name': 'KODEX 200'},
        # 당일 전량매도 잔고는 D-2까지 수량 0으로 잔존 (공식 문서) -> 제외돼야 함
        {'pdno': '000660', 'hldg_qty': '0', 'prpr': '200000', 'prdt_name': 'SK하이닉스'},
    ]
    output2 = [{'dnca_tot_amt': '500000', 'tot_evlu_amt': '1110000'}]
    s = snapshot_from_balance(output1, output2)
    assert s.cash == 500000.0
    assert s.holdings == {'005930': (3, 70000.0), '069500': (10, 40000.0)}
    assert s.total == 1110000.0


def test_snapshot_total_fallback_when_missing():
    output1 = [{'pdno': '005930', 'hldg_qty': '2', 'prpr': '70000'}]
    output2 = [{'dnca_tot_amt': '500000'}]  # tot_evlu_amt 누락 시 현금+평가 합산 폴백
    s = snapshot_from_balance(output1, output2)
    assert s.total == 500000.0 + 140000.0


def test_closes_from_chart_sorted_dedup_skips_blank():
    rows = [
        {'stck_bsop_date': '20260811', 'stck_clpr': '71000'},
        {'stck_bsop_date': '20260810', 'stck_clpr': '70000'},
        {'stck_bsop_date': '20260811', 'stck_clpr': '71000'},  # 중복
        {'stck_bsop_date': '', 'stck_clpr': ''},  # KIS 패딩 빈 행
    ]
    s = closes_from_chart(rows)
    assert list(s.values) == [70000.0, 71000.0]
    assert s.index[0] < s.index[1]


def make_broker(order_raises=False, price=71000.0, price_raises=False):
    """__init__(토큰 발급) 우회하고 I/O 지점만 페이크 주입"""
    b = KisBroker.__new__(KisBroker)
    b.mode = 'paper'
    b.cano, b.prdt = '12345678', '01'
    b.quote_calls = 0

    def fake_request(method, path, tr_id, params=None, body=None, tr_cont=''):
        if order_raises:
            raise RuntimeError('모의투자 장운영시간이 아닙니다')
        b.last_order = (tr_id, body)
        return {'rt_cd': '0', 'output': {'ODNO': '0000117057'}}

    def fake_price(code):
        b.quote_calls += 1
        if price_raises:
            raise RuntimeError('quote fail')
        return price

    b._request = fake_request
    b.current_price = fake_price
    return b


def test_market_order_success_uses_current_price():
    # KIS 주문응답엔 체결가가 없음(주문번호만) -> 항상 현재가로 기록
    b = make_broker()
    f = b.market_order('005930', 'BUY', 3)
    assert f == Fill('005930', 'BUY', 3, 71000.0, ok=True)
    assert b.quote_calls == 1
    tr_id, body = b.last_order
    assert tr_id == 'VTTC0012U'  # 모의 매수
    assert body['ORD_DVSN'] == '01' and body['ORD_UNPR'] == '0'  # 시장가
    assert body['ORD_QTY'] == '3' and body['PDNO'] == '005930'


def test_market_order_sell_tr_id():
    b = make_broker()
    b.market_order('005930', 'SELL', 2)
    assert b.last_order[0] == 'VTTC0011U'  # 모의 매도


def test_market_order_price_lookup_fail_stays_ok():
    b = make_broker(price_raises=True)
    f = b.market_order('005930', 'BUY', 3)
    assert f.ok is True and f.price == 0.0 and f.reason == 'price_lookup_failed'


def test_market_order_order_itself_fails_returns_not_ok():
    b = make_broker(order_raises=True)
    f = b.market_order('005930', 'BUY', 3)
    assert f.ok is False and '장운영시간' in f.reason
    assert b.quote_calls == 0  # 주문 실패 시 시세조회 안 함


def _chart_rows(dates):
    return [{'stck_bsop_date': d.strftime('%Y%m%d'), 'stck_clpr': '70000'} for d in dates]


def test_daily_closes_paginates_via_anchor():
    import pandas as pd
    page0 = pd.bdate_range(end='2026-08-10', periods=100)
    page1 = pd.bdate_range(end=page0[0] - pd.Timedelta(1, unit='D'), periods=100)
    b = KisBroker.__new__(KisBroker)
    b.mode = 'paper'
    calls = []

    def fake_request(method, path, tr_id, params=None, body=None, tr_cont=''):
        calls.append(dict(params))
        rows = _chart_rows(page0 if len(calls) == 1 else page1)
        return {'rt_cd': '0', 'output2': rows}

    b._request = fake_request
    s = b.daily_closes('005930', days=150)
    assert len(s) == 150
    assert s.index.is_monotonic_increasing and s.index.is_unique
    assert s.index[-1] == pd.Timestamp('2026-08-10')
    assert len(calls) == 2  # 100건 + 100건이면 150 충족 후 정지
    # 2번째 호출의 종료일 = 1페이지 최소일자 - 1일 (앵커 이동)
    expected_anchor = (page0[0] - pd.Timedelta(1, unit='D')).strftime('%Y%m%d')
    assert calls[1]['FID_INPUT_DATE_2'] == expected_anchor


def test_daily_closes_stops_on_empty_page():
    b = KisBroker.__new__(KisBroker)
    b.mode = 'paper'
    b._request = lambda *a, **k: {'rt_cd': '0', 'output2': []}
    s = b.daily_closes('005930', days=100)
    assert s.empty


def test_balance_continuation_accumulates_and_forwards_ctx():
    b = KisBroker.__new__(KisBroker)
    b.mode, b.cano, b.prdt = 'paper', '12345678', '01'
    calls = []

    def fake_request(method, path, tr_id, params=None, body=None, tr_cont=''):
        calls.append((dict(params), tr_cont))
        if len(calls) == 1:
            return {'rt_cd': '0', '_tr_cont': 'M',
                    'output1': [{'pdno': '005930', 'hldg_qty': '3', 'prpr': '70000'}],
                    'output2': [],
                    'ctx_area_fk100': 'FK1', 'ctx_area_nk100': 'NK1'}
        return {'rt_cd': '0', '_tr_cont': '',
                'output1': [{'pdno': '000660', 'hldg_qty': '1', 'prpr': '200000'}],
                'output2': [{'dnca_tot_amt': '500000', 'tot_evlu_amt': '910000'}]}

    b._request = fake_request
    s = b.balance()
    assert s.holdings == {'005930': (3, 70000.0), '000660': (1, 200000.0)}
    assert s.total == 910000.0 and s.cash == 500000.0
    assert calls[1][1] == 'N'  # 연속조회 헤더
    assert calls[1][0]['CTX_AREA_FK100'] == 'FK1'
    assert calls[1][0]['CTX_AREA_NK100'] == 'NK1'
