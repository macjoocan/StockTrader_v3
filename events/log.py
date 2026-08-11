import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))


class EventLog:
    def __init__(self, dir: Path):
        self.dir = Path(dir)

    def write(self, kind: str, **payload) -> None:
        try:
            now = datetime.now(KST)
            rec = {'ts': now.isoformat(), 'kind': kind, **payload}
            path = self.dir / f'events_{now:%Y-%m-%d}.jsonl'
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        except Exception:
            pass
