"""KIS 공식 Open API 직접 호출 어댑터 (REST).

공식 예제 리포(koreainvestment/open-trading-api, examples_llm) 패턴 기준:
- 도메인: 실전 openapi.koreainvestment.com:9443 / 모의 openapivts.koreainvestment.com:29443
- 토큰: POST /oauth2/tokenP (grant_type=client_credentials), 파일 캐시 (발급 제한 있음)
- TR: 잔고 TTTC8434R/VTTC8434R, 현금주문 매수 TTTC0012U/VTTC0012U·매도 TTTC0011U/VTTC0011U,
      현재가 FHKST01010100, 기간별시세 FHKST03010100 (실전/모의 동일)
- 일봉: 호출당 최대 100건 -> 페이지네이션. FID_ORG_ADJ_PRC="0" = 수정주가 (필수)
- 주문 응답에는 주문번호(ODNO)만 있고 체결가 없음 -> 체결가는 항상 현재가 폴백
- 응답 필드명(pdno/hldg_qty/prpr/dnca_tot_amt/tot_evlu_amt/stck_bsop_date/stck_clpr/stck_prpr)의
  실서버 재확인은 tools/smoke_kis.py (모의투자)
"""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

KST = timezone(timedelta(hours=9))

DOMAIN = {
    'live': 'https://openapi.koreainvestment.com:9443',
    'paper': 'https://openapivts.koreainvestment.com:29443',
}
# 유량 제한: 실전 20건/초, 모의 2건/초 (공식 문서) -> 호출 간 지연
CALL_DELAY = {'live': 0.06, 'paper': 0.55}

PATH_BALANCE = '/uapi/domestic-stock/v1/trading/inquire-balance'
PATH_ORDER = '/uapi/domestic-stock/v1/trading/order-cash'
PATH_PRICE = '/uapi/domestic-stock/v1/quotations/inquire-price'
PATH_CHART = '/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice'

TR_BALANCE = {'live': 'TTTC8434R', 'paper': 'VTTC8434R'}
TR_ORDER = {('live', 'BUY'): 'TTTC0012U', ('live', 'SELL'): 'TTTC0011U',
            ('paper', 'BUY'): 'VTTC0012U', ('paper', 'SELL'): 'VTTC0011U'}


@dataclass(frozen=True)
class Snapshot:
    total: float
    cash: float
    holdings: dict = field(default_factory=dict)  # code -> (qty, price)


@dataclass(frozen=True)
class Fill:
    code: str
    side: str  # 'BUY' | 'SELL'
    qty: int
    price: float
    ok: bool
    reason: str = ''


def snapshot_from_balance(output1: list, output2: list) -> Snapshot:
    """잔고조회(TTTC8434R) 응답 -> Snapshot. 수량 0 잔존행(당일 전량매도 D-2) 제외."""
    holdings = {}
    for row in output1 or []:
        qty = int(float(row.get('hldg_qty') or 0))
        if qty > 0:
            holdings[row['pdno']] = (qty, float(row.get('prpr') or 0))
    summary = (output2 or [{}])[0]
    cash = float(summary.get('dnca_tot_amt') or 0)
    total = float(summary.get('tot_evlu_amt') or 0)
    if total <= 0:  # 요약 누락 시 현금 + 보유평가 합산 폴백
        total = cash + sum(q * p for q, p in holdings.values())
    return Snapshot(total=total, cash=cash, holdings=holdings)


def closes_from_chart(rows: list) -> pd.Series:
    """기간별시세 output2 -> 날짜 오름차순 종가 Series. 빈 행(패딩)/중복 제거."""
    pairs = {}
    for row in rows or []:
        d, c = row.get('stck_bsop_date'), row.get('stck_clpr')
        if d and c:
            pairs[d] = float(c)
    if not pairs:
        return pd.Series(dtype=float)
    s = pd.Series(pairs)
    s.index = pd.to_datetime(s.index, format='%Y%m%d')
    return s.sort_index()


class KisBroker:
    """얇은 I/O 어댑터. KIS_MODE=paper(기본)|live — 모의/실전 도메인·TR·키 전환."""

    def __init__(self, env: dict):
        self.mode = (env.get('KIS_MODE') or 'paper').lower()
        if self.mode not in DOMAIN:
            raise ValueError(f'KIS_MODE는 paper|live: {self.mode}')
        if self.mode == 'live':
            self.appkey, self.secret = env['KIS_APPKEY'], env['KIS_SECRET']
        else:  # 모의: 전용 키 우선, 없으면 실전 키 (모의 키 미발급 계정 대비)
            self.appkey = env.get('KIS_VIRTUAL_APPKEY') or env['KIS_APPKEY']
            self.secret = env.get('KIS_VIRTUAL_SECRET') or env['KIS_SECRET']
        acct = env['KIS_ACCOUNT']
        if '-' in acct:
            self.cano, self.prdt = acct.split('-', 1)
        else:
            self.cano, self.prdt = acct[:8], (acct[8:] or '01')
        self.data_dir = Path(env.get('DATA_DIR') or '.')

    # ---- 인증 ----

    def _token(self) -> str:
        # 1) 인메모리 -> 2) 파일캐시 -> 3) 재발급 (KIS 토큰 발급 유량 제한 있음)
        memo = getattr(self, '_token_memo', None)
        if memo and memo['expires_at'] > time.time() + 600:
            return memo['token']
        cache = self.data_dir / f'kis_token_{self.mode}.json'
        try:
            saved = json.loads(cache.read_text(encoding='utf-8'))
            if saved['expires_at'] > time.time() + 600:
                self._token_memo = saved
                return saved['token']
        except (OSError, ValueError, KeyError):
            pass
        r = requests.post(
            DOMAIN[self.mode] + '/oauth2/tokenP',
            json={'grant_type': 'client_credentials',
                  'appkey': self.appkey, 'appsecret': self.secret},
            timeout=10)
        r.raise_for_status()
        body = r.json()
        saved = {'token': body['access_token'],
                 'expires_at': time.time() + int(body.get('expires_in') or 86400)}
        self._token_memo = saved
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(saved), encoding='utf-8')
            import os
            os.replace(tmp, cache)
        except OSError:
            pass  # 캐시 실패는 무해 (재발급 시도)
        return saved['token']

    # ---- 공통 호출 ----

    def _request(self, method: str, path: str, tr_id: str,
                 params=None, body=None, tr_cont=''):
        """일시 장애(5xx/타임아웃/유량초과)는 짧은 재시도. 비즈니스 거부(rt_cd!=0)는 즉시 raise.
        모의(VTS) 서버가 간헐적으로 500을 뱉는 실측 사례 있음 (2026-08-17 스모크)."""
        headers = {
            'authorization': f'Bearer {self._token()}',
            'appkey': self.appkey, 'appsecret': self.secret,
            'tr_id': tr_id, 'custtype': 'P',
        }
        if tr_cont:
            headers['tr_cont'] = tr_cont
        url = DOMAIN[self.mode] + path
        last_err = None
        for attempt in range(3):
            if attempt:
                time.sleep(attempt)  # 1s, 2s 백오프
            try:
                if method == 'GET':
                    r = requests.get(url, headers=headers, params=params, timeout=10)
                else:
                    r = requests.post(url, headers=headers, json=body, timeout=10)
            except requests.exceptions.RequestException as e:
                last_err = e
                continue
            time.sleep(CALL_DELAY[self.mode])
            if r.status_code >= 500:
                last_err = RuntimeError(f'KIS {tr_id} HTTP {r.status_code} (transient)')
                continue
            r.raise_for_status()
            data = r.json()
            if data.get('rt_cd') != '0':
                msg = f"{data.get('msg_cd')} {data.get('msg1')}"
                if '초당' in (data.get('msg1') or ''):  # 유량 초과는 재시도
                    last_err = RuntimeError(f'KIS {tr_id} 유량초과: {msg}')
                    continue
                raise RuntimeError(f'KIS {tr_id} 실패: {msg}')
            data['_tr_cont'] = r.headers.get('tr_cont', '')
            return data
        raise last_err

    # ---- 조회 ----

    def balance(self) -> Snapshot:
        params = {
            'CANO': self.cano, 'ACNT_PRDT_CD': self.prdt,
            'AFHR_FLPR_YN': 'N', 'OFL_YN': '', 'INQR_DVSN': '02',
            'UNPR_DVSN': '01', 'FUND_STTL_ICLD_YN': 'N',
            'FNCG_AMT_AUTO_RDPT_YN': 'N', 'PRCS_DVSN': '00',
            'CTX_AREA_FK100': '', 'CTX_AREA_NK100': '',
        }
        out1, out2 = [], []
        tr_cont = ''
        for _ in range(5):  # 연속조회 (모의 20건/호출 제한)
            data = self._request('GET', PATH_BALANCE, TR_BALANCE[self.mode],
                                 params=params, tr_cont=tr_cont)
            out1 += data.get('output1') or []
            out2 = data.get('output2') or out2
            if data['_tr_cont'] not in ('M', 'F'):
                break
            params['CTX_AREA_FK100'] = data.get('ctx_area_fk100', '')
            params['CTX_AREA_NK100'] = data.get('ctx_area_nk100', '')
            tr_cont = 'N'
        return snapshot_from_balance(out1, out2)

    def daily_closes(self, code: str, days: int = 260) -> pd.Series:
        frames = []
        end = datetime.now(KST).date()
        for _ in range(5):  # 호출당 최대 100봉 -> 반환 최소일자 앵커로 페이지네이션
            start = end - timedelta(days=140)
            data = self._request('GET', PATH_CHART, 'FHKST03010100', params={
                'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': code,
                'FID_INPUT_DATE_1': start.strftime('%Y%m%d'),
                'FID_INPUT_DATE_2': end.strftime('%Y%m%d'),
                'FID_PERIOD_DIV_CODE': 'D',
                'FID_ORG_ADJ_PRC': '0',  # 0=수정주가 (스펙 §5-(b))
            })
            s = closes_from_chart(data.get('output2') or [])
            if s.empty:
                break
            frames.append(s)
            if sum(len(f) for f in frames) >= days:
                break
            end = (s.index[0] - timedelta(days=1)).date()
        if not frames:
            return pd.Series(dtype=float)
        merged = pd.concat(frames).sort_index()
        merged = merged[~merged.index.duplicated(keep='last')]
        return merged.iloc[-days:]

    def current_price(self, code: str) -> float:
        data = self._request('GET', PATH_PRICE, 'FHKST01010100', params={
            'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': code})
        return float(data['output']['stck_prpr'])

    # ---- 주문 ----

    def market_order(self, code: str, side: str, qty: int) -> Fill:
        try:
            body = {
                'CANO': self.cano, 'ACNT_PRDT_CD': self.prdt, 'PDNO': code,
                'ORD_DVSN': '01',  # 시장가
                'ORD_QTY': str(qty), 'ORD_UNPR': '0',
                'EXCG_ID_DVSN_CD': 'KRX', 'SLL_TYPE': '', 'CNDT_PRIC': '',
            }
            self._request('POST', PATH_ORDER, TR_ORDER[(self.mode, side)], body=body)
        except Exception as e:
            return Fill(code, side, qty, 0.0, ok=False, reason=str(e))
        # 주문은 접수됨 — 이후 어떤 실패도 ok=False로 바꾸면 안 됨 (v3 교훈).
        # KIS 주문응답엔 체결가가 없어(ODNO만) 기록가는 현재가로.
        try:
            price = self.current_price(code)
        except Exception:
            return Fill(code, side, qty, 0.0, ok=True, reason='price_lookup_failed')
        return Fill(code, side, qty, float(price), ok=True)
