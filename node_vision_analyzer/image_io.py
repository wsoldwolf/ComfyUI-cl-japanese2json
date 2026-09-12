"""Safe ComfyUI input decoding and Vision-only image preprocessing."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
from io import BytesIO
import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps
import torch

from .errors import VisionImageError


NO_INPUT_IMAGES_PLACEHOLDER = "(no input images found)"
MAX_IMAGE_PIXELS = 100_000_000


@dataclass(frozen=True)
class LoadedImage:
    image: Any
    mask: Any
    pil_image: Image.Image
    image_hash: str
    image_mode: str
    width: int
    height: int
    frame_count: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class AnalysisImage:
    data_uri: str
    width: int
    height: int


def _folder_paths(folder_paths_module: Any | None = None) -> Any:
    if folder_paths_module is not None:
        return folder_paths_module
    try:
        import folder_paths  # type: ignore
    except Exception as exc:
        raise VisionImageError("ComfyUI folder_paths is unavailable") from exc
    return folder_paths


def discover_input_images(folder_paths_module: Any | None = None) -> list[str]:
    folder_paths = _folder_paths(folder_paths_module)
    input_root = Path(folder_paths.get_input_directory())
    if not input_root.is_dir():
        return [NO_INPUT_IMAGES_PLACEHOLDER]
    names: list[str] = []
    try:
        for candidate in input_root.rglob("*"):
            if candidate.is_file():
                names.append(candidate.relative_to(input_root).as_posix())
    except OSError:
        return [NO_INPUT_IMAGES_PLACEHOLDER]
    filter_files = getattr(folder_paths, "filter_files_content_types", None)
    if callable(filter_files):
        try:
            names = list(filter_files(names, ["image"]))
        except Exception:
            pass
    else:
        allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
        names = [name for name in names if Path(name).suffix.casefold() in allowed]
    names = sorted(set(names), key=str.casefold)
    return names or [NO_INPUT_IMAGES_PLACEHOLDER]


def resolve_input_image(
    image_name: str,
    folder_paths_module: Any | None = None,
) -> Path:
    if not isinstance(image_name, str) or not image_name or "\x00" in image_name:
        raise VisionImageError("image must name one uploaded ComfyUI input image")
    if image_name == NO_INPUT_IMAGES_PLACEHOLDER:
        raise VisionImageError("No ComfyUI input image is available")
    folder_paths = _folder_paths(folder_paths_module)
    try:
        input_root = Path(folder_paths.get_input_directory()).resolve(strict=True)
        resolved = Path(folder_paths.get_annotated_filepath(image_name)).resolve(
            strict=True
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise VisionImageError(f"Invalid input image {image_name!r}") from exc
    try:
        common = Path(os.path.commonpath((str(input_root), str(resolved))))
    except ValueError as exc:
        raise VisionImageError("Input image is outside ComfyUI/input") from exc
    if os.path.normcase(str(common)) != os.path.normcase(str(input_root)):
        raise VisionImageError("Input image is outside ComfyUI/input")
    if not resolved.is_file():
        raise VisionImageError(f"Input image does not exist: {image_name!r}")
    return resolved


def _intermediate_target() -> tuple[Any, Any]:
    try:
        import comfy.model_management as model_management  # type: ignore

        return (
            model_management.intermediate_device(),
            model_management.intermediate_dtype(),
        )
    except Exception:
        return "cpu", torch.float32


def _pixel_hash(image: Image.Image, mode: str) -> str:
    digest = hashlib.sha256()
    digest.update(mode.encode("ascii"))
    digest.update(b"\0")
    digest.update(str(image.width).encode("ascii"))
    digest.update(b"x")
    digest.update(str(image.height).encode("ascii"))
    digest.update(b"\0")
    digest.update(image.tobytes())
    return digest.hexdigest()


def load_image_asset(
    image_name: str,
    folder_paths_module: Any | None = None,
) -> LoadedImage:
    path = resolve_input_image(image_name, folder_paths_module)
    try:
        with Image.open(path) as opened:
            frame_count = int(getattr(opened, "n_frames", 1) or 1)
            opened.seek(0)
            oriented = ImageOps.exif_transpose(opened)
            if oriented.width * oriented.height > MAX_IMAGE_PIXELS:
                raise VisionImageError(
                    f"Input image exceeds {MAX_IMAGE_PIXELS:,} decoded pixels"
                )
            has_alpha = "A" in oriented.getbands() or "transparency" in opened.info
            normalized = oriented.convert("RGBA" if has_alpha else "RGB")
            rgb = normalized.convert("RGB")
            image_array = np.asarray(rgb, dtype=np.float32) / 255.0
            if has_alpha:
                alpha_array = (
                    np.asarray(normalized.getchannel("A"), dtype=np.float32) / 255.0
                )
                mask_array = 1.0 - alpha_array
            else:
                mask_array = np.zeros((rgb.height, rgb.width), dtype=np.float32)
    except VisionImageError:
        raise
    except Exception as exc:
        raise VisionImageError(f"Could not decode input image {path.name!r}") from exc

    device, dtype = _intermediate_target()
    image_tensor = torch.from_numpy(image_array.copy()).unsqueeze(0)
    mask_tensor = torch.from_numpy(mask_array.copy()).unsqueeze(0)
    image_tensor = image_tensor.to(device=device, dtype=dtype)
    mask_tensor = mask_tensor.to(device=device, dtype=dtype)
    mode = normalized.mode
    warnings = (
        ("Animated image detected; only its first frame was analyzed",)
        if frame_count > 1
        else ()
    )
    return LoadedImage(
        image=image_tensor,
        mask=mask_tensor,
        pil_image=normalized.copy(),
        image_hash=_pixel_hash(normalized, mode),
        image_mode=mode,
        width=normalized.width,
        height=normalized.height,
        frame_count=frame_count,
        warnings=warnings,
    )


def load_image_tensor_asset(image_tensor: Any) -> LoadedImage:
    """Normalize one standard ComfyUI IMAGE input without changing its pixels."""

    if not isinstance(image_tensor, torch.Tensor):
        raise VisionImageError("image_override must be a ComfyUI IMAGE tensor")
    if image_tensor.ndim != 4:
        raise VisionImageError(
            "image_override must have shape [batch, height, width, channels]"
        )
    batch, height, width, channels = image_tensor.shape
    if batch < 1 or height < 1 or width < 1:
        raise VisionImageError("image_override must contain at least one image")
    if channels not in {1, 3, 4}:
        raise VisionImageError(
            "image_override channels must be 1, 3, or 4"
        )
    if width * height > MAX_IMAGE_PIXELS:
        raise VisionImageError(
            f"image_override exceeds {MAX_IMAGE_PIXELS:,} decoded pixels"
        )

    first = image_tensor[0].detach().to(device="cpu", dtype=torch.float32)
    if not bool(torch.isfinite(first).all()):
        raise VisionImageError("image_override contains NaN or infinity")
    minimum = float(first.min())
    maximum = float(first.max())
    if minimum < -1e-6 or maximum > 1.0 + 1e-6:
        raise VisionImageError(
            "image_override pixel values must be between 0.0 and 1.0"
        )
    first = first.clamp(0.0, 1.0)
    array = np.rint(first.numpy() * 255.0).astype(np.uint8)

    if channels == 1:
        gray = array[..., 0]
        normalized = Image.fromarray(gray, mode="L").convert("RGB")
        output_image = image_tensor.repeat(1, 1, 1, 3)
        output_mask = torch.zeros(
            (batch, height, width),
            device=image_tensor.device,
            dtype=image_tensor.dtype,
        )
        mode = "RGB"
    elif channels == 4:
        normalized = Image.fromarray(array, mode="RGBA")
        output_image = image_tensor[..., :3]
        output_mask = 1.0 - image_tensor[..., 3]
        mode = "RGBA"
    else:
        normalized = Image.fromarray(array, mode="RGB")
        output_image = image_tensor
        output_mask = torch.zeros(
            (batch, height, width),
            device=image_tensor.device,
            dtype=image_tensor.dtype,
        )
        mode = "RGB"

    warnings = (
        (f"IMAGE batch contains {batch} images; only the first was analyzed",)
        if batch > 1
        else ()
    )
    return LoadedImage(
        image=output_image,
        mask=output_mask,
        pil_image=normalized,
        image_hash=_pixel_hash(normalized, mode),
        image_mode=mode,
        width=width,
        height=height,
        frame_count=batch,
        warnings=warnings,
    )


def prepare_analysis_image(
    image: Image.Image,
    analysis_max_edge: int,
) -> AnalysisImage:
    if not isinstance(analysis_max_edge, int) or isinstance(analysis_max_edge, bool):
        raise VisionImageError("analysis_max_edge must be an integer")
    if not 256 <= analysis_max_edge <= 2048:
        raise VisionImageError("analysis_max_edge must be between 256 and 2048")

    working = image.copy()
    longest = max(working.size)
    if longest > analysis_max_edge:
        scale = analysis_max_edge / longest
        size = (
            max(1, round(working.width * scale)),
            max(1, round(working.height * scale)),
        )
        working = working.resize(size, Image.Resampling.LANCZOS)
    if working.mode == "RGBA":
        background = Image.new("RGB", working.size, "white")
        background.paste(working, mask=working.getchannel("A"))
        working = background
    else:
        working = working.convert("RGB")
    output = BytesIO()
    working.save(output, format="PNG", optimize=False)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return AnalysisImage(
        data_uri=f"data:image/png;base64,{encoded}",
        width=working.width,
        height=working.height,
    )


def input_image_change_hash(
    image_name: str,
    folder_paths_module: Any | None = None,
) -> str:
    path = resolve_input_image(image_name, folder_paths_module)
    try:
        with Image.open(path) as opened:
            opened.seek(0)
            oriented = ImageOps.exif_transpose(opened)
            if oriented.width * oriented.height > MAX_IMAGE_PIXELS:
                raise VisionImageError(
                    f"Input image exceeds {MAX_IMAGE_PIXELS:,} decoded pixels"
                )
            has_alpha = "A" in oriented.getbands() or "transparency" in opened.info
            normalized = oriented.convert("RGBA" if has_alpha else "RGB")
            return _pixel_hash(normalized, normalized.mode)
    except VisionImageError:
        raise
    except Exception as exc:
        raise VisionImageError(f"Could not decode input image {path.name!r}") from exc
