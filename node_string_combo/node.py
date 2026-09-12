"""ComfyUI node for selecting one string from a user-defined list."""

from __future__ import annotations

import logging
from typing import Any

from ..common.logging import log_node_success
from .errors import StringComboError
from .parser import resolve_selected_string


LOGGER = logging.getLogger("cl_string_combo")


class CLStringCombo:
    """Expose a configurable list as a frontend combo and output one STRING."""

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("selected_string",)
    FUNCTION = "select_string"
    CATEGORY = "MiniMax H3/Prompt Tools"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        # Both fields remain STRINGs in the server schema. The frontend mirrors
        # string_list through a right-click node property and hides this
        # transport widget; selected_value is rendered as a dynamic combo.
        return {
            "required": {
                "string_list": (
                    "STRING",
                    {
                        "default": "foo|bar",
                        "multiline": False,
                        "tooltip": (
                            "Serialized transport for the right-click "
                            "string_list property. Items use | separators and "
                            "|| for a literal pipe."
                        ),
                    },
                ),
                "selected_value": (
                    "STRING",
                    {
                        "default": "foo",
                        "multiline": False,
                        "tooltip": "Select one parsed item from string_list.",
                    },
                ),
            }
        }

    @classmethod
    def VALIDATE_INPUTS(cls, string_list: str, selected_value: str) -> bool | str:
        try:
            resolve_selected_string(string_list, selected_value)
        except StringComboError as exc:
            return str(exc)
        return True

    @staticmethod
    def select_string(string_list: str, selected_value: str) -> tuple[str]:
        selected, selected_index, item_count = resolve_selected_string(
            string_list,
            selected_value,
        )
        log_node_success(
            LOGGER,
            "cl_string_combo",
            "selected item %d/%d",
            selected_index,
            item_count,
        )
        return (selected,)
