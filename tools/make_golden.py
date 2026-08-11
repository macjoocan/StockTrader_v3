# -*- coding: utf-8 -*-
"""bt_rsi2.py(검증된 백테)로 골든 픽스처 생성. 개발머신 전용(d:/tmp/marcap 필요)."""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, r'd:\tmp')
import bt_rsi2  # noqa: E402

CODES = ['005930', '000660', '005380']  # 삼성전자/SK하이닉스/현대차
OUT = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures'
OUT.mkdir(parents=True, exist_ok=True)

daily = []
for y in range(2016, 2027):
    try:
        df = pd.read_parquet(rf'd:\tmp\marcap\marcap-{y}.parquet',
                             columns=['Date', 'Code', 'Close', 'Stocks', 'Market'])
    except FileNotFoundError:
        # d:/tmp/marcap에 2016년 파일이 없음 (2017~2026만 존재) — bt_kospi_mom.main()과
        # 동일하게 없는 연도는 건너뜀 (원본 로직/상수 변경 아님)
        continue
    daily.append(df[df['Code'].isin(CODES)])
daily = pd.concat(daily, ignore_index=True)

closes_map, trades_map = {}, {}
for code in CODES:
    g = daily[daily['Code'] == code].sort_values('Date').set_index('Date')
    adj = bt_rsi2.adjust_close(g)
    closes_map[code] = adj
    trades = bt_rsi2.trades_from_signals(adj, thr=10.0)
    trades_map[code] = [
        {'entry': str(t[0].date()), 'exit': str(t[1].date()), 'ret': round(t[2], 10)}
        for t in trades if t[1] >= pd.Timestamp('2018-01-01')
    ]

pd.DataFrame(closes_map).to_csv(OUT / 'golden_closes.csv')
(OUT / 'golden_trades.json').write_text(json.dumps(trades_map, indent=1))
print({k: len(v) for k, v in trades_map.items()})
