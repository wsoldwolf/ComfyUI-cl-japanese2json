"""Deterministic merger for the three global reduced-Markdown sections."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata

from ..node_japanese_to_json.compiler.comments import strip_c_comments
from .errors import PromptMergerError


_SECTION_ORDER = ("subjects", "retention", "common")
_SECTION_HEADINGS = {
    "# サブジェクト": "subjects",
    "# 保持分析": "retention",
    "# 共通プロンプト": "common",
}
_CANONICAL_HEADINGS = {value: key for key, value in _SECTION_HEADINGS.items()}
_SUBJECT_TAG_RE = re.compile(r"<Subject\s+([1-9][0-9]*)>")
_LEADING_SUBJECT_SELECTOR_RE = re.compile(
    r"^<Subject\s+([1-9][0-9]*)>\s*[:：]\s*(.+)$"
)
_RETENTION_RE = re.compile(
    r"^<Subject\s+([1-9][0-9]*)>\s+"
    r"(完全に保持|部分的に保持|弱い参照)\s*[:：]\s*(.+)$"
)
_TRANSFER_RE = re.compile(
    r"^<Subject\s+([1-9][0-9]*)>\s+属性転送\s*->\s*"
    r"<Subject\s+([1-9][0-9]*)>\s*[:：]\s*(.+)$"
)
_COMMON_DIRECT_SPEECH_RE = re.compile(r"<d>|</d>|「|」")
_MAX_SUBJECTS = 4


@dataclass(frozen=True)
class _Bullet:
    semantic: str
    payload: str
    line_number: int
    prefix_lines: tuple[str, ...] = ()


@dataclass
class _Section:
    prefix_lines: tuple[str, ...] = ()
    bullets: list[_Bullet] = field(default_factory=list)


@dataclass(frozen=True)
class _Fragment:
    sections: dict[str, _Section]
    trailing_lines: tuple[str, ...]
    newline: str

    @property
    def is_empty(self) -> bool:
        return not self.sections


@dataclass(frozen=True)
class _RetentionRule:
    subject_number: int
    relationship: str
    target_subject_number: int | None
    description: str
    raw_description: str
    bullet: _Bullet


@dataclass(frozen=True)
class MergeOutcome:
    """Merged text and deterministic diagnostic counts for the node wrapper."""

    text: str
    subject_count: int
    retention_count: int
    common_count: int
    omitted_original_common_count: int
    passthrough: bool


def _input_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise PromptMergerError(f"{name} must be a string")
    if "\x00" in value:
        raise PromptMergerError(f"{name} contains NUL")
    return value


def _newline_for(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _parse_fragment(text: str, name: str) -> _Fragment:
    try:
        scan = strip_c_comments(text)
    except Exception as exc:
        raise PromptMergerError(f"{name} has invalid comments: {exc}") from exc

    raw_lines = text.splitlines()
    clean_lines = scan.text.splitlines()
    if len(raw_lines) != len(clean_lines):
        raise PromptMergerError(f"{name} comment scanning changed line structure")

    sections: dict[str, _Section] = {}
    current: str | None = None
    last_order = -1
    pending: list[str] = []
    for index, (raw_line, clean_line) in enumerate(
        zip(raw_lines, clean_lines), start=1
    ):
        clean = clean_line.strip()
        if not clean:
            pending.append(raw_line)
            continue

        if clean.startswith("#"):
            section_name = _SECTION_HEADINGS.get(clean)
            if section_name is None:
                raise PromptMergerError(
                    f"{name} contains unsupported directive at line {index}: {clean}"
                )
            if section_name in sections:
                raise PromptMergerError(
                    f"{name} repeats {_CANONICAL_HEADINGS[section_name]} at line {index}"
                )
            order = _SECTION_ORDER.index(section_name)
            if order < last_order:
                raise PromptMergerError(
                    f"{name} has an out-of-order directive at line {index}: {clean}"
                )
            last_order = order
            sections[section_name] = _Section(prefix_lines=tuple(pending))
            pending.clear()
            current = section_name
            continue

        if not clean.startswith("* "):
            raise PromptMergerError(
                f"{name} contains non-bullet text at line {index}: {clean}"
            )
        if current is None:
            raise PromptMergerError(
                f"{name} contains a bullet before a supported directive at line {index}"
            )
        semantic = clean[2:].strip()
        if not semantic:
            raise PromptMergerError(f"{name} contains an empty bullet at line {index}")
        if current == "common" and _COMMON_DIRECT_SPEECH_RE.search(semantic):
            raise PromptMergerError(
                f"{name} # 共通プロンプト cannot contain direct speech "
                f"at line {index}"
            )
        raw_stripped = raw_line.strip()
        if not raw_stripped.startswith("* "):
            raise PromptMergerError(
                f"{name} has an invalid bullet prefix at line {index}"
            )
        payload = raw_stripped[2:].strip()
        sections[current].bullets.append(
            _Bullet(
                semantic=semantic,
                payload=payload,
                line_number=index,
                prefix_lines=tuple(pending),
            )
        )
        pending.clear()

    for section_name, section in sections.items():
        if not section.bullets:
            raise PromptMergerError(
                f"{name} {_CANONICAL_HEADINGS[section_name]} has no bullets"
            )
    if len(sections.get("subjects", _Section()).bullets) > _MAX_SUBJECTS:
        raise PromptMergerError(
            f"{name} # サブジェクト contains more than {_MAX_SUBJECTS} subjects"
        )
    return _Fragment(
        sections=sections,
        trailing_lines=tuple(pending),
        newline=_newline_for(text),
    )


def _join_text(original: str, addition: str) -> str:
    return f"{original.rstrip()} {addition.lstrip()}"


def _merge_subjects(
    original: _Section | None,
    addition: _Section | None,
) -> dict[int, _Bullet]:
    merged: dict[int, _Bullet] = {}
    if original is not None:
        for subject_number, bullet in enumerate(original.bullets, start=1):
            merged[subject_number] = bullet

    if addition is not None:
        for ordinal, bullet in enumerate(addition.bullets, start=1):
            subject_numbers = {
                int(value) for value in _SUBJECT_TAG_RE.findall(bullet.semantic)
            }
            if len(subject_numbers) > 1:
                raise PromptMergerError(
                    "merge_markdown # サブジェクト line "
                    f"{bullet.line_number} refers to multiple Subject numbers"
                )
            target = next(iter(subject_numbers), ordinal)
            if not 1 <= target <= _MAX_SUBJECTS:
                raise PromptMergerError(
                    f"merge_markdown targets Subject {target}; allowed range is 1-{_MAX_SUBJECTS}"
                )

            payload = bullet.payload
            semantic = bullet.semantic
            selector = _LEADING_SUBJECT_SELECTOR_RE.fullmatch(bullet.semantic)
            if selector is not None and int(selector.group(1)) == target:
                semantic = selector.group(2).strip()
                raw_selector = _LEADING_SUBJECT_SELECTOR_RE.fullmatch(bullet.payload)
                payload = (
                    raw_selector.group(2).strip()
                    if raw_selector is not None
                    else semantic
                )
            normalized = _Bullet(
                semantic=semantic,
                payload=payload,
                line_number=bullet.line_number,
                prefix_lines=bullet.prefix_lines,
            )
            current = merged.get(target)
            if current is None:
                merged[target] = normalized
            else:
                merged[target] = _Bullet(
                    semantic=_join_text(current.semantic, normalized.semantic),
                    payload=_join_text(current.payload, normalized.payload),
                    line_number=current.line_number,
                    prefix_lines=(*current.prefix_lines, *normalized.prefix_lines),
                )

    if merged:
        expected = set(range(1, max(merged) + 1))
        missing = sorted(expected - set(merged))
        if missing:
            raise PromptMergerError(
                "Merged # サブジェクト has a numbering gap before Subject "
                + ", ".join(str(value) for value in missing)
            )
    return merged


def _parse_retention(section: _Section | None, source: str) -> dict[int, _RetentionRule]:
    rules: dict[int, _RetentionRule] = {}
    if section is None:
        return rules
    for bullet in section.bullets:
        transfer = _TRANSFER_RE.fullmatch(bullet.semantic)
        if transfer is not None:
            subject_number = int(transfer.group(1))
            target_subject_number = int(transfer.group(2))
            relationship = "属性転送"
            description = transfer.group(3).strip()
        else:
            standard = _RETENTION_RE.fullmatch(bullet.semantic)
            if standard is None:
                raise PromptMergerError(
                    f"{source} has an invalid retention rule at line {bullet.line_number}"
                )
            subject_number = int(standard.group(1))
            target_subject_number = None
            relationship = standard.group(2)
            description = standard.group(3).strip()
        if not 1 <= subject_number <= _MAX_SUBJECTS:
            raise PromptMergerError(
                f"{source} retention rule targets Subject {subject_number}; allowed range is 1-{_MAX_SUBJECTS}"
            )
        if target_subject_number is not None:
            if not 1 <= target_subject_number <= _MAX_SUBJECTS:
                raise PromptMergerError(
                    f"{source} attribute-transfer target Subject {target_subject_number} is outside 1-{_MAX_SUBJECTS}"
                )
            if target_subject_number == subject_number:
                raise PromptMergerError(
                    f"{source} attribute transfer at line {bullet.line_number} targets itself"
                )
        if subject_number in rules:
            raise PromptMergerError(
                f"{source} repeats a retention rule for Subject {subject_number}"
            )
        raw_parts = re.split(r"[:：]", bullet.payload, maxsplit=1)
        raw_description = raw_parts[1].strip() if len(raw_parts) == 2 else description
        rules[subject_number] = _RetentionRule(
            subject_number=subject_number,
            relationship=relationship,
            target_subject_number=target_subject_number,
            description=description,
            raw_description=raw_description,
            bullet=bullet,
        )
    return rules


def _merge_retention(
    original: _Section | None,
    addition: _Section | None,
) -> dict[int, _Bullet]:
    original_rules = _parse_retention(original, "original_markdown")
    addition_rules = _parse_retention(addition, "merge_markdown")
    merged: dict[int, _Bullet] = {
        number: rule.bullet for number, rule in original_rules.items()
    }
    for number, rule in addition_rules.items():
        current_rule = original_rules.get(number)
        if current_rule is None:
            merged[number] = rule.bullet
            continue
        if (
            current_rule.relationship != rule.relationship
            or current_rule.target_subject_number != rule.target_subject_number
        ):
            raise PromptMergerError(
                "Conflicting retention relationships for Subject "
                f"{number}: {current_rule.relationship} vs {rule.relationship}"
            )
        merged[number] = _Bullet(
            semantic=_join_text(current_rule.description, rule.description),
            payload=_join_text(current_rule.bullet.payload, rule.raw_description),
            line_number=current_rule.bullet.line_number,
            prefix_lines=(
                *current_rule.bullet.prefix_lines,
                *rule.bullet.prefix_lines,
            ),
        )
    return merged


def _normalize_match_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def parse_common_omit_rules(value: object) -> tuple[str, ...]:
    text = _input_text(value, "common_omit_rules")
    terms: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("|")
        if any(not part.strip() for part in parts):
            raise PromptMergerError(
                f"common_omit_rules line {line_number} contains an empty | field"
            )
        for part in parts:
            normalized = _normalize_match_text(part.strip())
            if normalized not in seen:
                seen.add(normalized)
                terms.append(normalized)
    return tuple(terms)


def _section_prefix(
    original: _Section | None,
    addition: _Section | None,
) -> tuple[str, ...]:
    if original is None:
        return addition.prefix_lines if addition is not None else ()
    if addition is None:
        return original.prefix_lines
    return (*original.prefix_lines, *addition.prefix_lines)


def _validate_retention_subjects(
    subjects: dict[int, _Bullet],
    original: _Section | None,
    addition: _Section | None,
) -> None:
    if not subjects:
        return
    defined = set(subjects)
    for source, section in (
        ("original_markdown", original),
        ("merge_markdown", addition),
    ):
        for rule in _parse_retention(section, source).values():
            referenced = {rule.subject_number}
            if rule.target_subject_number is not None:
                referenced.add(rule.target_subject_number)
            missing = sorted(referenced - defined)
            if missing:
                raise PromptMergerError(
                    f"{source} retention rule references undefined Subject "
                    + ", ".join(str(value) for value in missing)
                )


def _render_section(
    section_name: str,
    bullets: list[_Bullet],
    prefix_lines: tuple[str, ...],
) -> list[str]:
    lines = [*prefix_lines, _CANONICAL_HEADINGS[section_name]]
    for bullet in bullets:
        lines.extend(bullet.prefix_lines)
        lines.append(f"* {bullet.payload}")
    return lines


def merge_reduced_markdown_detailed(
    original_markdown: object,
    merge_markdown: object,
    common_omit_rules: object = "",
) -> MergeOutcome:
    """Validate and merge the three global reduced-Markdown sections."""

    original_text = _input_text(original_markdown, "original_markdown")
    merge_text = _input_text(merge_markdown, "merge_markdown")
    omit_terms = parse_common_omit_rules(common_omit_rules)
    original = _parse_fragment(original_text, "original_markdown")
    addition = _parse_fragment(merge_text, "merge_markdown")
    if addition.is_empty:
        return MergeOutcome(
            text=original_text,
            subject_count=len(original.sections.get("subjects", _Section()).bullets),
            retention_count=len(original.sections.get("retention", _Section()).bullets),
            common_count=len(original.sections.get("common", _Section()).bullets),
            omitted_original_common_count=0,
            passthrough=True,
        )

    original_subjects = original.sections.get("subjects")
    addition_subjects = addition.sections.get("subjects")
    subjects = _merge_subjects(original_subjects, addition_subjects)
    original_retention = original.sections.get("retention")
    addition_retention = addition.sections.get("retention")
    retention = _merge_retention(original_retention, addition_retention)
    _validate_retention_subjects(
        subjects,
        original_retention,
        addition_retention,
    )

    merge_common = addition.sections.get("common")
    original_common = original.sections.get("common")
    common: list[_Bullet] = []
    if merge_common is not None:
        common.extend(merge_common.bullets)
    omitted = 0
    if original_common is not None:
        for bullet in original_common.bullets:
            candidate = _normalize_match_text(bullet.semantic)
            if omit_terms and any(term in candidate for term in omit_terms):
                omitted += 1
                continue
            common.append(bullet)

    rendered_sections: list[list[str]] = []
    if subjects:
        rendered_sections.append(
            _render_section(
                "subjects",
                [subjects[number] for number in sorted(subjects)],
                _section_prefix(original_subjects, addition_subjects),
            )
        )
    if retention:
        rendered_sections.append(
            _render_section(
                "retention",
                [retention[number] for number in sorted(retention)],
                _section_prefix(original_retention, addition_retention),
            )
        )
    if common:
        rendered_sections.append(
            _render_section(
                "common",
                common,
                _section_prefix(original_common, merge_common),
            )
        )

    lines: list[str] = []
    for index, section_lines in enumerate(rendered_sections):
        if index:
            lines.append("")
        lines.extend(section_lines)
    lines.extend(original.trailing_lines)
    lines.extend(addition.trailing_lines)
    newline = original.newline if original_text else addition.newline
    text = newline.join(lines)
    if text and not text.endswith(newline):
        text += newline
    return MergeOutcome(
        text=text,
        subject_count=len(subjects),
        retention_count=len(retention),
        common_count=len(common),
        omitted_original_common_count=omitted,
        passthrough=False,
    )


def merge_reduced_markdown(
    original_markdown: object,
    merge_markdown: object,
    common_omit_rules: object = "",
) -> str:
    """Return only the merged reduced-Markdown text."""

    return merge_reduced_markdown_detailed(
        original_markdown,
        merge_markdown,
        common_omit_rules,
    ).text
