# StockTrader v3

한국투자증권(KIS) Open API 기반 **국내주식 코어-새틀라이트 자동매매 봇 + 분석 대시보드**.

> ⚠️ 현재 **모의투자 파일럿 단계**입니다. 이 저장소의 어떤 화면·수치도 투자 추천이 아니며,
> 모든 투자 판단과 책임은 사용자 본인에게 있습니다.

## 전략 (백테스트 근거)

marcap 데이터(2017~2026, point-in-time·생존편향 회피)로 5개 레짐 구간 + 비용 3단
스트레스(0.20/0.30/0.45%) 검증을 거쳐 확정한 구조:

| 구성 | 비중 | 내용 | 근거 |
|---|---|---|---|
| **코어** | 70% | KODEX 200 (069500), ±5%p 밴드 이탈 시 월 1회 리밸런싱 | 백테 전 구간에서 지수 B&H가 액티브 전략 대비 우위 |
| **새틀라이트** | 30% | RSI(2) 딥바잉 — `종가 > SMA200 & RSI(2) < 10` 진입, `종가 > SMA5` 청산, 손절 없음, 4슬롯 | 923건 백테: 비용 0.30% 후 평균 +66bp/건, 승률 66%, 파라미터 패밀리 robust |
| 유니버스 | — | 연초 KOSPI 보통주 시총 상위 20 (연 1회 갱신) | — |

검증에서 **탈락**한 것들(참고): KOSPI 모멘텀 24조합(지수 B&H에 열세), 변동성 돌파(비용 전멸),
나스닥 조건 필터 8종(파라미터 고원 부재). 검증 안 된 것은 매매에 넣지 않는 것이 이 프로젝트의 규칙입니다.

## 시스템 구성

```
┌─ stock-trader (봇, 상주)          ┌─ stock-dashboard (:5030, 읽기전용)
│  4분 heartbeat                    │  10분 시장캐시 (봇 유량 보호:
│  평일 15:19 신호 → 15:20:30       │   토큰 발급 안함·15:15~35 수집중지)
│  동시호가 시장가 (컷오프 15:25)    │
│  월초 코어 리밸런싱               └─ 공유: DATA_DIR 볼륨
│  텔레그램 알림                        (positions.json, events jsonl, 토큰캐시)
```

- **백테=라이브 동형성**: 라이브 신호 모듈(`signal_engine/`)이 검증 백테의 트레이드
  158건을 골든 테스트로 동일 재현 (`tests/test_golden.py`)
- **상태의 진실 = KIS 잔고**: 매 사이클 `positions.json`과 대사, 불일치 시 KIS 기준 채택

## 주요 기능

### 봇 (main.py)
- 일봉 전략 상주 프로세스 — 하루 1회 사이클(대사 → SELL → 월초 코어리밸 → BUY)
- 안전장치: 거래창 컷오프(15:25, 장외 주문 방지) · API 3회 재시도 후 "오늘 스킵" ·
  주문 성공/가격조회 실패 분리(성공 주문을 실패로 오보고 금지) · 체결가 누락 시 현재가 폴백 ·
  토큰 선제갱신(heartbeat, 크리티컬 경로에서 발급 제거) · 루프 예외 격리(상주 불사)
- 텔레그램: 체결/실패/일일요약(보유 상세 포함)

### 대시보드 (:5030)
| 패널 | 내용 |
|---|---|
| Market Pulse | KOSPI/KOSDAQ(KIS) · USD/KRW · BTC 실시간 배너 |
| 상태 카드 | 평가금액, 수익률 vs KODEX, 코어 비중 밴드, 슬롯, 게이트 D-day |
| 벤치마크 차트 | 계좌 vs KODEX 200 지수화(개시=100), 호버 툴팁 |
| 종목 진단 카드 | 등급(규칙 명시·근거 표시: 재무주의/저평가·우량/고평가/추세약세) + 가치·퀄리티·외인 바 + 최신 기사 |
| 시그널 레이더 | 유니버스 RSI2 오름차순, 진입조건 충족 "신호권" 배지 |
| 팩터 랭킹 | 가치(PER/PBR)·퀄리티(ROE/부채) 순위 백분위 — *참고용·매매 미연결* |
| 포지션/실현손익 | 평가손익, 보유일, 청산선(SMA5) 거리, FIFO 실현손익 |
| 종목 상세 | **캔들차트**(SMA5/20/200·BB20·RSI2·거래량·**수급(외인/기관 누적)** 서브패널, 기간버튼·드래그 줌·십자선 툴팁·📰뉴스/📋공시 말풍선 마커) + 밸류에이션 + 재무비율 + 뉴스 + DART 공시 |
| 투자 보고서 (/report) | **규칙 기반 서술 자동 생성** — 성과·레짐 판정·포지션 리뷰·거래·관찰종목·운영. LLM 미사용, 전 문장 데이터 역추적 가능. 주간/월간/전체 |

### 데이터 소스
KIS Open API(시세·재무·수급·주문) · DART(공시) · NAVER API HUB(뉴스) · marcap(유니버스 생성) — 크롤링 없음, 전부 공식 API.

## 설치 및 설정

```bash
pip install -r requirements.txt   # pandas, requests, flask
cp .env.example .env              # 키 입력 (아래 표)
```

| 키 | 필수 | 발급처 |
|---|---|---|
| `KIS_APPKEY` / `KIS_SECRET` | ✅ | apiportal.koreainvestment.com |
| `KIS_VIRTUAL_APPKEY` / `KIS_VIRTUAL_SECRET` | 모의투자 시 | 위 포털 (모의투자 신청 후) |
| `KIS_ACCOUNT` | ✅ | 계좌번호 8자리-01 (**모의계좌는 모의 번호**) |
| `KIS_MODE` | ✅ | `paper`(기본) / `live` |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | 권장 | 봇 알림용 (없으면 무알림 동작) |
| `DART_API_KEY` | 선택 | opendart.fss.or.kr (공시 패널) |
| `NAVER_HUB_KEY_ID` / `NAVER_HUB_KEY` | 선택 | 네이버클라우드 NAVER API HUB (뉴스 — 구 개발자센터 키는 2027-06까지 지원) |

⚠️ 키는 반드시 `.env`에만 — `.env.example`이나 코드에 절대 넣지 마세요.

## 실행

```bash
# Docker (권장)
mkdir -p data-volume && docker-compose up -d --build
# 봇: stock-trader / 대시보드: http://localhost:5030

# 테스트 (104개)
python -m pytest tests -q
```

## 운영 원칙 (하드 규칙)

1. **`KIS_MODE=paper` 기본** — 실계좌 전환은 모의투자 4주+ 게이트(신호일치·체결오차 검증) 통과 후
2. 검증 안 된 수치("상승확률", "적정주가", LLM 추천)는 만들지 않음 — 표시되는 모든 등급/점수는
   규칙이 화면에 공개되고 근거가 함께 표시됨
3. 대시보드는 읽기전용 — 매매 로직에 어떤 영향도 없음
4. 유니버스 갱신(연 1회): `python data/universe.py 2027` (marcap 데이터 필요)

## 폴더 구조

```
signal_engine/   RSI2/SMA 지표 + 진입/청산 판단 (순수함수, 백테·라이브 공용)
portfolio/       코어 밴드 리밸런싱 + 슬롯 사이징 (순수함수)
broker/          KIS 공식 REST 직구현 (토큰캐시·페이지네이션·재시도·모의/실전 스위치)
events/          jsonl 이벤트소싱 (signal/fill/rebalance/error/daily_summary)
notify/          텔레그램
data/            유니버스 (연 1회 marcap 생성, 종목명 포함)
executor.py      일일 사이클 오케스트레이션
main.py          상주 루프 (heartbeat/스케줄/재시도)
dashboard.py     Flask 대시보드 (+dashboard_data.py 분석 레이어)
scheduler.py     순수 시간판단 (trade/heartbeat/sleep)
reconcile.py     KIS 잔고 대사 + 원자쓰기 상태파일
tools/           smoke_kis.py (실서버 검증), make_golden.py (골든 픽스처)
tests/           104 tests (골든 테스트 포함)
docs/            설계 스펙
```

## 라이선스 / 면책

개인 프로젝트입니다. 시뮬레이션·백테스트 결과는 실거래 수익을 보장하지 않으며,
본 소프트웨어 사용으로 인한 모든 손실은 사용자 책임입니다.
