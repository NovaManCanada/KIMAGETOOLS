"""Temp file handling and automatic cleanup.

Each request writes its upload and result into TEMP_DIR:

- The upload is deleted as soon as processing finishes.
- The result is a NamedTemporaryFile, which is deleted when it is closed, i.e.
  once the server has finished streaming it to the client.
- A sweeper runs at startup and then every TEMP_SWEEP_INTERVAL_SECONDS on a
  background thread (even when no requests arrive), deleting anything older
  than TEMP_TTL_SECONDS. This catches files left behind by a crash or a
  killed worker. Set the interval to 0 to disable the thread.
"""

import logging
import os
import tempfile
import threading
import time
from pathlib import Path

from flask import current_app

PREFIX = "kit-"

log = logging.getLogger(__name__)


def init_app(app):
    temp_dir = Path(app.config["TEMP_DIR"])
    temp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    app.config["TEMP_DIR"] = temp_dir
    ttl = app.config["TEMP_TTL_SECONDS"]

    sweep(temp_dir, ttl)

    interval = app.config["TEMP_SWEEP_INTERVAL_SECONDS"]
    if interval > 0:
        thread = threading.Thread(target=_sweep_forever, args=(temp_dir, ttl, interval),
                                  name="kimagetools-sweeper", daemon=True)
        thread.start()
        app.extensions["kimagetools_sweeper"] = thread


def _sweep_forever(temp_dir, ttl, interval):
    while True:
        time.sleep(interval)
        try:
            sweep(temp_dir, ttl)
        except Exception:
            log.exception("Temp file sweep failed")


def sweep(temp_dir, max_age):
    """Delete our temp files older than max_age seconds. Returns how many were removed."""
    cutoff = time.time() - max_age
    removed = 0
    for path in Path(temp_dir).glob(f"{PREFIX}*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            pass  # deleted concurrently by its own request
        except OSError:
            log.warning("Could not delete stale temp file %s", path)
    return removed


def new_upload_path():
    """Create an empty temp file for an upload and return its path."""
    fd, name = tempfile.mkstemp(prefix=PREFIX, suffix=".upload",
                                dir=current_app.config["TEMP_DIR"])
    os.close(fd)
    return Path(name)


def new_output_file():
    """Return a temp file object for a result; it is deleted when closed."""
    return tempfile.NamedTemporaryFile(prefix=PREFIX, suffix=".out",
                                       dir=current_app.config["TEMP_DIR"])


def remove(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not delete temp file %s", path)
