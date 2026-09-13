"""Ordinary Common bullet convention shared by enhancer and planner."""

SCENE_ANCHOR_PREFIX = "情景の固定要素: "


def scene_anchor(value: str) -> str:
    return value if value.startswith(SCENE_ANCHOR_PREFIX) else SCENE_ANCHOR_PREFIX + value


def extract_scene_anchors(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value[len(SCENE_ANCHOR_PREFIX):].strip()
                             for value in values
                             if value.startswith(SCENE_ANCHOR_PREFIX)
                             and value[len(SCENE_ANCHOR_PREFIX):].strip()))
