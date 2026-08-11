REQUIRED = ['KIS_ID', 'KIS_ACCOUNT', 'KIS_APPKEY', 'KIS_SECRET',
            'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID', 'DATA_DIR']
OPTIONAL = ['KIS_VIRTUAL_ID', 'KIS_VIRTUAL_APPKEY', 'KIS_VIRTUAL_SECRET']


def load_config(environ: dict) -> dict:
    missing = [k for k in REQUIRED if k not in environ]
    if missing:
        raise KeyError(f'환경변수 누락: {missing}')
    cfg = {k: environ[k] for k in REQUIRED}
    cfg.update({k: environ.get(k, '') for k in OPTIONAL})
    return cfg
