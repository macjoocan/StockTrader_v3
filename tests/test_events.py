import json

from events.log import EventLog


def test_append_and_daily_file(tmp_path):
    log = EventLog(tmp_path)
    log.write('signal', code='005930', side='BUY')
    log.write('fill', code='005930', price=70000)
    files = list(tmp_path.glob('events_*.jsonl'))
    assert len(files) == 1
    lines = [json.loads(x) for x in files[0].read_text(encoding='utf-8').splitlines()]
    assert [x['kind'] for x in lines] == ['signal', 'fill']
    assert lines[0]['code'] == '005930'
    assert 'ts' in lines[0]


def test_write_never_raises(tmp_path):
    log = EventLog(tmp_path / 'no' / 'such' / 'dir')
    log.write('signal', x=1)  # 디렉토리 없음 -> 조용히 실패해야 함
