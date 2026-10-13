"""HTTP routes."""

import os
import time
from pathlib import PurePath

from flask import Blueprint, current_app, jsonify, render_template, request, send_file

from . import cleanup
from . import enhance
from .imaging import optimize_image
from .presets import (DEFAULT_PRESET, PRESETS, QUALITY_DEFAULT, QUALITY_MAX, QUALITY_MIN,
                      client_config)
from .validators import (ACCEPTED_DESCRIPTION, ACCEPTED_MIME_TYPES, FORMATS, SCALE_MAX,
                         SCALE_MIN, ValidationError, parse_options)

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    return render_template(
        "index.html",
        formats=list(FORMATS),
        accept=",".join(ACCEPTED_MIME_TYPES),
        accepted=ACCEPTED_DESCRIPTION,
        max_mb=current_app.config["MAX_UPLOAD_MB"],
        presets=PRESETS,
        default_preset=DEFAULT_PRESET,
        client_config={"presets": client_config(), **enhance.client_config()},
        denoise_levels=enhance.DENOISE,
        sharpen_levels=enhance.SHARPEN,
        quality=(QUALITY_MIN, QUALITY_MAX, QUALITY_DEFAULT),
        scale=(SCALE_MIN, SCALE_MAX),
    )


@bp.post("/optimize")
def optimize():
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return jsonify(error="Please choose an image to upload."), 400
    try:
        opts = parse_options(request.form)
    except ValidationError as exc:
        return jsonify(error=str(exc)), 400

    started = time.perf_counter()
    src = cleanup.new_upload_path()
    dst = cleanup.new_output_file()
    try:
        upload.save(src)
        original_size = src.stat().st_size
        info = optimize_image(src, dst, opts)
        optimized_size = os.fstat(dst.fileno()).st_size
        stem = PurePath(upload.filename).stem or "image"
        response = send_file(
            dst,
            mimetype=info["mime"],
            as_attachment=True,
            download_name=f"{stem}-optimized.{info['ext']}",
            max_age=0,
        )
    except ValidationError as exc:
        dst.close()
        return jsonify(error=str(exc)), 400
    except BaseException:
        dst.close()
        raise
    finally:
        cleanup.remove(src)

    # One line per request: settings and sizes only, never file names or content.
    current_app.logger.info(
        "optimize: %s %dx%d %d B -> %s %dx%d %d B | quality=%d preset=%s scale=%d%% "
        "strip=%d orient=%d lossless=%d reduce=%d denoise=%s sharpen=%s kept_original=%d | %.2fs",
        info["original_format"], info["original_width"], info["original_height"], original_size,
        info["format"], info["width"], info["height"], optimized_size,
        opts.quality, opts.preset, opts.scale, opts.strip_metadata, opts.auto_orient,
        opts.lossless, opts.reduce_colors, opts.denoise, opts.sharpen, info["kept_original"],
        time.perf_counter() - started)

    # send_file streams dst and closes it (which deletes it) once sending is done.
    response.content_length = optimized_size
    # No retention on the client side either: don't let browsers or proxies cache it.
    response.headers["Cache-Control"] = "no-store"
    response.headers.update({
        "X-Original-Size": str(original_size),
        "X-Optimized-Size": str(optimized_size),
        "X-Image-Width": str(info["width"]),
        "X-Image-Height": str(info["height"]),
        "X-Original-Width": str(info["original_width"]),
        "X-Original-Height": str(info["original_height"]),
        "X-Quality": str(info["quality"]),
        "X-Preset": info["preset"],
        "X-Denoise": info["denoise"],
        "X-Sharpen": info["sharpen"],
        "X-Kept-Original": "1" if info["kept_original"] else "0",
    })
    return response
