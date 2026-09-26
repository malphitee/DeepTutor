"""Bounded image payloads, including legacy uploads and transparent diagrams."""

from io import BytesIO
import random

from PIL import Image
import pytest

from deeptutor.services.llm.model_images import (
    MAX_INPUT_BYTES,
    MAX_MODEL_BYTES,
    MAX_MODEL_EDGE,
    MAX_MODEL_PIXELS,
    ModelImageError,
    normalize_image_for_model,
    normalize_local_image,
)


def image_bytes(size=(32, 24), mode="RGB", fmt="PNG", **kwargs):
    output = BytesIO()
    Image.new(mode, size, (32, 90, 150, 90) if mode == "RGBA" else (32, 90, 150)).save(
        output, format=fmt, **kwargs
    )
    return output.getvalue()


def test_small_valid_images_remain_byte_identical():
    for fmt in ("PNG", "JPEG", "GIF", "WEBP"):
        raw = image_bytes(fmt=fmt)
        result = normalize_image_for_model(raw)
        assert result.data == raw
        assert result.changed is False


def test_large_png_is_downscaled_even_when_encoded_file_is_small():
    raw = image_bytes(size=(5712, 4284))
    result = normalize_image_for_model(raw)
    assert result.changed is True
    assert (result.width, result.height) == (2560, 1920)
    assert result.width * result.height <= MAX_MODEL_PIXELS
    assert len(result.data) <= MAX_MODEL_BYTES


def test_file_size_alone_triggers_optimization():
    # PNG ancillary/trailing data must not bypass the byte-size policy.
    raw = image_bytes() + b"padding" * (MAX_MODEL_BYTES // 7 + 1)
    result = normalize_image_for_model(raw)
    assert result.changed is True
    assert len(result.data) <= MAX_MODEL_BYTES


def test_transparent_diagram_stays_transparent():
    result = normalize_image_for_model(image_bytes(size=(3000, 2400), mode="RGBA"))
    assert result.mime_type in ("image/png", "image/webp")
    with Image.open(BytesIO(result.data)) as decoded:
        assert max(decoded.size) <= MAX_MODEL_EDGE
        assert decoded.convert("RGBA").getpixel((0, 0))[3] == 90


def test_large_transparent_png_falls_back_to_webp_without_flattening_alpha():
    pixels = random.Random(4).randbytes(900 * 900 * 4)
    image = Image.frombytes("RGBA", (900, 900), pixels)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    assert len(buffer.getvalue()) > MAX_MODEL_BYTES
    result = normalize_image_for_model(buffer.getvalue())
    assert result.mime_type == "image/webp"
    assert len(result.data) <= MAX_MODEL_BYTES
    with Image.open(BytesIO(result.data)) as decoded:
        assert decoded.size == image.size
        assert decoded.convert("RGBA").getpixel((0, 0))[3] == pixels[3]


def test_large_rotated_jpeg_applies_exif_orientation():
    exif = Image.Exif()
    exif[274] = 6
    result = normalize_image_for_model(image_bytes(size=(3000, 2000), fmt="JPEG", exif=exif))
    assert (result.width, result.height) == (1706, 2560)


@pytest.mark.parametrize("raw", [b"not an image", b"\x89PNG\r\n\x1a\nFAKE"])
def test_rejects_invalid_or_truncated_images(raw):
    with pytest.raises(ModelImageError, match="valid|decode"):
        normalize_image_for_model(raw)


def test_input_byte_limit_is_checked_before_decode():
    with pytest.raises(ModelImageError, match="32 MiB"):
        normalize_image_for_model(b"x" * (MAX_INPUT_BYTES + 1))


def test_input_pixel_limit_is_checked_before_pixel_decode(monkeypatch):
    import deeptutor.services.llm.model_images as module

    monkeypatch.setattr(module, "MAX_INPUT_PIXELS", 100)
    with pytest.raises(ModelImageError, match="pixel"):
        normalize_image_for_model(image_bytes())


def test_local_variant_is_reused_and_cannot_escape_attachment_directory(tmp_path, monkeypatch):
    source = tmp_path / "att1_photo.png"
    source.write_bytes(image_bytes(size=(3000, 2400)))
    first = normalize_local_image(source)
    variants = list(tmp_path.glob("att1_photo.png.model-*"))
    assert len(variants) == 1
    # Cached requests never reopen the expensive original image.
    original_read = type(source).read_bytes

    def guarded_read(path):
        assert path != source
        return original_read(path)

    monkeypatch.setattr(type(source), "read_bytes", guarded_read)
    assert normalize_local_image(source).data == first.data


def test_local_variant_symlink_is_not_read_or_written(tmp_path):
    source = tmp_path / "att1_photo.png"
    source.write_bytes(image_bytes(size=(3000, 2400)))
    result = normalize_local_image(source)
    variant = next(tmp_path.glob("att1_photo.png.model-*"))
    outside = tmp_path / "unrelated.bin"
    outside.write_bytes(b"private")
    variant.unlink()
    variant.symlink_to(outside)
    assert normalize_local_image(source).data == result.data
    assert outside.read_bytes() == b"private"


def test_corrupt_cached_variant_is_rebuilt(tmp_path):
    source = tmp_path / "att1_photo.png"
    source.write_bytes(image_bytes(size=(3000, 2400)))
    result = normalize_local_image(source)
    variant = next(tmp_path.glob("att1_photo.png.model-*"))
    variant.write_bytes(b"corrupt")
    assert normalize_local_image(source).data == result.data
    assert variant.read_bytes() == result.data
