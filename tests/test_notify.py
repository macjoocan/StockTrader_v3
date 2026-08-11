from unittest.mock import patch

from notify.telegram import Notifier


def test_send_posts_to_api():
    n = Notifier('TOKEN', '123')
    with patch('notify.telegram.requests.post') as post:
        post.return_value.status_code = 200
        assert n.send('hello') is True
        url = post.call_args[0][0]
        assert 'botTOKEN/sendMessage' in url
        assert post.call_args[1]['json'] == {'chat_id': '123', 'text': 'hello'}


def test_empty_token_noop():
    with patch('notify.telegram.requests.post') as post:
        assert Notifier('', '').send('x') is True
        post.assert_not_called()


def test_exception_swallowed():
    n = Notifier('T', '1')
    with patch('notify.telegram.requests.post', side_effect=OSError):
        assert n.send('x') is False
