import pytest

from config import load_config

FULL = {'KIS_ID': 'i', 'KIS_ACCOUNT': 'a-01', 'KIS_APPKEY': 'k', 'KIS_SECRET': 's',
        'TELEGRAM_TOKEN': '', 'TELEGRAM_CHAT_ID': '', 'DATA_DIR': '/data'}


def test_load_ok_with_defaults():
    cfg = load_config(FULL)
    assert cfg['KIS_ID'] == 'i'
    assert cfg['DATA_DIR'] == '/data'
    assert cfg['KIS_VIRTUAL_ID'] == ''  # 옵션키 기본값


def test_missing_required_raises():
    with pytest.raises(KeyError):
        load_config({k: v for k, v in FULL.items() if k != 'KIS_APPKEY'})
