import json

from dashboard import equity_series, load_events, load_positions, svg_line


def test_equity_series_dedup_and_sort():
    events = [
        {'kind': 'daily_summary', 'ts': '2026-08-19T15:30:00+09:00', 'total': 50100000},
        {'kind': 'daily_summary', 'ts': '2026-08-18T15:30:00+09:00', 'total': 50000000},
        {'kind': 'daily_summary', 'ts': '2026-08-18T16:00:00+09:00', 'total': 50050000},  # 같은 날 마지막 값
        {'kind': 'signal', 'ts': '2026-08-18T15:19:00+09:00', 'code': 'X'},  # 무시
    ]
    assert equity_series(events) == [('2026-08-18', 50050000.0), ('2026-08-19', 50100000.0)]


def test_load_events_skips_bad_lines(tmp_path):
    (tmp_path / 'events_2026-08-18.jsonl').write_text(
        '{"kind":"signal","ts":"t"}\nnot-json\n{"kind":"fill"}\n', encoding='utf-8')
    ev = load_events(tmp_path)
    assert [e['kind'] for e in ev] == ['signal', 'fill']


def test_load_positions_default_on_missing_or_bad(tmp_path):
    assert load_positions(tmp_path)['positions'] == {}
    (tmp_path / 'positions.json').write_text('[]', encoding='utf-8')  # wrong shape
    assert load_positions(tmp_path)['positions'] == {}


def test_svg_line_needs_two_points():
    assert '데이터 2일' in svg_line([('2026-08-18', 1.0)])
    out = svg_line([('2026-08-18', 100.0), ('2026-08-19', 110.0)])
    assert '<polyline' in out and 'data-series' in out


def test_svg_line_flat_series_no_div_zero():
    out = svg_line([('2026-08-18', 100.0), ('2026-08-19', 100.0)])
    assert '<polyline' in out


def test_page_renders(tmp_path, monkeypatch):
    import dashboard
    monkeypatch.setattr(dashboard, 'DATA_DIR', tmp_path)
    (tmp_path / 'positions.json').write_text(json.dumps(
        {'positions': {'005930': {'qty': 3, 'entry_price': 274500, 'entry_date': '2026-08-18'}},
         'last_trade_date': '2026-08-18', 'last_rebal_ym': '2026-08'}), encoding='utf-8')
    (tmp_path / 'events_2026-08-18.jsonl').write_text(
        '{"kind":"daily_summary","ts":"2026-08-18T15:30:00+09:00","total":50000000}\n',
        encoding='utf-8')
    page = dashboard.render_page()
    assert '005930' in page and '50,000,000' in page and '모의투자' in page