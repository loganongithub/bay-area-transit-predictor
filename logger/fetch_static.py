"""Archive static GTFS schedules (stops, routes, trips, shapes).

Each realtime feed must be joined to the static schedule from the SAME source:
AC Transit's API and 511 use the same trip IDs but different stop IDs. So we
keep all three schedules, and a new copy each time one changes (a few times a
year), because a day's realtime data must be joined to the schedule that was
active that day.

Runs once a day (systemd timer). Writes only when a schedule's content changed:

  DATA_DIR/raw/static/<source>/<YYYY-MM-DD>/<source>_<YYYYMMDDTHHMMSS>Z_<sha12>.zip

The existing uploader moves raw/** to S3, so these land in s3://<bucket>/raw/static/.
Content hashes of the last saved copies live in DATA_DIR/static_state.json
(outside raw/, so the uploader leaves it alone).

Stdlib only, Python 3.9+.
"""

import hashlib
import io
import json
import logging
import os
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from logger import load_dotenv, redact  # noqa: E402  (same folder on the server)

log = logging.getLogger("fetch_static")

REQUIRED_FILES = {"stops.txt", "trips.txt", "stop_times.txt", "routes.txt"}


def build_sources():
    """Return [(name, url, kind)] for every source we have credentials for.
    kind is "zip" (a GTFS zip) or "json" (schedule metadata)."""
    sources = [("bart", "https://www.bart.gov/dev/schedules/google_transit.zip", "zip")]
    act = os.environ.get("API_ACT_TOKEN", "").strip()
    if act:
        base = "https://api.actransit.org/transit/gtfs"
        sources.append(("act", "%s/download?token=%s" % (base, act), "zip"))
        # List of past/current/future schedule versions with service dates:
        # tells us when AC Transit switches schedules.
        sources.append(("act_schedules", "%s/all?token=%s" % (base, act), "json"))
    key = os.environ.get("API_511_KEY", "").strip()
    if key:
        agency = os.environ.get("AGENCY_511", "AC")
        sources.append((
            "511_%s" % agency.lower(),
            "https://api.511.org/transit/datafeeds?api_key=%s&operator_id=%s" % (key, agency),
            "zip",
        ))
    return sources


def validate(body, kind):
    """Raise ValueError unless body looks like what we expect."""
    if kind == "json":
        json.loads(body.decode("utf-8-sig"))
        return
    if not zipfile.is_zipfile(io.BytesIO(body)):
        raise ValueError("not a zip file (%d bytes, starts %r)" % (len(body), body[:40]))
    names = set(zipfile.ZipFile(io.BytesIO(body)).namelist())
    missing = REQUIRED_FILES - names
    if missing:
        raise ValueError("zip is missing %s" % ", ".join(sorted(missing)))


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "transit-archiver/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def archive_one(data_dir, name, kind, body, state, now):
    """Save body if its content differs from the last saved copy.
    Returns the path written, or None if unchanged. Updates state in place."""
    digest = hashlib.sha256(body).hexdigest()
    if state.get(name, {}).get("sha256") == digest:
        return None
    ext = "json" if kind == "json" else "zip"
    day = now.strftime("%Y-%m-%d")
    filename = "%s_%sZ_%s.%s" % (name, now.strftime("%Y%m%dT%H%M%S"), digest[:12], ext)
    tmp_dir = os.path.join(data_dir, "tmp")
    final_dir = os.path.join(data_dir, "raw", "static", name, day)
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(final_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, filename)
    with open(tmp_path, "wb") as f:
        f.write(body)
    final_path = os.path.join(final_dir, filename)
    os.replace(tmp_path, final_path)  # atomic: the uploader never sees a partial file
    state[name] = {"sha256": digest, "saved": now.isoformat(), "file": filename}
    return final_path


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    load_dotenv()
    data_dir = os.environ.get("DATA_DIR", "./data")
    os.makedirs(data_dir, exist_ok=True)
    state_path = os.path.join(data_dir, "static_state.json")
    state = load_state(state_path)

    failures = 0
    for name, url, kind in build_sources():
        try:
            body = fetch(url)
            validate(body, kind)
            path = archive_one(data_dir, name, kind, body, state, datetime.now(timezone.utc))
            if path:
                log.info("%s: NEW version saved (%d bytes) -> %s", name, len(body), path)
            else:
                log.info("%s: unchanged (%d bytes)", name, len(body))
            save_state(state_path, state)
        except Exception as e:  # one bad source must not stop the others
            failures += 1
            log.warning("%s: %s: %s", name, type(e).__name__, redact(str(e)))

    # Non-zero exit marks the systemd run as failed, so it shows up in status.
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
