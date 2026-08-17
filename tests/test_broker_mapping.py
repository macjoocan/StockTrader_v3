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
