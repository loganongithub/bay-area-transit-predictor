"""Archive coverage report: did the logger capture what it should have?

Lists one UTC day's snapshot files in S3 (filenames only, nothing downloaded)
and reports, per feed: snapshots captured vs expected, the share of expected
polls captured, and the longest gaps between consecutive snapshots.

Usage (repo root, venv active, AWS CLI logged in):
  python tools/coverage.py 2026-11-27
  python tools/coverage.py 2026-11-27 --local      # count data/raw/ instead of S3

Expected counts assume the configured intervals below. A gap means the logger,
the network, or the feed was down; whatever wasn't captured is gone for good,
so this is the report to check after any outage.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

BUCKET = "logan-transit-raw-2026"
REGION = "us-east-2"
INTERVALS = {  # seconds between polls, matching the server config
    "act_tripupdates": 30,
    "act_vehicles": 30,
    "bart_tripupdates": 30,
    "511_ac_tripupdates": 150,
    "511_ac_vehiclepositions": 150,
}
STAMP_RE = re.compile(r"_(\d{8}T\d{6})_(\d{3})Z\.pb\.gz$")


def list_s3(feed, day):
    """Filenames under raw/<feed>/<day>/ in S3 (handles pagination via the CLI)."""
    out = subprocess.run(
        ["aws", "s3api", "list-objects-v2", "--bucket", BUCKET, "--region", REGION,
         "--prefix", "raw/%s/%s/" % (feed, day), "--query", "Contents[].Key", "--output", "json"],
        capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("aws CLI failed (expired login? run: aws login)\n" + out.stderr.strip())
    keys = json.loads(out.stdout or "null") or []
    return [k.rsplit("/", 1)[-1] for k in keys]


def list_local(feed, day):
    return [os.path.basename(p) for p in glob.glob(os.path.join("data", "raw", feed, day, "*.pb.gz"))]


def times(names):
    ts = []
    for n in names:
        m = STAMP_RE.search(n)
        if m:
            t = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            ts.append(t + timedelta(milliseconds=int(m.group(2))))
    return sorted(ts)


def report(feed, ts, day_start, day_end, interval, top_gaps):
    # The newest ~10 min are still on the server awaiting upload; don't call that a gap.
    window_end = min(day_end, datetime.now(timezone.utc) - timedelta(minutes=12))
    if not ts:
        print("%-24s no snapshots" % feed)
        return
    # Measure from the first snapshot if logging started partway through the day.
    window_start = max(day_start, ts[0]) if ts[0] - day_start > timedelta(hours=1) else day_start
    ts = [t for t in ts if window_start <= t <= window_end]
    span = (window_end - window_start).total_seconds()
    expected = int(span // interval) + 1  # fencepost: polls at both ends
    pct = 100.0 * len(ts) / expected
    edges = [window_start] + ts + [window_end]
    gaps = sorted(((b - a).total_seconds(), a) for a, b in zip(edges, edges[1:]))[::-1]
    big = [(g, a) for g, a in gaps if g > 3 * interval][:top_gaps]
    print("%-24s %6d / %6d expected  (%5.1f%%)  window %s-%s UTC" % (
        feed, len(ts), expected, pct, window_start.strftime("%H:%M"), window_end.strftime("%H:%M")))
    for g, a in big:
        print("    gap of %6.1f min starting %s UTC" % (g / 60, a.strftime("%H:%M:%S")))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("day", help="UTC date, YYYY-MM-DD")
    p.add_argument("--local", action="store_true", help="count files in data/raw/ instead of S3")
    p.add_argument("--gaps", type=int, default=5, help="largest gaps to list per feed")
    args = p.parse_args()

    day_start = datetime.strptime(args.day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    print("Coverage for %s (UTC), source: %s\n" % (args.day, "local" if args.local else "S3"))
    for feed, interval in INTERVALS.items():
        names = list_local(feed, args.day) if args.local else list_s3(feed, args.day)
        report(feed, times(names), day_start, day_end, interval, args.gaps)
    print("\nNote: the last 12 minutes are excluded (still on the server, awaiting upload).")


if __name__ == "__main__":
    main()
