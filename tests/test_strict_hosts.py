from __future__ import annotations

import ast
import configparser
import csv
import io
import json
import tomllib
import unittest
import xml.etree.ElementTree as ET

from polyglotmaker.core.bundle import InputFile
from polyglotmaker.core.container import make_container, unpack_bytes


class StrictHostSyntaxTests(unittest.TestCase):
    def setUp(self):
        self.payload = InputFile("sample.bin", b"arbitrary\x00payload")

    def test_json_and_xml_hosts_remain_parseable(self):
        for host in ("json", "xml", "svg"):
            with self.subTest(host=host):
                result = make_container([self.payload], host=host)
                if host == "json":
                    json.loads(result.data.decode("utf-8"))
                else:
                    ET.fromstring(result.data)
                self.assertEqual(unpack_bytes(result.data).files[self.payload.name], self.payload.data)

    def test_python_toml_ini_and_csv_hosts_parse(self):
        python_file = make_container([self.payload], host="python").data
        ast.parse(python_file.decode("utf-8"))

        toml_file = make_container([self.payload], host="toml").data
        tomllib.loads(toml_file.decode("utf-8"))

        ini_file = make_container([self.payload], host="ini").data
        config = configparser.ConfigParser()
        config.read_string(ini_file.decode("utf-8"))
        self.assertIn("polyglotmaker", config)

        csv_file = make_container([self.payload], host="csv").data
        rows = list(csv.reader(io.StringIO(csv_file.decode("utf-8"))))
        self.assertEqual(rows[0], ["kind", "value"])
        self.assertEqual(rows[1], ["PolyglotMaker", "PLGMK1"])


if __name__ == "__main__":
    unittest.main()
