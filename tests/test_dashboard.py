import json
from types import SimpleNamespace

import pandas as pd

from dashboard import equity_series, load_events, load_positions, svg_chart


def test_equity_series_dedup_and_sort():
    events = [
        {'kind': 'daily_summary', 'ts': '2026-08-19T15:30:00+09:00', 'total': 50100000},
        {'kind': 'daily_summary', 'ts': '2026-08-18T15:30:00+09:00', 'total': 50000000},
        {'kind': 'daily_summary', 'ts': '2026-08-18T16:00:00+09:00', 'total': 50050000},
        {'kind': 'signal', 'ts': '2026-08-18T15:19:00+09:00', 'code': 'X'},
    ]
    assert equity_series(events) == [('2026-08-18', 50050000.0), ('2026-08-19', 50100000.0)]


def test_load_events_skips_bad_lines(tmp_path):
    (tmp_path / 'events_2026-08-18.jsonl').write_text(
        '{"kind":"signal","ts":"t"}\nnot-json\n{"kind":"fill"}\n', encoding='utf-8')
    assert [e['kind'] for e in load_events(tmp_path)] == ['signal', 'fill']


def test_load_positions_default_on_missing_or_bad(tmp_path):
    assert load_positions(tmp_path)['positions'] == {}
    (tmp_path / 'positions.json').write_text('[]', encoding='utf-8')
    assert load_positions(tmp_path)['positions'] == {}


def test_svg_chart_two_series_with_legend():
    out = svg_chart(
        {'계좌': [('2026-08-18', 100.0), ('2026-08-19', 102.0)],
         'KODEX 200': [('2026-08-18', 100.0), ('2026-08-19', 101.0)]},
        {'계좌': '#3f7fc9', 'KODEX 200': '#bd8137'})
    assert out.count('<polyline') == 2
    assert '계좌' in out and 'KODEX 200' in out  # 범례 (2시리즈 필수)
    assert 'data-chart' in out and '#3f7fc9' in out


def test_svg_chart_needs_two_points():
    assert '2일 이상' in svg_chart({'계좌': [('2026-08-18', 100.0)]}, {})
    assert '2일 이상' in svg_chart({}, {})


def test_page_renders_with_fake_cache(tmp_path, monkeypatch):
    import dashboard
    monkeypatch.setattr(dashboard, 'DATA_DIR', tmp_path)
    (tmp_path / 'positions.json').write_text(json.dumps(
        {'positions': {'068270': {'qty': 19, 'entry_price': 195200, 'entry_date': '2026-08-18'}},
         'last_trade_date': '2026-08-21', 'last_rebal_ym': '2026-08'}), encoding='utf-8')
    (tmp_path / 'events_2026-08-18.jsonl').write_text(
        '{"kind":"daily_summary","ts":"2026-08-18T15:30:00+09:00","total":50000000,"signals":1}\n'
        '{"kind":"fill","ts":"2026-08-18T15:23:00+09:00","code":"068270","side":"BUY",'
        '"qty":19,"price":195200.0,"ok":true}\n', encoding='utf-8')

    idx = pd.bdate_range(end='2026-08-21', periods=250)
    mk = lambda base: pd.Series([base * (1 + i * 0.001) for i in range(250)], index=idx)  # noqa: E731
    cache = SimpleNamespace(
        snapshot={'ts': 'x', 'total': 50393970.0, 'cash': 11326710.0,
                  'holdings': {'069500': (322, 109980.0), '068270': (19, 192300.0)},
                  'closes': {'069500': mk(100000), '068270': mk(190000), '005930': mk(70000)}},
        status='테스트 캐시')
    page = dashboard.render_page(cache=cache)
    for expected in ('068270', '셀트리온', '/stock/068270', '50,393,970', 'KODEX 200',
                     '시그널 레이더', 'RSI2', '실현손익', '운영 리포트', 'D-', '테스트 캐시',
                     '팩터 랭킹', '매매 미연결'):
        assert expected in page, expected


def test_render_stock_detail(tmp_path, monkeypatch):
    import dashboard
    monkeypatch.setattr(dashboard, 'DATA_DIR', tmp_path)
    idx = pd.bdate_range(end='2026-08-21', periods=250)
    closes = pd.Series([190000.0 + i * 10 for i in range(250)], index=idx)
    cache = SimpleNamespace(
        snapshot={'quotes': {'068270': {
            'stck_prpr': '192300', 'prdy_ctrt': '-1.15', 'per': '58.3', 'pbr': '5.1',
            'eps': '3298', 'bps': '37699', 'hts_avls': '421000',
            'w52_hgpr': '215000', 'w52_lwpr': '140000', 'hts_frgn_ehrt': '21.5'}},
            'closes': {'068270': closes},
            'fin': {'_date': '2026-08-22', '068270': [
                {'stac_yymm': '202512', 'roe_val': '8.1', 'lblt_rate': '35.2',
                 'rsrv_rate': '5000', 'grs': '12.0', 'bsop_prfi_inrt': '20.1',
                 'eps': '3298', 'bps': '37699'}]}},
        status='ok')
    page = dashboard.render_stock('068270', cache=cache)
    for expected in ('셀트리온', '58.3', 'PBR', 'ROE%', '35.2', '52주 밴드',
                     '최근 60일', '참고용'):
        assert expected in page, expected


def test_stock_route_rejects_bad_code():
    import dashboard
    client = dashboard.app.test_client()
    assert client.get('/stock/../etc').status_code in (404, 400)
    assert client.get('/stock/abc123').status_code == 404