"""Image optimization pipeline (Pillow), plus OpenCV interop for future features."""

import shutil

from PIL import Image, ImageOps

from . import enhance
from .presets import PRESETS, quantize_method
from .validators import FORMATS, Options, check_output_size, open_image


def _flatten(img, background=(255, 255, 255)):
    """Composite an image with transparency onto a solid background."""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        base = Image.new("RGB", img.size, background)
        base.paste(img, mask=img.getchannel("A"))
        return base
    return img.convert("RGB")


def _has_metadata(info):
    """True if Pillow found EXIF, XMP, comments or text chunks in the image."""
    keys = ("exif", "xmp", "XML:com.adobe.xmp", "comment")
    return any(k in info for k in keys) or any(isinstance(v, str) for v in info.values())


def optimize_image(src_path, dst, opts=Options()):
    """Optimize the image at src_path, writing the result to dst.

    The upload is validated first (see validators.open_image), so this raises
    ValidationError for unsupported or broken files, or an oversized result.

    dst is a writable, seekable binary file object; it is left positioned at
    the start. If re-encoding in the same format at the same size would only
    make the file bigger, the original is copied to dst instead.
    Returns a dict describing the result.
    """
    preset = PRESETS[opts.preset]
    quality = opts.quality

    img, source_key = open_image(src_path)
    with img:
        fmt_key = source_key if opts.format == "original" else opts.format
        pil_format, ext, mime, _ = FORMATS[fmt_key]
        exif = img.info.get("exif")
        icc_profile = img.info.get("icc_profile")
        has_metadata = _has_metadata(img.info)
        original_size = img.size
        if opts.auto_orient:
            # Rotate/flip the pixels as the EXIF Orientation tag says, and reset
            # the tag, so the image looks right even once metadata is stripped.
            out_img = ImageOps.exif_transpose(img)
        else:
            out_img = img.copy()

    size = (max(1, round(out_img.width * opts.scale / 100)),
            max(1, round(out_img.height * opts.scale / 100)))
    check_output_size(*size, fmt_key)
    # Denoise runs at the smaller of the two resolutions: cheaper, and when
    # shrinking, the resize itself already averages some noise away.
    denoise_size = min(out_img.size, size, key=lambda s: s[0] * s[1])
    if opts.denoise != "off":
        enhance.check_denoise_size(*denoise_size)  # fail fast, before any work

    if opts.denoise != "off" and opts.scale > 100:
        out_img = enhance.denoise(out_img, opts.denoise)
    if opts.scale != 100:
        # When shrinking a lot, reducing_gap first downsamples cheaply, then
        # finishes with the preset's filter: much faster, visually identical.
        # (Pillow can't do this for 16-bit images.)
        gap = 3.0 if opts.scale < 50 and not out_img.mode.startswith("I;16") else None
        out_img = out_img.resize(size, preset["resample"], reducing_gap=gap)
    if opts.denoise != "off" and opts.scale <= 100:
        out_img = enhance.denoise(out_img, opts.denoise)
    # Sharpen last: resizing softens the image.
    out_img = enhance.sharpen(out_img, opts.sharpen)

    save_kwargs = {}
    # Always keep the colour profile: dropping it visibly shifts colours.
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile
    if exif and not opts.strip_metadata:
        # With auto-orient on, exif_transpose already reset the Orientation tag.
        save_kwargs["exif"] = out_img.getexif().tobytes()

    if pil_format == "JPEG":
        if out_img.mode not in ("RGB", "L", "CMYK"):
            out_img = _flatten(out_img)
        save_kwargs.update(quality=quality, **preset["jpeg"])
    elif pil_format == "PNG":
        if opts.reduce_colors:
            if out_img.mode not in ("RGB", "RGBA"):
                out_img = out_img.convert("RGBA")
            dither = (Image.Dither.FLOYDSTEINBERG if preset["quantize_dither"]
                      else Image.Dither.NONE)
            out_img = out_img.quantize(colors=256, dither=dither,
                                       method=quantize_method(opts.preset, out_img.mode))
        elif out_img.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "I;16"):
            out_img = out_img.convert("RGBA")
        save_kwargs.update(preset["png"])
    elif pil_format == "WEBP":
        if out_img.mode not in ("RGB", "RGBA"):
            out_img = out_img.convert("RGBA" if out_img.mode in ("LA", "P", "PA") else "RGB")
        # For lossless WebP, "quality" sets compression effort instead.
        save_kwargs.update(quality=quality, lossless=opts.lossless, **preset["webp"])

    out_img.save(dst, format=pil_format, **save_kwargs)

    # Fall back to the original file if re-encoding only made it bigger, but
    # never when that would hand back metadata the user asked to strip.
    kept_original = False
    if (source_key == fmt_key and out_img.size == original_size
            and opts.denoise == "off" and opts.sharpen == "off"  # those were asked for
            and not (opts.strip_metadata and has_metadata)
            and dst.tell() >= src_path.stat().st_size):
        dst.seek(0)
        dst.truncate()
        with open(src_path, "rb") as src:
            shutil.copyfileobj(src, dst)
        kept_original = True
    dst.seek(0)

    return {
        "format": fmt_key,
        "ext": ext,
        "mime": mime,
        "width": out_img.width,
        "height": out_img.height,
        "original_width": original_size[0],
        "original_height": original_size[1],
        "original_format": source_key,
        "quality": quality,
        "preset": opts.preset,
        "denoise": opts.denoise,
        "sharpen": opts.sharpen,
        "kept_original": kept_original,
    }


# --- OpenCV interop -------------------------------------------------------
# Pillow handles all file I/O. These helpers let future features (denoise,
# smart crop, face detection, ...) run OpenCV on an image already loaded
# with Pillow. cv2 is imported lazily so it doesn't slow app startup.

def pil_to_cv(img):
    """Convert a Pillow image to an OpenCV ndarray (BGR or BGRA, uint8)."""
    import cv2
    import numpy as np

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
    arr = np.asarray(img)
    code = cv2.COLOR_RGBA2BGRA if img.mode == "RGBA" else cv2.COLOR_RGB2BGR
    return cv2.cvtColor(arr, code)


def cv_to_pil(arr):
    """Convert an OpenCV ndarray (grayscale, BGR or BGRA) to a Pillow image."""
    import cv2

    if arr.ndim == 2:
        return Image.fromarray(arr)
    if arr.shape[2] == 4:
        return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA))
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))
