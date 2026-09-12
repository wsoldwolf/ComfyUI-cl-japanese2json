"""Discover and validate scalable MV visual-enrichment profiles."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .errors import MVPlannerError


DEFAULT_VISUAL_PROFILE_ID = "performance_only"
_PROFILE_ROOT = Path(__file__).resolve().parent / "prompts" / "profiles"
_PROFILE_ID_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_KIND_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_ASSIGNMENT_MODES = frozenset({"none", "cycle", "model"})
_STAGES = frozenset({"song_bible", "scene_plan"})


@dataclass(frozen=True)
class VisualEnrichmentProfile:
    profile_id: str
    display_name: str
    description: str
    ui_order: int
    minimum_aux_visuals_per_scene: int
    maximum_aux_visuals_per_scene: int
    long_lyric_scene_minimum_shots: int
    long_lyric_scene_maximum_shots: int
    assignment_mode: str
    allowed_kinds: tuple[str, ...]
    lyric_action_preplan: bool
    lyric_action_scenes_per_request: int
    song_bible_policy: str
    scene_plan_policy: str

    def scene_contract(self, scene_id: int) -> dict[str, Any]:
        required_kind: str | None = None
        if self.assignment_mode == "cycle":
            required_kind = self.allowed_kinds[
                (scene_id - 1) % len(self.allowed_kinds)
            ]
        return {
            "profile_id": self.profile_id,
            "minimum_count": self.minimum_aux_visuals_per_scene,
            "maximum_count": self.maximum_aux_visuals_per_scene,
            "allowed_kinds": list(self.allowed_kinds),
            "required_kind": required_kind,
        }

    def payload_summary(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "description": self.description,
            "minimum_aux_visuals_per_scene": (
                self.minimum_aux_visuals_per_scene
            ),
            "maximum_aux_visuals_per_scene": (
                self.maximum_aux_visuals_per_scene
            ),
            "long_lyric_scene_minimum_shots": (
                self.long_lyric_scene_minimum_shots
            ),
            "long_lyric_scene_maximum_shots": (
                self.long_lyric_scene_maximum_shots
            ),
            "assignment_mode": self.assignment_mode,
            "allowed_kinds": list(self.allowed_kinds),
            "lyric_action_preplan": self.lyric_action_preplan,
            "lyric_action_scenes_per_request": (
                self.lyric_action_scenes_per_request
            ),
        }


def _profile_directories() -> tuple[Path, ...]:
    try:
        directories = tuple(
            path
            for path in _PROFILE_ROOT.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
    except OSError as exc:
        raise MVPlannerError(
            f"Could not enumerate MV visual profiles in {_PROFILE_ROOT}: {exc}"
        ) from exc
    if not directories:
        raise MVPlannerError("No MV visual-enrichment profiles are installed")
    return directories


def _profile_files(directory: Path) -> tuple[Path, Path, Path]:
    return (
        directory / "profile.json",
        directory / "song_bible.txt",
        directory / "scene_plan.txt",
    )


def visual_profiles_fingerprint() -> tuple[object, ...]:
    values: list[object] = []
    for directory in sorted(_profile_directories(), key=lambda path: path.name):
        for path in _profile_files(directory):
            relative = path.relative_to(_PROFILE_ROOT).as_posix()
            try:
                data = path.read_bytes()
                stat = path.stat()
            except OSError as exc:
                values.extend(
                    ("visual-profile-error", relative, type(exc).__name__)
                )
                continue
            values.extend(
                (
                    relative,
                    stat.st_size,
                    stat.st_mtime_ns,
                    hashlib.sha256(data).hexdigest(),
                )
            )
    return tuple(values)


def _require_string(data: dict[str, Any], name: str, context: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise MVPlannerError(f"{context}.{name} must be a non-empty string")
    return value.strip()


@lru_cache(maxsize=32)
def _load_profile_cached(
    directory_text: str,
    fingerprint: tuple[object, ...],
) -> VisualEnrichmentProfile:
    del fingerprint
    directory = Path(directory_text)
    manifest_path, song_path, scene_path = _profile_files(directory)
    context = f"visual profile {directory.name!r}"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MVPlannerError(f"Could not load {context}: {exc}") from exc
    if not isinstance(data, dict):
        raise MVPlannerError(f"{context} manifest must be a JSON object")
    expected_fields = {
        "schema_version",
        "profile_id",
        "display_name",
        "description",
        "ui_order",
        "minimum_aux_visuals_per_scene",
        "maximum_aux_visuals_per_scene",
        "long_lyric_scene_minimum_shots",
        "long_lyric_scene_maximum_shots",
        "assignment_mode",
        "allowed_kinds",
        "lyric_action_preplan",
        "lyric_action_scenes_per_request",
    }
    if set(data) != expected_fields:
        missing = sorted(expected_fields - set(data))
        extra = sorted(set(data) - expected_fields)
        raise MVPlannerError(
            f"{context} manifest fields are invalid; missing={missing}, extra={extra}"
        )
    if data["schema_version"] != 4:
        raise MVPlannerError(f"{context}.schema_version must be 4")
    profile_id = _require_string(data, "profile_id", context)
    if not _PROFILE_ID_RE.fullmatch(profile_id) or profile_id != directory.name:
        raise MVPlannerError(
            f"{context}.profile_id must match its lowercase directory name"
        )
    display_name = _require_string(data, "display_name", context)
    description = _require_string(data, "description", context)
    ui_order = data["ui_order"]
    minimum = data["minimum_aux_visuals_per_scene"]
    maximum = data["maximum_aux_visuals_per_scene"]
    minimum_long_shots = data["long_lyric_scene_minimum_shots"]
    maximum_long_shots = data["long_lyric_scene_maximum_shots"]
    if not isinstance(ui_order, int) or isinstance(ui_order, bool):
        raise MVPlannerError(f"{context}.ui_order must be an integer")
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or not 0 <= minimum <= maximum <= 3
    ):
        raise MVPlannerError(
            f"{context} auxiliary visual counts must satisfy 0 <= minimum <= maximum <= 3"
        )
    if (
        not isinstance(minimum_long_shots, int)
        or isinstance(minimum_long_shots, bool)
        or not isinstance(maximum_long_shots, int)
        or isinstance(maximum_long_shots, bool)
        or not 1 <= minimum_long_shots <= maximum_long_shots <= 6
    ):
        raise MVPlannerError(
            f"{context} long lyric Scene Shot counts must satisfy "
            "1 <= minimum <= maximum <= 6"
        )
    assignment_mode = _require_string(data, "assignment_mode", context)
    if assignment_mode not in _ASSIGNMENT_MODES:
        raise MVPlannerError(
            f"{context}.assignment_mode must be one of {sorted(_ASSIGNMENT_MODES)}"
        )
    raw_kinds = data["allowed_kinds"]
    lyric_action_preplan = data["lyric_action_preplan"]
    lyric_action_scenes_per_request = data[
        "lyric_action_scenes_per_request"
    ]
    if not isinstance(lyric_action_preplan, bool):
        raise MVPlannerError(
            f"{context}.lyric_action_preplan must be a boolean"
        )
    if (
        not isinstance(lyric_action_scenes_per_request, int)
        or isinstance(lyric_action_scenes_per_request, bool)
        or not 0 <= lyric_action_scenes_per_request <= 16
    ):
        raise MVPlannerError(
            f"{context}.lyric_action_scenes_per_request must be an integer "
            "between 0 and 16"
        )
    if lyric_action_preplan and lyric_action_scenes_per_request < 1:
        raise MVPlannerError(
            f"{context} enables lyric_action_preplan and requires "
            "lyric_action_scenes_per_request >= 1"
        )
    if not lyric_action_preplan and lyric_action_scenes_per_request != 0:
        raise MVPlannerError(
            f"{context} disables lyric_action_preplan and requires "
            "lyric_action_scenes_per_request=0"
        )
    if not isinstance(raw_kinds, list) or any(
        not isinstance(value, str) or not _KIND_RE.fullmatch(value)
        for value in raw_kinds
    ):
        raise MVPlannerError(
            f"{context}.allowed_kinds must be a list of lowercase identifiers"
        )
    allowed_kinds = tuple(raw_kinds)
    if len(set(allowed_kinds)) != len(allowed_kinds):
        raise MVPlannerError(f"{context}.allowed_kinds contains duplicates")
    if maximum == 0:
        if assignment_mode != "none" or allowed_kinds:
            raise MVPlannerError(
                f"{context} with maximum 0 requires assignment_mode=none and no allowed_kinds"
            )
    elif not allowed_kinds or assignment_mode == "none":
        raise MVPlannerError(
            f"{context} with auxiliary visuals requires allowed_kinds and a non-none assignment_mode"
        )
    if assignment_mode == "cycle" and (minimum != 1 or maximum != 1):
        raise MVPlannerError(
            f"{context} cycle assignment requires exactly one auxiliary visual per Scene"
        )
    try:
        song_policy = song_path.read_text(encoding="utf-8-sig").strip()
        scene_policy = scene_path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise MVPlannerError(f"Could not load prompt policy for {context}: {exc}") from exc
    if not song_policy or not scene_policy:
        raise MVPlannerError(f"{context} prompt policies must not be empty")
    return VisualEnrichmentProfile(
        profile_id=profile_id,
        display_name=display_name,
        description=description,
        ui_order=ui_order,
        minimum_aux_visuals_per_scene=minimum,
        maximum_aux_visuals_per_scene=maximum,
        long_lyric_scene_minimum_shots=minimum_long_shots,
        long_lyric_scene_maximum_shots=maximum_long_shots,
        assignment_mode=assignment_mode,
        allowed_kinds=allowed_kinds,
        lyric_action_preplan=lyric_action_preplan,
        lyric_action_scenes_per_request=lyric_action_scenes_per_request,
        song_bible_policy=song_policy,
        scene_plan_policy=scene_policy,
    )


def _directory_fingerprint(directory: Path) -> tuple[object, ...]:
    values: list[object] = []
    for path in _profile_files(directory):
        try:
            data = path.read_bytes()
            stat = path.stat()
        except OSError as exc:
            raise MVPlannerError(
                f"Could not inspect visual profile file {path}: {exc}"
            ) from exc
        values.extend(
            (path.name, stat.st_mtime_ns, stat.st_size, hashlib.sha256(data).hexdigest())
        )
    return tuple(values)


def load_visual_profile(profile_id: str) -> VisualEnrichmentProfile:
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise MVPlannerError(f"Unknown visual_enrichment_profile {profile_id!r}")
    directory = _PROFILE_ROOT / profile_id
    if not directory.is_dir():
        raise MVPlannerError(f"Unknown visual_enrichment_profile {profile_id!r}")
    return _load_profile_cached(str(directory), _directory_fingerprint(directory))


def discover_visual_profile_ids() -> list[str]:
    profiles = [load_visual_profile(path.name) for path in _profile_directories()]
    profiles.sort(key=lambda profile: (profile.ui_order, profile.profile_id))
    ids = [profile.profile_id for profile in profiles]
    if DEFAULT_VISUAL_PROFILE_ID not in ids:
        raise MVPlannerError(
            f"Default visual profile {DEFAULT_VISUAL_PROFILE_ID!r} is missing"
        )
    return ids


def compose_profiled_system_prompt(
    base_prompt: str,
    profile: VisualEnrichmentProfile,
    *,
    stage: str,
) -> str:
    if stage not in _STAGES:
        raise MVPlannerError(f"Unknown planner prompt stage {stage!r}")
    policy = (
        profile.song_bible_policy
        if stage == "song_bible"
        else profile.scene_plan_policy
    )
    return (
        f"{base_prompt.rstrip()}\n\n"
        f"VISUAL ENRICHMENT PROFILE: {profile.profile_id}\n"
        f"{policy.strip()}"
    )
