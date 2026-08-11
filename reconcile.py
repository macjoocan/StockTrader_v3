import json
import os
from pathlib import Path

DEFAULT = {'positions': {}, 'last_trade_date': None, 'last_rebal_ym': None}


def load_state(path: Path) -> dict:
    try:
        state = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(DEFAULT, positions={})
    # finding #4: 파싱은 성공했지만 형태가 기대와 다르면(예: '{}') positions
    # 접근에서 KeyError -> main 루프가 60초 sleep 후 무한 재시도하게 됨. DEFAULT로 방어.
    if not isinstance(state, dict) or not isinstance(state.get('positions'), dict):
        return dict(DEFAULT, positions={})
    return state


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def reconcile(state: dict, snap, core_code: str) -> tuple[dict, list[str]]:
    warns = []
    new_pos = {}
    kis = {c: qp for c, qp in snap.holdings.items() if c != core_code}
    for code, (qty, price) in kis.items():
        old = state['positions'].get(code)
        if old and old['qty'] == qty:
            new_pos[code] = old
        else:
            new_pos[code] = {'qty': qty, 'entry_price': float(price), 'entry_date': 'adopted'}
            warns.append(f'대사: {code} KIS기준 채택 (qty {qty}, state={old})')
    for code in state['positions']:
        if code != core_code and code not in kis:
            warns.append(f'대사: {code} state에 있으나 KIS에 없음 → 제거')
    new_state = dict(state, positions=new_pos)
    return new_state, warns
