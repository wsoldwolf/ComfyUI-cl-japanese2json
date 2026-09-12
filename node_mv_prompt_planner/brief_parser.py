"""Parser for the existing reduced-Markdown planning subset."""

from __future__ import annotations

from ..node_japanese_to_json.compiler.comments import strip_c_comments
from .errors import PlanningBriefError
from .structures import PlanningBrief


# H3 tends to turn even explicitly "illegible" writing into pseudo-typography.
# Keep the physical writing/carving action available, but describe its visible
# residue only in geometric, non-linguistic terms.
NO_SCREEN_TEXT_DIRECTIVE = (
    "画面内に文字、数字、記号、字幕、ロゴ、透かし及び"
    "タイポグラフィを表示しない。書字・彫刻の痕跡は、長さ・向き・"
    "曲率・間隔が不規則な孤立した短い傷又は溝だけで表し、"
    "横一列又は縦一列に並べず、基準線、反復字形、字間、単語間隔、"
    "鏡文字及び反射文字を作らない。"
)
_DIRECTIVES = {
    "# サブジェクト": "subjects",
    "# 保持分析": "retention",
    "# 共通プロンプト": "common",
}
_ORDER = {name: index for index, name in enumerate(_DIRECTIVES.values())}


def parse_planning_brief(text: str) -> PlanningBrief:
    if not isinstance(text, str) or not text.strip():
        raise PlanningBriefError("planning_markdown must not be empty")
    if len(text) > 262_144:
        raise PlanningBriefError("planning_markdown exceeds 262144 characters")
    try:
        stripped = strip_c_comments(text).text
    except Exception as exc:
        raise PlanningBriefError(str(exc)) from exc

    sections: dict[str, list[str]] = {name: [] for name in _ORDER}
    seen: set[str] = set()
    current: str | None = None
    previous_order = -1
    for line_number, raw in enumerate(stripped.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            section = _DIRECTIVES.get(line)
            if section is None:
                raise PlanningBriefError(
                    f"Unsupported planning directive at line {line_number}: {line}"
                )
            if section in seen:
                raise PlanningBriefError(
                    f"Duplicate planning directive at line {line_number}: {line}"
                )
            order = _ORDER[section]
            if order < previous_order:
                raise PlanningBriefError(
                    f"Planning directives are out of order at line {line_number}"
                )
            previous_order = order
            seen.add(section)
            current = section
            continue
        if not line.startswith("* "):
            raise PlanningBriefError(
                f"Planning content must be a '* ' bullet at line {line_number}"
            )
        if current is None:
            raise PlanningBriefError(
                f"Planning bullet appears before a directive at line {line_number}"
            )
        value = line[2:].strip()
        if not value:
            raise PlanningBriefError(f"Empty planning bullet at line {line_number}")
        sections[current].append(value)

    if not sections["subjects"]:
        raise PlanningBriefError("# サブジェクト requires at least one bullet")
    if len(sections["subjects"]) > 4:
        raise PlanningBriefError("# サブジェクト supports at most 4 subjects")
    for name in seen:
        if not sections[name]:
            label = next(key for key, value in _DIRECTIVES.items() if value == name)
            raise PlanningBriefError(f"{label} requires at least one bullet")

    return PlanningBrief(
        subjects=tuple(sections["subjects"]),
        retention=tuple(sections["retention"]),
        common=tuple(sections["common"]),
    )
