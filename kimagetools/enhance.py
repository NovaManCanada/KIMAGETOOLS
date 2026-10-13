"""Optional enhancements: denoise (OpenCV) and adjustments (Pillow).

All run on an 8-bit working copy (L or RGB, with any alpha channel split off
and reattached untouched), so every input mode is supported:

- denoise   OpenCV non-local means. Removes sensor grain (e.g. night/IR camera
            shots) while keeping edges. Colour images are split into
            brightness (Y) and colour (Cb, Cr): brightness is always denoised,
            and the colour channels only when the image really has colour, so
            mostly-grey images (like IR shots with a coloured logo) get the
            same strength and speed as pure greyscale and keep their colours.
            Slow: ~0.4 s per megapixel for grey images, ~1.2 s for colour, so
            it's limited to DENOISE_MAX_PIXELS.
- adjust    slider adjustments, applied in this order:
              brightness  -100..100  multiplies pixel values by 1 + v/100
              contrast    -100..100  scales distance from mean grey by 1 + v/100
              blur        0..100     Gaussian blur, radius v/10 px (up to 10 px)
              sharpen     0..100     unsharp mask, amount 2v % (up to 200 %),
                                     radius 1 + v/100 px; its threshold skips
                                     low-contrast detail, so edges are sharpened
                                     more than grain

Order in the pipeline (see imaging.optimize_image): denoise runs at whichever
of the original and output resolutions is smaller (cheaper, and shrinking
already averages noise away). Adjustments run after resizing, so blur and
sharpen radii are in output pixels and sharpening isn't softened by the resize.
"""

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

DENOISE_MAX_PIXELS = 8_500_000  # ~10 s worst case; covers 4K UHD (3840×2160)
# An image "has colour" if more than this share of pixels has R/G/B spread > 6.
_COLOUR_PIXEL_SHARE = 0.01

# h = filter strength (higher removes more noise, and more fine detail).
DENOISE = {
    "off": None,
    "light": {"label": "Light", "h": 3},
    "medium": {"label": "Medium", "h": 5},
    "strong": {"label": "Strong", "h": 8},
}
_TEMPLATE_WINDOW, _SEARCH_WINDOW = 7, 21

# Slider adjustments: name -> (min, max). The neutral value is always 0.
ADJUSTMENTS = {
    "brightness": (-100, 100),
    "contrast": (-100, 100),
    "blur": (0, 100),
    "sharpen": (0, 100),
}
_SHARPEN_THRESHOLD = 3


def sharpen_params(value):
    return {"radius": round(1 + value / 100, 2), "percent": 2 * value,
            "threshold": _SHARPEN_THRESHOLD}


def _split_alpha(img):
    """Return (8-bit L or RGB image, alpha band or None)."""
    if img.mode.startswith("I;16") or img.mode in ("I", "F"):
        # Pillow clips (not scales) these when converting to L, so scale by hand.
        arr = np.asarray(img, dtype=np.float64)
        top = 65535.0 if img.mode.startswith("I;16") else max(float(arr.max()), 1.0)
        return Image.fromarray(np.clip(arr * (255.0 / top), 0, 255).astype(np.uint8), "L"), None
    if img.mode == "P":
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
    elif img.mode == "PA":
        img = img.convert("RGBA")
    elif img.mode == "1":
        img = img.convert("L")
    elif img.mode not in ("L", "LA", "RGB", "RGBA"):
        img = img.convert("RGB")  # CMYK, YCbCr, ...
    if img.mode in ("LA", "RGBA"):
        *bands, alpha = img.split()
        return Image.merge("L" if len(bands) == 1 else "RGB", bands), alpha
    return img, None


def _join_alpha(img, alpha):
    if alpha is None:
        return img
    img.putalpha(alpha)
    return img


def check_denoise_size(width, height):
    from .validators import ValidationError  # imported here: validators imports this module

    if width * height > DENOISE_MAX_PIXELS:
        raise ValidationError(
            f"Denoise is limited to {DENOISE_MAX_PIXELS / 1e6:.1f} megapixels, and this image "
            f"is {width * height / 1e6:.1f} MP at that stage. Choose a smaller resize "
            "percentage or turn denoise off.")


def denoise(img, level):
    params = DENOISE[level]
    if params is None:
        return img
    check_denoise_size(*img.size)
    import cv2

    h = params["h"]

    def nlm(band):
        out = cv2.fastNlMeansDenoising(np.asarray(band), None, h, _TEMPLATE_WINDOW, _SEARCH_WINDOW)
        return Image.fromarray(out, "L")

    base, alpha = _split_alpha(img)
    if base.mode == "L":
        return _join_alpha(nlm(base), alpha)

    rgb = np.asarray(base, dtype=np.int16)
    has_colour = (np.ptp(rgb, axis=2) > 6).mean() > _COLOUR_PIXEL_SHARE
    y, cb, cr = base.convert("YCbCr").split()
    bands = [nlm(y), nlm(cb), nlm(cr)] if has_colour else [nlm(y), cb, cr]
    return _join_alpha(Image.merge("YCbCr", bands).convert("RGB"), alpha)


def adjust(img, brightness=0, contrast=0, blur=0, sharpen=0):
    """Apply the slider adjustments. All zero returns the image untouched."""
    if not (brightness or contrast or blur or sharpen):
        return img
    out, alpha = _split_alpha(img)
    if brightness:
        out = ImageEnhance.Brightness(out).enhance(1 + brightness / 100)
    if contrast:
        out = ImageEnhance.Contrast(out).enhance(1 + contrast / 100)
    if blur:
        out = out.filter(ImageFilter.GaussianBlur(blur / 10))
    if sharpen:
        out = out.filter(ImageFilter.UnsharpMask(**sharpen_params(sharpen)))
    return _join_alpha(out, alpha)


def client_config():
    """Labels and descriptions for the UI's settings preview."""
    return {
        "denoise": {
            k: v and {"label": v["label"],
                      "describe": f"OpenCV non-local means, strength {v['h']}"}
            for k, v in DENOISE.items()
        },
        "adjustments": {k: list(v) for k, v in ADJUSTMENTS.items()},
        "sharpen_threshold": _SHARPEN_THRESHOLD,
        "denoise_max_pixels": DENOISE_MAX_PIXELS,
    }
