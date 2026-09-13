"""ComfyUI node whose choices follow its downstream string COMBO."""

from __future__ import annotations

import logging
from typing import Any

from ..common.logging import log_node_success
from .errors import ConnectedComboError
from .parser import resolve_connected_selection


LOGGER = logging.getLogger("cl_connected_combo")


class CLConnectedCombo:
    """Select one value from enum metadata discovered through output links."""

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("selected_string",)
    FUNCTION = "select_string"
    CATEGORY = "MiniMax H3/Prompt Tools"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        # The web extension owns discovery. JSON remains a hidden STRING so a
        # saved workflow and an API prompt carry the exact validated enum.
        return {
            "required": {
                "enum_values_json": (
                    "STRING",
                    {
                        "default": "[]",
                        "multiline": False,
                        "tooltip": (
                            "Frontend transport for choices discovered from the "
                            "connected downstream COMBO."
                        ),
                    },
                ),
                "selected_value": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Select one value exposed by the connected COMBO.",
                    },
                ),
            }
        }

    @classmethod
    def VALIDATE_INPUTS(
        cls,
        enum_values_json: str,
        selected_value: str,
    ) -> bool | str:
        try:
            resolve_connected_selection(enum_values_json, selected_value)
        except ConnectedComboError as exc:
            return str(exc)
        return True

    @staticmethod
    def select_string(
        enum_values_json: str,
        selected_value: str,
    ) -> tuple[str]:
        selected, selected_index, item_count = resolve_connected_selection(
            enum_values_json,
            selected_value,
        )
        log_node_success(
            LOGGER,
            "cl_connected_combo",
            "selected connected item %d/%d",
            selected_index,
            item_count,
        )
        return (selected,)
