from __future__ import annotations

import json
import ssl
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from src.codex_usage import CodexAuth, CodexUsageError, _get_json


class CodexUsageNetworkTests(unittest.TestCase):
    @patch("src.codex_usage.time.sleep")
    @patch("src.codex_usage.urllib.request.urlopen")
    def test_transient_ssl_error_is_retried(self, urlopen: MagicMock, sleep: MagicMock) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps({"plan_type": "plus"}).encode()
        response.__enter__.return_value = response
        urlopen.side_effect = [
            urllib.error.URLError(ssl.SSLEOFError("unexpected eof")),
            response,
        ]

        payload = _get_json(CodexAuth("token", "account"), "https://example.test/usage")

        self.assertEqual(payload, {"plan_type": "plus"})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.4)

    @patch("src.codex_usage.time.sleep")
    @patch("src.codex_usage.urllib.request.urlopen")
    def test_exhausted_network_retries_return_friendly_error(
        self, urlopen: MagicMock, sleep: MagicMock
    ) -> None:
        urlopen.side_effect = urllib.error.URLError(
            ssl.SSLEOFError("unexpected eof while reading")
        )

        with self.assertRaises(CodexUsageError) as raised:
            _get_json(CodexAuth("token", None), "https://example.test/usage")

        self.assertTrue(raised.exception.transient)
        self.assertEqual(str(raised.exception), "网络连接暂时异常，已重试 3 次。")
        self.assertNotIn("SSL", str(raised.exception))
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
