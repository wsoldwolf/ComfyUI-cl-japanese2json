"""Deterministic Japanese reduced-Markdown renderer for validated MV plans."""

from __future__ import annotations

from .brief_parser import NO_SCREEN_TEXT_DIRECTIVE
from ..node_japanese_to_json.compiler.llmj2e import lex_japanese_markdown
from .errors import MVPlannerError
from .structures import MVPlan, PlanningBrief, TimelineDocument
from .timeline_parser import parse_prompt_timeline


_CAMERA_LABELS = {
    "zoom": "レンズズーム",
    "push": "前進移動",
    "pull": "後退移動",
    "pan": "水平パン",
    "truck": "水平トラック移動",
    "tilt": "垂直チルト",
    "pedestal": "垂直ペデスタル移動",
    "arc": "アーク移動",
    "tracking": "追従移動",
    "static": "固定",
    "shake": "カメラシェイク",
    "pov": "主観視点移動",
    "roll": "ロール回転",
}
_AMPLITUDE_LABELS = {
    "none": "移動なし",
    "small": "小さな移動量",
    "medium": "中程度の移動量",
    "large": "大きな移動量",
}
_SPEED_LABELS = {"slow": "ゆっくり", "moderate": "適度な速度で", "fast": "素早く"}


def _shot_seconds(milliseconds: int) -> str:
    whole, remainder = divmod(milliseconds, 1000)
    if remainder == 0:
        return str(whole)
    return f"{whole}.{remainder:03d}".rstrip("0")


def _ordered_actions(actions: tuple[str, ...]) -> tuple[str, ...]:
    """Make the model-supplied action order explicit for H3."""

    if len(actions) <= 1:
        return actions
    ordered: list[str] = []
    for index, action in enumerate(actions):
        if index == 0:
            prefix = "最初に、"
        elif index == len(actions) - 1:
            prefix = "最後に、"
        else:
            prefix = "次に、"
        ordered.append(prefix + action)
    return tuple(ordered)


def render_planned_markdown(
    brief: PlanningBrief,
    timeline: TimelineDocument,
    plan: MVPlan,
) -> str:
    planned_by_id = {scene.scene_id: scene for scene in plan.scenes}
    expected_ids = [scene.scene_id for scene in timeline.scenes]
    if sorted(planned_by_id) != expected_ids or len(planned_by_id) != len(plan.scenes):
        raise MVPlannerError("Validated plan does not contain every Timeline Scene exactly once")

    render_brief = brief
    if NO_SCREEN_TEXT_DIRECTIVE not in brief.common:
        render_brief = PlanningBrief(
            subjects=brief.subjects,
            retention=brief.retention,
            common=(*brief.common, NO_SCREEN_TEXT_DIRECTIVE),
        )
    lines = [render_brief.to_markdown()]
    for scene in timeline.scenes:
        planned = planned_by_id[scene.scene_id]
        continuation = " 継続" if scene.is_continue else ""
        lines.extend(
            [
                "",
                f"// シーン {scene.scene_id}",
                f"# シーン {scene.duration_seconds}秒{continuation}",
                "// 検出状態: "
                f"{scene.state}。ソース範囲 "
                f"{_source_timestamp(scene.source_start_ms)}-"
                f"{_source_timestamp(scene.source_end_ms)}。",
            ]
        )
        previous_section: str | None = None
        for lyric in scene.lyrics:
            if lyric.section_label and lyric.section_label != previous_section:
                lines.append(f"// 楽曲セクション: {lyric.section_label}")
                previous_section = lyric.section_label
            lines.append(f"// 歌詞: {lyric.text}")
        for shot_index, shot in enumerate(planned.shots):
            if shot_index == 0:
                lines.append("## ショット")
            else:
                lines.append(f"## ショット {_shot_seconds(shot.start_ms)}秒")
            lines.append(f"* {shot.composition}")
            lines.extend(f"* {action}" for action in _ordered_actions(shot.subject_actions))
            lines.extend(
                f"* 付加映像として、{visual.description}"
                for visual in shot.auxiliary_visuals
            )
            lines.append(f"* {shot.environment}")
            camera = shot.camera
            if camera.type == "static":
                lines.append(f"* カメラは固定し、移動せず、{camera.description}")
            else:
                lines.append(
                    "* カメラは"
                    f"{_CAMERA_LABELS[camera.type]}を使用し、"
                    f"{_AMPLITUDE_LABELS[camera.amplitude]}で"
                    f"{_SPEED_LABELS[camera.speed]}、{camera.description}"
                )
            if shot_index == 0:
                lines.extend(f"* {value}" for value in scene.lip_sync_lines)
        lines.append("## 音響")
        lines.extend(f"* {value}" for value in scene.soundscape_lines)

    result = "\n".join(lines).rstrip() + "\n"
    try:
        lex_japanese_markdown(result)
    except Exception as exc:
        raise MVPlannerError(
            "Rendered planner output failed reduced-Markdown validation: "
            f"{exc}"
        ) from exc
    reparsed = parse_prompt_timeline(result)
    if reparsed != timeline:
        raise MVPlannerError(
            "Rendered planner output changed locked Scene timing, lyrics, lip-sync, or soundscape"
        )
    return result


def _source_timestamp(milliseconds: int) -> str:
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"
