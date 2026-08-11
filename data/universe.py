# -*- coding: utf-8 -*-
"""연 1회: 연초 KOSPI 보통주 시총 top20 유니버스 생성 (개발머신, marcap 필요)"""
import json
import sys
from pathlib import Path

import pandas as pd

YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
df = pd.read_parquet(rf'd:\tmp\marcap\marcap-{YEAR}.parquet',
                     columns=['Date', 'Code', 'Marcap', 'Market'])
df = df[(df['Market'] == 'KOSPI') & df['Code'].str.endswith('0')]
first = df[df['Date'] == df['Date'].min()]
codes = first.nlargest(20, 'Marcap')['Code'].tolist()
out = Path(__file__).parent / f'universe_{YEAR}.json'
out.write_text(json.dumps({'year': YEAR, 'codes': codes}, indent=1), encoding='utf-8')
print(YEAR, codes)
