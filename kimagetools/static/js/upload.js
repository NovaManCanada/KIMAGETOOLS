"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("form"), fileInput = $("file"), drop = $("drop"), go = $("go");

  // Server-side settings, passed in as data-* attributes and a JSON block.
  const ALLOWED = form.dataset.accept.split(",");
  const ACCEPTED = form.dataset.accepted;
  const MAX_MB = Number(form.dataset.maxMb);
  const MAX_BYTES = MAX_MB * 1024 * 1024;
  const CONFIG = JSON.parse($("config").textContent);
  const PRESETS = CONFIG.presets;
  // Must match validators.py.
  const MAX_OUTPUT_PIXELS = 100_000_000;
  const MAX_SIDE = { jpeg: 65500, png: 1000000, webp: 16383 };
  const NAMES = { jpeg: "JPEG", png: "PNG", webp: "WebP" };
  const MIME_TO_FMT = { "image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp" };
  const PH_NEW = "Click Optimize to create the After image";

  let origUrl = null, newUrl = null;
  let pending = null;        // AbortController of the request in flight, if any
  let autoTimer = null;      // debounce timer for automatic updates
  let natural = null;        // {w, h} of the selected image, once it has loaded
  let resultFor = null;      // settings snapshot the current After image was made with

  const fmtBytes = (n) => {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(2) + " MB";
  };

  // --- Current settings -----------------------------------------------------

  const settings = () => {
    const file = fileInput.files[0];
    const source = MIME_TO_FMT[file?.type] || "";
    const chosen = $("format").value;
    const format = chosen === "original" ? source : chosen;
    const preset = form.elements.preset.value;
    return {
      file, source, format, preset,
      quality: Number($("quality").value),
      scale: Number($("scale").value),
      strip: form.elements.strip_metadata.checked,
      orient: form.elements.auto_orient.checked,
      lossless: format === "webp" && form.elements.lossless.checked,
      reduce: format === "png" && form.elements.reduce_colors.checked,
      denoise: $("denoise").value,
      sharpen: $("sharpen").value,
    };
  };

  const snapshot = (s) => JSON.stringify([s.format, s.preset, s.quality, s.scale,
                                          s.strip, s.orient, s.lossless, s.reduce, s.denoise, s.sharpen]);

  const outputSize = (s) => natural && {
    w: Math.max(1, Math.round(natural.w * s.scale / 100)),
    h: Math.max(1, Math.round(natural.h * s.scale / 100)),
  };

  // Returns a reason the server would reject these settings, or "".
  const sizeProblem = (s) => {
    const out = outputSize(s);
    if (!out || !s.format) return "";
    if (out.w * out.h > MAX_OUTPUT_PIXELS) return `${out.w}×${out.h} px is too large. Choose a smaller resize percentage.`;
    if (Math.max(out.w, out.h) > MAX_SIDE[s.format]) {
      return `${NAMES[s.format]} images can be at most ${MAX_SIDE[s.format]} px on a side. Choose a smaller resize or another format.`;
    }
    // Denoise runs at the smaller of the original and output sizes.
    const dn = Math.min(out.w * out.h, natural.w * natural.h);
    if (s.denoise !== "off" && dn > CONFIG.denoise_max_pixels) {
      return `Denoise is limited to ${(CONFIG.denoise_max_pixels / 1e6).toFixed(1)} megapixels (this would be ${(dn / 1e6).toFixed(1)} MP). Choose a smaller resize or turn denoise off.`;
    }
    return "";
  };

  // --- Live preview of the chosen options -----------------------------------
  // Nothing is processed in the browser: this only describes what the server
  // will do with the current settings.

  const qualityText = (s) => {
    if (s.format === "png") return "Not used for PNG";
    if (s.lossless) return `Lossless: ${s.quality} sets compression effort, not image quality`;
    return String(s.quality);
  };

  const render = () => {
    const s = settings();
    const p = PRESETS[s.preset];

    $("lossless-opt").hidden = s.format !== "webp";
    $("reduce-opt").hidden = s.format !== "png";

    $("qval").textContent = s.quality;
    $("quality").disabled = s.format === "png";
    $("qhint").textContent = s.format === "png" ? "Not used for PNG"
      : s.lossless ? "Lossless WebP: sets compression effort" : "Used by JPEG and WebP";

    $("scaleval").textContent = s.scale + "%";
    const out = outputSize(s);
    $("scalehint").textContent = out && s.scale !== 100
      ? `${natural.w}×${natural.h} → ${out.w}×${out.h} px` : "Aspect ratio is preserved";

    const rows = [];
    if (s.format) {
      const how = $("format").value === "original" ? "same as original"
        : s.format === s.source ? "same as original" : `converted from ${NAMES[s.source]}`;
      rows.push(["Format", `${NAMES[s.format]} (${how})`]);
      rows.push(["Encoder", `${p.label}: ${p.describe[s.format]}${s.lossless ? ", lossless" : ""}`]);
    }
    rows.push(["Quality", qualityText(s)]);
    if (out) {
      rows.push(["Size", s.scale === 100 ? `${out.w}×${out.h} px (unchanged)`
        : `${natural.w}×${natural.h} → ${out.w}×${out.h} px (${s.scale}%, ${p.describe.resample})`]);
    }
    if (s.reduce) rows.push(["Colours", "Reduced to a 256-colour palette"]);
    const dn = CONFIG.denoise[s.denoise], sh = CONFIG.sharpen[s.sharpen];
    rows.push(["Denoise", dn ? `${dn.label}: ${dn.describe}${s.scale > 100 ? ", before enlarging" : s.scale < 100 ? ", after shrinking" : ""}. Takes longer` : "Off"]);
    rows.push(["Sharpen", sh ? `${sh.label}: ${sh.describe}${s.scale !== 100 ? ", after resizing" : ""}` : "Off"]);
    rows.push(["Metadata", s.strip ? "EXIF and GPS removed (colour profile kept)" : "Kept"]);
    rows.push(["Orientation", s.orient ? "Rotated upright using EXIF"
      : s.strip ? "Left as stored. May appear rotated, since the EXIF tag is removed" : "Left as stored (EXIF tag kept)"]);

    const problem = sizeProblem(s);
    if (problem) rows.push(["Problem", problem, "bad"]);

    const dl = $("summary");
    dl.replaceChildren(...rows.flatMap(([k, v, cls]) => {
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd"); dd.textContent = v;
      if (cls) { dt.className = cls; dd.className = cls; }
      return [dt, dd];
    }));

    go.disabled = !s.file || !!problem;
    const updating = !!(pending || autoTimer);
    const stale = updating ? !!newUrl : !!resultFor && resultFor !== snapshot(s);
    $("stale").hidden = !stale;
    $("stale").classList.toggle("updating", updating);
    $("stale").textContent = updating ? "Updating" : "Settings changed. Optimize again to update.";
  };

  // --- Before / After panes ------------------------------------------------

  const resetAfter = (text = PH_NEW) => {
    if (newUrl) { URL.revokeObjectURL(newUrl); newUrl = null; }
    resultFor = null;
    $("img-new").hidden = true; $("img-new").removeAttribute("src");
    $("sw-after").removeAttribute("src");  // swipe keeps showing the Before meanwhile
    layout();
    $("ph-new").hidden = false; $("ph-new").textContent = text;
    $("pane-new").classList.remove("busy");
    $("stale").hidden = true;
    $("stats").hidden = true; $("note").hidden = true; $("download").hidden = true;
  };

  const showBefore = (file) => {
    if (origUrl) { URL.revokeObjectURL(origUrl); origUrl = null; }
    natural = null;
    $("img-orig").hidden = !file; $("ph-orig").hidden = !!file;
    if (file) { origUrl = URL.createObjectURL(file); $("img-orig").src = origUrl; }
    else $("img-orig").removeAttribute("src");
  };

  // Browsers display images EXIF-rotated, so these are the upright dimensions.
  $("img-orig").addEventListener("load", (e) => {
    natural = { w: e.target.naturalWidth, h: e.target.naturalHeight };
    $("sw-before").src = e.target.src;
    render();
  });

  // --- Comparison view --------------------------------------------------------
  // Side by side: both images are drawn at the SAME scale, from their real pixel
  // sizes, so a resized result really looks bigger or smaller. "Fit" picks the
  // largest scale (≤ 100%) at which both fit; 100% / 200% show real pixels.
  // Panes scroll together, proportionally, so they stay on the same spot.
  // Swipe: the After is laid over the Before at the Before's size (so the same
  // spot lines up even if it was resized) and revealed right of a divider.

  const panes = [$("pane-orig"), $("pane-new")];
  const imgs = [$("img-orig"), $("img-new")];
  const stage = $("swipe-stage"), inner = $("swipe-inner"), handle = $("swipe-handle");
  let zoom = 0;        // 0 = fit
  let view = "side";
  let split = 50;      // swipe divider position, % from the left

  const sizeOf = (img) => (!img.hidden && img.naturalWidth)
    ? { w: img.naturalWidth, h: img.naturalHeight } : null;
  const pct = (scale) => `${Math.round(scale * 100)}%`;

  const layoutSide = () => {
    const sizes = imgs.map(sizeOf);
    const known = sizes.filter(Boolean);
    const box = panes[0];
    let scale = zoom;
    if (!zoom && known.length && box.clientWidth) {
      scale = Math.min(box.clientWidth / Math.max(...known.map((s) => s.w)),
                       box.clientHeight / Math.max(...known.map((s) => s.h)), 1);
    }
    panes.forEach((pane, i) => {
      pane.classList.toggle("zoomed", !!zoom);
      pane.classList.toggle("pixelated", zoom >= 2);
      const s = sizes[i];
      imgs[i].style.width = s && scale ? Math.round(s.w * scale) + "px" : "";
    });
    $("dim-orig").textContent = sizes[0] && scale ? `${sizes[0].w}×${sizes[0].h} px · ${pct(scale)}` : "";
    $("dim-new").textContent = sizes[1] && scale ? `${sizes[1].w}×${sizes[1].h} px · ${pct(scale)}` : "";
  };

  const layoutSwipe = () => {
    const b = sizeOf(imgs[0]), a = sizeOf(imgs[1]);
    if (!b || !stage.clientWidth) return;
    const scale = zoom || Math.min(stage.clientWidth / b.w, stage.clientHeight / b.h, 1);
    inner.style.width = Math.round(b.w * scale) + "px";
    inner.style.height = Math.round(b.h * scale) + "px";
    stage.classList.toggle("zoomed", !!zoom);
    stage.classList.toggle("pixelated", zoom >= 2);
    $("swipe-note").hidden = !a || (a.w === b.w && a.h === b.h);
  };

  const setSplit = (value) => {
    split = Math.min(100, Math.max(0, value));
    inner.style.setProperty("--split", split + "%");
    handle.setAttribute("aria-valuenow", String(Math.round(split)));
  };

  const layout = () => {
    const swipe = view === "swipe";
    $("compare-side").hidden = swipe;
    $("compare-swipe").hidden = !swipe;
    if (swipe) layoutSwipe(); else layoutSide();
    $("viewhint").textContent = swipe ? "Drag the divider (or use the arrow keys) to compare the same spot."
      : zoom ? "Both panes show the same spot. Drag or scroll either one."
      : "Both images are shown at the same scale, so resizing is visible.";
  };

  const setView = (value) => {
    view = value;
    document.querySelector(`input[name=view][value=${value}]`).checked = true;
    layout();
  };

  // Keep the same point centred when switching zoom levels.
  const scroller = () => (view === "swipe" ? stage : panes[0]);
  document.querySelectorAll("input[name=zoom]").forEach((radio) => radio.addEventListener("change", () => {
    const el = scroller();
    const cx = (el.scrollLeft + el.clientWidth / 2) / (el.scrollWidth || 1);
    const cy = (el.scrollTop + el.clientHeight / 2) / (el.scrollHeight || 1);
    zoom = radio.value === "fit" ? 0 : Number(radio.value);
    layout();
    el.scrollLeft = cx * el.scrollWidth - el.clientWidth / 2;
    el.scrollTop = cy * el.scrollHeight - el.clientHeight / 2;
  }));
  document.querySelectorAll("input[name=view]").forEach((radio) => radio.addEventListener("change", () => setView(radio.value)));
  window.addEventListener("resize", layout);
  imgs.forEach((img) => img.addEventListener("load", layout));

  // Proportional scroll sync. The echo from the other pane computes (nearly)
  // the position this pane already has, and the 1px tolerance stops it there.
  const frac = (el, axis) => {
    const max = axis === "x" ? el.scrollWidth - el.clientWidth : el.scrollHeight - el.clientHeight;
    return max > 0 ? (axis === "x" ? el.scrollLeft : el.scrollTop) / max : 0;
  };
  panes.forEach((pane, i) => {
    const other = panes[1 - i];
    pane.addEventListener("scroll", () => {
      const x = frac(pane, "x") * (other.scrollWidth - other.clientWidth);
      const y = frac(pane, "y") * (other.scrollHeight - other.clientHeight);
      if (Math.abs(other.scrollLeft - x) > 1) other.scrollLeft = x;
      if (Math.abs(other.scrollTop - y) > 1) other.scrollTop = y;
    });
  });

  // Drag to pan when zoomed (side by side).
  panes.forEach((pane) => {
    let drag = null;
    pane.addEventListener("pointerdown", (e) => {
      if (!zoom || e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, left: pane.scrollLeft, top: pane.scrollTop };
      pane.setPointerCapture(e.pointerId);
      pane.classList.add("dragging");
    });
    pane.addEventListener("pointermove", (e) => {
      if (!drag) return;
      pane.scrollLeft = drag.left - (e.clientX - drag.x);
      pane.scrollTop = drag.top - (e.clientY - drag.y);
    });
    const endDrag = () => { drag = null; pane.classList.remove("dragging"); };
    pane.addEventListener("pointerup", endDrag);
    pane.addEventListener("pointercancel", endDrag);
    pane.addEventListener("dragstart", (e) => e.preventDefault());
  });

  // Swipe divider: drag anywhere on the image, or use the arrow keys.
  let swiping = false;
  const splitAt = (e) => {
    const r = inner.getBoundingClientRect();
    setSplit(((e.clientX - r.left) / r.width) * 100);
  };
  inner.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    swiping = true; inner.setPointerCapture(e.pointerId); splitAt(e);
  });
  inner.addEventListener("pointermove", (e) => { if (swiping) splitAt(e); });
  inner.addEventListener("pointerup", () => { swiping = false; });
  inner.addEventListener("pointercancel", () => { swiping = false; });
  handle.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 10 : 2;
    if (e.key === "ArrowLeft") setSplit(split - step);
    else if (e.key === "ArrowRight") setSplit(split + step);
    else if (e.key === "Home") setSplit(0);
    else if (e.key === "End") setSplit(100);
    else return;
    e.preventDefault();
  });
  setSplit(50);

  // --- File selection ---------------------------------------------------------

  const validate = (file) => {
    if (!file) return "";
    const name = file.name.toLowerCase();
    if (file.type === "image/svg+xml" || name.endsWith(".svg")) return `SVG files aren't supported. Please choose ${ACCEPTED}.`;
    if (file.type === "image/gif" || name.endsWith(".gif")) return `GIF files aren't supported. Please choose ${ACCEPTED}.`;
    if (!ALLOWED.includes(file.type)) return `Unsupported file type. Please choose ${ACCEPTED}.`;
    if (file.size > MAX_BYTES) return `File is too large. The limit is ${MAX_MB} MB.`;
    return "";
  };

  const setFile = () => {
    let file = fileInput.files[0];
    const problem = validate(file);
    if (problem) { fileInput.value = ""; file = undefined; }
    $("drop-text").textContent = file ? `${file.name} (${fmtBytes(file.size)})` : "Drop an image here or click to choose";
    $("error").textContent = problem;
    // Until there's a valid file, only the drop area is shown.
    document.querySelectorAll("[data-needs-file]").forEach((el) => { el.hidden = !file; });
    document.querySelector("main").classList.toggle("empty", !file);
    // A result still on its way belongs to the previous file: drop it.
    if (pending) { pending.abort(); pending = null; go.textContent = "Optimize"; }
    showBefore(file);
    resetAfter();
    // Swipe needs an After image, which a new file doesn't have yet.
    $("view-swipe").disabled = true;
    if (view === "swipe") setView("side");
    scheduleAuto();
    render();
  };

  fileInput.addEventListener("change", setFile);
  // Any settings change updates the preview, and (with auto-update on) re-runs
  // the optimization shortly after the last change.
  const onSettingsChange = (e) => {
    if (e.target === fileInput) return;
    scheduleAuto();
    render();
  };
  form.addEventListener("input", onSettingsChange);
  form.addEventListener("change", onSettingsChange);

  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.remove("over");
  }));
  drop.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; setFile(); }
  });

  // --- Optimize -----------------------------------------------------------------

  const AUTO_DELAY_MS = 500;

  // Debounced so dragging a slider sends one request when you pause, not dozens.
  const scheduleAuto = () => {
    clearTimeout(autoTimer);
    autoTimer = null;
    const s = settings();
    if (!$("auto").checked || !s.file || sizeProblem(s)) return;
    if (resultFor && resultFor === snapshot(s) && !pending) return;  // already showing this
    autoTimer = setTimeout(() => { autoTimer = null; runOptimize({ auto: true }); }, AUTO_DELAY_MS);
  };

  const runOptimize = async ({ auto = false } = {}) => {
    clearTimeout(autoTimer); autoTimer = null;
    const s = settings();
    if (!s.file || sizeProblem(s)) return;

    // A newer request replaces an older one still in flight.
    if (pending) pending.abort();
    const ctl = new AbortController();
    pending = ctl;

    $("error").textContent = "";
    go.disabled = true; go.textContent = "Optimizing…";
    // Keep showing the previous result (marked "Updating…") until the new one
    // arrives; only the very first result for a file shows a placeholder.
    if (!newUrl) { resetAfter("Optimizing"); $("pane-new").classList.add("busy"); }
    render();

    const data = new FormData(form);
    // Unchecked checkboxes aren't submitted, and the server's defaults (e.g.
    // strip metadata = on) would then apply, so always send every toggle.
    for (const name of ["strip_metadata", "auto_orient"]) {
      data.set(name, form.elements[name].checked ? "1" : "0");
    }
    data.set("lossless", s.lossless ? "1" : "0");
    data.set("reduce_colors", s.reduce ? "1" : "0");
    data.set("quality", String(s.quality));  // a disabled slider isn't submitted

    try {
      const res = await fetch("/optimize", { method: "POST", body: data, signal: ctl.signal });
      if (!res.ok) {
        let msg = `Request failed (${res.status}).`;
        try { msg = (await res.json()).error || msg; } catch {}
        throw new Error(msg);
      }
      const blob = await res.blob();
      if (pending !== ctl) return;  // superseded while downloading

      const h = (k) => res.headers.get(k);
      const before = +h("X-Original-Size"), after = +h("X-Optimized-Size");
      const pct = before ? (1 - after / before) * 100 : 0;

      $("s-orig").textContent = fmtBytes(before);
      $("s-new").textContent = fmtBytes(after);
      const saved = $("s-saved");
      saved.textContent = (pct >= 0 ? "" : "+") + Math.abs(pct).toFixed(1) + "%" + (pct < 0 ? " larger" : "");
      saved.className = "value " + (pct > 0 ? "good" : pct < 0 ? "bad" : "");
      const ow = h("X-Original-Width"), oh = h("X-Original-Height");
      const nw = h("X-Image-Width"), nh = h("X-Image-Height");
      $("s-dim").textContent = ow === nw && oh === nh ? `${nw}×${nh}` : `${ow}×${oh} → ${nw}×${nh}`;
      const fmt = MIME_TO_FMT[blob.type] || s.format;
      $("s-settings").textContent = [NAMES[fmt], PRESETS[h("X-Preset")]?.label,
        fmt === "png" || s.lossless ? null : `q${h("X-Quality")}`,
        h("X-Denoise") !== "off" ? `denoise ${h("X-Denoise")}` : null,
        h("X-Sharpen") !== "off" ? `sharpen ${h("X-Sharpen")}` : null].filter(Boolean).join(" · ");
      $("stats").hidden = false;

      const note = $("note");
      note.hidden = h("X-Kept-Original") !== "1";
      note.textContent = "The original was already well optimized, so it was returned unchanged.";

      const first = !newUrl;
      const oldUrl = newUrl;
      newUrl = URL.createObjectURL(blob);
      $("pane-new").classList.remove("busy");
      $("img-new").src = newUrl; $("img-new").hidden = false; $("ph-new").hidden = true;
      $("sw-after").src = newUrl;
      if (oldUrl) URL.revokeObjectURL(oldUrl);
      $("view-swipe").disabled = false;
      resultFor = snapshot(s);

      const cd = h("Content-Disposition") || "";
      const m = cd.match(/filename\*=UTF-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
      const dl = $("download");
      dl.href = newUrl;
      dl.download = m ? decodeURIComponent(m[1]) : "optimized";
      dl.hidden = false;
      if (first && !auto) $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      if (err.name === "AbortError") return;  // replaced by a newer request
      $("error").textContent = err.message;
      if (!newUrl) resetAfter();
    } finally {
      if (pending === ctl) {
        pending = null;
        go.textContent = "Optimize";
        render();
      }
    }
  };

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    runOptimize();
  });
})();
