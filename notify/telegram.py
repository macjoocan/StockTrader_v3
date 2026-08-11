import requests


class Notifier:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    def send(self, text: str) -> bool:
        if not self.token:
            return True
        try:
            r = requests.post(
                f'https://api.telegram.org/bot{self.token}/sendMessage',
                json={'chat_id': self.chat_id, 'text': text}, timeout=10)
            return r.status_code == 200
        except Exception:
            return False
