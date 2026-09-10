from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

from .helpers import PKG, ROOT, module


text_nodes = module("node_text_file.node")
errors = module("node_text_file.errors")


def encoded(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class TextFileNodeTests(unittest.TestCase):
    def test_dedicated_spec_tracks_browser_and_backend_contract(self) -> None:
        spec = (ROOT / "docs" / "cl_text_file_spec.md").read_text(encoding="utf-8")
        for marker in (
            "CLLoadTextFile",
            "file_name",
            "file_base64",
            "file_signature",
            "File.arrayBuffer()",
            "IS_CHANGED()",
            "16 MiB",
        ):
            self.assertIn(marker, spec)

    def test_registration_metadata_and_frontend_directory(self) -> None:
        cls = PKG.NODE_CLASS_MAPPINGS["CLLoadTextFile"]
        self.assertIs(cls, text_nodes.CLLoadTextFile)
        self.assertEqual(
            PKG.NODE_DISPLAY_NAME_MAPPINGS["CLLoadTextFile"],
            "CL Load Text File (Drag & Drop)",
        )
        self.assertEqual(PKG.WEB_DIRECTORY, "./web")
        self.assertEqual(cls.CATEGORY, "MiniMax H3/Prompt Tools")
        self.assertEqual(cls.FUNCTION, "load_text")
        self.assertEqual(cls.RETURN_TYPES, ("STRING",))
        self.assertEqual(cls.RETURN_NAMES, ("text",))
        self.assertFalse(cls.OUTPUT_NODE)

    def test_hidden_transport_input_contract(self) -> None:
        required = text_nodes.CLLoadTextFile.INPUT_TYPES()["required"]
        self.assertEqual(
            list(required), ["file_name", "file_base64", "file_signature"]
        )
        self.assertTrue(required["file_base64"][1]["multiline"])

    def test_utf8_bom_and_newlines_are_normalized(self) -> None:
        payload = encoded(b"\xef\xbb\xbfline 1\r\nline 2\rline 3\n")
        result = text_nodes.CLLoadTextFile.load_text(
            "notes.txt", payload, '{"size":26,"lastModified":1}'
        )
        self.assertEqual(result, ("line 1\nline 2\nline 3\n",))

    def test_empty_plain_text_file_is_supported(self) -> None:
        self.assertEqual(
            text_nodes.CLLoadTextFile.load_text("empty.txt", "", ""), ("",)
        )

    def test_source_name_is_metadata_and_never_opened_as_a_path(self) -> None:
        payload = encoded("任意パスから選択".encode("utf-8"))
        with self.assertLogs("cl_textfile", level="INFO") as captured:
            result = text_nodes.CLLoadTextFile.load_text(
                r"C:\outside\lyrics.txt", payload, "signature"
            )
        self.assertEqual(result, ("任意パスから選択",))
        output = "\n".join(captured.output)
        self.assertIn("\x1b[96m", output)
        self.assertIn("[cl_textfile] success: loaded lyrics.txt", output)

    def test_invalid_selection_payload_encoding_and_binary_text_are_rejected(self) -> None:
        with self.assertRaisesRegex(errors.TextFileLoadError, "No text file"):
            text_nodes.CLLoadTextFile.load_text("", "", "")
        with self.assertRaisesRegex(errors.TextFileLoadError, "Base64"):
            text_nodes.CLLoadTextFile.load_text("bad.txt", "not base64!", "")
        with self.assertRaisesRegex(errors.TextFileLoadError, "UTF-8"):
            text_nodes.CLLoadTextFile.load_text("bad.txt", encoded(b"\xff"), "")
        with self.assertRaisesRegex(errors.TextFileLoadError, "NUL"):
            text_nodes.CLLoadTextFile.load_text(
                "bad.txt", encoded(b"hello\x00world"), ""
            )

    def test_size_and_metadata_limits_are_enforced(self) -> None:
        with patch.object(text_nodes, "MAX_TEXT_FILE_BYTES", 2):
            with self.assertRaisesRegex(errors.TextFileLoadError, "exceeds"):
                text_nodes.CLLoadTextFile.load_text(
                    "large.txt", encoded(b"abc"), ""
                )
        with self.assertRaisesRegex(errors.TextFileLoadError, "metadata"):
            text_nodes.CLLoadTextFile.load_text(
                "ok.txt", encoded(b"ok"), "x" * 513
            )

    def test_cache_fingerprint_changes_with_content_and_is_compact(self) -> None:
        first = text_nodes.CLLoadTextFile.IS_CHANGED("a.txt", encoded(b"a"), "1")
        second = text_nodes.CLLoadTextFile.IS_CHANGED("a.txt", encoded(b"b"), "1")
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_frontend_uses_browser_bytes_without_upload_endpoint_or_preview(self) -> None:
        script = (ROOT / "web" / "cl_text_file_loader.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('nodeData.name !== NODE_NAME', script)
        self.assertIn("file.arrayBuffer()", script)
        self.assertIn("node.onDragDrop", script)
        self.assertIn("TextDecoder", script)
        self.assertNotIn("fetchApi", script)
        self.assertNotIn("/upload/", script)
        self.assertNotIn("preview", script.lower())


if __name__ == "__main__":
    unittest.main()
