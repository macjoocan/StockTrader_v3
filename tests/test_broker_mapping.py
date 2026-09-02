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


def test_ohlcv_from_chart_full_and_fallback():
    from broker.kis import ohlcv_from_chart
    rows = [
        {'stck_bsop_date': '20260810', 'stck_oprc': '100', 'stck_hgpr': '110',
         'stck_lwpr': '95', 'stck_clpr': '105', 'acml_vol': '1000'},
        {'stck_bsop_date': '20260811', 'stck_clpr': '107'},  # o/h/l/v 누락 -> 폴백
    ]
    df = ohlcv_from_chart(rows)
    assert list(df.columns) == ['open', 'high', 'low', 'close', 'volume']
    assert df.iloc[0].tolist() == [100.0, 110.0, 95.0, 105.0, 1000.0]
    assert df.iloc[1]['open'] == 107.0 and df.iloc[1]['volume'] == 0.0
    assert ohlcv_from_chart([]).empty


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


def test_request_retries_transient_5xx(monkeypatch):
    import broker.kis as kis_mod
    b = KisBroker.__new__(KisBroker)
    b.mode = 'paper'
    b.appkey, b.secret = 'k', 's'
    b._token = lambda: 'T'
    monkeypatch.setattr(kis_mod.time, 'sleep', lambda *_: None)  # 백오프/유량지연 스킵

    class Resp:
        def __init__(self, status, body=None):
            self.status_code = status
            self._body = body or {}
            self.headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    responses = [Resp(500), Resp(200, {'rt_cd': '0', 'output': {'x': 1}})]
    monkeypatch.setattr(kis_mod.requests, 'get',
                        lambda *a, **k: responses.pop(0))
    data = b._request('GET', '/p', 'TR')  # 1차 500 -> 재시도 성공
    assert data['output'] == {'x': 1}


def test_request_business_error_no_retry(monkeypatch):
    import broker.kis as kis_mod
    b = KisBroker.__new__(KisBroker)
    b.mode = 'paper'
    b.appkey, b.secret = 'k', 's'
    b._token = lambda: 'T'
    monkeypatch.setattr(kis_mod.time, 'sleep', lambda *_: None)
    calls = []

    class Resp:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {'rt_cd': '1', 'msg_cd': 'OPSQ2000', 'msg1': 'INVALID_CHECK_ACNO'}

    monkeypatch.setattr(kis_mod.requests, 'get',
                        lambda *a, **k: calls.append(1) or Resp())
    import pytest
    with pytest.raises(RuntimeError, match='INVALID_CHECK_ACNO'):
        b._request('GET', '/p', 'TR')
    assert len(calls) == 1  # 비즈니스 거부는 재시도 없음


def _token_broker(tmp_path, expires_in_sec):
    import time as _t
    b = KisBroker.__new__(KisBroker)
    b.mode, b.appkey, b.secret = 'paper', 'k', 's'
    b.data_dir = tmp_path
    b._token_memo = {'token': 'OLD', 'expires_at': _t.time() + expires_in_sec}
    return b


def test_ensure_token_noop_when_fresh(tmp_path, monkeypatch):
    import broker.kis as kis_mod
    b = _token_broker(tmp_path, expires_in_sec=7200)  # 2시간 남음
    monkeypatch.setattr(kis_mod.requests, 'post',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('재발급 불필요')))
    assert b.ensure_token(min_left=3600) is True  # 발급 호출 없이 통과


def test_ensure_token_reissues_when_expiring(tmp_path, monkeypatch):
    import broker.kis as kis_mod

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {'access_token': 'NEW', 'expires_in': 86400}

    b = _token_broker(tmp_path, expires_in_sec=1800)  # 30분 남음 -> 재발급 대상
    monkeypatch.setattr(kis_mod.requests, 'post', lambda *a, **k: Resp())
    assert b.ensure_token(min_left=3600) is True
    assert b._token_memo['token'] == 'NEW'


def test_ensure_token_swallows_failure(tmp_path, monkeypatch):
    import broker.kis as kis_mod
    b = _token_broker(tmp_path, expires_in_sec=1800)
    monkeypatch.setattr(kis_mod.requests, 'post',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('timeout')))
    assert b.ensure_token(min_left=3600) is False  # 예외 삼킴, 다음 heartbeat 재시도


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


def test_us_order_tr_mapping_asymmetric_paper_sell():
    # 공식 예제: 미국 매도 모의 TR은 VTTT1001U (매수 VTTT1002U와 비대칭 — 오타 아님)
    assert KisBroker.US_ORDER_TR[('paper', 'BUY')] == 'VTTT1002U'
    assert KisBroker.US_ORDER_TR[('paper', 'SELL')] == 'VTTT1001U'
    assert KisBroker.US_ORDER_TR[('live', 'SELL')] == 'TTTT1006U'


def test_us_limit_order_control_flow():
    b = KisBroker.__new__(KisBroker)
    b.mode, b.cano, b.prdt = 'paper', '12345678', '01'
    sent = {}

    def fake_request(method, path, tr_id, params=None, body=None, tr_cont=''):
        sent.update({'tr': tr_id, 'body': body})
        return {'rt_cd': '0', 'output': {'ODNO': '1'}}

    b._request = fake_request
    f = b.us_limit_order('SPY', 'AMS', 'BUY', 3, 772.73)
    assert f.ok and f.price == 772.73
    assert sent['tr'] == 'VTTT1002U'
    assert sent['body']['OVRS_ORD_UNPR'] == '772.73' and sent['body']['ORD_DVSN'] == '00'
    b._request = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('장운영시간'))
    f2 = b.us_limit_order('SPY', 'AMS', 'SELL', 1, 700.0)
    assert f2.ok is False and '장운영시간' in f2.reason
