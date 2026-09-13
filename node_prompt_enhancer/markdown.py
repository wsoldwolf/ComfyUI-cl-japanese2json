"""Reduced-Markdown inspection and narrowly scoped Common rewriting."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..node_japanese_to_json.compiler.comments import strip_c_comments
from ..node_prompt_merger.merger import merge_reduced_markdown
from .errors import PromptEnhancerError


_HEADINGS = {"# サブジェクト", "# 保持分析", "# 共通プロンプト"}
_SELECTOR_RE = re.compile(r"^<Subject\s+[1-9][0-9]*>\s*[:：]\s*(.+)$")
_RETENTION_RE = re.compile(
    r"^<Subject\s+[1-9][0-9]*>\s+(?:完全に保持|部分的に保持|弱い参照)\s*[:：]\s*(.+)$"
)
_TRANSFER_RE = re.compile(
    r"^<Subject\s+[1-9][0-9]*>\s+属性転送\s*->\s*"
    r"<Subject\s+[1-9][0-9]*>\s*[:：]\s*(.+)$"
)


@dataclass(frozen=True)
class PromptBullet:
    source_id: str
    section: str
    text: str
    line_index: int


@dataclass(frozen=True)
class PromptInspection:
    bullets: tuple[PromptBullet, ...]
    common: tuple[PromptBullet, ...]
    newline: str
    trailing_newline: bool

    def payload(self) -> dict[str, list[str]]:
        result = {"subjects": [], "retention": [], "common": []}
        for bullet in self.bullets:
            result[bullet.section].append(bullet.text)
        return result


def _validated_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise PromptEnhancerError(f"{name} must be a string")
    if "\x00" in value:
        raise PromptEnhancerError(f"{name} contains NUL")
    try:
        merge_reduced_markdown(value, "")
    except Exception as exc:
        raise PromptEnhancerError(f"{name} is not valid global reduced Markdown: {exc}") from exc
    return value


def inspect_prompt(value: object, name: str) -> PromptInspection:
    text = _validated_text(value, name)
    try:
        scan = strip_c_comments(text)
    except Exception as exc:
        raise PromptEnhancerError(f"{name} has invalid comments: {exc}") from exc
    raw_lines = text.splitlines()
    clean_lines = scan.text.splitlines()
    if len(raw_lines) != len(clean_lines):
        raise PromptEnhancerError(f"{name} comment scanning changed line structure")
    section: str | None = None
    counts = {"subjects": 0, "retention": 0, "common": 0}
    bullets: list[PromptBullet] = []
    heading_map = {
        "# サブジェクト": "subjects",
        "# 保持分析": "retention",
        "# 共通プロンプト": "common",
    }
    for line_index, clean_line in enumerate(clean_lines):
        clean = clean_line.strip()
        if clean in _HEADINGS:
            section = heading_map[clean]
            continue
        if not clean or not clean.startswith("* "):
            continue
        if section is None:
            raise PromptEnhancerError(f"{name} contains a bullet before a supported heading")
        counts[section] += 1
        prefix = {"subjects": "S", "retention": "R", "common": "C"}[section]
        bullets.append(
            PromptBullet(
                source_id=f"{prefix}{counts[section]:03d}",
                section=section,
                text=raw_lines[line_index].strip()[2:].strip(),
                line_index=line_index,
            )
        )
    return PromptInspection(
        bullets=tuple(bullets),
        common=tuple(item for item in bullets if item.section == "common"),
        newline="\r\n" if "\r\n" in text else "\n",
        trailing_newline=text.endswith(("\n", "\r")),
    )


def rewrite_common(
    markdown: str,
    inspection: PromptInspection,
    *,
    removed_source_ids: set[str],
    prepended_bullets: tuple[str, ...],
) -> str:
    if not removed_source_ids and not prepended_bullets:
        return markdown
    common_by_id = {item.source_id: item for item in inspection.common}
    unknown = sorted(removed_source_ids - set(common_by_id))
    if unknown:
        raise PromptEnhancerError(f"Unknown Common source id(s): {', '.join(unknown)}")
    removed_indices = {common_by_id[value].line_index for value in removed_source_ids}
    lines = markdown.splitlines()
    common_heading_index: int | None = None
    try:
        clean_lines = strip_c_comments(markdown).text.splitlines()
    except Exception as exc:
        raise PromptEnhancerError(f"Could not scan reduced Markdown comments: {exc}") from exc
    for index, clean_line in enumerate(clean_lines):
        if clean_line.strip() == "# 共通プロンプト":
            common_heading_index = index
            break
    additions = [f"* {value}" for value in prepended_bullets]
    if common_heading_index is None:
        rendered = [line for index, line in enumerate(lines) if index not in removed_indices]
        if additions:
            if rendered and rendered[-1].strip():
                rendered.append("")
            rendered.extend(("# 共通プロンプト", *additions))
    else:
        rendered = []
        for index, line in enumerate(lines):
            if index in removed_indices:
                continue
            rendered.append(line)
            if index == common_heading_index:
                rendered.extend(additions)
    result = inspection.newline.join(rendered)
    if result and (inspection.trailing_newline or prepended_bullets):
        result += inspection.newline
    try:
        merge_reduced_markdown(result, "")
    except Exception as exc:
        raise PromptEnhancerError(f"Enhanced base Markdown is invalid: {exc}") from exc
    return result


def protected_user_fragments(inspection: PromptInspection) -> tuple[str, ...]:
    """Return semantic user fragments that must survive deterministic merging."""

    result: list[str] = []
    for bullet in inspection.bullets:
        value = bullet.text
        for pattern in (_SELECTOR_RE, _RETENTION_RE, _TRANSFER_RE):
            match = pattern.fullmatch(value)
            if match is not None:
                value = match.group(1).strip()
                break
        if value:
            result.append(value)
    return tuple(result)
