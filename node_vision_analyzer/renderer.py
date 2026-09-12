"""Deterministic renderers for validated Vision observations."""

from __future__ import annotations

import json
import re

from .errors import VisionAnalyzerError
from .structures import FEATURE_CATEGORIES, SubjectFeature, VisionObservation


ANALYSIS_PROFILES = (
    "general",
    "subject_only",
    "scene_only",
    "planner_brief",
    "structured_json",
)
RENDERER_VERSION = "vision-renderer-v3"
_COMMON_DIRECT_SPEECH_RE = re.compile(r"<d>|</d>|「|」")


def _sentence(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if text[-1] in "。！？.!?":
        return text
    return text + "。"


def _fragment(value: str) -> str:
    return value.strip().rstrip("。！？.!? ")


def _join(values: list[str]) -> str:
    cleaned = [_fragment(value) for value in values if _fragment(value)]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return "、".join(cleaned[:-1]) + "及び" + cleaned[-1]


def _ordered_features(
    observation: VisionObservation,
) -> tuple[list[SubjectFeature], list[SubjectFeature], list[SubjectFeature]]:
    order = {name: index for index, name in enumerate(FEATURE_CATEGORIES)}
    indexed = list(enumerate(observation.primary_subject.features))
    indexed.sort(key=lambda item: (order[item[1].category], item[0]))
    clear = [feature for _, feature in indexed if feature.visibility == "clear"]
    partial = [feature for _, feature in indexed if feature.visibility == "partial"]
    uncertain = [
        feature for _, feature in indexed if feature.visibility == "uncertain"
    ]
    return clear, partial, uncertain


def _subject_and_retention(
    observation: VisionObservation,
    *,
    subject_index: int,
    picture_index: int | None,
    subject_hint: str,
    hint_mode: str,
) -> tuple[str, str]:
    observed_identity = _fragment(observation.primary_subject.identity)
    if not observed_identity:
        raise VisionAnalyzerError(
            "The selected profile requires one visible primary Subject"
        )
    locked_hint = _fragment(subject_hint) if hint_mode == "lock_identity" else ""
    identity = locked_hint or observed_identity
    clear, partial, _ = _ordered_features(observation)
    clear_text = _join([feature.description for feature in clear])
    partial_text = _join([feature.description for feature in partial])
    picture_tag = (
        f"<Picture {picture_index}>" if picture_index is not None else None
    )
    subject_tag = f"<Subject {subject_index}>"

    if picture_tag:
        subject = f"{picture_tag}を外観参照としてのみ使用する{identity}。"
    else:
        subject = _sentence(identity)
    if clear_text:
        subject += f"明瞭に確認できる特徴は、{clear_text}。"
    if partial_text:
        subject += f"見えている範囲では、{partial_text}。"

    retained = clear_text
    if partial_text:
        retained = (
            f"{retained}、見えている範囲の{partial_text}"
            if retained
            else f"見えている範囲の{partial_text}"
        )
    retention_parts: list[str] = []
    if locked_hint:
        retention_parts.append(f"ユーザー指定の{locked_hint}")
    source = f"{picture_tag}由来の" if picture_tag else ""
    if retained:
        retention_parts.append(f"{source}{retained}")
    elif not locked_hint:
        retention_parts.append(f"{source}顔及び体格")
    retention = (
        f"{subject_tag} 完全に保持: {_join(retention_parts)}及び"
        "識別可能な特徴を維持する。"
    )
    return subject, retention


def _core_safe_common_value(value: str) -> tuple[str, int]:
    """Remove image-text clauses that would become Common direct speech."""

    clauses = [part.strip() for part in re.split(r"[、,]", value) if part.strip()]
    kept = [
        clause
        for clause in clauses
        if _COMMON_DIRECT_SPEECH_RE.search(clause) is None
    ]
    return "、".join(kept), len(clauses) - len(kept)


def _scene_bullets(
    observation: VisionObservation,
    *,
    core_safe: bool = False,
) -> tuple[list[str], int]:
    bullets: list[str] = []
    excluded_clause_count = 0

    def safe(value: str) -> str:
        nonlocal excluded_clause_count
        if not core_safe:
            return value
        result, excluded = _core_safe_common_value(value)
        excluded_clause_count += excluded
        return result

    setting = safe(observation.scene.setting)
    if setting:
        bullets.append(_sentence(f"舞台は{_fragment(setting)}とする"))
    elements = [safe(value) for value in observation.scene.elements]
    elements = [value for value in elements if value]
    if elements:
        bullets.append(
            _sentence(
                "背景要素として"
                + _join(elements)
                + "を配置する"
            )
        )
    time_weather = safe(observation.scene.time_weather)
    if time_weather:
        bullets.append(
            _sentence(
                "時間帯と天候は"
                + _fragment(time_weather)
                + "とする"
            )
        )
    lighting = safe(observation.scene.lighting)
    if lighting:
        bullets.append(
            _sentence(
                "照明は" + _fragment(lighting) + "とする"
            )
        )

    composition = observation.composition
    composition_text = _join(
        [
            safe(composition.shot_size),
            safe(composition.viewpoint),
            safe(composition.subject_placement),
            safe(composition.depth),
        ]
    )
    if composition_text:
        bullets.append(_sentence(f"構図は{composition_text}とする"))

    style = observation.style
    style_text = _join(
        [safe(style.medium), safe(style.rendering), safe(style.palette)]
    )
    if style_text:
        bullets.append(_sentence(f"画風は{style_text}とする"))
    return (
        bullets or ["画像から明瞭に確認できる情景情報だけを使用する。"],
        excluded_clause_count,
    )


def _render_general(
    observation: VisionObservation,
    *,
    subject_hint: str,
    hint_mode: str,
) -> str:
    clear, partial, _ = _ordered_features(observation)
    subject = observation.primary_subject
    identity = (
        subject_hint
        if hint_mode == "lock_identity" and subject.identity.strip()
        else subject.identity
    )
    subject_parts = [identity]
    subject_parts.extend(feature.description for feature in clear)
    subject_parts.extend(
        f"一部だけ確認できる{feature.description}" for feature in partial
    )
    subject_summary = _join(subject_parts)
    if not subject_summary:
        subject_summary = "明瞭な主要人物は確認できない"

    scene_bullets, _ = _scene_bullets(observation)
    blocks = [
        "## 画像概要",
        f"* {_sentence(observation.overview)}",
        "",
        "## 主要人物",
        f"* {_sentence(subject_summary)}",
        "",
        "## 情景と構図",
        *(f"* {line}" for line in scene_bullets),
    ]
    if observation.visible_text:
        blocks.extend(
            [
                "",
                "## 画像内文字",
                *(
                    f"* {_sentence(text)}"
                    for text in observation.visible_text
                ),
            ]
        )
    if observation.uncertainties:
        blocks.extend(
            [
                "",
                "## 不確実な観測",
                *(
                    f"* {_sentence(text)}"
                    for text in observation.uncertainties
                ),
            ]
        )
    return "\n".join(blocks).rstrip() + "\n"


def _render_subject(
    observation: VisionObservation,
    *,
    subject_index: int,
    picture_index: int | None,
    subject_hint: str,
    hint_mode: str,
) -> str:
    subject, retention = _subject_and_retention(
        observation,
        subject_index=subject_index,
        picture_index=picture_index,
        subject_hint=subject_hint,
        hint_mode=hint_mode,
    )
    return (
        "# サブジェクト\n"
        f"* {subject}\n\n"
        "# 保持分析\n"
        f"* {retention}\n"
    )


def _render_scene(observation: VisionObservation) -> tuple[str, int]:
    scene_bullets, excluded = _scene_bullets(observation, core_safe=True)
    return (
        "# 共通プロンプト\n"
        + "\n".join(f"* {line}" for line in scene_bullets)
        + "\n",
        excluded,
    )


def _render_planner_brief(
    observation: VisionObservation,
    *,
    subject_index: int,
    picture_index: int | None,
    subject_hint: str,
    hint_mode: str,
) -> tuple[str, int]:
    subject, retention = _subject_and_retention(
        observation,
        subject_index=subject_index,
        picture_index=picture_index,
        subject_hint=subject_hint,
        hint_mode=hint_mode,
    )
    common: list[str] = []
    if picture_index is not None:
        common.append(
            f"<Picture {picture_index}>は<Subject {subject_index}>の外観だけに"
            "使用する。画像の背景、照明、構図、ポーズ及びカメラ位置を"
            "直接の情景参照として使用せず、情景は以下の文章から構成する。"
        )
    scene_bullets, excluded = _scene_bullets(observation, core_safe=True)
    common.extend(scene_bullets)
    return (
        "# サブジェクト\n"
        f"* {subject}\n\n"
        "# 保持分析\n"
        f"* {retention}\n\n"
        "# 共通プロンプト\n"
        + "\n".join(f"* {line}" for line in common)
        + "\n",
        excluded,
    )


def render_observation(
    observation: VisionObservation,
    *,
    profile: str,
    subject_index: int,
    picture_index: int | None,
    subject_hint: str = "",
    hint_mode: str = "observe_only",
) -> tuple[str, tuple[str, ...]]:
    if profile not in ANALYSIS_PROFILES:
        raise VisionAnalyzerError(f"Unsupported analysis_profile: {profile!r}")
    excluded_common_clauses = 0
    if profile == "general":
        result = _render_general(
            observation,
            subject_hint=subject_hint,
            hint_mode=hint_mode,
        )
    elif profile == "subject_only":
        result = _render_subject(
            observation,
            subject_index=subject_index,
            picture_index=picture_index,
            subject_hint=subject_hint,
            hint_mode=hint_mode,
        )
    elif profile == "scene_only":
        result, excluded_common_clauses = _render_scene(observation)
    elif profile == "planner_brief":
        result, excluded_common_clauses = _render_planner_brief(
            observation,
            subject_index=subject_index,
            picture_index=picture_index,
            subject_hint=subject_hint,
            hint_mode=hint_mode,
        )
    else:
        structured = observation.to_dict()
        structured["subject_hint"] = {
            "mode": hint_mode,
            "value": subject_hint,
            "identity_locked": bool(
                subject_hint and hint_mode == "lock_identity"
            ),
        }
        result = (
            json.dumps(
                structured,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    _, _, uncertain_features = _ordered_features(observation)
    warnings = [
        f"Uncertain feature excluded: {feature.description}"
        for feature in uncertain_features
    ]
    warnings.extend(
        f"Uncertain observation: {value}"
        for value in observation.uncertainties
    )
    if excluded_common_clauses:
        warnings.append(
            "Excluded "
            f"{excluded_common_clauses} scene clause(s) containing direct-speech "
            "markup from reduced Markdown"
        )
    return result, tuple(warnings)
