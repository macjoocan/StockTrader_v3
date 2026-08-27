# KIS_ID(HTS ID)는 REST엔 불필요 — 웹소켓 체결통보(ccnl_notice) 도입 시에만 필요
# KIS_MODE: paper(기본)|live — 모의/실전 도메인·TR·키 전환 (broker/kis.py)
REQUIRED = ['KIS_ACCOUNT', 'KIS_APPKEY', 'KIS_SECRET',
            'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID', 'DATA_DIR']
OPTIONAL = ['KIS_ID', 'KIS_MODE', 'DART_API_KEY',
            'NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET',
            'KIS_VIRTUAL_ID', 'KIS_VIRTUAL_APPKEY', 'KIS_VIRTUAL_SECRET']


def load_config(environ: dict) -> dict:
    missing = [k for k in REQUIRED if k not in environ]
    if missing:
        raise KeyError(f'환경변수 누락: {missing}')
    cfg = {k: environ[k] for k in REQUIRED}
    cfg.update({k: environ.get(k, '') for k in OPTIONAL})
    return cfg
