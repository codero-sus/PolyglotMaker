from __future__ import annotations

import unittest

from polyglotmaker.web.server import _parse_multipart


class MultipartParserTests(unittest.TestCase):
    def test_fields_and_binary_file_are_preserved(self):
        boundary = "XBoundary42"
        file_bytes = b"\x00binary\xff\r\n"
        body = (
            b"--XBoundary42\r\n"
            b'Content-Disposition: form-data; name="host"\r\n\r\n'
            b"png\r\n"
            b"--XBoundary42\r\n"
            b'Content-Disposition: form-data; name="files"; filename="sample.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
            + file_bytes
            + b"\r\n--XBoundary42--\r\n"
        )
        fields, uploads = _parse_multipart(f"multipart/form-data; boundary={boundary}", body)
        self.assertEqual(fields["host"], ["png"])
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].name, "sample.bin")
        self.assertEqual(uploads[0].data, file_bytes)

    def test_traversal_filename_is_reduced_to_basename(self):
        boundary = "B"
        body = (
            b"--B\r\nContent-Disposition: form-data; name=\"files\"; filename=\"../../evil.bin\"\r\n"
            b"Content-Type: application/octet-stream\r\n\r\nabc\r\n--B--\r\n"
        )
        _fields, uploads = _parse_multipart("multipart/form-data; boundary=B", body)
        self.assertEqual(uploads[0].name, "evil.bin")


if __name__ == "__main__":
    unittest.main()
