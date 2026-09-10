"""Hierarchical, partially recoverable MV planning over a local GGUF model."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import logging
import re
from typing import Any, Callable

from .errors import MVPlannerError, PlannerResponseError
from .placeholders import ReferenceProtector
from .prompt_loader import load_planner_prompt
from .structures import (
    MVPlan,
    PlannedScene,
    PlanningBrief,
    SongBible,
    TimelineDocument,
    TimelineScene,
)
from .validation import (
    camera_guard_issues,
    parse_scene_response,
    parse_song_bible_response,
    scene_signature,
    vocal_guard_issues,
)


LOGGER = logging.getLogger("cl_mv_prompt_planner")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
ProgressCallback = Callable[[str, int], None]
DebugEvents = list[dict[str, Any]]
_CAMERA_PROTOCOL_REPAIRS = (
    (
        "arc",
        "medium",
        "moderate",
        "被写体の左側から背後を通って右前方へ回り込む。",
    ),
    (
        "tracking",
        "medium",
        "moderate",
        "移動する被写体を斜め側方から一定距離で追従する。",
    ),
    (
        "push",
        "small",
        "slow",
        "被写体へゆっくり接近して表情を強調する。",
    ),
    (
        "pull",
        "medium",
        "slow",
        "被写体からゆっくり後退して周囲の空間を見せる。",
    ),
    (
        "truck",
        "medium",
        "moderate",
        "被写体の正面を保ちながら左から右へ平行移動する。",
    ),
    (
        "pan",
        "medium",
        "moderate",
        "固定位置から左から右へ水平に振って動作を追う。",
    ),
)


def _response_content(response: Any) -> str:
    if not isinstance(response, dict):
        raise PlannerResponseError("Planner model returned a non-object response")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise PlannerResponseError("Planner model response has no choices")
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        raise PlannerResponseError("Planner model response reached max_tokens")
    message = choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise PlannerResponseError("Planner model response has no text content")
    content = _THINK_RE.sub("", message["content"]).strip()
    if "<think" in content.casefold() or "</think" in content.casefold():
        raise PlannerResponseError("Planner model response contains incomplete thinking markup")
    if content.startswith("```") or "```" in content:
        raise PlannerResponseError("Planner model response contains a Markdown code fence")
    if not content:
        raise PlannerResponseError("Planner model response content is empty")
    return content


def _seed(seed: int, request_index: int) -> int:
    return ((seed - 1 + request_index) % 4_294_967_295) + 1


def _call(
    backend: Any,
    *,
    system: str,
    payload: dict[str, Any],
    max_tokens: int,
    temperature: float,
    top_p: float,
    repetition_penalty: float,
    seed: int,
    label: str,
    progress_callback: ProgressCallback | None,
    interrupt_callback: Callable[[], Any] | None,
    debug_events: DebugEvents | None,
) -> tuple[str, dict[str, Any] | None]:
    event: dict[str, Any] | None = None
    if debug_events is not None:
        event = {
            "label": label,
            "seed": seed,
            "settings": {
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "repetition_penalty": repetition_penalty,
            },
            "system_prompt": system,
            "request_payload": payload,
            "response_content": None,
            "parsed_response": None,
            "call_result": "started",
            "validation_result": "not_started",
        }
        debug_events.append(event)
    if progress_callback is not None:
        progress_callback(label, 0)

    def token_progress(count: int) -> None:
        if progress_callback is not None:
            progress_callback(label, count)

    try:
        response = backend.complete_chat(
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            repeat_penalty=repetition_penalty,
            seed=seed,
            progress_callback=token_progress,
            interrupt_callback=interrupt_callback,
        )
        if event is not None:
            event["response_type"] = type(response).__name__
            if isinstance(response, dict):
                event["usage"] = response.get("usage")
                choices = response.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    event["finish_reason"] = choices[0].get("finish_reason")
                    message = choices[0].get("message")
                    if isinstance(message, dict) and isinstance(
                        message.get("content"), str
                    ):
                        event["response_content"] = message["content"]
            if event["response_content"] is None:
                event["response_content"] = repr(response)
        content = _response_content(response)
        if event is not None:
            event["call_result"] = "received"
        if progress_callback is not None:
            progress_callback(label, max_tokens)
        return content, event
    except Exception as exc:
        if event is not None:
            event["call_result"] = "error"
            event["error_type"] = type(exc).__name__
            event["error"] = str(exc)
        raise


def _hard_requirements(
    brief: PlanningBrief, protector: ReferenceProtector
) -> dict[str, list[str]]:
    return {
        "subjects": [protector.protect(value) for value in brief.subjects],
        "retention": [protector.protect(value) for value in brief.retention],
        "common": [protector.protect(value) for value in brief.common],
    }


def _lyric_groups(
    scene: TimelineScene, protector: ReferenceProtector
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for lyric in scene.lyrics:
        key = (lyric.section_label, lyric.section_kind)
        if not groups or (groups[-1]["section"], groups[-1]["kind"]) != key:
            groups.append(
                {
                    "section": lyric.section_label,
                    "kind": lyric.section_kind,
                    "lyrics": [],
                }
            )
        groups[-1]["lyrics"].append(protector.protect(lyric.text))
    return groups


def _section_sources(
    timeline: TimelineDocument, protector: ReferenceProtector
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    by_label: dict[str, dict[str, Any]] = {}
    seen_lyrics: dict[str, set[str]] = {}
    for scene in timeline.scenes:
        for lyric in scene.lyrics:
            label = lyric.section_label
            if label is None:
                continue
            if label not in by_label:
                source = {
                    "label": label,
                    "kind": lyric.section_kind,
                    "lyrics": [],
                }
                by_label[label] = source
                seen_lyrics[label] = set()
                sources.append(source)
            if lyric.text not in seen_lyrics[label]:
                by_label[label]["lyrics"].append(protector.protect(lyric.text))
                seen_lyrics[label].add(lyric.text)
    return sources


def _active_section_motifs(
    scene: TimelineScene, bible: SongBible
) -> list[dict[str, str]]:
    motif_by_section = {item.section: item.motif for item in bible.section_motifs}
    active: list[dict[str, str]] = []
    seen: set[str] = set()
    for lyric in scene.lyrics:
        label = lyric.section_label
        if label is None or label in seen or label not in motif_by_section:
            continue
        seen.add(label)
        active.append({"section": label, "motif": motif_by_section[label]})
    return active


def _previous_scene_tail(
    scene: PlannedScene | None, protector: ReferenceProtector
) -> dict[str, Any] | None:
    if scene is None:
        return None
    final_shot = scene.shots[-1]
    final_action = final_shot.subject_actions[-1]
    return {
        "scene_id": scene.scene_id,
        "scene_intent": protector.protect(scene.scene_intent),
        "final_composition": protector.protect(final_shot.composition),
        "final_action": protector.protect(final_action),
        "environment": protector.protect(final_shot.environment),
        "camera": {
            "type": final_shot.camera.type,
            "amplitude": final_shot.camera.amplitude,
            "speed": final_shot.camera.speed,
            "description": protector.protect(final_shot.camera.description),
        },
    }


def _scene_duplicate_fingerprint(scene: PlannedScene) -> dict[str, Any]:
    """Describe a duplicate without priming the model with its full prose."""

    serialized = json.dumps(
        scene_signature(scene),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "scene_id": scene.scene_id,
        "signature_sha256": hashlib.sha256(serialized).hexdigest(),
        "shot_count": len(scene.shots),
        "first_shot_action_count": len(scene.shots[0].subject_actions),
        "camera_types": [shot.camera.type for shot in scene.shots],
    }


def _duplicate_repair_spec(
    avoided_scenes: tuple[PlannedScene, ...],
    *,
    retry_index: int,
) -> dict[str, Any] | None:
    """Choose a small structural difference absent from every avoided plan."""

    if not avoided_scenes:
        return None
    used_shapes = {
        (len(scene.shots), len(scene.shots[0].subject_actions))
        for scene in avoided_scenes
    }
    candidates = [
        (shot_count, action_count)
        for shot_count in range(1, 7)
        for action_count in range(1, 9)
        if (shot_count, action_count) not in used_shapes
    ]
    if not candidates:
        raise MVPlannerError(
            "Duplicate repair exhausted every supported Shot/ACTION shape"
        )
    shot_count, action_count = candidates[
        (max(1, retry_index) - 1) % len(candidates)
    ]
    return {
        "retry_index": max(1, retry_index),
        "duplicate_of_scene_ids": sorted(
            scene.scene_id for scene in avoided_scenes
        ),
        "required_shot_count": shot_count,
        "required_first_shot_action_count": action_count,
    }


def _camera_protocol_repair_spec(retry_index: int) -> dict[str, Any]:
    index = max(1, retry_index)
    camera_type, amplitude, speed, description = _CAMERA_PROTOCOL_REPAIRS[
        (index - 1) % len(_CAMERA_PROTOCOL_REPAIRS)
    ]
    return {
        "retry_index": index,
        "forbidden_literal_values": ["type", "amplitude", "speed"],
        "required_camera_type": camera_type,
        "required_camera_amplitude": amplitude,
        "required_camera_speed": speed,
        "matching_description_example": description,
    }


def _nearest_previous_scene(
    scene_id: int, planned: dict[int, PlannedScene]
) -> PlannedScene | None:
    candidates = [value for key, value in planned.items() if key < scene_id]
    return max(candidates, key=lambda value: value.scene_id, default=None)


def _scene_input(
    scene: TimelineScene,
    protector: ReferenceProtector,
    *,
    bible: SongBible | None = None,
    previous_scene: PlannedScene | None = None,
    avoid_duplicate_plans: tuple[PlannedScene, ...] = (),
    duplicate_repair: dict[str, Any] | None = None,
    camera_protocol_repair: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "scene_id": scene.scene_id,
        "duration_ms": scene.duration_seconds * 1000,
        "state": scene.state,
        "source_start_ms": scene.source_start_ms,
        "source_end_ms": scene.source_end_ms,
        "lyric_groups": _lyric_groups(scene, protector),
        "active_section_motifs": (
            _active_section_motifs(scene, bible) if bible is not None else []
        ),
        "previous_scene_tail": _previous_scene_tail(previous_scene, protector),
        "avoid_duplicate_plans": [
            _scene_duplicate_fingerprint(value)
            for value in avoid_duplicate_plans
        ],
        "duplicate_repair": duplicate_repair,
        "camera_protocol_repair": camera_protocol_repair,
        "locked_lip_sync": [
            protector.protect(value) for value in scene.lip_sync_lines
        ],
        "locked_soundscape": [
            protector.protect(value) for value in scene.soundscape_lines
        ],
    }


def _build_protector(
    brief: PlanningBrief, timeline: TimelineDocument
) -> tuple[ReferenceProtector, dict[str, list[str]], list[dict[str, Any]]]:
    protector = ReferenceProtector()
    hard_requirements = _hard_requirements(brief, protector)
    section_sources = _section_sources(timeline, protector)
    for scene in timeline.scenes:
        _scene_input(scene, protector)
    return protector, hard_requirements, section_sources


def _plan_song_bible(
    backend: Any,
    *,
    hard_requirements: dict[str, list[str]],
    section_sources: list[dict[str, Any]],
    timeline_scene_count: int,
    protector: ReferenceProtector,
    call_settings: dict[str, Any],
    retry_max: int,
    request_index: int,
    progress_callback: ProgressCallback | None,
    interrupt_callback: Callable[[], Any] | None,
    debug_events: DebugEvents | None,
    debug_state: dict[str, Any] | None,
) -> tuple[SongBible, int, int]:
    section_labels = [str(source["label"]) for source in section_sources]
    payload = {
        "protocol": "clmv-song-bible-line-v2",
        "hard_requirements": hard_requirements,
        "reference_legend": protector.legend(),
        "timeline_scene_count": timeline_scene_count,
        "section_sources": section_sources,
    }
    last_error: Exception | None = None
    for attempt in range(retry_max + 1):
        request_index += 1
        try:
            content, event = _call(
                backend,
                system=load_planner_prompt("song_bible_system_prompt.txt"),
                payload=payload,
                seed=_seed(call_settings["seed"], request_index - 1),
                label=f"song-bible attempt {attempt + 1}",
                progress_callback=progress_callback,
                interrupt_callback=interrupt_callback,
                debug_events=debug_events,
                **{key: value for key, value in call_settings.items() if key != "seed"},
            )
            bible = parse_song_bible_response(
                content,
                protector,
                expected_sections=tuple(section_labels),
            )
            if event is not None:
                event["validation_result"] = "success"
                event["parsed_response"] = asdict(bible)
            if debug_state is not None:
                debug_state.update(
                    {
                        "stage": "song_bible_complete",
                        "song_bible": asdict(bible),
                        "request_count": request_index,
                        "last_error": None,
                    }
                )
            return bible, request_index, attempt
        except (PlannerResponseError, MVPlannerError) as exc:
            last_error = exc
            if debug_events:
                debug_events[-1]["validation_result"] = "error"
                debug_events[-1]["validation_error"] = str(exc)
            if debug_state is not None:
                debug_state.update(
                    {
                        "stage": "song_bible_failed",
                        "request_count": request_index,
                        "last_error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if attempt >= retry_max:
                break
            LOGGER.warning(
                "[cl_mv_prompt_planner] Song bible validation failed; retry %d/%d: %s",
                attempt + 1,
                retry_max,
                exc,
            )
    raise MVPlannerError(
        f"Song bible failed after {retry_max} retry attempt(s): {last_error}"
    ) from last_error


def generate_mv_plan(
    brief: PlanningBrief,
    timeline: TimelineDocument,
    backend: Any,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    top_p: float = 0.9,
    repetition_penalty: float = 1.05,
    seed: int = 1,
    scenes_per_batch: int = 6,
    retry_max: int = 10,
    camera_guard: str = "warn",
    vocal_guard: str = "warn",
    progress_callback: ProgressCallback | None = None,
    interrupt_callback: Callable[[], Any] | None = None,
    debug_events: DebugEvents | None = None,
    debug_state: dict[str, Any] | None = None,
) -> MVPlan:
    if not 1 <= scenes_per_batch <= 16:
        raise MVPlannerError("scenes_per_batch must be between 1 and 16")
    if not 0 <= retry_max <= 20:
        raise MVPlannerError("retry_max must be between 0 and 20")
    if camera_guard not in {"warn", "strict"}:
        raise MVPlannerError("camera_guard must be warn or strict")
    if vocal_guard not in {"warn", "strict"}:
        raise MVPlannerError("vocal_guard must be warn or strict")
    protector, hard_requirements, section_sources = _build_protector(
        brief, timeline
    )
    if debug_state is not None:
        debug_state.clear()
        debug_state.update(
            {
                "status": "running",
                "stage": "initialized",
                "timeline_scene_ids": [
                    scene.scene_id for scene in timeline.scenes
                ],
                "planned_scene_ids": [],
                "pending_scene_ids": [],
                "request_count": 0,
                "reference_legend": protector.legend(),
                "hard_requirements": hard_requirements,
                "section_sources": section_sources,
                "camera_guard": camera_guard,
                "camera_warnings": [],
                "vocal_guard": vocal_guard,
                "vocal_warnings": [],
                "duplicate_scene_diagnostics": [],
                "last_error": None,
            }
        )
    call_settings = {
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "repetition_penalty": repetition_penalty,
        "seed": seed,
    }
    bible, request_index, bible_retries = _plan_song_bible(
        backend,
        hard_requirements=hard_requirements,
        section_sources=section_sources,
        timeline_scene_count=len(timeline.scenes),
        protector=protector,
        call_settings=call_settings,
        retry_max=retry_max,
        request_index=0,
        progress_callback=progress_callback,
        interrupt_callback=interrupt_callback,
        debug_events=debug_events,
        debug_state=debug_state,
    )

    planned: dict[int, PlannedScene] = {}
    camera_warnings: list[dict[str, object]] = []
    vocal_warnings: list[dict[str, object]] = []
    duplicate_diagnostics: list[dict[str, int]] = []
    scene_retries = 0
    scenes = list(timeline.scenes)
    for batch_start in range(0, len(scenes), scenes_per_batch):
        initial_group = scenes[batch_start : batch_start + scenes_per_batch]
        pending = {scene.scene_id: scene for scene in initial_group}
        duplicate_avoidance: dict[int, dict[int, PlannedScene]] = {}
        duplicate_repair_indices: dict[int, int] = {}
        camera_protocol_repair_indices: dict[int, int] = {}
        failure_counts = {scene.scene_id: 0 for scene in initial_group}
        serial_retry = False
        retry_feedback: str | None = None
        retry_feedback_by_scene: dict[int, str] = {}
        batch_attempt = 0
        while pending:
            batch_attempt += 1
            request_index += 1
            if serial_retry and len(pending) > 1:
                next_scene_id = min(pending)
                request_pending = {next_scene_id: pending[next_scene_id]}
            else:
                request_pending = pending
            pending_before = sorted(request_pending)
            scene_inputs: list[dict[str, Any]] = []
            request_repairs: dict[int, dict[str, Any]] = {}
            request_camera_repairs: dict[int, dict[str, Any]] = {}
            for scene in request_pending.values():
                previous_scene = _nearest_previous_scene(
                    scene.scene_id, planned
                )
                avoidance = dict(
                    duplicate_avoidance.get(scene.scene_id, {})
                )
                if avoidance and previous_scene is not None:
                    avoidance.setdefault(
                        previous_scene.scene_id, previous_scene
                    )
                avoided_scenes = tuple(avoidance.values())
                repair = _duplicate_repair_spec(
                    avoided_scenes,
                    retry_index=duplicate_repair_indices.get(
                        scene.scene_id, 1
                    ),
                )
                if repair is not None:
                    request_repairs[scene.scene_id] = repair
                    duplicate_repair_indices[scene.scene_id] = (
                        int(repair["retry_index"]) + 1
                    )
                camera_repair: dict[str, Any] | None = None
                if scene.scene_id in camera_protocol_repair_indices:
                    camera_repair = _camera_protocol_repair_spec(
                        camera_protocol_repair_indices[scene.scene_id]
                    )
                    request_camera_repairs[scene.scene_id] = camera_repair
                    camera_protocol_repair_indices[scene.scene_id] = (
                        int(camera_repair["retry_index"]) + 1
                    )
                scene_inputs.append(
                    _scene_input(
                        scene,
                        protector,
                        bible=bible,
                        previous_scene=previous_scene,
                        avoid_duplicate_plans=avoided_scenes,
                        duplicate_repair=repair,
                        camera_protocol_repair=camera_repair,
                    )
                )
            payload = {
                "protocol": "clmv-scene-line-v1",
                "hard_requirements": hard_requirements,
                "reference_legend": protector.legend(),
                "song_bible": {
                    "visual_arc": bible.visual_arc,
                    "camera_strategy": list(bible.camera_strategy),
                },
                "requested_scene_ids": pending_before,
                "requested_scene_count": len(pending_before),
                "scenes": scene_inputs,
            }
            request_retry_feedback = retry_feedback
            specific_feedback = [
                retry_feedback_by_scene[scene_id]
                for scene_id in pending_before
                if scene_id in retry_feedback_by_scene
            ]
            if specific_feedback:
                request_retry_feedback = "; ".join(specific_feedback)
            if request_retry_feedback is not None:
                payload["retry_feedback"] = (
                    "The previous response failed validation. "
                    f"{request_retry_feedback} Return exactly the requested "
                    "Scene IDs. "
                    "Use the exact line protocol. Start every requested record "
                    "with SCENE<TAB>id and close it with END_SCENE."
                )
            last_error: Exception | None = None
            if debug_state is not None:
                debug_state.update(
                    {
                        "stage": "scene_batch_running",
                        "active_batch": batch_start // scenes_per_batch + 1,
                        "active_attempt": batch_attempt,
                        "request_count": request_index,
                        "planned_scene_ids": sorted(planned),
                        "pending_scene_ids": pending_before,
                        "scene_failure_counts": dict(failure_counts),
                        "last_error": None,
                    }
                )
            failed_this_attempt: list[int] = []
            recovered_count = 0
            retry_log_kind = "partial"
            try:
                content, event = _call(
                    backend,
                    system=load_planner_prompt("scene_plan_system_prompt.txt"),
                    payload=payload,
                    seed=_seed(seed, request_index - 1),
                    label=(
                        f"scene batch {batch_start // scenes_per_batch + 1} "
                        f"attempt {batch_attempt}"
                    ),
                    progress_callback=progress_callback,
                    interrupt_callback=interrupt_callback,
                    debug_events=debug_events,
                    **{key: value for key, value in call_settings.items() if key != "seed"},
                )
                recovered, errors = parse_scene_response(
                    content, request_pending, protector
                )
                for scene_id, message in errors.items():
                    if any(
                        marker in message
                        for marker in (
                            "uses unknown camera type",
                            "uses invalid camera amplitude",
                            "uses invalid camera speed",
                            "static camera requires amplitude",
                            "moving camera cannot use amplitude",
                            "moving camera cannot use speed",
                        )
                    ):
                        camera_protocol_repair_indices.setdefault(
                            scene_id, 1
                        )
                if not recovered and len(pending) > 1:
                    serial_retry = True
                accepted: dict[int, PlannedScene] = {}
                duplicate_sources: dict[int, int] = {}
                camera_guard_diagnostics: dict[
                    int, list[dict[str, object]]
                ] = {}
                vocal_guard_diagnostics: dict[
                    int, list[dict[str, object]]
                ] = {}
                for scene_id, recovered_scene in sorted(recovered.items()):
                    camera_issues = camera_guard_issues(recovered_scene)
                    vocal_issues = vocal_guard_issues(
                        recovered_scene, pending[scene_id]
                    )
                    strict_issues: list[dict[str, object]] = []
                    if camera_issues and camera_guard == "strict":
                        camera_guard_diagnostics[scene_id] = camera_issues
                        strict_issues.extend(camera_issues)
                    if vocal_issues and vocal_guard == "strict":
                        vocal_guard_diagnostics[scene_id] = vocal_issues
                        strict_issues.extend(vocal_issues)
                    if strict_issues:
                        errors[scene_id] = "; ".join(
                            str(issue["message"]) for issue in strict_issues
                        )
                        continue

                    existing = {**planned, **accepted}
                    duplicate_id = next(
                        (
                            other_id
                            for other_id, other_scene in sorted(existing.items())
                            if other_id < scene_id
                            and scene_signature(other_scene)
                            == scene_signature(recovered_scene)
                        ),
                        None,
                    )
                    if duplicate_id is not None:
                        duplicate_sources[scene_id] = duplicate_id
                        diagnostic = {
                            "scene_id": scene_id,
                            "duplicate_of": duplicate_id,
                        }
                        if diagnostic not in duplicate_diagnostics:
                            duplicate_diagnostics.append(diagnostic)
                        duplicate_avoidance.setdefault(scene_id, {})[
                            duplicate_id
                        ] = existing[duplicate_id]
                        duplicate_repair_indices.setdefault(scene_id, 1)
                        serial_retry = True
                        errors[scene_id] = (
                            f"Scene {scene_id} duplicates the complete visual plan "
                            f"of Scene {duplicate_id}"
                        )
                        continue

                    accepted[scene_id] = recovered_scene
                    if camera_issues:
                        for issue in camera_issues:
                            LOGGER.warning(
                                "[cl_mv_prompt_planner] %s; continuing because "
                                "camera_guard=warn",
                                issue["message"],
                            )
                        camera_warnings.extend(camera_issues)
                    if vocal_issues:
                        for issue in vocal_issues:
                            LOGGER.warning(
                                "[cl_mv_prompt_planner] %s; continuing because "
                                "vocal_guard=warn",
                                issue["message"],
                            )
                        vocal_warnings.extend(vocal_issues)

                recovered = accepted
                recovered_count = len(recovered)
                for scene_id, message in errors.items():
                    retry_feedback_by_scene[scene_id] = message
                for scene_id in recovered:
                    retry_feedback_by_scene.pop(scene_id, None)
                planned.update(accepted)
                pending = {
                    scene_id: scene for scene_id, scene in pending.items() if scene_id not in recovered
                }
                failed_this_attempt = [
                    scene_id
                    for scene_id in request_pending
                    if scene_id in pending
                ]
                if event is not None:
                    event.update(
                        {
                            "parsed_response": {
                                str(scene_id): asdict(scene)
                                for scene_id, scene in sorted(recovered.items())
                            },
                            "pending_scene_ids_before": pending_before,
                            "all_pending_scene_ids_before": sorted(
                                set(pending_before) | set(pending)
                            ),
                            "recovered_scene_ids": sorted(recovered),
                            "scene_errors": errors,
                            "camera_guard": camera_guard,
                            "camera_guard_diagnostics": camera_guard_diagnostics,
                            "camera_warnings": [
                                issue
                                for issue in camera_warnings
                                if issue["scene_id"] in recovered
                            ],
                            "vocal_guard": vocal_guard,
                            "vocal_guard_diagnostics": vocal_guard_diagnostics,
                            "vocal_warnings": [
                                issue
                                for issue in vocal_warnings
                                if issue["scene_id"] in recovered
                            ],
                            "duplicate_scene_sources": duplicate_sources,
                            "duplicate_repair_requirements": request_repairs,
                            "camera_protocol_repair_requirements": (
                                request_camera_repairs
                            ),
                            "pending_scene_ids_after": sorted(pending),
                            "validation_result": (
                                "success"
                                if not pending
                                else "partial"
                                if recovered
                                else "error"
                            ),
                        }
                    )
                if debug_state is not None:
                    debug_state.update(
                        {
                            "stage": (
                                "scene_batch_complete"
                                if not pending
                                else "scene_batch_partial"
                            ),
                            "planned_scene_ids": sorted(planned),
                            "planned_scenes": {
                                str(scene_id): asdict(scene)
                                for scene_id, scene in sorted(planned.items())
                            },
                            "pending_scene_ids": sorted(pending),
                            "scene_failure_counts": dict(failure_counts),
                            "last_scene_errors": errors,
                            "camera_warnings": camera_warnings,
                            "vocal_warnings": vocal_warnings,
                            "duplicate_scene_diagnostics": duplicate_diagnostics,
                        }
                    )
                if not pending:
                    break
                combined_errors = "; ".join(
                    errors[scene_id] for scene_id in sorted(errors)[:4]
                )
                if not combined_errors:
                    combined_errors = (
                        "Continue with the next unresolved Scene using its newly "
                        "updated previous_scene_tail and avoid_duplicate_plans"
                    )
                last_error = PlannerResponseError(combined_errors)
                retry_feedback = str(last_error)
                if debug_state is not None:
                    debug_state["last_error"] = (
                        f"{type(last_error).__name__}: {last_error}"
                    )
            except (PlannerResponseError, MVPlannerError) as exc:
                last_error = exc
                retry_feedback = str(exc)
                failed_this_attempt = list(request_pending)
                retry_log_kind = "validation"
                if debug_events:
                    debug_events[-1]["validation_result"] = "error"
                    debug_events[-1]["validation_error"] = str(exc)
                    debug_events[-1].setdefault(
                        "pending_scene_ids_before", pending_before
                    )
                    debug_events[-1].setdefault(
                        "pending_scene_ids_after", sorted(pending)
                    )
                if debug_state is not None:
                    debug_state.update(
                        {
                            "stage": "scene_batch_failed",
                            "planned_scene_ids": sorted(planned),
                            "planned_scenes": {
                                str(scene_id): asdict(scene)
                                for scene_id, scene in sorted(planned.items())
                            },
                            "pending_scene_ids": sorted(pending),
                            "scene_failure_counts": dict(failure_counts),
                            "last_error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            for scene_id in failed_this_attempt:
                failure_counts[scene_id] += 1
            exhausted = [
                scene_id
                for scene_id in failed_this_attempt
                if failure_counts[scene_id] > retry_max
            ]
            if exhausted:
                unresolved = ", ".join(
                    str(value) for value in sorted(exhausted)
                )
                if debug_state is not None:
                    debug_state.update(
                        {
                            "status": "error",
                            "stage": "scene_planning_exhausted",
                            "unresolved_scene_ids": sorted(exhausted),
                            "scene_failure_counts": dict(failure_counts),
                        }
                    )
                raise MVPlannerError(
                    f"Scene planning failed after {retry_max} retry "
                    f"attempt(s) for unresolved Scene(s): {unresolved}; "
                    f"{last_error}"
                ) from last_error
            retry_counts = ", ".join(
                f"Scene {scene_id}={failure_counts[scene_id]}/{retry_max}"
                for scene_id in failed_this_attempt
            )
            if retry_log_kind == "validation":
                LOGGER.warning(
                    "[cl_mv_prompt_planner] Scene request validation failed; "
                    "retrying %d unresolved Scene(s) with per-Scene budget "
                    "(%s): %s",
                    len(pending),
                    retry_counts,
                    last_error,
                )
            else:
                LOGGER.warning(
                    "[cl_mv_prompt_planner] Retained %d valid Scene(s); "
                    "continuing with %d unresolved Scene(s) using per-Scene "
                    "retry budget (%s): %s",
                    recovered_count,
                    len(pending),
                    retry_counts or "no failed requested Scene",
                    last_error,
                )
            scene_retries += 1

    ordered = tuple(planned[index] for index in range(1, len(scenes) + 1))
    restored_bible = SongBible(
        visual_arc=protector.restore(bible.visual_arc),
        camera_strategy=tuple(
            protector.restore(value) for value in bible.camera_strategy
        ),
        section_motifs=tuple(
            type(item)(
                section=item.section,
                motif=protector.restore(item.motif),
            )
            for item in bible.section_motifs
        ),
    )
    plan = MVPlan(
        song_bible=restored_bible,
        scenes=ordered,
        attempts=bible_retries + scene_retries,
        metadata={
            "request_count": request_index,
            "scenes_per_batch": scenes_per_batch,
            "response_protocol": "clmv-line-v2",
            "song_bible_protocol": "clmv-song-bible-line-v2",
            "scene_protocol": "clmv-scene-line-v1",
            "camera_guard": camera_guard,
            "camera_warnings": camera_warnings,
            "vocal_guard": vocal_guard,
            "vocal_warnings": vocal_warnings,
            "duplicate_scene_diagnostics": duplicate_diagnostics,
            "reference_legend": protector.legend(),
            "planning_brief": asdict(brief),
            "locked_timeline": asdict(timeline),
        },
    )
    if debug_state is not None:
        debug_state.update(
            {
                "status": "success",
                "stage": "complete",
                "planned_scene_ids": sorted(planned),
                "planned_scenes": {
                    str(scene_id): asdict(scene)
                    for scene_id, scene in sorted(planned.items())
                },
                "pending_scene_ids": [],
                "camera_warnings": camera_warnings,
                "vocal_warnings": vocal_warnings,
                "duplicate_scene_diagnostics": duplicate_diagnostics,
                "request_count": request_index,
                "last_error": None,
            }
        )
    return plan
