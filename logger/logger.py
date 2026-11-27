"""Phase 0 raw feed logger.

Polls GTFS-Realtime feeds on a fixed schedule and writes every response,
byte-for-byte, to disk as gzip. It does not parse anything: parsing happens
later, from the archive, so a parsing bug can never lose data.

Files land in  DATA_DIR/raw/<feed>/<YYYY-MM-DD>/<feed>_<YYYYMMDDTHHMMSS>_<ms>Z.pb.gz
(UTC, timestamp = when we received the response). A separate job
(deploy/upload.sh) moves finished files to S3.

Stdlib only, Python 3.9+ (Amazon Linux 2023 ships 3.9).
"""

import gzip
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger("logger")


def load_dotenv(path=".env"):
    """Load KEY=value lines from a local .env (for running on a laptop).
    Never overrides variables already set (systemd sets them on the server)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_feeds():
    """Return the list of feeds to poll, based on environment variables."""
    feeds = []
    key = os.environ.get("API_511_KEY", "").strip()
    agency = os.environ.get("AGENCY_511", "AC")
    interval_511 = float(os.environ.get("INTERVAL_511_S", "150"))
    if key:
        base = "https://api.511.org/transit"
        for kind in ("tripupdates", "vehiclepositions"):
            feeds.append({
                "name": "511_%s_%s" % (agency.lower(), kind),
                "url": "%s/%s?api_key=%s&agency=%s" % (base, kind, key, agency),
                "interval": interval_511,
            })
    else:
        log.warning("API_511_KEY not set: 511 AC Transit feeds disabled")

    # AC Transit's own API: the original source 511 republishes, with no
    # 60 req/hour cap, so it gets the fine-grained polling. 511 stays as backup.
    act_token = os.environ.get("API_ACT_TOKEN", "").strip()
    interval_act = float(os.environ.get("INTERVAL_ACT_S", "30"))
    if act_token:
        base = "https://api.actransit.org/transit/gtfsrt"
        for i, kind in enumerate(("tripupdates", "vehicles")):
            feeds.append({
                "name": "act_%s" % kind,
                "url": "%s/%s?token=%s" % (base, kind, act_token),
                "interval": interval_act,
                # Half an interval apart so we never send AC Transit two
                # requests at once.
                "offset": i * interval_act / 2,
            })
    else:
        log.warning("API_ACT_TOKEN not set: AC Transit API feeds disabled")

    interval_bart = float(os.environ.get("INTERVAL_BART_S", "30"))
    feeds.append({
        "name": "bart_tripupdates",
        "url": "https://api.bart.gov/gtfsrt/tripupdate.aspx",
        "interval": interval_bart,
    })
    return feeds


def redact(text):
    """Strip API keys out of anything we log."""
    for var in ("API_511_KEY", "API_ACT_TOKEN"):
        key = os.environ.get(var, "").strip()
        if key:
            text = text.replace(key, "***")
    return text


def fetch(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "transit-archiver/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def save(data_dir, feed_name, body, received):
    """Write atomically: tmp file first, then rename into raw/. The uploader
    only looks in raw/, so it can never pick up a half-written file."""
    day = received.strftime("%Y-%m-%d")
    stamp = received.strftime("%Y%m%dT%H%M%S") + "_%03dZ" % (received.microsecond // 1000)
    filename = "%s_%s.pb.gz" % (feed_name, stamp)

    tmp_dir = os.path.join(data_dir, "tmp")
    final_dir = os.path.join(data_dir, "raw", feed_name, day)
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(final_dir, exist_ok=True)

    tmp_path = os.path.join(tmp_dir, filename)
    with gzip.open(tmp_path, "wb", compresslevel=6) as f:
        f.write(body)
    os.replace(tmp_path, os.path.join(final_dir, filename))
    return len(body)


class Health:
    """Optional dead-man's switch: ping HEALTHCHECK_URL every few minutes,
    but only while every feed has succeeded recently. If the pings stop,
    the monitoring service emails you."""

    def __init__(self, url, feeds, every_s=300, stale_s=900):
        self.url = url
        self.every_s = every_s
        self.stale_s = stale_s
        self.last_ok = {f["name"]: 0.0 for f in feeds}
        self.last_ping = 0.0

    def ok(self, name):
        self.last_ok[name] = time.time()

    def maybe_ping(self):
        if not self.url:
            return
        now = time.time()
        if now - self.last_ping < self.every_s:
            return
        if all(now - t < self.stale_s for t in self.last_ok.values()):
            try:
                urllib.request.urlopen(self.url, timeout=10).read()
                self.last_ping = now
            except Exception as e:  # monitoring must never crash logging
                log.warning("healthcheck ping failed: %s", e)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    load_dotenv()

    data_dir = os.environ.get("DATA_DIR", "./data")
    timeout = float(os.environ.get("HTTP_TIMEOUT_S", "20"))
    feeds = build_feeds()
    health = Health(os.environ.get("HEALTHCHECK_URL", "").strip(), feeds)

    stopping = {"flag": False}

    def handle_stop(signum, frame):
        log.info("received signal %s, stopping after current fetch", signum)
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    for f in feeds:
        log.info("feed %s every %.0fs", f["name"], f["interval"])
    log.info("writing to %s", os.path.abspath(data_dir))

    # Stagger start times so feeds sharing a rate limit don't fire together.
    now = time.monotonic()
    for i, f in enumerate(feeds):
        f["next"] = now + f.get("offset", i * 2.0)

    while not stopping["flag"]:
        f = min(feeds, key=lambda x: x["next"])
        delay = f["next"] - time.monotonic()
        if delay > 0:
            time.sleep(min(delay, 1.0))  # wake every second so SIGTERM is quick
            continue

        # Schedule the next fetch on a fixed grid; if we fell far behind
        # (laptop sleep, long outage), restart the grid instead of bursting.
        f["next"] += f["interval"]
        if f["next"] < time.monotonic():
            f["next"] = time.monotonic() + f["interval"]

        try:
            status, body = fetch(f["url"], timeout)
            received = datetime.now(timezone.utc)
            if status != 200 or not body:
                log.warning("%s: HTTP %s, %d bytes, not saved", f["name"], status, len(body))
                continue
            if body[:1] != b"\x0a":
                # A FeedMessage starts with its header (field 1) -> byte 0x0A.
                # Anything else is probably an error page; keep it for debugging.
                log.warning("%s: body doesn't look like protobuf, saving under _invalid", f["name"])
                save(data_dir, f["name"] + "_invalid", body, received)
                continue
            size = save(data_dir, f["name"], body, received)
            health.ok(f["name"])
            log.info("%s: saved %d bytes", f["name"], size)
        except urllib.error.HTTPError as e:
            # 429 = rate limited. Don't retry early; just wait for the next slot.
            log.warning("%s: HTTP error %s", f["name"], e.code)
        except Exception as e:
            log.warning("%s: %s: %s", f["name"], type(e).__name__, redact(str(e)))

        health.maybe_ping()

    log.info("stopped")


if __name__ == "__main__":
    main()
