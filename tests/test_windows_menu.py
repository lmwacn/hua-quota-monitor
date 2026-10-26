import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from src.windows_menu import quota_entries


class WindowsMenuTests(unittest.TestCase):
    def test_details_and_account_selection_match_macos_content(self):
        profile = SimpleNamespace(name='main', display_name='主账号')
        other = SimpleNamespace(name='other', display_name='工作账号')
        result = {'profile': profile, 'updated': datetime.now(), 'usage': {
            'rate_limit': {'primary_window': {'limit_window_seconds': 604800, 'used_percent': 22, 'reset_at': 1791645600}},
            'credits': {'balance': '62064.89'}, 'rate_limit_reset_credits': {'available_count': 1}},
            'reset': {'credits': [{'title': 'Full reset', 'expires_at': '2026-10-10T00:00:00Z'}]}}
        actions = {name: Mock() for name in ('add', 'import', 'refresh', 'dashboard')}
        select, rename = Mock(), Mock()
        rows = quota_entries(result, [profile, other], {'main': result}, 'main', actions, select, rename)
        titles = [row.title for row in rows]
        self.assertTrue(any('剩余 78%' in title for title in titles))
        self.assertTrue(any('62,064.89' in title and '$2,482.60' in title for title in titles))
        credit = next(row for row in rows if row.title.startswith('重置卡到期时间'))
        self.assertIn('1 张', credit.title)
        self.assertIn('到期', credit.children[0].title)
        accounts = next(row for row in rows if row.title == '账号监控')
        next(row for row in accounts.children[1].children if row.title == '设为主账号').action()
        select.assert_called_once_with('other')
        next(row for row in accounts.children[1].children if row.title == '重命名账号…').action()
        rename.assert_called_once_with('other')
        next(row for row in rows if row.title == '刷新所有账号').action()
        actions['refresh'].assert_called_once()

    def test_waiting_or_failed_data_is_not_reported_as_success(self):
        actions = {name: Mock() for name in ('add', 'import', 'refresh', 'dashboard')}
        rows = quota_entries(None, [], {}, None, actions, Mock(), Mock())
        self.assertTrue(any('等待刷新' in row.title for row in rows))
        rows = quota_entries({'usage': None, 'error': 'offline'}, [], {}, None, actions, Mock(), Mock())
        self.assertTrue(any('查询失败' in row.title for row in rows))
