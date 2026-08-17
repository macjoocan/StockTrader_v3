# -*- coding: utf-8 -*-
"""KIS 모의투자 스모크: 공식 REST 콜사이트 3곳 검증 (잔고/일봉/현재가 — 주문은 수동확인 후).
   응답 필드명(pdno/hldg_qty/dnca_tot_amt/stck_clpr/stck_prpr) 실서버 재확인 목적.
   사전조건: 환경변수로 export 필요 (스크립트는 os.environ만 읽음; docker-compose 경유 시
   env_file이 주입). 실행: python tools/smoke_kis.py"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parents[1]))

from broker.kis import KisBroker
from config import load_config

env = dict(os.environ)
env.setdefault('DATA_DIR', '.')
cfg = load_config(env)
b = KisBroker(cfg)

snap = b.balance()
print('잔고 OK:', snap.total, snap.cash, list(snap.holdings)[:3])
closes = b.daily_closes('005930', 260)
print('일봉 OK:', len(closes), '개, 최근', closes.index[-1], closes.iloc[-1])
print('현재가 OK:', b.current_price('005930'))
print('=== 3개 콜사이트 정상. 주문(market_order)은 모의계좌에서 1주 수동 스모크 권장 ===')
