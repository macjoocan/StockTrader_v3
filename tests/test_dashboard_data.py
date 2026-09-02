from datetime import date, datetime, timezone, timedelta

import numpy as np
import pandas as pd

from dashboard_data import (KST, enrich_positions, gate_status, in_bot_window,
                            indexed_pair, ops_report, radar_rows,
                            realized_trades, token_file_fresh)


def series(vals, end='2026-08-21'):
    idx = pd.bdate_range(end=end, periods=len(vals))
    return pd.Series([float(v) for v in vals], index=idx)


def test_enrich_positions_pnl_and_exit_distance():
    positions = {'068270': {'qty': 19, 'entry_price': 195200.0, 'entry_date': '2026-08-18'}}
    closes = {'068270': series([200000, 198000, 196000, 194000, 192300])}
    rows = enrich_positions(positions, closes, today=date(2026, 8, 22))
    r = rows[0]
    assert r['cur'] == 192300.0
    assert round(r['pnl']) == round((192300 - 195200) * 19)
    assert r['pnl_pct'] < 0
    assert r['days'] == 4
    assert r['sma5_dist'] < 0  # 하락 추세라 SMA5 아래 = 청산조건 미충족


def test_radar_sorted_by_rsi_and_signal_flag():
    n = 250
    up = list(np.linspace(100, 130, n))
    dip = up[:-2] + [up[-3] * 0.97, up[-3] * 0.94]  # 이틀 급락 -> RSI2 극저, SMA200 위
    flat_down = list(np.linspace(130, 100, n - 1)) + [102.0]  # SMA200 아래, 막날 반등(RSI2 상승)
    closes = {'AAA': series(up), 'BBB': series(dip), 'CCC': series(flat_down)}
    rows = radar_rows(closes, holdings={'CCC'})
    assert rows[0]['code'] == 'BBB' and rows[0]['signal'] is True
    aaa = next(r for r in rows if r['code'] == 'AAA')
    assert aaa['signal'] is False and aaa['above_sma200'] is True
    ccc = next(r for r in rows if r['code'] == 'CCC')
    assert ccc['holding'] is True and ccc['above_sma200'] is False


def test_indexed_pair_base_100_and_live_override():
    eq = [('2026-08-18', 50000000.0), ('2026-08-19', 50500000.0)]
    kodex = series([108000, 109080], end='2026-08-19')  # +1%
    out = indexed_pair(eq, kodex, live_point=('2026-08-19', 51000000.0))
    assert out['acct'][0] == ('2026-08-18', 100.0)
    assert round(out['acct'][-1][1], 1) == 102.0  # live가 같은 날 요약을 덮음
    assert round(out['kodex'][-1][1], 1) == 101.0


def test_ops_report_counts():
    events = [
        {'kind': 'daily_summary', 'ts': '2026-08-18T15:23:00+09:00', 'signals': 1},
        {'kind': 'fill', 'ts': '2026-08-18T15:23:00+09:00', 'ok': True},
        {'kind': 'fill', 'ts': '2026-08-18T15:23:10+09:00', 'ok': False},
        {'kind': 'error', 'ts': '2026-08-20T15:21:00+09:00', 'msg': '일일작업 3회 실패'},
    ]
    r = ops_report(events)
    assert r['summary_days'] == 1 and r['skip_days'] == 1
    assert r['fills_ok'] == 1 and r['fills_fail'] == 1
    assert r['first_day'] == '2026-08-18' and r['last_day'] == '2026-08-20'
    assert '3회 실패' in r['last_error']


def test_realized_trades_fifo_excludes_core():
    events = [
        {'kind': 'fill', 'ts': '2026-08-18T15:23:00+09:00', 'code': '069500',
         'side': 'BUY', 'qty': 322, 'price': 108375.0, 'ok': True},
        {'kind': 'fill', 'ts': '2026-08-18T15:23:00+09:00', 'code': '068270',
         'side': 'BUY', 'qty': 19, 'price': 195200.0, 'ok': True},
        {'kind': 'fill', 'ts': '2026-08-25T15:23:00+09:00', 'code': '068270',
         'side': 'SELL', 'qty': 19, 'price': 200000.0, 'ok': True},
    ]
    trades = realized_trades(events)
    assert len(trades) == 1
    t = trades[0]
    assert t['code'] == '068270' and t['qty'] == 19
    assert round(t['pnl']) == round((200000 - 195200) * 19)
    assert t['buy_day'] == '2026-08-18' and t['sell_day'] == '2026-08-25'


def test_valuation_row_parses_and_defends():
    from dashboard_data import valuation_row
    v = valuation_row({'stck_prpr': '192300', 'per': '58.30', 'pbr': '0',
                       'w52_hgpr': '215000', 'w52_lwpr': '140000', 'eps': ''})
    assert v['cur'] == 192300.0 and v['per'] == 58.3
    assert v['pbr'] is None and v['eps'] is None  # '0'/빈값 -> None
    assert 0 < v['w52_band'] < 1
    empty = valuation_row({})
    assert empty['cur'] is None and empty['w52_band'] is None


def test_fin_rows_labels_and_missing():
    from dashboard_data import fin_rows
    rows = fin_rows([{'stac_yymm': '202512', 'roe_val': '8.1', 'lblt_rate': ''}])
    assert rows[0]['결산'] == '202512' and rows[0]['ROE%'] == '8.1'
    assert rows[0]['부채비율%'] == '—'
    assert fin_rows(None) == []


def test_factor_ranking_orders_and_defends():
    from dashboard_data import factor_ranking
    quotes = {
        'GOOD': {'per': '5.0', 'pbr': '0.8'},    # 싸다
        'MID':  {'per': '15.0', 'pbr': '1.5'},
        'EXP':  {'per': '60.0', 'pbr': '6.0'},   # 비싸다
        'LOSS': {'per': '-8.0', 'pbr': '2.0'},   # 적자 -> PER 결측 처리
    }
    fin = {
        'GOOD': [{'roe_val': '15.0', 'lblt_rate': '40'}],   # 퀄리티 최상
        'MID':  [{'roe_val': '8.0', 'lblt_rate': '90'}],
        'EXP':  [{'roe_val': '3.0', 'lblt_rate': '150'}],
        'LOSS': [{'roe_val': '-5.0', 'lblt_rate': '200'}],  # 음수 ROE 허용(최하)
    }
    rows = factor_ranking(quotes, fin)
    assert rows[0]['code'] == 'GOOD' and rows[0]['rank'] == 1
    assert rows[0]['total'] == 100  # 전 지표 1위
    loss = next(r for r in rows if r['code'] == 'LOSS')
    assert loss['per'] is None          # 적자 PER 제외
    assert loss['roe'] == -5.0          # 음수 ROE는 유지(랭크 최하)
    assert rows[-1]['code'] in ('EXP', 'LOSS')


def test_factor_ranking_skips_sparse_metric():
    from dashboard_data import factor_ranking
    quotes = {'A': {'per': '5'}, 'B': {'per': '10'}, 'C': {'per': '20'}}
    rows = factor_ranking(quotes, {})  # pbr/roe/debt 전부 결측 -> per만으로 랭킹
    assert rows[0]['code'] == 'A' and rows[0]['value'] == 100
    assert rows[0]['quality'] is None
    # 유효표본 2개뿐인 지표는 통째로 제외
    rows2 = factor_ranking({'A': {'per': '5'}, 'B': {'per': '10'}}, {})
    assert all(r['total'] is None for r in rows2)


def _diag_setup():
    n = 250
    up = series(list(np.linspace(100, 130, n)))          # SMA200 위, RSI 보통
    down = series(list(np.linspace(130, 100, n - 1)) + [102.0])  # SMA200 아래
    quotes = {
        'GOOD': {'stck_prpr': str(up.iloc[-1]), 'per': '5', 'pbr': '0.8', 'hts_frgn_ehrt': '30'},
        'DEBTY': {'stck_prpr': str(up.iloc[-1]), 'per': '8', 'pbr': '1.0'},
        'EXPS': {'stck_prpr': str(up.iloc[-1]), 'per': '80', 'pbr': '9.0'},
        'WEAK': {'stck_prpr': str(down.iloc[-1]), 'per': '15', 'pbr': '2.0'},
    }
    fin = {
        'GOOD': [{'roe_val': '15', 'lblt_rate': '40'}],
        'DEBTY': [{'roe_val': '10', 'lblt_rate': '350'}],   # 재무 주의
        'EXPS': [{'roe_val': '9', 'lblt_rate': '80'}],
        'WEAK': [{'roe_val': '5', 'lblt_rate': '90'}],
    }
    closes = {'GOOD': up, 'DEBTY': up, 'EXPS': up, 'WEAK': down}
    return quotes, fin, closes


def test_diagnose_cards_rules():
    from dashboard_data import diagnose_cards
    quotes, fin, closes = _diag_setup()
    cards = {c['code']: c for c in diagnose_cards(quotes, fin, closes, holdings={'GOOD'})}
    assert cards['DEBTY']['grade'] == '재무 주의' and '부채비율' in cards['DEBTY']['reasons'][0]
    assert cards['GOOD']['grade'] == '저평가·우량' and cards['GOOD']['holding'] is True
    assert cards['EXPS']['grade'] == '고평가'
    assert cards['WEAK']['grade'] == '추세 약세' and cards['WEAK']['above200'] is False


def test_diagnose_cards_sorted_by_total():
    from dashboard_data import diagnose_cards
    quotes, fin, closes = _diag_setup()
    cards = diagnose_cards(quotes, fin, closes, holdings=set())
    assert cards[0]['code'] == 'GOOD'  # 종합 최고가 앞


def test_parse_dart_corp_xml():
    from dashboard_data import parse_dart_corp_xml
    xml = b'''<?xml version="1.0" encoding="UTF-8"?><result>
    <list><corp_code>00126380</corp_code><corp_name>\xec\x82\xbc\xec\x84\xb1\xec\xa0\x84\xec\x9e\x90</corp_name><stock_code>005930</stock_code></list>
    <list><corp_code>00999999</corp_code><corp_name>X</corp_name><stock_code></stock_code></list>
    <list><corp_code>00421045</corp_code><corp_name>\xec\x85\x80\xed\x8a\xb8\xeb\xa6\xac\xec\x98\xa8</corp_name><stock_code>068270</stock_code></list>
    </result>'''
    m = parse_dart_corp_xml(xml, {'005930', '068270'})
    assert m == {'005930': '00126380', '068270': '00421045'}


def test_parse_dart_list_builds_urls():
    from dashboard_data import parse_dart_list
    data = {'status': '000', 'list': [
        {'rcept_no': '20260826000123', 'report_nm': '주요사항보고서',
         'rcept_dt': '20260826', 'flr_nm': '셀트리온'},
        {'rcept_no': '20260820000456', 'report_nm': '반기보고서',
         'rcept_dt': '20260820', 'flr_nm': '셀트리온'},
    ]}
    rows = parse_dart_list(data)
    assert len(rows) == 2
    assert rows[0]['url'].endswith('rcpNo=20260826000123')
    assert rows[0]['title'] == '주요사항보고서'
    assert parse_dart_list({'status': '013'}) == []


def test_naver_news_endpoint_hub_first_then_legacy():
    from dashboard_data import (NAVER_HUB_NEWS_URL, NAVER_NEWS_URL,
                                naver_news_endpoint)
    url, h = naver_news_endpoint({'NAVER_HUB_KEY_ID': 'i', 'NAVER_HUB_KEY': 'k',
                                  'NAVER_CLIENT_ID': 'x', 'NAVER_CLIENT_SECRET': 'y'})
    assert url == NAVER_HUB_NEWS_URL and h['X-NCP-APIGW-API-KEY-ID'] == 'i'
    url2, h2 = naver_news_endpoint({'NAVER_CLIENT_ID': 'x', 'NAVER_CLIENT_SECRET': 'y'})
    assert url2 == NAVER_NEWS_URL and h2['X-Naver-Client-Id'] == 'x'
    assert naver_news_endpoint({}) is None
    assert naver_news_endpoint({'NAVER_HUB_KEY_ID': 'i'}) is None  # 짝 없으면 무효


def test_parse_naver_news_strips_and_dates():
    from dashboard_data import parse_naver_news
    data = {'items': [
        {'title': '<b>셀트리온</b>, 키트루다 시밀러 &quot;허가 신청&quot;',
         'originallink': 'https://news.example.com/1',
         'link': 'https://n.naver.com/1',
         'pubDate': 'Wed, 27 Aug 2026 09:30:00 +0900'},
        {'title': '후속 기사', 'link': 'https://n.naver.com/2', 'pubDate': 'bad-date'},
    ]}
    rows = parse_naver_news(data)
    assert rows[0]['title'] == '셀트리온, 키트루다 시밀러 "허가 신청"'
    assert rows[0]['url'] == 'https://news.example.com/1'  # 원문 우선
    assert rows[0]['date'] == '08-27 09:30'
    assert rows[1]['url'] == 'https://n.naver.com/2' and rows[1]['date'] == ''
    assert parse_naver_news({}) == []


def test_chart_payload_indicators_and_events():
    from dashboard_data import chart_payload
    n = 30
    idx = pd.bdate_range(end='2026-08-28', periods=n)
    df = pd.DataFrame({'open': 100.0, 'high': 102.0, 'low': 99.0,
                       'close': np.linspace(100, 110, n), 'volume': 1000.0}, index=idx)
    news = [{'iso': f'{idx[-1]:%Y-%m-%d}', 'title': '기사A', 'url': 'u', 'date': 'x'},
            {'iso': '2020-01-01', 'title': '범위밖', 'url': 'u', 'date': 'x'}]
    dart = [{'date': f'{idx[-2]:%Y%m%d}', 'title': '공시B', 'url': 'u'}]
    p = chart_payload(df, news, dart)
    assert len(p['d']) == n == len(p['c']) == len(p['rsi']) == len(p['sma20'])
    assert p['sma20'][10] is None and p['sma20'][25] is not None  # 워밍업 None
    assert p['sma200'][-1] is None  # 데이터 부족
    evs = {(e['k'], e['t']) for e in p['ev']}
    assert ('news', '기사A') in evs and ('dart', '공시B') in evs
    assert not any(e['t'] == '범위밖' for e in p['ev'])
    assert chart_payload(None) == {} and chart_payload(pd.DataFrame()) == {}


def test_build_report_weak_regime_and_narratives():
    from dashboard_data import build_report
    eq = [('2026-08-26', 50000000.0), ('2026-09-01', 49600000.0), ('2026-09-02', 47980000.0)]
    kodex = series([110000, 109000, 105000], end='2026-09-02')
    radar = [
        {'code': 'A', 'rsi2': 2.2, 'above_sma200': False, 'signal': False, 'holding': True,
         'cur': 1, 'chg': 0, 'sma5_dist': None},
        {'code': 'B', 'rsi2': 3.4, 'above_sma200': False, 'signal': False, 'holding': False,
         'cur': 1, 'chg': 0},
        {'code': 'C', 'rsi2': 15.0, 'above_sma200': True, 'signal': False, 'holding': False,
         'cur': 1, 'chg': 0},
    ]
    pos_rows = [{'code': 'A', 'qty': 8, 'entry': 463000.0, 'entry_date': '2026-08-26',
                 'days': 7, 'cur': 415500.0, 'pnl': -380000.0, 'pnl_pct': -0.103,
                 'sma5_dist': -0.02}]
    events = [
        {'kind': 'daily_summary', 'ts': '2026-09-02T15:22:00+09:00', 'signals': 0},
        {'kind': 'fill', 'ts': '2026-08-26T15:29:00+09:00', 'code': 'X', 'side': 'BUY',
         'qty': 19, 'price': 100.0, 'ok': True},
        {'kind': 'fill', 'ts': '2026-09-01T15:29:00+09:00', 'code': 'X', 'side': 'SELL',
         'qty': 19, 'price': 110.0, 'ok': True},
    ]
    news = {'A': [{'title': 'A사 신제품', 'url': 'u', 'date': 'x', 'iso': '2026-09-02'}]}
    r = build_report(eq=eq, kodex=kodex, live=None, state={}, events=events,
                     radar=radar, pos_rows=pos_rows, news=news,
                     names={'A': '에이사', 'C': '씨사'}, days=7)
    assert r['regime'] == '약세 조정' and '방어 모드' in r['regime_p']
    assert '-4.0' in r['perf_p']  # 계좌 -4.04%
    assert 'KODEX' in r['perf_p']
    assert r['realized'] == (110.0 - 100.0) * 19
    assert '에이사' in r['pos_list'][0] and '반등 대기' in r['pos_list'][0]
    assert 'A사 신제품' in r['pos_list'][0]
    assert r['watch_list'] == ['씨사 RSI2 15.0']  # SMA200 위+미보유만


def test_build_report_bull_regime():
    from dashboard_data import build_report
    radar = [{'code': c, 'rsi2': 50.0, 'above_sma200': True, 'signal': False,
              'holding': False, 'cur': 1, 'chg': 0} for c in 'ABCDEFGHIJ']
    r = build_report(eq=[('2026-09-01', 100.0), ('2026-09-02', 101.0)], kodex=None,
                     live=None, state={}, events=[], radar=radar, pos_rows=[],
                     news={}, names={}, days=0)
    assert r['regime'] == '상승 추세' and '전체 기간' == r['label']


def test_parse_investor():
    from dashboard_data import parse_investor
    rows = [{'stck_bsop_date': '20260902', 'frgn_ntby_qty': '-12345',
             'orgn_ntby_qty': '6789', 'prsn_ntby_qty': '5556'},
            {'stck_bsop_date': 'bad'}]
    out = parse_investor(rows)
    assert out == {'2026-09-02': {'frgn': -12345.0, 'orgn': 6789.0}}
    assert parse_investor(None) == {}


def test_chart_payload_investor_alignment():
    from dashboard_data import chart_payload
    idx = pd.bdate_range(end='2026-09-02', periods=5)
    df = pd.DataFrame({'open': 100.0, 'high': 101.0, 'low': 99.0,
                       'close': 100.0, 'volume': 10.0}, index=idx)
    inv = {f'{idx[-1]:%Y-%m-%d}': {'frgn': -100.0, 'orgn': 50.0}}
    p = chart_payload(df, investor=inv)
    assert p['frgn'][-1] == -100.0 and p['orgn'][-1] == 50.0
    assert p['frgn'][0] is None  # 데이터 없는 날은 None


def test_gate_status():
    g = gate_status(date(2026, 8, 22))
    assert g['d_left'] == 23 and g['over'] is False


def test_in_bot_window():
    assert in_bot_window(datetime(2026, 8, 21, 15, 20, tzinfo=KST)) is True   # 금 15:20
    assert in_bot_window(datetime(2026, 8, 21, 15, 36, tzinfo=KST)) is False
    assert in_bot_window(datetime(2026, 8, 22, 15, 20, tzinfo=KST)) is False  # 토


def test_token_file_fresh(tmp_path):
    import json as j
    import time as t
    assert token_file_fresh(tmp_path, 'paper', t.time()) is False  # 파일 없음
    (tmp_path / 'kis_token_paper.json').write_text(
        j.dumps({'token': 'X', 'expires_at': t.time() + 3600}), encoding='utf-8')
    assert token_file_fresh(tmp_path, 'paper', t.time()) is True
    assert token_file_fresh(tmp_path, 'paper', t.time() + 3000) is False  # 잔여 10분뿐