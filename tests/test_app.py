import io
import tempfile

import pytest
from PIL import Image

from kimagetools import cleanup, create_app
from kimagetools.imaging import cv_to_pil, pil_to_cv
from kimagetools.validators import Options, ValidationError, parse_options

TTL = 600


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "TEMP_DIR": tmp_path, "TEMP_TTL_SECONDS": TTL,
                       "TEMP_SWEEP_INTERVAL_SECONDS": 0})


@pytest.fixture
def client(app):
    return app.test_client()


def make_image(fmt="PNG", size=(400, 300), mode="RGB", exif=None):
    # Noise over a gradient behaves like a photo: hard for PNG to compress.
    noise = Image.effect_noise(size, 40).convert("RGB")
    gradient = Image.linear_gradient("L").resize(size).convert("RGB")
    img = Image.blend(gradient, noise, 0.5).convert(mode)
    buf = io.BytesIO()
    kwargs = {"exif": exif} if exif else {}
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def post(client, data, filename="photo.png", **fields):
    form = {"image": (io.BytesIO(data), filename)}
    form.update({k: str(v) for k, v in fields.items()})
    return client.post("/optimize", data=form, content_type="multipart/form-data")


def open_result(resp):
    return Image.open(io.BytesIO(resp.data))


def test_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Image Optimizer" in resp.data


def test_png_to_jpeg_shrinks(client):
    src = make_image("PNG")
    resp = post(client, src, format="jpeg", quality=70)
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert open_result(resp).format == "JPEG"
    assert int(resp.headers["X-Optimized-Size"]) < len(src)
    assert "photo-optimized.jpg" in resp.headers["Content-Disposition"]


def test_keep_original_format(client):
    resp = post(client, make_image("JPEG"), filename="a.jpg", format="original")
    assert resp.status_code == 200
    assert open_result(resp).format == "JPEG"


@pytest.mark.parametrize("scale,expected", [(50, (200, 150)), (10, (40, 30)), (200, (800, 600))])
def test_resize_by_percentage(client, scale, expected):
    resp = post(client, make_image("PNG", size=(400, 300)), format="webp", scale=scale)
    assert resp.status_code == 200
    assert open_result(resp).size == expected
    assert resp.headers["X-Original-Width"] == "400"


@pytest.mark.parametrize("scale", ["5", "205", "abc"])
def test_rejects_bad_scale(client, scale):
    assert post(client, make_image(), scale=scale).status_code == 400


def test_rejects_upscale_beyond_webp_limit(client):
    resp = post(client, make_image("PNG", size=(9000, 10)), format="webp", scale=200)
    assert resp.status_code == 400
    assert "16383" in resp.get_json()["error"]


def test_rgba_to_jpeg_flattens(client):
    resp = post(client, make_image("PNG", mode="RGBA"), format="jpeg")
    assert resp.status_code == 200
    assert open_result(resp).mode == "RGB"


def test_png_reduce_colors(client):
    resp = post(client, make_image("PNG", mode="RGBA"), format="png", reduce_colors="1")
    assert resp.status_code == 200
    assert open_result(resp).mode == "P"


def test_webp_lossless(client):
    resp = post(client, make_image("PNG"), format="webp", lossless="1")
    assert resp.status_code == 200
    assert open_result(resp).format == "WEBP"


def jpeg_with_exif(size=(400, 300), **tags):
    exif = Image.Exif()
    for tag, value in tags.items():
        exif[{"make": 0x010F, "orientation": 0x0112}[tag]] = value
    return make_image("JPEG", size=size, exif=exif.tobytes())


def test_strip_metadata_is_default(client):
    resp = post(client, jpeg_with_exif(make="TestCam"), filename="a.jpg", format="jpeg")
    assert 0x010F not in open_result(resp).getexif()


def test_keep_metadata(client):
    resp = post(client, jpeg_with_exif(make="TestCam"), filename="a.jpg", format="jpeg",
                strip_metadata="0")
    assert open_result(resp).getexif().get(0x010F) == "TestCam"


def test_auto_orient_is_default(client):
    resp = post(client, jpeg_with_exif(orientation=6), filename="a.jpg", format="jpeg")
    assert open_result(resp).size == (300, 400)


def test_auto_orient_off_keeps_pixels_and_tag(client):
    resp = post(client, jpeg_with_exif(orientation=6), filename="a.jpg", format="jpeg",
                auto_orient="0", strip_metadata="0")
    img = open_result(resp)
    assert img.size == (400, 300)
    assert img.getexif().get(0x0112) == 6


def test_auto_orient_resets_kept_tag(client):
    resp = post(client, jpeg_with_exif(orientation=6, make="X"), filename="a.jpg",
                format="jpeg", strip_metadata="0")
    assert open_result(resp).getexif().get(0x0112, 1) == 1


def test_returns_original_when_reencode_is_larger(client):
    src = make_image("JPEG")  # Pillow's default quality 75
    resp = post(client, src, filename="a.jpg", format="jpeg", quality=95, preset="max")
    assert resp.headers["X-Kept-Original"] == "1"
    assert resp.data == src


def test_never_returns_original_with_metadata_when_stripping(client):
    src = jpeg_with_exif(make="TestCam")
    resp = post(client, src, filename="a.jpg", format="jpeg", quality=95, preset="max")
    assert resp.headers["X-Kept-Original"] == "0"
    assert 0x010F not in open_result(resp).getexif()


# --- Presets -------------------------------------------------------------------

@pytest.mark.parametrize("preset", ["speed", "balanced", "max"])
@pytest.mark.parametrize("quality", [1, 6, 40, 85, 95])
def test_slider_quality_always_wins_over_preset(client, preset, quality):
    resp = post(client, make_image(), format="jpeg", preset=preset, quality=quality)
    assert resp.status_code == 200
    assert resp.headers["X-Quality"] == str(quality)
    assert resp.headers["X-Preset"] == preset


def test_low_quality_is_much_smaller_on_every_preset(client):
    src = make_image()
    for preset in ("speed", "balanced", "max"):
        low = post(client, src, format="jpeg", preset=preset, quality=6)
        high = post(client, src, format="jpeg", preset=preset, quality=90)
        assert int(low.headers["X-Optimized-Size"]) * 3 < int(high.headers["X-Optimized-Size"])


def test_default_quality_and_preset(client):
    resp = post(client, make_image(), format="jpeg")
    assert resp.headers["X-Quality"] == "85"
    assert resp.headers["X-Preset"] == "balanced"


def test_jpeg_preset_encoder_params(client):
    from PIL import JpegImagePlugin
    src = make_image()
    fast = open_result(post(client, src, format="jpeg", preset="speed"))
    best = open_result(post(client, src, format="jpeg", preset="max"))
    assert "progressive" not in fast.info and best.info.get("progressive")
    assert JpegImagePlugin.get_sampling(fast) == 2  # 4:2:0
    assert JpegImagePlugin.get_sampling(best) == 0  # 4:4:4


def test_webp_effort_varies_by_preset(client, monkeypatch):
    calls = []
    real_save = Image.Image.save
    def spy(self, fp, format=None, **kw):
        if format == "WEBP":
            calls.append(kw.get("method"))
        return real_save(self, fp, format=format, **kw)
    src = make_image()
    monkeypatch.setattr(Image.Image, "save", spy)
    for preset in ("speed", "balanced", "max"):
        post(client, src, format="webp", preset=preset)
    assert calls == [2, 4, 6]


def test_rejects_unknown_preset(client):
    assert post(client, make_image(), preset="turbo").status_code == 400


def test_result_not_cached(client):
    assert post(client, make_image()).headers["Cache-Control"] == "no-store"


def test_rejects_gif(client):
    resp = post(client, make_image("GIF"), filename="anim.gif")
    assert resp.status_code == 400
    assert "GIF" in resp.get_json()["error"]


def test_rejects_svg(client):
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    resp = post(client, svg, filename="logo.svg")
    assert resp.status_code == 400
    assert "SVG" in resp.get_json()["error"]


def test_rejects_svg_disguised_as_png(client):
    resp = post(client, b"<svg xmlns='http://www.w3.org/2000/svg'/>", filename="logo.png")
    assert resp.status_code == 400
    assert "SVG" in resp.get_json()["error"]


@pytest.mark.parametrize("fmt", ["BMP", "TIFF"])
def test_rejects_other_formats(client, fmt):
    resp = post(client, make_image(fmt), filename="x.img")
    assert resp.status_code == 400
    assert "JPEG, PNG or WebP" in resp.get_json()["error"]


@pytest.mark.parametrize("fmt,filename", [("JPEG", "a.jpg"), ("PNG", "a.png"), ("WEBP", "a.webp")])
def test_accepts_supported_formats(client, fmt, filename):
    resp = post(client, make_image(fmt), filename=filename)
    assert resp.status_code == 200
    assert open_result(resp).format == fmt


def test_rejects_non_image(client):
    resp = post(client, b"not an image", filename="x.png")
    assert resp.status_code == 400
    assert "supported image" in resp.get_json()["error"]


def test_rejects_missing_file(client):
    resp = client.post("/optimize", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


@pytest.mark.parametrize("field,value", [("quality", "0"), ("quality", "96"), ("quality", "abc"),
                                         ("strip_metadata", "maybe")])
def test_rejects_bad_numbers(client, field, value):
    resp = post(client, make_image(), **{field: value})
    assert resp.status_code == 400


def test_rejects_unknown_format(client):
    resp = post(client, make_image(), format="avif")
    assert resp.status_code == 400


def test_rejects_oversized_upload(app, client):
    app.config["MAX_CONTENT_LENGTH"] = 1000
    resp = post(client, make_image())
    assert resp.status_code == 413


def test_rate_limit(client):
    for _ in range(30):
        assert client.get("/").status_code == 200
    resp = post(client, make_image())
    assert resp.status_code == 429
    assert "Too many requests" in resp.get_json()["error"]
    assert "Retry-After" in resp.headers


def test_static_files_dont_count_toward_rate_limit(client):
    for _ in range(40):
        assert client.get("/static/js/upload.js").status_code == 200
    assert client.get("/").status_code == 200


def test_sweeper_runs_at_startup(tmp_path):
    import os, time
    stale = tmp_path / "kit-left-behind.out"
    stale.write_bytes(b"x")
    old = time.time() - TTL - 5
    os.utime(stale, (old, old))
    create_app({"TESTING": True, "TEMP_DIR": tmp_path, "TEMP_TTL_SECONDS": TTL,
                "TEMP_SWEEP_INTERVAL_SECONDS": 0})
    assert not stale.exists()


def test_scheduled_sweeper_runs_without_requests(tmp_path):
    import time
    create_app({"TESTING": True, "TEMP_DIR": tmp_path, "TEMP_TTL_SECONDS": 0,
                "TEMP_SWEEP_INTERVAL_SECONDS": 0.05})
    leftover = tmp_path / "kit-crashed.out"
    leftover.write_bytes(b"x")
    deadline = time.time() + 3
    while leftover.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert not leftover.exists()


def test_rate_limit_is_per_ip(client):
    for _ in range(30):
        client.get("/", environ_base={"REMOTE_ADDR": "10.0.0.1"})
    assert client.get("/", environ_base={"REMOTE_ADDR": "10.0.0.1"}).status_code == 429
    assert client.get("/", environ_base={"REMOTE_ADDR": "10.0.0.2"}).status_code == 200


def test_temp_files_removed_after_success(client, tmp_path):
    resp = post(client, make_image(), format="webp")
    assert resp.status_code == 200
    resp.close()
    assert list(tmp_path.iterdir()) == []


def test_temp_files_removed_after_error(client, tmp_path):
    post(client, make_image("GIF"), filename="a.gif")
    post(client, make_image(), quality="999")
    assert list(tmp_path.iterdir()) == []


def test_sweeper_removes_only_stale_files(tmp_path):
    import os, time
    stale = tmp_path / "kit-stale.upload"
    fresh = tmp_path / "kit-fresh.upload"
    other = tmp_path / "unrelated.txt"
    for f in (stale, fresh, other):
        f.write_bytes(b"x")
    old = time.time() - TTL - 5
    os.utime(stale, (old, old))
    os.utime(other, (old, old))
    assert cleanup.sweep(tmp_path, TTL) == 1
    assert not stale.exists() and fresh.exists() and other.exists()


def test_index_shows_before_and_after_panes(client):
    html = client.get("/").get_data(as_text=True)
    assert "Before" in html and "After" in html
    assert "Click Optimize to create the After image" in html
    assert "What you&#39;ll get" in html or "What you'll get" in html
    assert 'accept="image/jpeg,image/png,image/webp"' in html


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_opencv_roundtrip(mode):
    img = Image.effect_noise((32, 24), 50).convert(mode)
    arr = pil_to_cv(img)
    assert arr.shape == (24, 32, len(mode))
    back = cv_to_pil(arr)
    assert back.mode == mode and back.tobytes() == img.tobytes()


def test_parse_options_defaults():
    assert parse_options({}) == Options()


def test_parse_options_values():
    opts = parse_options({"format": "WEBP", "quality": "55", "scale": "50", "preset": "Speed",
                          "strip_metadata": "0", "auto_orient": "off", "lossless": "on"})
    assert opts == Options(format="webp", quality=55, scale=50, preset="speed",
                           strip_metadata=False, auto_orient=False, lossless=True)


def test_options_defaults_match_spec():
    d = Options()
    assert (d.quality, d.preset, d.scale, d.strip_metadata, d.auto_orient) == \
        (85, "balanced", 100, True, True)


@pytest.mark.parametrize("form", [{"format": "gif"}, {"quality": "96"}, {"scale": "x"},
                                  {"preset": "fast"}, {"auto_orient": "2"}])
def test_parse_options_rejects(form):
    with pytest.raises(ValidationError):
        parse_options(form)


def test_static_assets_served(client):
    html = client.get("/").get_data(as_text=True)
    assert "/static/css/style.css" in html and "/static/js/upload.js" in html
    assert 'data-max-mb="25"' in html
    assert client.get("/static/css/style.css").status_code == 200


# --- Denoise / sharpen -----------------------------------------------------------

def _noisy(size=(320, 240), mode="RGB"):
    """Flat grey with camera-like grain (std ~8), plus one hard edge."""
    img = Image.blend(Image.new("L", size, 128), Image.effect_noise(size, 16), 0.5)
    img.paste(255, (size[0] // 2, 0, size[0], size[1]))
    return img.convert(mode)


def _png(img):
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()


def _grain(img):
    """Std deviation of a flat region: lower means less noise."""
    from PIL import ImageStat
    return ImageStat.Stat(img.convert("L").crop((10, 10, 140, 230))).stddev[0]


def _edge_strength(img):
    from PIL import ImageFilter, ImageStat
    return ImageStat.Stat(img.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]


def test_enhancements_off_by_default(client):
    resp = post(client, make_image(), format="png")
    assert resp.headers["X-Denoise"] == "off"
    for name in ("Brightness", "Contrast", "Blur", "Sharpen"):
        assert resp.headers[f"X-{name}"] == "0"


@pytest.mark.parametrize("mode", ["RGB", "L"])
def test_denoise_reduces_grain(client, mode):
    src = _png(_noisy(mode=mode))
    grains = [_grain(open_result(post(client, src, format="png", reduce_colors="0", denoise=lvl)))
              for lvl in ("off", "light", "medium", "strong")]
    assert grains == sorted(grains, reverse=True) and grains[-1] < grains[0] * 0.8


def test_denoise_colour_image(client):
    img = Image.merge("RGB", [Image.effect_noise((200, 150), 50) for _ in range(3)])
    resp = post(client, _png(img), format="png", denoise="light")
    assert resp.status_code == 200 and open_result(resp).mode == "RGB"


def test_sharpen_increases_edge_strength(client):
    src = _png(Image.new("L", (200, 200), 60).resize((200, 200)))
    img = Image.new("L", (200, 200), 60); img.paste(200, (60, 60, 140, 140))
    from PIL import ImageFilter
    src = _png(img.filter(ImageFilter.GaussianBlur(2)))
    values = [_edge_strength(open_result(post(client, src, format="png", sharpen=v)))
              for v in (0, 25, 50, 100)]
    assert values == sorted(values) and values[0] < values[-1]


def test_enhancements_preserve_alpha(client):
    img = _noisy(mode="RGBA"); img.putalpha(Image.linear_gradient("L").resize(img.size))
    resp = post(client, _png(img), format="png", denoise="light", sharpen=50,
                brightness=30, contrast=-20, blur=10)
    out = open_result(resp)
    assert out.mode == "RGBA"
    assert out.getchannel("A").tobytes() == img.getchannel("A").tobytes()


def test_enhancements_never_return_original(client):
    src = make_image("JPEG")
    resp = post(client, src, filename="a.jpg", format="jpeg", quality=95, preset="max",
                sharpen=20)
    assert resp.headers["X-Kept-Original"] == "0"


def test_denoise_size_limit(client, monkeypatch):
    from kimagetools import enhance
    monkeypatch.setattr(enhance, "DENOISE_MAX_PIXELS", 10_000)
    resp = post(client, make_image(size=(200, 100)), format="png", denoise="light")
    assert resp.status_code == 400 and "Denoise is limited" in resp.get_json()["error"]
    # Shrinking first brings it under the limit (denoise runs after the resize).
    assert post(client, make_image(size=(200, 100)), format="png", denoise="light",
                scale=50).status_code == 200


@pytest.mark.parametrize("field,value", [("denoise", "max"), ("sharpen", "extreme"),
                                         ("sharpen", "101"), ("blur", "-1"),
                                         ("brightness", "-101"), ("contrast", "150")])
def test_rejects_bad_enhancement_values(client, field, value):
    assert post(client, make_image(), **{field: value}).status_code == 400


@pytest.mark.parametrize("mode", ["1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I;16"])
def test_enhancements_handle_all_modes(tmp_path, mode):
    from kimagetools.imaging import optimize_image
    path = tmp_path / "in"
    fmt = "JPEG" if mode == "CMYK" else "PNG"
    Image.new(mode, (40, 30)).save(path, fmt)
    for scale in (50, 100, 150):
        with tempfile.TemporaryFile() as out:
            optimize_image(path, out, Options(scale=scale, denoise="light", sharpen=100, blur=20,
                                              brightness=30, contrast=-30))


# --- Adjustments ------------------------------------------------------------------

def _mean_std(img):
    from PIL import ImageStat
    st = ImageStat.Stat(img.convert("L"))
    return st.mean[0], st.stddev[0]


def test_brightness_slider(client):
    src = make_image("PNG")
    base, _ = _mean_std(open_result(post(client, src, format="png")))
    brighter, _ = _mean_std(open_result(post(client, src, format="png", brightness=40)))
    darker, _ = _mean_std(open_result(post(client, src, format="png", brightness=-40)))
    assert darker < base * 0.7 and brighter > base * 1.2


def test_contrast_slider(client):
    src = make_image("PNG")
    _, base = _mean_std(open_result(post(client, src, format="png")))
    _, more = _mean_std(open_result(post(client, src, format="png", contrast=50)))
    _, less = _mean_std(open_result(post(client, src, format="png", contrast=-50)))
    assert less < base < more


def test_blur_slider_softens_edges(client):
    img = Image.new("L", (200, 200), 40); img.paste(220, (60, 60, 140, 140))
    src = _png(img)
    values = [_edge_strength(open_result(post(client, src, format="png", blur=v))) for v in (0, 10, 40)]
    assert values[0] > values[1] > values[2]


def test_adjustment_headers_echo_values(client):
    resp = post(client, make_image(), format="png", brightness=-15, contrast=25, blur=5, sharpen=60)
    assert [resp.headers[f"X-{n}"] for n in ("Brightness", "Contrast", "Blur", "Sharpen")] == \
        ["-15", "25", "5", "60"]


# --- Format conversion ------------------------------------------------------------

@pytest.mark.parametrize("src_fmt", ["JPEG", "PNG", "WEBP"])
@pytest.mark.parametrize("out_fmt,mime,ext", [("jpeg", "image/jpeg", "jpg"),
                                              ("png", "image/png", "png"),
                                              ("webp", "image/webp", "webp")])
def test_converts_between_all_formats(client, src_fmt, out_fmt, mime, ext):
    resp = post(client, make_image(src_fmt), filename="pic.img", format=out_fmt)
    assert resp.status_code == 200
    assert resp.mimetype == mime
    assert open_result(resp).format == out_fmt.upper()
    assert f"pic-optimized.{ext}" in resp.headers["Content-Disposition"]


def test_transparent_png_to_jpeg_is_flattened_and_reported(client):
    img = Image.new("RGBA", (40, 30), (255, 0, 0, 0)); img.paste((0, 0, 255, 255), (0, 0, 20, 30))
    resp = post(client, _png(img), format="jpeg")
    assert resp.headers["X-Flattened"] == "1"
    out = open_result(resp).convert("RGB")
    assert out.getpixel((35, 15))[0] > 240 and out.getpixel((35, 15))[1] > 240  # white, not red


def test_opaque_rgba_to_jpeg_is_not_reported_as_flattened(client):
    resp = post(client, _png(Image.new("RGBA", (40, 30), (9, 9, 9, 255))), format="jpeg")
    assert resp.headers["X-Flattened"] == "0"


@pytest.mark.parametrize("out_fmt", ["png", "webp"])
def test_transparency_kept_for_png_and_webp(client, out_fmt):
    img = Image.new("RGBA", (40, 30), (255, 0, 0, 0)); img.paste((0, 0, 255, 255), (0, 0, 20, 30))
    resp = post(client, _png(img), format=out_fmt, reduce_colors="0")
    assert resp.headers["X-Flattened"] == "0"
    assert open_result(resp).convert("RGBA").getpixel((35, 15))[3] == 0
