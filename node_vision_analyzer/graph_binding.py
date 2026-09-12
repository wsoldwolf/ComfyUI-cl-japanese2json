"""Resolve one analyzer IMAGE output to MiniMax H3 Picture numbering."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterator

from .errors import PictureBindingError


PICTURE_REFERENCE_MODES = ("auto_h3", "manual", "none")
H3_REFERENCE_NODE_TYPES = frozenset({"MiniMaxH3ReferenceToVideo"})
_H3_IMAGE_INPUT_RE = re.compile(
    r"^(?:ref_images\.)?ref_image_([0-8])$"
)


@dataclass(frozen=True)
class PictureTarget:
    node_id: str
    node_type: str
    input_name: str
    picture_index: int


@dataclass(frozen=True)
class PictureBinding:
    mode: str
    picture_index: int | None
    targets: tuple[PictureTarget, ...] = ()

    def fingerprint(self) -> str:
        payload = {
            "mode": self.mode,
            "picture_index": self.picture_index,
            "targets": [
                {
                    "node_id": target.node_id,
                    "node_type": target.node_type,
                    "input_name": target.input_name,
                    "picture_index": target.picture_index,
                }
                for target in self.targets
            ],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def status(self) -> str:
        if self.mode == "none":
            return "none -> disabled"
        if self.mode == "manual":
            return f"manual -> <Picture {self.picture_index}>"
        if self.picture_index is None:
            return "auto_h3 -> none"
        targets = ", ".join(
            f"node {target.node_id}, {target.input_name}"
            for target in self.targets
        )
        return (
            f"auto_h3 -> <Picture {self.picture_index}>"
            + (f" ({targets})" if targets else "")
        )


def _is_source_reference(value: Any, source_id: str, output_index: int) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    return str(value[0]) == source_id and value[1] == output_index


def _walk_input_values(
    value: Any, prefix: str
) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for name, child in value.items():
            if not isinstance(name, str):
                continue
            child_prefix = f"{prefix}.{name}" if prefix else name
            yield from _walk_input_values(child, child_prefix)
        return
    yield prefix, value


def _candidate_targets(
    prompt: Any,
    *,
    unique_id: Any,
    image_output_index: int,
) -> list[PictureTarget]:
    if not isinstance(prompt, dict):
        return []
    source_id = str(unique_id)
    targets: list[PictureTarget] = []
    for raw_node_id, raw_node in prompt.items():
        if not isinstance(raw_node, dict):
            continue
        node_type = raw_node.get("class_type", raw_node.get("type"))
        if node_type not in H3_REFERENCE_NODE_TYPES:
            continue
        inputs = raw_node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for top_name, raw_value in inputs.items():
            if not isinstance(top_name, str):
                continue
            for input_name, value in _walk_input_values(raw_value, top_name):
                match = _H3_IMAGE_INPUT_RE.fullmatch(input_name)
                if match is None or not _is_source_reference(
                    value, source_id, image_output_index
                ):
                    continue
                targets.append(
                    PictureTarget(
                        node_id=str(raw_node_id),
                        node_type=str(node_type),
                        input_name=input_name,
                        picture_index=int(match.group(1)) + 1,
                    )
                )
    targets.sort(
        key=lambda target: (
            target.picture_index,
            target.node_id,
            target.input_name,
        )
    )
    return targets


def resolve_picture_binding(
    mode: str,
    *,
    picture_index: int,
    prompt: Any,
    unique_id: Any,
    image_output_index: int = 0,
) -> PictureBinding:
    if mode not in PICTURE_REFERENCE_MODES:
        raise PictureBindingError(
            f"picture_reference_mode must be one of {PICTURE_REFERENCE_MODES}"
        )
    if (
        not isinstance(picture_index, int)
        or isinstance(picture_index, bool)
        or not 1 <= picture_index <= 9
    ):
        raise PictureBindingError(
            "picture_index must be an integer between 1 and 9"
        )
    if mode == "none":
        return PictureBinding(mode=mode, picture_index=None)
    if mode == "manual":
        return PictureBinding(mode=mode, picture_index=picture_index)

    targets = _candidate_targets(
        prompt,
        unique_id=unique_id,
        image_output_index=image_output_index,
    )
    indices = sorted({target.picture_index for target in targets})
    if len(indices) > 1:
        details = "; ".join(
            f"node {target.node_id} {target.input_name} -> "
            f"<Picture {target.picture_index}>"
            for target in targets
        )
        raise PictureBindingError(
            "The analyzer image is connected to different H3 Picture "
            f"numbers: {details}. Use one H3 reference number or select manual."
        )
    return PictureBinding(
        mode=mode,
        picture_index=indices[0] if indices else None,
        targets=tuple(targets),
    )
