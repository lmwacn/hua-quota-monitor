import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.account_store import AccountStore
from src.quota_service import QuotaService
from src.usage_monitor import UsageMonitor
from src.windows_widget import quota_text


class WindowsWidgetTests(unittest.TestCase):
    def test_refresh_records_history_and_falls_back_to_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            auth = base / 'auth.json'
            auth.write_text(json.dumps({'tokens': {'access_token': 'test', 'account_id': 'test-id'}}))
            store = AccountStore(base / 'store', canonical_auth_path=auth)
            store.import_account('test', auth, make_current=True)
            service = QuotaService(auth_file=auth, base_url='https://example.com',
                monitor=UsageMonitor(base / 'store' / 'usage.sqlite3'), account_store=store)
            usage = {'rate_limit': {'primary_window': {'limit_window_seconds': 18000, 'used_percent': 18}}}
            with patch('src.quota_service.get_codex_usage', return_value=usage), patch('src.quota_service.get_codex_reset_credits', return_value={}):
                result = service.fetch_profile(store.get_current())
            self.assertIn('82%', quota_text(result))
            self.assertFalse(result['usage_cached'])
            self.assertTrue(service.current_history(result))
            with patch('src.quota_service.get_codex_usage', side_effect=RuntimeError('offline')), patch('src.quota_service.get_codex_reset_credits', return_value={}):
                result = service.fetch_profile(store.get_current())
            self.assertIn('缓存', quota_text(result))
            self.assertIn('82%', quota_text(result))

    def test_missing_and_invalid_data_do_not_show_false_remaining(self):
        self.assertIn('查询失败', quota_text({'usage': None}))
        for used in (None, 'invalid', float('nan')):
            self.assertIn('暂无', quota_text({'usage': {'rate_limit': {'primary_window': {'limit_window_seconds': 18000, 'used_percent': used}}}}))


if __name__ == '__main__':
    unittest.main()
