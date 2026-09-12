"""Hierarchical, partially recoverable MV planning over a local GGUF model."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import logging
import re
import threading
import time
from typing import Any, Callable

from .camera_policy import required_camera_sequence
from .errors import (
    MVPlannerError,
    PlannerInferenceStallError,
    PlannerResponseError,
)
from .placeholders import ReferenceProtector
from .prompt_loader import load_planner_prompt, load_profiled_planner_prompt
from .structures import (
    AuxiliaryVisual,
    LyricActionBlueprint,
    MVPlan,
    PlannedScene,
    PlannedShot,
    PlanningBrief,
    SongBible,
    TimelineDocument,
    TimelineScene,
)
from .validation import (
    auxiliary_visual_description_signature,
    auxiliary_visual_signatures,
    camera_guard_issues,
    parse_scene_response,
    parse_auxiliary_visual_repair_response,
    parse_lyric_action_response,
    parse_song_bible_response,
    repair_subject_motion,
    scene_signature,
    subject_motion_issues,
    vocal_guard_issues,
)
from .visual_profiles import (
    DEFAULT_VISUAL_PROFILE_ID,
    VisualEnrichmentProfile,
    load_visual_profile,
)


LOGGER = logging.getLogger("cl_mv_prompt_planner")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_TARGETED_AUXILIARY_REPAIR_LIMIT = 2
INFERENCE_HEARTBEAT_SECONDS = 10.0
INFERENCE_FIRST_CHUNK_TIMEOUT_SECONDS = 120.0
INFERENCE_STALL_SECONDS = 45.0
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

    streamed_chunks = 0
    last_chunk_at: float | None = None
    progress_callback_active = False
    progress_lock = threading.Lock()
    heartbeat_stop = threading.Event()
    inference_started = time.monotonic()

    def token_progress(count: int) -> None:
        nonlocal streamed_chunks, last_chunk_at, progress_callback_active
        with progress_lock:
            streamed_chunks = max(streamed_chunks, int(count))
            current = streamed_chunks
            last_chunk_at = time.monotonic()
        if progress_callback is not None:
            with progress_lock:
                progress_callback_active = True
            try:
                progress_callback(label, current)
            finally:
                with progress_lock:
                    progress_callback_active = False

    def log_inference_heartbeat() -> None:
        while not heartbeat_stop.wait(INFERENCE_HEARTBEAT_SECONDS):
            with progress_lock:
                current = streamed_chunks
                chunk_at = last_chunk_at
                callback_active = progress_callback_active
            now = time.monotonic()
            chunk_age = (
                "n/a" if chunk_at is None else f"{now - chunk_at:.1f}s"
            )
            LOGGER.info(
                "[cl_mv_prompt_planner] LLM inference active: %s "
                "elapsed=%.1fs streamed_chunks=%d last_chunk_age=%s "
                "progress_callback_active=%s",
                label,
                now - inference_started,
                current,
                chunk_age,
                callback_active,
            )

    def check_inference_abort() -> bool:
        if interrupt_callback is not None:
            requested = interrupt_callback()
            if requested:
                return True
        with progress_lock:
            chunk_at = last_chunk_at
            current = streamed_chunks
        now = time.monotonic()
        idle_started = chunk_at if chunk_at is not None else inference_started
        idle_seconds = now - idle_started
        timeout_seconds = (
            INFERENCE_STALL_SECONDS
            if chunk_at is not None
            else INFERENCE_FIRST_CHUNK_TIMEOUT_SECONDS
        )
        if idle_seconds >= timeout_seconds:
            phase = (
                "after output began"
                if chunk_at is not None
                else "before first output"
            )
            raise PlannerInferenceStallError(
                f"Planner LLM inference stalled {phase}: no new streamed "
                f"chunk for {idle_seconds:.1f}s (received {current} chunk(s), "
                f"timeout {timeout_seconds:.0f}s)"
            )
        return False

    heartbeat = threading.Thread(
        target=log_inference_heartbeat,
        name="cl_mv_prompt_planner-llm-heartbeat",
        daemon=True,
    )
    heartbeat.start()

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
            interrupt_callback=check_inference_abort,
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
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=0.25)
        LOGGER.info(
            "[cl_mv_prompt_planner] LLM inference completed: %s "
            "elapsed=%.1fs streamed_chunks=%d",
            label,
            time.monotonic() - inference_started,
            streamed_chunks,
        )


def _planning_brief_payload(
    brief: PlanningBrief, protector: ReferenceProtector
) -> dict[str, list[str]]:
    """Expose the existing Markdown subset with explicit planning roles.

    Subject identity and retention remain immutable.  Common prompt bullets are
    global art/world direction: they constrain the plan, but are not a list of
    Scene events for the model to copy into every response.
    """

    return {
        "subject_identity": [
            protector.protect(value) for value in brief.subjects
        ],
        "retention_constraints": [
            protector.protect(value) for value in brief.retention
        ],
        "global_visual_direction": [
            protector.protect(value) for value in brief.common
        ],
    }


def _lyric_lines(
    scene: TimelineScene, protector: ReferenceProtector
) -> list[dict[str, Any]]:
    """Return each Scene lyric once with a stable one-based anchor index."""

    return [
        {
            "index": index,
            "section": lyric.section_label,
            "kind": lyric.section_kind,
            "text": protector.protect(lyric.text),
        }
        for index, lyric in enumerate(scene.lyrics, start=1)
    ]


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


def _replace_single_auxiliary_visual(
    scene: PlannedScene,
    replacement: AuxiliaryVisual,
) -> PlannedScene:
    """Replace the sole AUX_VISUAL without changing any Scene choreography."""

    locations = [
        shot_index
        for shot_index, shot in enumerate(scene.shots)
        if shot.auxiliary_visuals
    ]
    if len(locations) != 1 or len(scene.shots[locations[0]].auxiliary_visuals) != 1:
        raise MVPlannerError(
            "Targeted AUX_VISUAL repair requires exactly one existing visual"
        )
    target_index = locations[0]
    shots = tuple(
        PlannedShot(
            start_ms=shot.start_ms,
            composition=shot.composition,
            subject_actions=shot.subject_actions,
            environment=shot.environment,
            camera=shot.camera,
            auxiliary_visuals=(replacement,) if index == target_index else (),
        )
        for index, shot in enumerate(scene.shots)
    )
    return PlannedScene(
        scene_id=scene.scene_id,
        scene_intent=scene.scene_intent,
        shots=shots,
        lyric_anchor_index=scene.lyric_anchor_index,
        lyric_response_mode=scene.lyric_response_mode,
    )


def _targeted_auxiliary_visual_repair(
    backend: Any,
    *,
    scene: PlannedScene,
    timeline_scene: TimelineScene,
    planning_brief: dict[str, list[str]],
    protector: ReferenceProtector,
    visual_profile: VisualEnrichmentProfile,
    forbidden_descriptions: tuple[str, ...],
    repair_index: int,
    call_settings: dict[str, Any],
    retry_max: int,
    request_index: int,
    progress_callback: ProgressCallback | None,
    interrupt_callback: Callable[[], Any] | None,
    debug_events: DebugEvents | None,
) -> tuple[PlannedScene | None, int, int]:
    """Repair one exact duplicate while preserving a valid Scene record."""

    existing_visuals = [
        visual
        for shot in scene.shots
        for visual in shot.auxiliary_visuals
    ]
    if len(existing_visuals) != 1:
        return None, request_index, 0
    expected_kind = existing_visuals[0].kind
    attempt_limit = min(_TARGETED_AUXILIARY_REPAIR_LIMIT, retry_max)
    if attempt_limit <= 0:
        return None, request_index, 0
    payload = {
        "protocol": "clmv-auxiliary-visual-repair-line-v1",
        "scene_id": scene.scene_id,
        "expected_kind": expected_kind,
        "required_development_operation": _AUXILIARY_REPAIR_OPERATIONS[
            (max(1, repair_index) - 1) % len(_AUXILIARY_REPAIR_OPERATIONS)
        ],
        "forbidden_exact_descriptions": [
            protector.protect(value) for value in forbidden_descriptions
        ],
        "planning_brief": planning_brief,
        "lyric_lines": _lyric_lines(timeline_scene, protector),
        "selected_lyric_response": {
            "anchor_index": scene.lyric_anchor_index,
            "mode": scene.lyric_response_mode,
        },
        "scene_context": {
            "composition": protector.protect(scene.shots[-1].composition),
            "actions": [
                protector.protect(value)
                for shot in scene.shots
                for value in shot.subject_actions
            ],
            "environment": protector.protect(scene.shots[-1].environment),
        },
        "reference_legend": protector.legend(),
    }
    last_error: Exception | None = None
    for attempt in range(1, attempt_limit + 1):
        request_index += 1
        try:
            content, event = _call(
                backend,
                system=load_planner_prompt(
                    "auxiliary_visual_repair_system_prompt.txt"
                ),
                payload=payload,
                max_tokens=min(512, call_settings["max_tokens"]),
                temperature=call_settings["temperature"],
                top_p=call_settings["top_p"],
                repetition_penalty=call_settings["repetition_penalty"],
                seed=_seed(call_settings["seed"], request_index - 1),
                label=(
                    f"targeted AUX_VISUAL repair Scene {scene.scene_id} "
                    f"attempt {attempt}"
                ),
                progress_callback=progress_callback,
                interrupt_callback=interrupt_callback,
                debug_events=debug_events,
            )
            replacement = parse_auxiliary_visual_repair_response(
                content,
                protector,
                expected_kind=expected_kind,
                visual_profile=visual_profile,
            )
            signature = auxiliary_visual_description_signature(
                replacement.description
            )
            forbidden_signatures = {
                auxiliary_visual_description_signature(value)
                for value in forbidden_descriptions
            }
            if signature in forbidden_signatures:
                raise PlannerResponseError(
                    "Targeted AUX_VISUAL repair repeated a forbidden exact description"
                )
            repaired = _replace_single_auxiliary_visual(scene, replacement)
            repair_guard_issues = [
                *camera_guard_issues(repaired),
                *vocal_guard_issues(repaired, timeline_scene),
            ]
            if repair_guard_issues:
                raise PlannerResponseError(
                    "Targeted AUX_VISUAL repair violated a locked field boundary: "
                    + "; ".join(
                        str(issue["message"]) for issue in repair_guard_issues
                    )
                )
            if event is not None:
                event["validation_result"] = "success"
                event["parsed_response"] = asdict(replacement)
            LOGGER.info(
                "[cl_mv_prompt_planner] Repaired Scene %d duplicated "
                "AUX_VISUAL with a targeted one-line inference on attempt %d",
                scene.scene_id,
                attempt,
            )
            return repaired, request_index, attempt
        except (PlannerResponseError, MVPlannerError) as exc:
            last_error = exc
            if debug_events:
                debug_events[-1]["validation_result"] = "error"
                debug_events[-1]["validation_error"] = str(exc)
            payload["retry_feedback"] = (
                "The previous one-line repair failed: "
                f"{exc}. Return exactly one AUX_VISUAL line with a genuinely "
                "different description."
            )
            if attempt < attempt_limit:
                LOGGER.warning(
                    "[cl_mv_prompt_planner] Targeted AUX_VISUAL repair for "
                    "Scene %d failed; retry %d/%d: %s",
                    scene.scene_id,
                    attempt,
                    attempt_limit,
                    exc,
                )
    LOGGER.warning(
        "[cl_mv_prompt_planner] Targeted AUX_VISUAL repair for Scene %d "
        "failed after %d attempt(s): %s",
        scene.scene_id,
        attempt_limit,
        last_error,
    )
    return None, request_index, attempt_limit


def _recent_scene_patterns(
    scene_id: int,
    planned: dict[int, PlannedScene],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Return compact recent choices without replaying creative prose."""

    candidates = [
        value for key, value in sorted(planned.items()) if key < scene_id
    ][-limit:]
    return [
        {
            "scene_id": scene.scene_id,
            "camera_types": [shot.camera.type for shot in scene.shots],
            "camera_motion_signatures": [
                f"{shot.camera.type}:{shot.camera.amplitude}:{shot.camera.speed}"
                for shot in scene.shots
            ],
            "auxiliary_visual_kinds": [
                visual.kind
                for shot in scene.shots
                for visual in shot.auxiliary_visuals
            ],
        }
        for scene in candidates
    ]


def _camera_choreography_contract(
    scene: TimelineScene,
    visual_profile: VisualEnrichmentProfile | None = None,
) -> dict[str, object]:
    """Return compact spatial-motion requirements instead of creative prose."""

    long_scene = scene.duration_seconds >= 10
    lyric_scene = bool(scene.lyrics)
    scheduled_profile = (
        visual_profile is not None
        and visual_profile.profile_id
        in {"lyric_visuals_light_8b", "lyric_visuals_full"}
    )
    if long_scene and lyric_scene and visual_profile is not None:
        minimum_shots = visual_profile.long_lyric_scene_minimum_shots
        maximum_shots = visual_profile.long_lyric_scene_maximum_shots
        shot_count = (
            f"required {minimum_shots}-{maximum_shots} Shots for this long "
            "lyric Scene; place boundaries at semantic action or reveal transitions"
        )
    elif scheduled_profile:
        minimum_shots = 1
        maximum_shots = 1
        shot_count = (
            "required exactly 1 Shot with a continuous translating camera "
            "path for this short or non-lyric Scene"
        )
    else:
        shot_count = "1-6 Shots as permitted by the base protocol"
    if long_scene and lyric_scene and visual_profile is not None:
        if minimum_shots == maximum_shots:
            long_scene_instruction = (
                f"for this long Scene, use exactly {minimum_shots} motivated "
                "Shots at semantic preparation, action, or result transitions"
            )
        else:
            long_scene_instruction = (
                f"for this long Scene, use {minimum_shots}-{maximum_shots} "
                "motivated Shots at semantic preparation, action, or result "
                "transitions"
            )
    elif long_scene:
        long_scene_instruction = (
            "for this long Scene, use one continuous large spatial trajectory "
            "across preparation, action, and result, or 2-3 motivated Shots at "
            "those semantic transitions"
        )
    else:
        long_scene_instruction = "not applicable"
    required_sequence = required_camera_sequence(
        scene_id=scene.scene_id,
        profile_id=(
            visual_profile.profile_id
            if visual_profile is not None
            else ""
        ),
        shot_count=(
            maximum_shots
            if long_scene and lyric_scene and visual_profile is not None
            else (1 if scheduled_profile else 0)
        ),
    )
    return {
        "shot_count": shot_count,
        "start_path_end": (
            "a moving camera must name a distinct start view, physical travel "
            "path, and distinct end view"
        ),
        "parallax": (
            "name at least one foreground and one background depth anchor whose "
            "relative motion proves camera translation"
        ),
        "coverage": (
            "camera development must remain active through the final visible "
            "action instead of completing early and holding a tableau"
        ),
        "tracking": (
            "use tracking only when the Subject or main object changes world "
            "position; never track only a hand, face, or local gesture"
        ),
        "static_and_shake": (
            "static requires a deliberately still visual purpose; shake is a "
            "brief impact accent and must not be the sole long-Scene movement"
        ),
        "long_scene": long_scene_instruction,
        "required_camera_sequence": required_sequence,
        "sequence_application": (
            "binding: Shot N must copy type, amplitude, and speed from entry N "
            "and realize its spatial_goal; if the profile permits fewer Shots "
            "than entries, use the first entries only"
            if required_sequence
            else "no binding sequence; choose a camera motivated by this Scene"
        ),
    }


def _subject_motion_contract(scene: TimelineScene) -> dict[str, object]:
    """Give the model a compact, content-neutral performance floor."""

    minimum_phases = 2 if scene.duration_seconds >= 10 else 1
    motion_families = (
        "travel through space and recover balance",
        "rotate the torso while reaching across a clear path",
        "change body level and rise or settle into a new stance",
        "make deliberate contact with an available object or surface",
        "change direction with articulated limb follow-through",
        "transfer weight while the arms describe a broad controlled path",
    )
    return {
        "required_when_a_subject_is_named_in_actions": True,
        "minimum_deliberate_action_phases": minimum_phases,
        "suggested_motion_family": motion_families[
            (scene.scene_id - 1) % len(motion_families)
        ],
        "not_counted_as_deliberate_motion": [
            "passive floating, bobbing, or swaying",
            "breathing, blinking, gaze, expression, or mouth motion alone",
            "hair, clothing, particles, light, or camera motion",
        ],
        "application": (
            "derive the exact action from the current lyric and world; use the "
            "suggested family only when compatible, never replace a locked lyric "
            "predicate, and finish in a visibly changed body position"
        ),
    }


def _subject_rendering_contract() -> dict[str, object]:
    """Prevent stylistic roughness from collapsing Subject construction."""

    return {
        "identity_detail": (
            "preserve the Subject's individual head, face, body proportions, "
            "anatomy, clothing construction, and identifying features"
        ),
        "volumetric_construction": (
            "show coherent overlapping head, neck, ribcage, pelvis, and limb "
            "masses with foreshortening, contour turns, depth separation, and "
            "style-compatible value or shadow planes"
        ),
        "style_boundary": (
            "apply the requested medium to those constructed forms; rough or "
            "abstract marks must not simplify the Subject into a flat icon, "
            "generic cheap illustration, mascot, or featureless silhouette "
            "unless planning_brief explicitly requires that result"
        ),
        "camera_continuity": (
            "preserve the same three-dimensional landmarks, anatomy, clothing, "
            "and identity while a moving camera reveals different sides"
        ),
    }


def _line_protocol_shape_contract(
    scene: TimelineScene,
    visual_profile: VisualEnrichmentProfile,
) -> dict[str, object]:
    """Describe the required record shape with numeric values for small LLMs."""

    scheduled_profile = visual_profile.profile_id in {
        "lyric_visuals_light_8b",
        "lyric_visuals_full",
    }
    if scene.duration_seconds >= 10 and scene.lyrics:
        minimum_shots = visual_profile.long_lyric_scene_minimum_shots
        maximum_shots = visual_profile.long_lyric_scene_maximum_shots
    elif scheduled_profile:
        minimum_shots = 1
        maximum_shots = 1
    else:
        minimum_shots = 1
        maximum_shots = 6
    exact_shot_count = (
        minimum_shots if minimum_shots == maximum_shots else None
    )
    exact_auxiliary_count = (
        visual_profile.minimum_aux_visuals_per_scene
        if visual_profile.minimum_aux_visuals_per_scene
        == visual_profile.maximum_aux_visuals_per_scene
        else None
    )
    auxiliary_lines_per_shot: list[int] | None = None
    if exact_shot_count is not None and exact_auxiliary_count is not None:
        if exact_auxiliary_count == 0:
            auxiliary_lines_per_shot = [0] * exact_shot_count
        elif exact_auxiliary_count == 1:
            auxiliary_lines_per_shot = [0] * (exact_shot_count - 1) + [1]
    return {
        "minimum_shot_blocks": minimum_shots,
        "maximum_shot_blocks": maximum_shots,
        "exact_shot_blocks": exact_shot_count,
        "first_shot_start_ms": 0,
        "later_shot_start_ms": (
            f"strictly increasing integer from 1 through "
            f"{scene.duration_seconds * 1000 - 1}"
        ),
        "end_scene_after_shot_blocks": (
            exact_shot_count
            if exact_shot_count is not None
            else f"between {minimum_shots} and {maximum_shots}"
        ),
        "allowed_lyric_response_mode_tokens": (
            [
                "direct_subject_action",
                "direct_object_action",
                "spatial_metaphor",
            ]
            if scene.lyrics
            else ["instrumental_continuity"]
        ),
        "exact_auxiliary_visual_lines": exact_auxiliary_count,
        "auxiliary_visual_lines_per_shot": auxiliary_lines_per_shot,
        "mandatory_order": [
            "SCENE",
            "SCENE_INTENT",
            "LYRIC_RESPONSE",
            "complete SHOT through END_SHOT block(s)",
            "END_SCENE only after all required SHOT blocks",
        ],
    }


def _retry_record_shape_reminder(
    scene: TimelineScene,
    visual_profile: VisualEnrichmentProfile,
) -> str:
    """Return cumulative structural repair guidance for every Scene retry."""

    shape = _line_protocol_shape_contract(scene, visual_profile)
    exact = shape["exact_shot_blocks"]
    if exact is not None:
        shot_rule = (
            f"write exactly {exact} complete SHOT-through-END_SHOT blocks; "
            "the first SHOT start is 0; every later SHOT start is a positive "
            f"integer below {scene.duration_seconds * 1000}; write END_SCENE "
            f"only after END_SHOT number {exact}"
        )
    else:
        shot_rule = (
            f"write {shape['minimum_shot_blocks']}-"
            f"{shape['maximum_shot_blocks']} complete SHOT-through-END_SHOT "
            "blocks and write END_SCENE only after the final END_SHOT"
        )
    lyric_rule = (
        "write LYRIC_RESPONSE as exactly three tab-separated columns: "
        "LYRIC_RESPONSE, a valid numeric lyric index, and exactly one of these "
        "literal ASCII tokens without translation: direct_subject_action, "
        "direct_object_action, spatial_metaphor"
        if scene.lyrics
        else "write LYRIC_RESPONSE with numeric index 0 and the literal ASCII "
        "token instrumental_continuity without translation"
    )
    auxiliary_lines_per_shot = shape["auxiliary_visual_lines_per_shot"]
    if auxiliary_lines_per_shot is not None:
        auxiliary_rule = (
            "; write AUX_VISUAL counts per SHOT exactly as "
            f"{auxiliary_lines_per_shot} in Shot order"
        )
    else:
        auxiliary_rule = ""
    camera_sequence = required_camera_sequence(
        scene_id=scene.scene_id,
        profile_id=visual_profile.profile_id,
        shot_count=(
            visual_profile.long_lyric_scene_maximum_shots
            if scene.duration_seconds >= 10 and scene.lyrics
            else (
                1
                if visual_profile.profile_id
                in {"lyric_visuals_light_8b", "lyric_visuals_full"}
                else 0
            )
        ),
    )
    if camera_sequence:
        camera_rule = "; required CAMERA sequence is " + ", ".join(
            f"Shot {value['shot_number']}={value['type']}/"
            f"{value['amplitude']}/{value['speed']}"
            for value in camera_sequence
        )
    else:
        camera_rule = ""
    return (
        f"Scene {scene.scene_id} mandatory record shape: {shot_rule}; "
        f"{lyric_rule}{auxiliary_rule}{camera_rule}; do not quote or repeat source lyric text in "
        "SCENE_INTENT; an auxiliary_visual_contract required_kind belongs only "
        "in AUX_VISUAL and is never a LYRIC_RESPONSE mode"
    )


_AUXILIARY_REPAIR_OPERATIONS = (
    "reveal_or_occlude",
    "extend_or_contract",
    "converge_or_diverge",
    "accumulate_or_erode",
    "compress_or_release",
    "fragment_or_reassemble",
    "rise_or_sink",
    "accelerate_or_decelerate",
)


def _auxiliary_visual_repair_spec(
    duplicate_scene_ids: set[int],
    *,
    retry_index: int,
) -> dict[str, Any] | None:
    if not duplicate_scene_ids:
        return None
    index = max(1, retry_index)
    return {
        "retry_index": index,
        "duplicate_of_scene_ids": sorted(duplicate_scene_ids),
        "required_development_operation": _AUXILIARY_REPAIR_OPERATIONS[
            (index - 1) % len(_AUXILIARY_REPAIR_OPERATIONS)
        ],
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
        "auxiliary_visual_kinds": [
            visual.kind
            for shot in scene.shots
            for visual in shot.auxiliary_visuals
        ],
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
    recent_scene_patterns: list[dict[str, Any]] | None = None,
    avoid_duplicate_plans: tuple[PlannedScene, ...] = (),
    duplicate_repair: dict[str, Any] | None = None,
    auxiliary_visual_repair: dict[str, Any] | None = None,
    camera_protocol_repair: dict[str, Any] | None = None,
    lyric_action_blueprint: LyricActionBlueprint | None = None,
    visual_profile: VisualEnrichmentProfile,
) -> dict[str, Any]:
    payload = {
        "scene_id": scene.scene_id,
        "duration_ms": scene.duration_seconds * 1000,
        "state": scene.state,
        "source_start_ms": scene.source_start_ms,
        "source_end_ms": scene.source_end_ms,
        "lyric_lines": _lyric_lines(scene, protector),
        "lyric_response_contract": {
            "required": bool(scene.lyrics),
            "anchor_index": (
                "one index from lyric_lines"
                if scene.lyrics
                else 0
            ),
            "anchor_policy": (
                "earliest complete depictable predicate with a physical head "
                "verb and all compatible explicit roles, otherwise 1"
                if scene.lyrics
                else "no lyric anchor"
            ),
            "priority": [
                "direct_subject_action",
                "direct_object_action",
                "spatial_metaphor",
            ],
            "semantic_fidelity": (
                "preserve the selected lyric's actor, physical action, "
                "recognizable concrete target or object, and visible result; "
                "bind the dominant action to the selected lyric's actual head "
                "verb instead of substituting another physical operation; the "
                "shared medium may stylize the predicate but must not replace it"
                if scene.lyrics
                else "not applicable"
            ),
            "interaction_choreography": (
                "derive the necessary effector or tool, exact contact point, "
                "motion direction and path, cadence or repetition, force, "
                "resistance, accumulated target response, and release from the "
                "selected lyric before section energy or motifs; use only the "
                "phases required by that verb and keep the critical contact visible"
                if scene.lyrics
                else "not applicable"
            ),
            "inscription_fallback": (
                "when readable text is prohibited but the selected lyric is "
                "about naming, writing, or carving, preserve the physical operation "
                "but leave only isolated non-linguistic short scratches or grooves "
                "with irregular length, direction, curvature, and spacing on the "
                "recognizable surface; never describe the visible result as a name, "
                "text, word, letter, glyph, inscription, symbol, or typography, and "
                "never form a baseline, aligned row, repeated glyph-like shape, word "
                "spacing, mirrored lettering, or reflected lettering"
                if scene.lyrics
                else "not applicable"
            ),
            "no_lyrics_mode": "instrumental_continuity",
        },
        "retention_execution_contract": {
            "immutable_properties": (
                "every body, identity, anatomy, clothing, and appearance property "
                "named in planning_brief.retention_constraints remains physically present "
                "and unchanged before, during, and after every action"
            ),
            "transformation_boundary": (
                "never use a retained Subject property as the material source or target "
                "of removal, exposure, detachment, opening, cracking, peeling, tearing, "
                "dissolution, erosion, or re-formation"
            ),
            "safe_fallback": (
                "apply lyric-driven damage or transformation to a separate permitted "
                "external object or non-character layer while retaining the complete Subject"
            ),
        },
        "active_section_motifs": (
            _active_section_motifs(scene, bible) if bible is not None else []
        ),
        "previous_scene_tail": _previous_scene_tail(previous_scene, protector),
        "recent_scene_patterns": recent_scene_patterns or [],
        "camera_choreography_contract": _camera_choreography_contract(
            scene, visual_profile
        ),
        "subject_motion_contract": _subject_motion_contract(scene),
        "subject_rendering_contract": _subject_rendering_contract(),
        "line_protocol_shape_contract": _line_protocol_shape_contract(
            scene, visual_profile
        ),
        "avoid_duplicate_plans": [
            _scene_duplicate_fingerprint(value)
            for value in avoid_duplicate_plans
        ],
        "duplicate_repair": duplicate_repair,
        "auxiliary_visual_repair": auxiliary_visual_repair,
        "camera_protocol_repair": camera_protocol_repair,
        "auxiliary_visual_contract": visual_profile.scene_contract(
            scene.scene_id
        ),
        "locked_lip_sync": [
            protector.protect(value) for value in scene.lip_sync_lines
        ],
        "locked_soundscape": [
            protector.protect(value) for value in scene.soundscape_lines
        ],
    }
    if lyric_action_blueprint is not None:
        anchor = lyric_action_blueprint.lyric_anchor_index
        payload["locked_lyric_action_blueprint"] = {
            "lyric_anchor_index": anchor,
            "lyric_anchor_text": _lyric_lines(scene, protector)[anchor - 1][
                "text"
            ],
            "lyric_response_mode": (
                lyric_action_blueprint.lyric_response_mode
            ),
            "composition_requirement": protector.protect(
                lyric_action_blueprint.composition_requirement
            ),
            "chronological_actions": [
                protector.protect(value)
                for value in lyric_action_blueprint.subject_actions
            ],
            "visible_result": protector.protect(
                lyric_action_blueprint.visible_result
            ),
            "application": (
                "Python will replace generated ACTION choreography with this "
                "blueprint after parsing; make COMPOSITION, ENVIRONMENT, and "
                "CAMERA support its exact target, contact path, and result"
            ),
        }
    return payload


def _build_protector(
    brief: PlanningBrief, timeline: TimelineDocument
) -> tuple[ReferenceProtector, dict[str, list[str]], list[dict[str, Any]]]:
    protector = ReferenceProtector()
    planning_brief = _planning_brief_payload(brief, protector)
    section_sources = _section_sources(timeline, protector)
    for scene in timeline.scenes:
        # Protect every reference that can appear later in Scene payloads.
        for lyric in scene.lyrics:
            protector.protect(lyric.text)
        for value in scene.lip_sync_lines:
            protector.protect(value)
        for value in scene.soundscape_lines:
            protector.protect(value)
    return protector, planning_brief, section_sources


def _plan_song_bible(
    backend: Any,
    *,
    planning_brief: dict[str, list[str]],
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
    visual_profile: VisualEnrichmentProfile,
) -> tuple[SongBible, int, int]:
    section_labels = [str(source["label"]) for source in section_sources]
    payload = {
        "protocol": "clmv-song-bible-line-v4",
        "visual_enrichment_profile": visual_profile.payload_summary(),
        "planning_brief": planning_brief,
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
                system=load_profiled_planner_prompt(
                    "song_bible_system_prompt.txt", visual_profile
                ),
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


def _lyric_action_request_scene(
    scene: TimelineScene, protector: ReferenceProtector
) -> dict[str, Any]:
    return {
        "scene_id": scene.scene_id,
        "duration_ms": scene.duration_seconds * 1000,
        "lyric_lines": _lyric_lines(scene, protector),
        "allowed_response_modes": [
            "direct_subject_action",
            "direct_object_action",
            "spatial_metaphor",
        ],
        "semantic_grounding_order": [
            "choose the earliest complete source predicate",
            "identify its actor, physical head verb, and concrete target",
            "make the target recognizable before contact",
            "execute that exact verb instead of a visually convenient substitute",
            "show the completed result on the same target after release",
        ],
        "style_separation": (
            "This stage has intentionally omitted global medium, background, "
            "motif, and camera directions. Do not invent them or use them as "
            "the action. Resolve only the source lyric's literal visible predicate."
        ),
    }


def _plan_lyric_action_blueprints(
    backend: Any,
    *,
    scenes: list[TimelineScene],
    planning_brief: dict[str, list[str]],
    protector: ReferenceProtector,
    call_settings: dict[str, Any],
    retry_max: int,
    request_index: int,
    batch_number: int,
    progress_callback: ProgressCallback | None,
    interrupt_callback: Callable[[], Any] | None,
    debug_events: DebugEvents | None,
    debug_state: dict[str, Any] | None,
    scenes_per_request: int,
) -> tuple[dict[int, LyricActionBlueprint], int, int]:
    """Resolve lyric semantics in a compact task before full Scene planning."""

    if not 1 <= scenes_per_request <= 16:
        raise MVPlannerError(
            "lyric action scenes_per_request must be between 1 and 16"
        )
    pending = {scene.scene_id: scene for scene in scenes if scene.lyrics}
    if not pending:
        return {}, request_index, 0
    resolved: dict[int, LyricActionBlueprint] = {}
    failure_counts = {scene_id: 0 for scene_id in pending}
    feedback: dict[int, str] = {}
    serial_retry = False
    retry_count = 0
    attempt = 0
    while pending:
        attempt += 1
        request_limit = 1 if serial_retry else scenes_per_request
        request_ids = sorted(pending)[:request_limit]
        request_pending = {
            scene_id: pending[scene_id] for scene_id in request_ids
        }
        requested_ids = sorted(request_pending)
        payload: dict[str, Any] = {
            "protocol": "clmv-lyric-action-line-v1",
            "planning_constraints": {
                "subject_identity": planning_brief["subject_identity"],
                "retention_constraints": planning_brief[
                    "retention_constraints"
                ],
            },
            "reference_legend": protector.legend(),
            "requested_scene_ids": requested_ids,
            "requested_scene_count": len(requested_ids),
            "scenes": [
                _lyric_action_request_scene(request_pending[scene_id], protector)
                for scene_id in requested_ids
            ],
        }
        specific_feedback = [
            feedback[scene_id]
            for scene_id in requested_ids
            if scene_id in feedback
        ]
        if specific_feedback:
            payload["retry_feedback"] = (
                "Correct only these record errors and return every requested "
                "LYRIC_SCENE record: " + "; ".join(specific_feedback)
            )
        request_index += 1
        if debug_state is not None:
            debug_state.update(
                {
                    "stage": "lyric_action_preplan_running",
                    "active_batch": batch_number,
                    "active_attempt": attempt,
                    "pending_lyric_action_scene_ids": requested_ids,
                    "request_count": request_index,
                }
            )
        try:
            content, event = _call(
                backend,
                system=load_planner_prompt("lyric_action_system_prompt.txt"),
                payload=payload,
                max_tokens=min(
                    call_settings["max_tokens"],
                    max(512, len(request_pending) * 384),
                ),
                temperature=call_settings["temperature"],
                top_p=call_settings["top_p"],
                repetition_penalty=call_settings["repetition_penalty"],
                seed=_seed(call_settings["seed"], request_index - 1),
                label=(
                    f"lyric action preplan batch {batch_number} "
                    f"attempt {attempt}"
                ),
                progress_callback=progress_callback,
                interrupt_callback=interrupt_callback,
                debug_events=debug_events,
            )
            recovered, errors = parse_lyric_action_response(
                content, request_pending, protector
            )
            if event is not None:
                event.update(
                    {
                        "parsed_response": {
                            str(scene_id): asdict(value)
                            for scene_id, value in sorted(recovered.items())
                        },
                        "requested_scene_ids": requested_ids,
                        "recovered_scene_ids": sorted(recovered),
                        "scene_errors": errors,
                        "validation_result": (
                            "success" if not errors else "partial" if recovered else "error"
                        ),
                    }
                )
        except (PlannerResponseError, MVPlannerError) as exc:
            recovered = {}
            errors = {scene_id: str(exc) for scene_id in requested_ids}
            if debug_events:
                debug_events[-1]["validation_result"] = "error"
                debug_events[-1]["validation_error"] = str(exc)
        resolved.update(recovered)
        for scene_id in recovered:
            pending.pop(scene_id, None)
            feedback.pop(scene_id, None)
        failed = [scene_id for scene_id in requested_ids if scene_id in pending]
        for scene_id in failed:
            failure_counts[scene_id] += 1
            feedback[scene_id] = errors.get(
                scene_id, f"Scene {scene_id} lyric blueprint is missing"
            )
        exhausted = [
            scene_id
            for scene_id in failed
            if failure_counts[scene_id] > retry_max
        ]
        if exhausted:
            unresolved = ", ".join(str(value) for value in exhausted)
            raise MVPlannerError(
                "Lyric action preplan failed after "
                f"{retry_max} retry attempt(s) for Scene(s): {unresolved}; "
                + "; ".join(feedback[value] for value in exhausted)
            )
        if failed:
            retry_count += 1
            if not recovered and len(pending) > 1:
                serial_retry = True
            LOGGER.warning(
                "[cl_mv_prompt_planner] Retained %d lyric action blueprint(s); "
                "retrying %d unresolved Scene(s): %s",
                len(recovered),
                len(pending),
                "; ".join(feedback[value] for value in failed),
            )
    LOGGER.info(
        "[cl_mv_prompt_planner] Locked %d lyric action blueprint(s) for "
        "Scene batch %d",
        len(resolved),
        batch_number,
    )
    if debug_state is not None:
        debug_state["lyric_action_blueprints"] = {
            str(scene_id): asdict(value)
            for scene_id, value in sorted(resolved.items())
        }
        debug_state["pending_lyric_action_scene_ids"] = []
    return resolved, request_index, retry_count


def _combine_composition(base: str, requirement: str) -> str:
    if requirement in base:
        return base
    combined = f"{base.rstrip('。')}。{requirement}"
    # Both source fields have already passed the 1000-character protocol
    # limit.  Combining them must not create an invalid PlannedShot that the
    # deterministic renderer cannot represent safely.
    if len(combined) <= 1000:
        return combined
    return requirement


def _apply_lyric_action_blueprint(
    scene: PlannedScene, blueprint: LyricActionBlueprint
) -> PlannedScene:
    """Lock focused semantic choreography while retaining spatial/camera work."""

    action_units = [*blueprint.subject_actions, blueprint.visible_result]
    shot_count = len(scene.shots)
    assignments: list[list[str]] = [[] for _ in scene.shots]
    for index, action in enumerate(action_units):
        target = min((index * shot_count) // len(action_units), shot_count - 1)
        assignments[target].append(action)
    shots: list[PlannedShot] = []
    for index, shot in enumerate(scene.shots):
        assigned = assignments[index]
        if not assigned:
            # A future profile may request more Shots than the compact
            # blueprint has action units.  Repeat the locked end state rather
            # than reintroducing unrelated choreography from the Scene LLM.
            assigned = [action_units[-1]]
        shots.append(
            PlannedShot(
                start_ms=shot.start_ms,
                composition=_combine_composition(
                    shot.composition, blueprint.composition_requirement
                ),
                subject_actions=tuple(assigned),
                environment=shot.environment,
                camera=shot.camera,
                auxiliary_visuals=shot.auxiliary_visuals,
            )
        )
    return PlannedScene(
        scene_id=scene.scene_id,
        scene_intent=scene.scene_intent,
        shots=tuple(shots),
        lyric_anchor_index=blueprint.lyric_anchor_index,
        lyric_response_mode=blueprint.lyric_response_mode,
    )


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
    visual_enrichment_profile: str = DEFAULT_VISUAL_PROFILE_ID,
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
    visual_profile = load_visual_profile(visual_enrichment_profile)
    protector, planning_brief, section_sources = _build_protector(
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
                "planning_brief": planning_brief,
                "section_sources": section_sources,
                "camera_guard": camera_guard,
                "camera_warnings": [],
                "vocal_guard": vocal_guard,
                "vocal_warnings": [],
                "visual_enrichment_profile": visual_profile.payload_summary(),
                "duplicate_scene_diagnostics": [],
                "duplicate_auxiliary_visual_diagnostics": [],
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
        planning_brief=planning_brief,
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
        visual_profile=visual_profile,
    )

    planned: dict[int, PlannedScene] = {}
    camera_warnings: list[dict[str, object]] = []
    vocal_warnings: list[dict[str, object]] = []
    motion_repairs: list[dict[str, object]] = []
    duplicate_diagnostics: list[dict[str, int]] = []
    duplicate_auxiliary_diagnostics: list[dict[str, int]] = []
    scene_retries = 0
    lyric_action_blueprints: dict[int, LyricActionBlueprint] = {}
    scenes = list(timeline.scenes)
    for batch_start in range(0, len(scenes), scenes_per_batch):
        initial_group = scenes[batch_start : batch_start + scenes_per_batch]
        if visual_profile.lyric_action_preplan:
            batch_blueprints, request_index, blueprint_retries = (
                _plan_lyric_action_blueprints(
                    backend,
                    scenes=initial_group,
                    planning_brief=planning_brief,
                    protector=protector,
                    call_settings=call_settings,
                    retry_max=retry_max,
                    request_index=request_index,
                    batch_number=batch_start // scenes_per_batch + 1,
                    progress_callback=progress_callback,
                    interrupt_callback=interrupt_callback,
                    debug_events=debug_events,
                    debug_state=debug_state,
                    scenes_per_request=(
                        visual_profile.lyric_action_scenes_per_request
                    ),
                )
            )
            lyric_action_blueprints.update(batch_blueprints)
            scene_retries += blueprint_retries
        pending = {scene.scene_id: scene for scene in initial_group}
        duplicate_avoidance: dict[int, dict[int, PlannedScene]] = {}
        duplicate_repair_indices: dict[int, int] = {}
        auxiliary_duplicate_avoidance: dict[int, set[int]] = {}
        auxiliary_repair_indices: dict[int, int] = {}
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
            request_auxiliary_repairs: dict[int, dict[str, Any]] = {}
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
                auxiliary_repair = _auxiliary_visual_repair_spec(
                    auxiliary_duplicate_avoidance.get(scene.scene_id, set()),
                    retry_index=auxiliary_repair_indices.get(
                        scene.scene_id, 1
                    ),
                )
                if auxiliary_repair is not None:
                    request_auxiliary_repairs[scene.scene_id] = (
                        auxiliary_repair
                    )
                    auxiliary_repair_indices[scene.scene_id] = (
                        int(auxiliary_repair["retry_index"]) + 1
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
                        recent_scene_patterns=_recent_scene_patterns(
                            scene.scene_id, planned
                        ),
                        avoid_duplicate_plans=avoided_scenes,
                        duplicate_repair=repair,
                        auxiliary_visual_repair=auxiliary_repair,
                        camera_protocol_repair=camera_repair,
                        lyric_action_blueprint=lyric_action_blueprints.get(
                            scene.scene_id
                        ),
                        visual_profile=visual_profile,
                    )
                )
            payload = {
                "protocol": "clmv-scene-line-v4",
                "planning_brief": planning_brief,
                "reference_legend": protector.legend(),
                "visual_enrichment_profile": visual_profile.payload_summary(),
                "song_bible": {
                    "visual_arc": bible.visual_arc,
                    "camera_strategy": list(bible.camera_strategy),
                    "visual_enrichment_strategy": (
                        bible.visual_enrichment_strategy
                    ),
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
                shape_reminders = " ".join(
                    _retry_record_shape_reminder(
                        request_pending[scene_id], visual_profile
                    )
                    for scene_id in pending_before
                )
                payload["retry_feedback"] = (
                    "The previous response failed validation. "
                    f"{request_retry_feedback} {shape_reminders} Return exactly the requested "
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
                    system=load_profiled_planner_prompt(
                        "scene_plan_system_prompt.txt", visual_profile
                    ),
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
                    content,
                    request_pending,
                    protector,
                    visual_profile=visual_profile,
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
                        timeline_scene = request_pending[scene_id]
                        scheduled = required_camera_sequence(
                            scene_id=scene_id,
                            profile_id=visual_profile.profile_id,
                            shot_count=(
                                visual_profile.long_lyric_scene_maximum_shots
                                if timeline_scene.duration_seconds >= 10
                                and timeline_scene.lyrics
                                else (
                                    1
                                    if visual_profile.profile_id
                                    in {
                                        "lyric_visuals_light_8b",
                                        "lyric_visuals_full",
                                    }
                                    else 0
                                )
                            ),
                        )
                        # A single generic repair camera for every Shot would
                        # contradict the binding per-Shot schedule.  The exact
                        # sequence is already present in both the Scene input
                        # and cumulative retry reminder.
                        if not scheduled:
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
                    blueprint = lyric_action_blueprints.get(scene_id)
                    if blueprint is not None:
                        recovered_scene = _apply_lyric_action_blueprint(
                            recovered_scene, blueprint
                        )
                    # Blueprint ACTIONs are immutable at this stage and were
                    # already motion-validated while the preplan was still
                    # repairable.  Retrying the full Scene cannot change them.
                    if blueprint is None:
                        motion_issues = subject_motion_issues(
                            recovered_scene,
                            duration_seconds=pending[scene_id].duration_seconds,
                        )
                        if motion_issues:
                            recovered_scene, added_actions = repair_subject_motion(
                                recovered_scene,
                                duration_seconds=(
                                    pending[scene_id].duration_seconds
                                ),
                            )
                            if not added_actions:
                                errors[scene_id] = "; ".join(
                                    str(issue["message"])
                                    for issue in motion_issues
                                )
                                continue
                            diagnostic = {
                                "scene_id": scene_id,
                                "reason": str(motion_issues[0]["message"]),
                                "added_actions": list(added_actions),
                            }
                            motion_repairs.append(diagnostic)
                            LOGGER.warning(
                                "[cl_mv_prompt_planner] Scene %d omitted "
                                "purposeful whole-Subject motion; appended %d "
                                "content-neutral ACTION phase(s) without "
                                "retrying the complete Scene",
                                scene_id,
                                len(added_actions),
                            )
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

                    existing_auxiliary: dict[str, int] = {}
                    for other_id, other_scene in sorted(existing.items()):
                        if other_id >= scene_id:
                            continue
                        for signature in auxiliary_visual_signatures(
                            other_scene
                        ):
                            existing_auxiliary.setdefault(signature, other_id)
                    seen_in_scene: set[str] = set()
                    auxiliary_duplicate_ids: set[int] = set()
                    for signature in auxiliary_visual_signatures(
                        recovered_scene
                    ):
                        if signature in seen_in_scene:
                            auxiliary_duplicate_ids.add(scene_id)
                        seen_in_scene.add(signature)
                        source_id = existing_auxiliary.get(signature)
                        if source_id is not None:
                            auxiliary_duplicate_ids.add(source_id)
                    if auxiliary_duplicate_ids:
                        for source_id in sorted(auxiliary_duplicate_ids):
                            diagnostic = {
                                "scene_id": scene_id,
                                "duplicate_of": source_id,
                            }
                            if diagnostic not in duplicate_auxiliary_diagnostics:
                                duplicate_auxiliary_diagnostics.append(
                                    diagnostic
                                )
                        if (
                            visual_profile.assignment_mode == "cycle"
                            and visual_profile.minimum_aux_visuals_per_scene == 1
                            and visual_profile.maximum_aux_visuals_per_scene == 1
                        ):
                            repaired_scene, request_index, repair_attempts = (
                                _targeted_auxiliary_visual_repair(
                                    backend,
                                    scene=recovered_scene,
                                    timeline_scene=pending[scene_id],
                                    planning_brief=planning_brief,
                                    protector=protector,
                                    visual_profile=visual_profile,
                                    forbidden_descriptions=tuple(
                                        sorted(existing_auxiliary)
                                    ),
                                    repair_index=1,
                                    call_settings=call_settings,
                                    retry_max=retry_max,
                                    request_index=request_index,
                                    progress_callback=progress_callback,
                                    interrupt_callback=interrupt_callback,
                                    debug_events=debug_events,
                                )
                            )
                            scene_retries += repair_attempts
                            if repaired_scene is not None:
                                recovered_scene = repaired_scene
                            else:
                                LOGGER.warning(
                                    "[cl_mv_prompt_planner] Scene %d retains an "
                                    "exact cross-Scene AUX_VISUAL duplicate after "
                                    "bounded targeted repair; accepting the "
                                    "original structurally valid Scene instead "
                                    "of entering a full-Scene retry loop",
                                    scene_id,
                                )
                            auxiliary_duplicate_ids.clear()
                    if auxiliary_duplicate_ids:
                        auxiliary_duplicate_avoidance.setdefault(
                            scene_id, set()
                        ).update(auxiliary_duplicate_ids)
                        auxiliary_repair_indices.setdefault(scene_id, 1)
                        serial_retry = True
                        source_list = ", ".join(
                            str(value)
                            for value in sorted(auxiliary_duplicate_ids)
                        )
                        errors[scene_id] = (
                            f"Scene {scene_id} repeats an exact AUX_VISUAL "
                            f"description from Scene(s) {source_list}"
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
                            "auxiliary_visual_repair_requirements": (
                                request_auxiliary_repairs
                            ),
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
                            "motion_repairs": motion_repairs,
                            "duplicate_scene_diagnostics": duplicate_diagnostics,
                            "duplicate_auxiliary_visual_diagnostics": (
                                duplicate_auxiliary_diagnostics
                            ),
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
                if (
                    isinstance(exc, PlannerInferenceStallError)
                    and len(pending) > 1
                ):
                    serial_retry = True
                    LOGGER.warning(
                        "[cl_mv_prompt_planner] Stalled Scene batch will "
                        "retry one Scene at a time; pending Scene(s): %s",
                        ", ".join(str(value) for value in sorted(pending)),
                    )
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

    ordered = tuple(planned[scene.scene_id] for scene in scenes)
    restored_bible = SongBible(
        visual_arc=protector.restore(bible.visual_arc),
        camera_strategy=tuple(
            protector.restore(value) for value in bible.camera_strategy
        ),
        visual_enrichment_strategy=protector.restore(
            bible.visual_enrichment_strategy
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
            "response_protocol": "clmv-line-v5",
            "song_bible_protocol": "clmv-song-bible-line-v4",
            "scene_protocol": "clmv-scene-line-v4",
            "visual_enrichment_profile": visual_profile.payload_summary(),
            "camera_guard": camera_guard,
            "camera_warnings": camera_warnings,
            "vocal_guard": vocal_guard,
            "vocal_warnings": vocal_warnings,
            "motion_repairs": motion_repairs,
            "duplicate_scene_diagnostics": duplicate_diagnostics,
            "duplicate_auxiliary_visual_diagnostics": (
                duplicate_auxiliary_diagnostics
            ),
            "lyric_action_blueprints": {
                str(scene_id): asdict(value)
                for scene_id, value in sorted(lyric_action_blueprints.items())
            },
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
                "motion_repairs": motion_repairs,
                "duplicate_scene_diagnostics": duplicate_diagnostics,
                "duplicate_auxiliary_visual_diagnostics": (
                    duplicate_auxiliary_diagnostics
                ),
                "lyric_action_blueprints": {
                    str(scene_id): asdict(value)
                    for scene_id, value in sorted(lyric_action_blueprints.items())
                },
                "request_count": request_index,
                "last_error": None,
            }
        )
    return plan
