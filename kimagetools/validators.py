"""Validation of uploaded files and form options.

Everything here raises ValidationError, whose message is safe to show to users.
"""

from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from .enhance import ADJUSTMENTS, DENOISE
from .presets import (DEFAULT_PRESET, PRESETS, QUALITY_DEFAULT, QUALITY_MAX,
                      QUALITY_MIN)

# Guard against decompression bombs (~80 megapixels).
Image.MAX_IMAGE_PIXELS = 80_000_000

# Limits on the *output* image, since scaling can enlarge it up to 2x.
MAX_OUTPUT_PIXELS = 100_000_000

# Format key -> (Pillow format name, file extension, MIME type, max side in px).
# These are both the accepted upload formats and the available output formats.
FORMATS = {
    "jpeg": ("JPEG", "jpg", "image/jpeg", 65_500),
    "png": ("PNG", "png", "image/png", 1_000_000),
    "webp": ("WEBP", "webp", "image/webp", 16_383),
}
ACCEPTED_MIME_TYPES = [f[2] for f in FORMATS.values()]
ACCEPTED_DESCRIPTION = "JPEG, PNG or WebP"

# Pillow format name -> format key, for detecting the uploaded image's type.
PIL_TO_KEY = {f[0]: key for key, f in FORMATS.items()}
PIL_TO_KEY["MPO"] = "jpeg"  # some cameras emit MPO, which is JPEG-compatible

_PLEASE = f"Please upload {ACCEPTED_DESCRIPTION}."


class ValidationError(ValueError):
    """Invalid user input; the message is safe to show to the user."""


# --- Uploaded file ------------------------------------------------------------

def _looks_like_svg(path):
    with open(path, "rb") as f:
        head = f.read(1024).removeprefix(b"\xef\xbb\xbf").lstrip().lower()
    # Only text files start with "<"; real JPEG/PNG/WebP start with binary magic.
    return head.startswith(b"<") and b"<svg" in head


def open_image(path):
    """Open and fully decode an upload, accepting only JPEG/PNG/WebP.

    The type is decided by the file's content, not its name or the
    browser-supplied MIME type. Returns (image, format key).
    """
    if path.stat().st_size == 0:
        raise ValidationError("The uploaded file is empty.")
    if _looks_like_svg(path):
        raise ValidationError(f"SVG files aren't supported. {_PLEASE}")
    try:
        img = Image.open(path)
    except Image.DecompressionBombError:
        raise ValidationError("Image is too large (too many pixels).")
    except (UnidentifiedImageError, OSError):
        raise ValidationError(f"That file isn't a supported image. {_PLEASE}")

    source_key = PIL_TO_KEY.get(img.format)
    if source_key is None:
        name = img.format or "This"
        img.close()
        if name == "GIF":
            raise ValidationError(f"GIF files aren't supported. {_PLEASE}")
        raise ValidationError(f"{name} images aren't supported. {_PLEASE}")

    try:
        img.load()
    except Image.DecompressionBombError:
        img.close()
        raise ValidationError("Image is too large (too many pixels).")
    except OSError:
        img.close()
        raise ValidationError("The image file is corrupt or truncated.")
    return img, source_key


def check_output_size(width, height, fmt_key):
    """Reject a planned output size that's too big overall or for the format."""
    if width * height > MAX_OUTPUT_PIXELS:
        raise ValidationError(
            f"The result would be {width}×{height} px, which is too large. "
            "Choose a smaller resize percentage.")
    max_side = FORMATS[fmt_key][3]
    if max(width, height) > max_side:
        raise ValidationError(
            f"The result would be {width}×{height} px, but {fmt_key.upper()} images "
            f"can be at most {max_side} px on a side. Choose a smaller resize "
            "percentage or another format.")


# --- Form options -------------------------------------------------------------

SCALE_MIN, SCALE_MAX = 10, 200  # resize percentage

@dataclass(frozen=True)
class Options:
    format: str = "original"   # "original" or a FORMATS key
    quality: int = QUALITY_DEFAULT
    preset: str = DEFAULT_PRESET
    scale: int = 100           # percent; aspect ratio is always preserved
    strip_metadata: bool = True
    auto_orient: bool = True
    lossless: bool = False     # WebP only
    reduce_colors: bool = False  # PNG only
    denoise: str = "off"       # a key of enhance.DENOISE
    # Slider adjustments (see enhance.ADJUSTMENTS); 0 = unchanged.
    brightness: int = 0
    contrast: int = 0
    blur: int = 0
    sharpen: int = 0

    @property
    def adjustments(self):
        return {k: getattr(self, k) for k in ADJUSTMENTS}


def _int_field(form, name, default, lo, hi):
    raw = (form.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValidationError(f"'{name}' must be a whole number.")
    if not lo <= value <= hi:
        raise ValidationError(f"'{name}' must be between {lo} and {hi}.")
    return value


_TRUE = ("1", "true", "on", "yes")
_FALSE = ("0", "false", "off", "no")


def _bool_field(form, name, default):
    raw = (form.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ValidationError(f"'{name}' must be 1 or 0.")


def _choice_field(form, name, default, choices):
    raw = (form.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw not in choices:
        raise ValidationError(f"'{name}' must be one of: {', '.join(choices)}.")
    return raw


def parse_options(form):
    """Build Options from a submitted form (any mapping of str -> str).

    Missing fields take the Options defaults, so API clients can send only
    what they want to change. (The web UI always sends every toggle
    explicitly, because an unchecked checkbox isn't submitted at all.)
    """
    fmt = (form.get("format") or "original").strip().lower()
    if fmt != "original" and fmt not in FORMATS:
        raise ValidationError(f"Unsupported output format: {fmt}. "
                              f"Choose {ACCEPTED_DESCRIPTION}.")
    preset = (form.get("preset") or DEFAULT_PRESET).strip().lower()
    if preset not in PRESETS:
        raise ValidationError(f"Unknown preset: {preset}. "
                              f"Choose one of: {', '.join(PRESETS)}.")
    d = Options()
    return Options(
        denoise=_choice_field(form, "denoise", d.denoise, DENOISE),
        **{k: _int_field(form, k, 0, lo, hi) for k, (lo, hi) in ADJUSTMENTS.items()},
        format=fmt,
        quality=_int_field(form, "quality", d.quality, QUALITY_MIN, QUALITY_MAX),
        preset=preset,
        scale=_int_field(form, "scale", d.scale, SCALE_MIN, SCALE_MAX),
        strip_metadata=_bool_field(form, "strip_metadata", d.strip_metadata),
        auto_orient=_bool_field(form, "auto_orient", d.auto_orient),
        lossless=_bool_field(form, "lossless", d.lossless),
        reduce_colors=_bool_field(form, "reduce_colors", d.reduce_colors),
    )
