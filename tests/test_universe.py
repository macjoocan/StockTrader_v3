import json
from pathlib import Path


def test_universe_schema():
    p = Path(__file__).parents[1] / 'data' / 'universe_2026.json'
    u = json.loads(p.read_text(encoding='utf-8'))
    assert u['year'] == 2026
    assert len(u['codes']) == 20
    assert all(c.isdigit() and len(c) == 6 for c in u['codes'])
