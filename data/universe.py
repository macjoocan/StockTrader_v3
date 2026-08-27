# -*- coding: utf-8 -*-
"""연 1회: 연초 KOSPI 보통주 시총 top20 유니버스 생성 (개발머신, marcap 필요)"""
import json
import sys
from pathlib import Path

import pandas as pd


# 참고: 2026-08-22부터 universe json에 names(코드->종목명, 코어 포함) 필드가 추가됨.
# 재생성 시 아래 main()이 names까지 채운다.
def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    df = pd.read_parquet(rf'd:\tmp\marcap\marcap-{year}.parquet',
                         columns=['Date', 'Code', 'Name', 'Marcap', 'Market'])
    df = df[(df['Market'] == 'KOSPI') & df['Code'].str.endswith('0')]
    first = df[df['Date'] == df['Date'].min()]
    top = first.nlargest(20, 'Marcap')
    codes = top['Code'].tolist()
    names = dict(top[['Code', 'Name']].values)
    names['069500'] = 'KODEX 200'  # 코어 ETF (marcap에 없음)
    out = Path(__file__).parent / f'universe_{year}.json'
    out.write_text(json.dumps({'year': year, 'codes': codes, 'names': names},
                              ensure_ascii=False, indent=1), encoding='utf-8')
    print(year, codes)


if __name__ == '__main__':
    main()
