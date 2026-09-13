"""Validation for enum metadata transported by the frontend extension."""

from __future__ import annotations

import json
from typing import Any

from .errors import ConnectedComboError


MAX_ENUM_ITEMS = 4096
MAX_ITEM_LENGTH = 4096


def parse_enum_values_json(value: str) -> tuple[str, ...]:
    """Parse the frontend-generated JSON array without altering its strings."""

    if not isinstance(value, str):
        raise ConnectedComboError("enum_values_json must be a string")
    try:
        parsed: Any = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ConnectedComboError(
            "Connected COMBO metadata is not a valid JSON array; reconnect the node"
        ) from exc
    if not isinstance(parsed, list):
        raise ConnectedComboError("Connected COMBO metadata must be a JSON array")
    if not parsed:
        raise ConnectedComboError(
            "No downstream string COMBO was found; connect selected_string to a "
            "COMBO input or a declared combo override input"
        )
    if len(parsed) > MAX_ENUM_ITEMS:
        raise ConnectedComboError(
            f"Connected COMBO exposes more than {MAX_ENUM_ITEMS} items"
        )

    values: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, str):
            raise ConnectedComboError(
                f"Connected COMBO item {index} is not a string"
            )
        if not item:
            raise ConnectedComboError(
                f"Connected COMBO item {index} is empty"
            )
        if "\x00" in item or len(item) > MAX_ITEM_LENGTH:
            raise ConnectedComboError(
                f"Connected COMBO item {index} is not a safe transport string"
            )
        if item in seen:
            raise ConnectedComboError(
                f"Connected COMBO contains duplicate item {item!r}"
            )
        seen.add(item)
        values.append(item)
    return tuple(values)


def resolve_connected_selection(
    enum_values_json: str,
    selected_value: str,
) -> tuple[str, int, int]:
    """Resolve the selected string and return one-based position metadata."""

    if not isinstance(selected_value, str):
        raise ConnectedComboError("selected_value must be a string")
    values = parse_enum_values_json(enum_values_json)
    selected = values[0] if selected_value == "" else selected_value
    try:
        zero_based_index = values.index(selected)
    except ValueError as exc:
        raise ConnectedComboError(
            "selected_value is not present in the connected COMBO; refresh or select "
            "a current item"
        ) from exc
    return selected, zero_based_index + 1, len(values)

