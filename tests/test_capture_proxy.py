import socket
import unittest
from unittest.mock import patch

from backend.capture_proxy import public_address


class CaptureProxyTests(unittest.TestCase):
    def test_rejects_private_special_and_mixed_dns_answers(self):
        for ip in ["127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1", "192.0.2.1"]:
            answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
            with patch("backend.capture_proxy.socket.getaddrinfo", return_value=answers), self.assertRaises(ValueError):
                public_address("untrusted.example", 80)
        with self.assertRaises(ValueError):
            public_address("example.com", 6379)

    def test_returns_numeric_address_for_connection_pinning(self):
        with patch("backend.capture_proxy.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]) as lookup:
            self.assertEqual(public_address("example.com", 443), "93.184.216.34")
            lookup.assert_called_once()
