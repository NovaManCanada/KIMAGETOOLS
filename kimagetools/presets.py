"""Speed vs quality presets.

Each preset maps to concrete encoder parameters (chroma subsampling, WebP
effort, PNG compression level, resize filter, ...). Presets never change the
quality setting: the quality slider always wins.

- speed      fastest encoding
- balanced   the default
- max        slowest encoding, best-looking output for a given quality
"""

from PIL import Image, features

QUALITY_MIN, QUALITY_MAX, QUALITY_DEFAULT = 1, 95, 85
DEFAULT_PRESET = "balanced"

_HAS_LIQ = features.check_feature("libimagequant")

PRESETS = {
    "speed": {
        "label": "Speed",
        "jpeg": {"optimize": False, "progressive": False, "subsampling": "4:2:0"},
        "webp": {"method": 2},
        "png": {"optimize": False, "compress_level": 1},
        "quantize_dither": False,
        "resample": Image.Resampling.BILINEAR,
    },
    "balanced": {
        "label": "Balanced",
        "jpeg": {"optimize": True, "progressive": True, "subsampling": "4:2:0"},
        "webp": {"method": 4},
        "png": {"optimize": True},
        "quantize_dither": True,
        "resample": Image.Resampling.BICUBIC,
    },
    "max": {
        "label": "Max quality",
        "jpeg": {"optimize": True, "progressive": True, "subsampling": "4:4:4"},
        "webp": {"method": 6},
        "png": {"optimize": True},
        "quantize_dither": True,
        "resample": Image.Resampling.LANCZOS,
    },
}


def quantize_method(preset, mode):
    """Pick a palette quantizer for PNG colour reduction."""
    if preset == "max" and _HAS_LIQ:
        return Image.Quantize.LIBIMAGEQUANT
    if preset == "speed" or mode == "RGBA":
        return Image.Quantize.FASTOCTREE  # the only built-in that handles alpha
    return Image.Quantize.MEDIANCUT


def describe(preset):
    """Human-readable encoder settings per output format, for the UI preview."""
    p = PRESETS[preset]
    j = p["jpeg"]
    jpeg = ", ".join(filter(None, [
        "progressive" if j["progressive"] else "baseline",
        "optimized Huffman tables" if j["optimize"] else None,
        f"{j['subsampling']} chroma",
    ]))
    png = (f"zlib level {p['png'].get('compress_level', 9)}"
           + (", optimized" if p["png"]["optimize"] else ""))
    return {
        "jpeg": jpeg,
        "webp": f"effort {p['webp']['method']}/6",
        "png": png,
        "resample": p["resample"].name.lower(),
    }


def client_config():
    """Everything the browser needs to preview presets, as JSON-able data."""
    return {
        name: {
            "label": p["label"],
            "describe": describe(name),
        }
        for name, p in PRESETS.items()
    }
