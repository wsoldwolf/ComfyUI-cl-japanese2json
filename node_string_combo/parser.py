"""Deterministic parser for ``|`` separated configurable string lists."""

from __future__ import annotations

from .errors import StringComboError


def parse_string_list(value: str) -> tuple[str, ...]:
    """Parse a list where ``|`` separates items and ``||`` means a literal pipe.

    Surrounding whitespace is removed from each item. Internal whitespace and
    character case are preserved because the selected item is an output value,
    not merely a display label.
    """

    if not isinstance(value, str):
        raise StringComboError("string_list must be a string")
    if "\r" in value or "\n" in value:
        raise StringComboError("string_list must be one physical line")

    items: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character != "|":
            current.append(character)
            index += 1
            continue
        if index + 1 < len(value) and value[index + 1] == "|":
            current.append("|")
            index += 2
            continue
        items.append("".join(current).strip())
        current = []
        index += 1
    items.append("".join(current).strip())

    if any(not item for item in items):
        raise StringComboError(
            "string_list contains an empty item; remove leading/trailing separators "
            "or use || for a literal pipe"
        )

    seen: set[str] = set()
    for item in items:
        if item in seen:
            raise StringComboError(
                f"string_list contains duplicate item {item!r}; items must be unique"
            )
        seen.add(item)
    return tuple(items)


def resolve_selected_string(string_list: str, selected_value: str) -> tuple[str, int, int]:
    """Return the selected string, its one-based index, and item count.

    An empty selection resolves to the first item so a newly created node also
    works when its frontend extension has not initialized the combo yet.
    Any non-empty stale selection is an error instead of silently outputting a
    different value in headless/API execution.
    """

    if not isinstance(selected_value, str):
        raise StringComboError("selected_value must be a string")
    values = parse_string_list(string_list)
    selected = values[0] if selected_value == "" else selected_value
    try:
        zero_based_index = values.index(selected)
    except ValueError as exc:
        raise StringComboError(
            "selected_value is not present in string_list; select a current item"
        ) from exc
    return selected, zero_based_index + 1, len(values)
