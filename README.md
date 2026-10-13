# KIMAGETOOLS

A small Flask web app for optimizing one image at a time. Drag and drop a JPEG, PNG or WebP image to see it in the **Before** pane. Pick a preset, quality, resize percentage and metadata options; a live summary shows exactly what you'll get. Click Optimize, and the real result appears in the **After** pane next to the original, with the size savings and a download button.

## Features

- **Uploads:** drag and drop or file picker. Only JPEG, PNG and WebP are accepted, up to 25 MB. The type is checked from the file's content, so SVG and GIF are rejected even if they're renamed.
- **Format conversion:** "Convert to" turns any JPEG, PNG or WebP into any of the three, or "Keep original". JPEG has no transparency, so transparent areas are filled with white. The settings summary warns about this before converting, and the result shows a note if it happened (`X-Flattened: 1`).
- **Quality slider:** 1–95, default 85. Always used exactly as set, whatever the preset. Used by JPEG and lossy WebP. For lossless WebP it sets compression effort instead, and PNG ignores it.
- **Resize by percentage:** 10–200% in steps of 5 (the API accepts any whole number in that range). The aspect ratio is always preserved. Results over 100 megapixels, or over a format's size limit (16383 px per side for WebP), are rejected, and the UI warns you before you submit.
- **Strip metadata:** on by default. Removes EXIF (including GPS), XMP and comments. The colour profile is always kept, since dropping it shifts colours.
- **Auto-orient:** on by default. Rotates and flips the pixels as the EXIF Orientation tag says, then resets the tag. With it off, the pixels are left as stored. If metadata is kept, viewers will still rotate the image using the tag. If metadata is stripped too, the image may then appear sideways.
- **Denoise** (Off / Light / Medium / Strong, default Off): OpenCV non-local means, which removes sensor grain (night and IR camera shots especially) while keeping edges. Brightness is always denoised, and the colour channels only when the image really has colour, so mostly-grey photos keep any coloured overlay (e.g. a camera logo) intact. It runs at the smaller of the original and resized resolutions. It's slow (about 0.4 s per megapixel for grey images, 1.2 s for colour), so it's limited to 8.5 MP at that stage, and the UI warns before you hit the limit.
- **Adjustment sliders**, all neutral at 0, applied after resizing in this order:
  - **Brightness** (−100 to +100): scales pixel values by 1 + v/100.
  - **Contrast** (−100 to +100): scales the distance from mean grey by 1 + v/100.
  - **Blur** (0–100): Gaussian blur with a radius of v/10 px, up to 10 px.
  - **Sharpen** (0–100): unsharp mask with an amount of 2v % and a radius of 1 + v/100 px. Its threshold skips low-contrast detail, so edges are sharpened more than grain. Denoise Medium + Sharpen 50 works well on grainy photos.

  **Reset** sets all four back to 0, and double-clicking a slider resets just that one.
- Transparency is preserved: denoise and the adjustments work on the colour channels only, and the alpha channel is reattached unchanged.
- **Speed / Balanced / Max quality presets:** see [Presets](#presets).
- **Auto-update:** on by default. The After image re-renders about half a second after you stop changing a setting, and as soon as you drop a file. The previous result stays visible (marked "Updating…") until the new one arrives, and a request still in progress is cancelled when a newer one starts. Turn it off to update only when you click Optimize. Each update is one request, so it counts toward the 30-per-minute rate limit.
- **Live settings preview:** a "What you'll get" panel describes the exact output (format, encoder settings, effective quality, final dimensions, metadata, orientation) as you change options. Nothing is processed in the browser.
- **Real Before/After:** the server's actual output is compared with the original, with sizes, savings and the settings used. **Side by side** draws both at the same scale from their real pixel sizes, so a resized result really looks bigger or smaller. Each pane is labelled with its real size and the zoom it's shown at. **Swipe** lays the After over the Before with a draggable divider (arrow keys work too), which is the easiest way to spot compression artefacts. **Zoom** (Fit / 100% / 200%) shows real pixels, with the panes scrolled in sync. If you change a setting afterwards, the After is marked as out of date.
- **No larger files:** if re-encoding in the same format and size would make the file bigger, the original is returned instead (unless you asked for denoise or an adjustment). This never happens when stripping metadata from a file that has any, so stripping is always honoured.
- **Rate limiting:** 30 requests per minute per IP, shared across all routes. Static files are exempt.
- **No retention:** see [Temp files](#temp-files).
- **OpenCV:** used for denoising. `pil_to_cv` and `cv_to_pil` in `kimagetools/imaging.py` convert between Pillow and OpenCV images for future features.

## Presets

Each preset maps to encoder parameters, trading encoding speed against how good the output looks *at a given quality*. Presets never change the quality: the slider always wins.

| | Speed | Balanced (default) | Max quality |
|---|---|---|---|
| JPEG | baseline, standard Huffman tables, 4:2:0 chroma | progressive, optimized Huffman tables, 4:2:0 chroma | progressive, optimized Huffman tables, 4:4:4 chroma (no colour subsampling) |
| WebP effort (`method`) | 2 of 6 | 4 of 6 | 6 of 6 |
| PNG | zlib level 1, no optimize pass | zlib level 9, optimize pass | same as Balanced |
| PNG colour reduction | fast octree, no dithering | median cut (fast octree with transparency), dithered | libimagequant when Pillow has it, otherwise as Balanced |
| Resize filter | bilinear | bicubic | Lanczos |

The presets are defined in `kimagetools/presets.py`. The UI preview reads the same definitions, so the two can't drift apart.

## Temp files

Nothing is kept:

- Each upload is written to a private temp directory (`0700`) and deleted as soon as processing finishes, whether it succeeded or failed.
- Each result is written to a temp file that deletes itself once the server has finished sending it.
- Results are sent with `Cache-Control: no-store`, so browsers and proxies don't keep a copy either.
- A background sweeper runs at startup and then every 60 s, even when the app is idle. It deletes anything older than 5 minutes, which only happens if a worker crashed mid-request.

## Project layout

```
app.py                      entry point (creates the app)
kimagetools/
  __init__.py               app factory: config, rate limiter, error handlers
  config.py                 settings (overridable via env vars)
  routes.py                 GET / and POST /optimize
  validators.py             upload type checks (JPEG/PNG/WebP only), form parsing, size limits
  presets.py                Speed / Balanced / Max quality encoder settings
  enhance.py                denoise (OpenCV) and brightness/contrast/blur/sharpen (Pillow)
  imaging.py                Pillow optimization pipeline + OpenCV interop helpers
  cleanup.py                temp file creation, deletion and the stale-file sweeper
  templates/index.html      page markup
  static/css/style.css      styles (light + dark)
  static/js/upload.js       drag and drop, client-side checks, live settings preview, Before/After views
tests/test_app.py
```

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/flask --app app run           # http://127.0.0.1:5000
```

For production, use a WSGI server such as `gunicorn app:app`. With more than one worker, set `KIMAGETOOLS_RATELIMIT_STORAGE=redis://...` so the workers share one rate-limit count.

## Configuration

| Env var | Default | |
|---|---|---|
| `KIMAGETOOLS_MAX_MB` | `25` | Max upload size |
| `KIMAGETOOLS_RATE_LIMIT` | `30 per minute` | Per-IP limit |
| `KIMAGETOOLS_RATELIMIT_STORAGE` | `memory://` | Limiter storage, e.g. `redis://localhost:6379` |
| `KIMAGETOOLS_TRUSTED_PROXIES` | `0` | Number of reverse proxies in front of the app; set it so limits apply to the real client IP |
| `KIMAGETOOLS_TEMP_DIR` | `<system temp>/kimagetools` | Where temp files go |
| `KIMAGETOOLS_TEMP_TTL` | `300` | Seconds before a leftover temp file is swept |
| `KIMAGETOOLS_TEMP_SWEEP_INTERVAL` | `60` | Seconds between background sweeps (`0` disables the thread) |

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

## API

`POST /optimize` (multipart form)

| Field | Default | Notes |
|---|---|---|
| `image` | — | required; JPEG, PNG or WebP |
| `format` | `original` | `original`, `jpeg`, `png`, `webp` |
| `quality` | `85` | 1–95 (JPEG/WebP) |
| `preset` | `balanced` | `speed`, `balanced`, `max` |
| `scale` | `100` | resize percentage, 10–200 |
| `strip_metadata` | `1` | `1` or `0` |
| `auto_orient` | `1` | `1` or `0` |
| `lossless` | `0` | WebP only |
| `reduce_colors` | `0` | PNG only |
| `denoise` | `off` | `off`, `light`, `medium`, `strong` |
| `brightness` | `0` | −100 to 100 |
| `contrast` | `0` | −100 to 100 |
| `blur` | `0` | 0–100 (radius v/10 px) |
| `sharpen` | `0` | 0–100 |

The response is the optimized file. The headers `X-Original-Size`, `X-Optimized-Size`, `X-Image-Width`/`Height`, `X-Original-Width`/`Height`, `X-Quality`, `X-Preset`, `X-Denoise`, `X-Brightness`, `X-Contrast`, `X-Blur`, `X-Sharpen`, `X-Flattened` and `X-Kept-Original` give the stats. Errors return JSON `{"error": ...}`: 400 for invalid input, 413 if the file is too large, 429 if the rate limit is hit.
