from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from polyglotmaker.cli import _do_serve, build_parser


class WebServeCliTests(unittest.TestCase):
    def _run(self, *options: str) -> tuple[tuple[object, ...], str]:
        args = build_parser().parse_args(["serve", *options])
        output = io.StringIO()
        with patch("polyglotmaker.cli._lan_ipv4_address", return_value="192.168.1.42"), patch(
            "polyglotmaker.web.server.serve"
        ) as server, redirect_stdout(output):
            self.assertEqual(_do_serve(args), 0)
        return server.call_args.args, output.getvalue()

    def test_default_server_is_loopback_only(self):
        call, output = self._run()
        self.assertEqual(call, ("127.0.0.1", 8765))
        self.assertIn("http://127.0.0.1:8765/", output)
        self.assertNotIn("LAN address", output)

    def test_lan_option_binds_detected_private_address_and_warns(self):
        call, output = self._run("--lan", "--port", "9000")
        self.assertEqual(call, ("192.168.1.42", 9000))
        self.assertIn("http://192.168.1.42:9000/", output)
        self.assertIn("no sign-in", output)
        self.assertIn("trusted private network", output)

    def test_lan_mode_falls_back_to_all_interfaces_when_no_private_address_is_found(self):
        args = build_parser().parse_args(["serve", "--lan"])
        output = io.StringIO()
        with patch("polyglotmaker.cli._lan_ipv4_address", return_value=None), patch(
            "polyglotmaker.web.server.serve"
        ) as server, redirect_stdout(output):
            self.assertEqual(_do_serve(args), 0)
        server.assert_called_once_with("0.0.0.0", 8765)
        self.assertIn("<this-device-LAN-IP>:8765", output.getvalue())
        self.assertIn("no sign-in", output.getvalue())

    def test_advanced_bind_address_is_respected(self):
        call, output = self._run("--bind", "127.0.0.2", "--port", "8080")
        self.assertEqual(call, ("127.0.0.2", 8080))
        self.assertIn("http://127.0.0.2:8080/", output)

    def test_lan_and_bind_options_are_mutually_exclusive(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["serve", "--lan", "--bind", "0.0.0.0"])


if __name__ == "__main__":
    unittest.main()
