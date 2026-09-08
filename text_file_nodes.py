"""Browser-backed plain-text file loader for ComfyUI workflows."""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
from typing import Any

from .compiler.errors import TextFileLoadError


LOGGER = logging.getLogger("cl_textfile")
MAX_TEXT_FILE_BYTES = 16 * 1024 * 1024


def _display_name(file_name: str) -> str:
    """Return a safe basename for diagnostics without accessing the filesystem."""

    return file_name.replace("\\", "/").rsplit("/", 1)[-1] or "(unnamed)"


def decode_text_file(file_name: str, file_base64: str) -> str:
    """Decode a browser-provided UTF-8 file payload.

    ``file_name`` is metadata only.  It is deliberately never opened as a path.
    """

    if not isinstance(file_name, str) or not file_name.strip():
        raise TextFileLoadError(
            "No text file is selected; choose a file or drop one onto the node"
        )
    if "\x00" in file_name or len(file_name) > 1024:
        raise TextFileLoadError("The selected text file name is invalid")
    if not isinstance(file_base64, str):
        raise TextFileLoadError("The embedded text file payload must be a string")

    maximum_encoded_length = ((MAX_TEXT_FILE_BYTES + 2) // 3) * 4
    if len(file_base64) > maximum_encoded_length:
        raise TextFileLoadError(
            f"Text file exceeds the {MAX_TEXT_FILE_BYTES // (1024 * 1024)} MiB limit"
        )

    try:
        raw = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TextFileLoadError("The embedded text file payload is not valid Base64") from exc

    if len(raw) > MAX_TEXT_FILE_BYTES:
        raise TextFileLoadError(
            f"Text file exceeds the {MAX_TEXT_FILE_BYTES // (1024 * 1024)} MiB limit"
        )

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TextFileLoadError(
            "Text file must be UTF-8 or UTF-8 with BOM"
        ) from exc
    if "\x00" in text:
        raise TextFileLoadError("Text file contains NUL bytes and is not plain text")

    return text.replace("\r\n", "\n").replace("\r", "\n")


class CLLoadTextFile:
    """Load UTF-8 text selected by the ComfyUI browser frontend."""

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "load_text"
    CATEGORY = "MiniMax H3/Prompt Tools"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        # These values are serialized so the workflow is self-contained.  The
        # frontend extension hides them and exposes a file picker/drop target.
        return {
            "required": {
                "file_name": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "Browser-selected source name; never opened as a backend path.",
                    },
                ),
                "file_base64": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "tooltip": "UTF-8 file bytes embedded by the drag-and-drop frontend.",
                    },
                ),
                "file_signature": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "Browser metadata used to invalidate cached execution.",
                    },
                ),
            }
        }

    @classmethod
    def IS_CHANGED(
        cls, file_name: str, file_base64: str, file_signature: str
    ) -> str:
        # The content itself is included so replacing a same-name/same-size file
        # still invalidates the node cache even if filesystem timestamps match.
        fingerprint = hashlib.sha256()
        for value in (file_signature, file_name, file_base64):
            fingerprint.update(str(value).encode("utf-8"))
            fingerprint.update(b"\0")
        return fingerprint.hexdigest()

    @staticmethod
    def load_text(
        file_name: str, file_base64: str, file_signature: str = ""
    ) -> tuple[str]:
        if not isinstance(file_signature, str) or len(file_signature) > 512:
            raise TextFileLoadError("The selected text file metadata is invalid")
        text = decode_text_file(file_name, file_base64)
        byte_count = len(base64.b64decode(file_base64, validate=True))
        LOGGER.info(
            "[cl_textfile] Loaded %s: %d byte(s), %d character(s)",
            _display_name(file_name),
            byte_count,
            len(text),
        )
        return (text,)
