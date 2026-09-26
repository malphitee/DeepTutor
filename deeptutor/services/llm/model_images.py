"""One bounded representation of uploaded images for every model consumer.

Small valid images are byte-stable. Oversized images are decoded once, oriented,
and reduced without cropping; transparent diagrams retain their alpha channel.
Variants of stored uploads live beside their source, under the same private
attachment directory and deletion prefix. No image bytes are cached globally.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import math
import os
from pathlib import Path
import tempfile
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_INPUT_PIXELS = 64_000_000
MAX_MODEL_BYTES = 3 * 1024 * 1024
MAX_MODEL_PIXELS = 6_000_000
MAX_MODEL_EDGE = 2560
_VARIANT_SUFFIX = ".model-v1"
_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}


class ModelImageError(ValueError):
    """The image cannot safely be supplied to a model."""


@dataclass(frozen=True)
class NormalizedModelImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    original_size: int
    changed: bool


def _encode(image: Image.Image, fmt: str, **kwargs) -> bytes:
    output = BytesIO()
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def normalize_image_for_model(data: bytes) -> NormalizedModelImage:
    """Validate and bound PNG/JPEG/GIF/WebP bytes, or raise ModelImageError.

    Oversized animated images become a still of their first frame. Small GIFs
    and WebPs retain their original bytes. Image headers and raw byte limits
    are checked before allocating the decoded pixel buffer.
    """
    if len(data) > MAX_INPUT_BYTES:
        raise ModelImageError("Image exceeds the 32 MiB input limit.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=list(_FORMATS)) as source:
                width, height = source.size
                if width < 1 or height < 1 or width * height > MAX_INPUT_PIXELS:
                    raise ModelImageError("Image exceeds the 64 megapixel input limit.")
                mime = _FORMATS[source.format]
                # Even byte-stable images must decode: a matching signature
                # alone does not make a truncated PNG safe for a provider.
                source.load()
                if (
                    len(data) <= MAX_MODEL_BYTES
                    and max(width, height) <= MAX_MODEL_EDGE
                    and width * height <= MAX_MODEL_PIXELS
                ):
                    return NormalizedModelImage(data, mime, width, height, len(data), False)

                image = ImageOps.exif_transpose(source)
                transparent = "A" in image.getbands() or "transparency" in image.info
                image = image.convert("RGBA" if transparent else "RGB")
                scale = min(
                    1.0,
                    MAX_MODEL_EDGE / max(image.size),
                    math.sqrt(MAX_MODEL_PIXELS / (image.width * image.height)),
                )
                target = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
                if image.size != target:
                    image = image.resize(target, Image.Resampling.LANCZOS)

                for _ in range(6):
                    # Preserve crisp text/geometry when a resized PNG fits.
                    if transparent or mime == "image/png":
                        encoded = _encode(image, "PNG")
                        if len(encoded) <= MAX_MODEL_BYTES:
                            return _result(encoded, "image/png", image, len(data))
                    for quality in (88, 80, 70):
                        fmt = "WEBP" if transparent else "JPEG"
                        try:
                            encoded = _encode(image, fmt, quality=quality)
                        except (OSError, KeyError):
                            if transparent:
                                # A Pillow build without WebP support can
                                # still preserve alpha by reducing its PNG.
                                break
                            raise
                        if len(encoded) <= MAX_MODEL_BYTES:
                            return _result(encoded, _FORMATS[fmt], image, len(data))
                    image = image.resize(
                        (max(1, int(image.width * 0.8)), max(1, int(image.height * 0.8))),
                        Image.Resampling.LANCZOS,
                    )
    except ModelImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ModelImageError("Image exceeds the safe decoded pixel limit.") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, KeyError) as exc:
        raise ModelImageError("Cannot decode a valid JPG, PNG, GIF, or WebP image.") from exc
    raise ModelImageError("Image could not be reduced to the 3 MiB model limit.")


def _result(data: bytes, mime: str, image: Image.Image, original_size: int) -> NormalizedModelImage:
    return NormalizedModelImage(data, mime, image.width, image.height, original_size, True)


def normalize_local_image(path: Path) -> NormalizedModelImage:
    """Use one best-effort, per-attachment disk variant; never alter originals.

    The caller must first resolve the original via the scoped AttachmentStore.
    The cache is optional: read-only volumes and concurrent writers may safely
    fall back to normalizing the original without changing the result.
    """
    source_stat = path.stat()
    if source_stat.st_size > MAX_INPUT_BYTES:
        raise ModelImageError("Image exceeds the 32 MiB input limit.")
    cached = path.with_name(path.name + _VARIANT_SUFFIX)
    if not cached.is_symlink():
        try:
            cache_stat = cached.stat()
            if (
                cache_stat.st_mtime_ns >= source_stat.st_mtime_ns
                and cache_stat.st_size <= MAX_MODEL_BYTES
            ):
                normalized = normalize_image_for_model(cached.read_bytes())
                return NormalizedModelImage(
                    normalized.data,
                    normalized.mime_type,
                    normalized.width,
                    normalized.height,
                    source_stat.st_size,
                    True,
                )
        except (OSError, ModelImageError):
            pass

    result = normalize_image_for_model(path.read_bytes())
    if result.changed and not cached.is_symlink():
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=path.name + ".tmp-", delete=False
            ) as fh:
                tmp_name = fh.name
                fh.write(result.data)
            os.replace(tmp_name, cached)
        except OSError:
            logger.debug("Cannot persist model image variant; using normalized bytes")
        finally:
            if tmp_name:
                try:
                    Path(tmp_name).unlink(missing_ok=True)
                except OSError:
                    logger.debug("Cannot clean up temporary model image variant")
    return result
