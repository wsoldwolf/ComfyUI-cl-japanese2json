"""Discover external Prompt Enhancer profiles."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .errors import EnhancerProfileError


PROFILE_SCHEMA_VERSION = 1
DEFAULT_STYLE_PROFILE_ID = "passthrough"
DEFAULT_BACKGROUND_PROFILE_ID = "passthrough"
DEFAULT_MOTION_PROFILE_ID = "passthrough"
DEFAULT_CAMERA_PROFILE_ID = "passthrough"
_PROFILE_ID_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"
_STYLE_ROOT = _PROMPT_ROOT / "styles"
_BACKGROUND_ROOT = _PROMPT_ROOT / "backgrounds"
_MOTION_ROOT = _PROMPT_ROOT / "motions"
_CAMERA_ROOT = _PROMPT_ROOT / "cameras"


@dataclass(frozen=True)
class StyleProfile:
    profile_id: str
    display_name: str
    description: str
    ui_order: int
    directives: tuple[str, ...]
    system_instruction: str

    @property
    def passthrough(self) -> bool:
        return self.profile_id == DEFAULT_STYLE_PROFILE_ID


@dataclass(frozen=True)
class BackgroundProfile:
    profile_id: str
    display_name: str
    description: str
    ui_order: int
    minimum_lines: int
    maximum_lines: int
    system_instruction: str

    @property
    def passthrough(self) -> bool:
        return self.profile_id == DEFAULT_BACKGROUND_PROFILE_ID


@dataclass(frozen=True)
class MotionProfile:
    profile_id: str
    display_name: str
    description: str
    ui_order: int
    directives: tuple[str, ...]
    system_instruction: str

    @property
    def passthrough(self) -> bool:
        return self.profile_id == DEFAULT_MOTION_PROFILE_ID


@dataclass(frozen=True)
class CameraProfile:
    profile_id: str
    display_name: str
    description: str
    ui_order: int
    directives: tuple[str, ...]
    system_instruction: str

    @property
    def passthrough(self) -> bool:
        return self.profile_id == DEFAULT_CAMERA_PROFILE_ID


def _profile_directories(root: Path, kind: str) -> tuple[Path, ...]:
    try:
        directories = tuple(
            path
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
    except OSError as exc:
        raise EnhancerProfileError(
            f"Could not enumerate Prompt Enhancer {kind} profiles in {root}: {exc}"
        ) from exc
    if not directories:
        raise EnhancerProfileError(f"No Prompt Enhancer {kind} profiles are installed")
    return directories


def _fingerprint(path: Path) -> tuple[object, ...]:
    try:
        data = path.read_bytes()
        stat = path.stat()
    except OSError as exc:
        raise EnhancerProfileError(f"Could not inspect enhancer profile {path}: {exc}") from exc
    return (stat.st_mtime_ns, stat.st_size, hashlib.sha256(data).hexdigest())


def enhancer_profiles_fingerprint() -> tuple[object, ...]:
    values: list[object] = []
    for kind, root in (
        ("style", _STYLE_ROOT),
        ("background", _BACKGROUND_ROOT),
        ("motion", _MOTION_ROOT),
        ("camera", _CAMERA_ROOT),
    ):
        for directory in sorted(_profile_directories(root, kind), key=lambda item: item.name):
            path = directory / "profile.json"
            values.extend((kind, directory.name, *_fingerprint(path)))
    return tuple(values)


def _read_manifest(path: Path, kind: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EnhancerProfileError(f"Could not load {kind} profile {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EnhancerProfileError(f"{kind} profile {path} must be a JSON object")
    return value


def _required_string(data: dict[str, Any], name: str, context: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise EnhancerProfileError(f"{context}.{name} must be a non-empty string")
    return value.strip()


def _common_fields(data: dict[str, Any], directory: Path, context: str) -> tuple[str, str, str, int, str]:
    if data.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise EnhancerProfileError(
            f"{context}.schema_version must be {PROFILE_SCHEMA_VERSION}"
        )
    profile_id = _required_string(data, "profile_id", context)
    if not _PROFILE_ID_RE.fullmatch(profile_id) or profile_id != directory.name:
        raise EnhancerProfileError(
            f"{context}.profile_id must match its lowercase directory name"
        )
    display_name = _required_string(data, "display_name", context)
    description = _required_string(data, "description", context)
    system_instruction = _required_string(data, "system_instruction", context)
    ui_order = data.get("ui_order")
    if not isinstance(ui_order, int) or isinstance(ui_order, bool):
        raise EnhancerProfileError(f"{context}.ui_order must be an integer")
    return profile_id, display_name, description, ui_order, system_instruction


def _validate_directive(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EnhancerProfileError(f"{context} must be a non-empty string")
    result = value.strip()
    if "\n" in result or "\r" in result or result.startswith(("#", "*")):
        raise EnhancerProfileError(f"{context} must be one unprefixed Common bullet")
    if any(token in result for token in ("「", "」", "<d>", "</d>")):
        raise EnhancerProfileError(f"{context} cannot contain direct speech")
    return result


def _load_fixed_directive_profile(
    directory: Path,
    *,
    kind: str,
    default_profile_id: str,
    profile_type: type[MotionProfile] | type[CameraProfile],
) -> MotionProfile | CameraProfile:
    context = f"{kind} profile {directory.name!r}"
    data = _read_manifest(directory / "profile.json", kind)
    expected = {
        "schema_version",
        "profile_id",
        "display_name",
        "description",
        "ui_order",
        "directives",
        "system_instruction",
    }
    if set(data) != expected:
        raise EnhancerProfileError(
            f"{context} fields are invalid; missing={sorted(expected - set(data))}, "
            f"extra={sorted(set(data) - expected)}"
        )
    profile_id, display_name, description, ui_order, instruction = _common_fields(
        data, directory, context
    )
    raw_directives = data["directives"]
    if not isinstance(raw_directives, list):
        raise EnhancerProfileError(f"{context}.directives must be a list")
    directives = tuple(
        _validate_directive(value, f"{context}.directives[{index}]")
        for index, value in enumerate(raw_directives)
    )
    if profile_id == default_profile_id and directives:
        raise EnhancerProfileError(f"passthrough {kind} profile cannot add directives")
    if profile_id != default_profile_id and not directives:
        raise EnhancerProfileError(f"{context} must add at least one directive")
    return profile_type(
        profile_id=profile_id,
        display_name=display_name,
        description=description,
        ui_order=ui_order,
        directives=directives,
        system_instruction=instruction,
    )


@lru_cache(maxsize=32)
def _load_motion_cached(
    directory_text: str, fingerprint: tuple[object, ...]
) -> MotionProfile:
    del fingerprint
    result = _load_fixed_directive_profile(
        Path(directory_text),
        kind="motion",
        default_profile_id=DEFAULT_MOTION_PROFILE_ID,
        profile_type=MotionProfile,
    )
    assert isinstance(result, MotionProfile)
    return result


@lru_cache(maxsize=32)
def _load_camera_cached(
    directory_text: str, fingerprint: tuple[object, ...]
) -> CameraProfile:
    del fingerprint
    result = _load_fixed_directive_profile(
        Path(directory_text),
        kind="camera",
        default_profile_id=DEFAULT_CAMERA_PROFILE_ID,
        profile_type=CameraProfile,
    )
    assert isinstance(result, CameraProfile)
    return result


@lru_cache(maxsize=64)
def _load_style_cached(directory_text: str, fingerprint: tuple[object, ...]) -> StyleProfile:
    del fingerprint
    directory = Path(directory_text)
    context = f"style profile {directory.name!r}"
    data = _read_manifest(directory / "profile.json", "style")
    expected = {
        "schema_version",
        "profile_id",
        "display_name",
        "description",
        "ui_order",
        "directives",
        "system_instruction",
    }
    if set(data) != expected:
        raise EnhancerProfileError(
            f"{context} fields are invalid; missing={sorted(expected - set(data))}, "
            f"extra={sorted(set(data) - expected)}"
        )
    profile_id, display_name, description, ui_order, instruction = _common_fields(
        data, directory, context
    )
    raw_directives = data["directives"]
    if not isinstance(raw_directives, list):
        raise EnhancerProfileError(f"{context}.directives must be a list")
    directives = tuple(
        _validate_directive(value, f"{context}.directives[{index}]")
        for index, value in enumerate(raw_directives)
    )
    if profile_id == DEFAULT_STYLE_PROFILE_ID and directives:
        raise EnhancerProfileError("passthrough style profile cannot add directives")
    if profile_id != DEFAULT_STYLE_PROFILE_ID and not directives:
        raise EnhancerProfileError(f"{context} must add at least one directive")
    return StyleProfile(
        profile_id=profile_id,
        display_name=display_name,
        description=description,
        ui_order=ui_order,
        directives=directives,
        system_instruction=instruction,
    )


@lru_cache(maxsize=32)
def _load_background_cached(directory_text: str, fingerprint: tuple[object, ...]) -> BackgroundProfile:
    del fingerprint
    directory = Path(directory_text)
    context = f"background profile {directory.name!r}"
    data = _read_manifest(directory / "profile.json", "background")
    expected = {
        "schema_version",
        "profile_id",
        "display_name",
        "description",
        "ui_order",
        "minimum_lines",
        "maximum_lines",
        "system_instruction",
    }
    if set(data) != expected:
        raise EnhancerProfileError(
            f"{context} fields are invalid; missing={sorted(expected - set(data))}, "
            f"extra={sorted(set(data) - expected)}"
        )
    profile_id, display_name, description, ui_order, instruction = _common_fields(
        data, directory, context
    )
    minimum = data["minimum_lines"]
    maximum = data["maximum_lines"]
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or not 0 <= minimum <= maximum <= 12
    ):
        raise EnhancerProfileError(
            f"{context} line counts must satisfy 0 <= minimum <= maximum <= 12"
        )
    if profile_id == DEFAULT_BACKGROUND_PROFILE_ID and (minimum != 0 or maximum != 0):
        raise EnhancerProfileError("passthrough background profile requires zero lines")
    if profile_id != DEFAULT_BACKGROUND_PROFILE_ID and minimum < 1:
        raise EnhancerProfileError(f"{context} must require at least one line")
    return BackgroundProfile(
        profile_id=profile_id,
        display_name=display_name,
        description=description,
        ui_order=ui_order,
        minimum_lines=minimum,
        maximum_lines=maximum,
        system_instruction=instruction,
    )


def load_style_profile(profile_id: str) -> StyleProfile:
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise EnhancerProfileError(f"Unknown style_profile {profile_id!r}")
    directory = _STYLE_ROOT / profile_id
    path = directory / "profile.json"
    if not directory.is_dir() or not path.is_file():
        raise EnhancerProfileError(f"Unknown style_profile {profile_id!r}")
    return _load_style_cached(str(directory), _fingerprint(path))


def load_background_profile(profile_id: str) -> BackgroundProfile:
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise EnhancerProfileError(f"Unknown background_detail {profile_id!r}")
    directory = _BACKGROUND_ROOT / profile_id
    path = directory / "profile.json"
    if not directory.is_dir() or not path.is_file():
        raise EnhancerProfileError(f"Unknown background_detail {profile_id!r}")
    return _load_background_cached(str(directory), _fingerprint(path))


def load_motion_profile(profile_id: str) -> MotionProfile:
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise EnhancerProfileError(f"Unknown motion_profile {profile_id!r}")
    directory = _MOTION_ROOT / profile_id
    path = directory / "profile.json"
    if not directory.is_dir() or not path.is_file():
        raise EnhancerProfileError(f"Unknown motion_profile {profile_id!r}")
    return _load_motion_cached(str(directory), _fingerprint(path))


def load_camera_profile(profile_id: str) -> CameraProfile:
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise EnhancerProfileError(f"Unknown camera_profile {profile_id!r}")
    directory = _CAMERA_ROOT / profile_id
    path = directory / "profile.json"
    if not directory.is_dir() or not path.is_file():
        raise EnhancerProfileError(f"Unknown camera_profile {profile_id!r}")
    return _load_camera_cached(str(directory), _fingerprint(path))


def discover_style_profile_ids() -> list[str]:
    profiles = [load_style_profile(path.name) for path in _profile_directories(_STYLE_ROOT, "style")]
    profiles.sort(key=lambda item: (item.ui_order, item.profile_id))
    result = [item.profile_id for item in profiles]
    if DEFAULT_STYLE_PROFILE_ID not in result:
        raise EnhancerProfileError("The passthrough style profile is missing")
    return result


def discover_background_profile_ids() -> list[str]:
    profiles = [
        load_background_profile(path.name)
        for path in _profile_directories(_BACKGROUND_ROOT, "background")
    ]
    profiles.sort(key=lambda item: (item.ui_order, item.profile_id))
    result = [item.profile_id for item in profiles]
    if DEFAULT_BACKGROUND_PROFILE_ID not in result:
        raise EnhancerProfileError("The passthrough background profile is missing")
    return result


def discover_motion_profile_ids() -> list[str]:
    profiles = [
        load_motion_profile(path.name)
        for path in _profile_directories(_MOTION_ROOT, "motion")
    ]
    profiles.sort(key=lambda item: (item.ui_order, item.profile_id))
    result = [item.profile_id for item in profiles]
    if DEFAULT_MOTION_PROFILE_ID not in result:
        raise EnhancerProfileError("The passthrough motion profile is missing")
    return result


def discover_camera_profile_ids() -> list[str]:
    profiles = [
        load_camera_profile(path.name)
        for path in _profile_directories(_CAMERA_ROOT, "camera")
    ]
    profiles.sort(key=lambda item: (item.ui_order, item.profile_id))
    result = [item.profile_id for item in profiles]
    if DEFAULT_CAMERA_PROFILE_ID not in result:
        raise EnhancerProfileError("The passthrough camera profile is missing")
    return result
