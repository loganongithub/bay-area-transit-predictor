"""Look inside archived GTFS-Realtime snapshots.

Usage (from the repo root, with the venv active):
  python tools/inspect_feed.py summary FILE           # header + one line per entity
  python tools/inspect_feed.py dump FILE              # full protobuf as text
  python tools/inspect_feed.py trips DIR              # which trips appear, in how many snapshots
  python tools/inspect_feed.py trip TRIP_ID DIR       # one trip's predictions, snapshot by snapshot
  python tools/inspect_feed.py trip TRIP_ID DIR --stop STOP_ID   # one stop's prediction over time

DIR is searched recursively for *.pb.gz (or *.pb) files. Times print in Bay Area
local time. This tool only displays data; it makes no inferences.
"""

import argparse
import glob
import gzip
import os
import sys
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from google.protobuf import text_format
from google.transit import gtfs_realtime_pb2

LOCAL = ZoneInfo("America/Los_Angeles")


def load(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        msg = gtfs_realtime_pb2.FeedMessage()
        msg.ParseFromString(f.read())
    return msg


def fmt_time(epoch):
    """Epoch seconds -> 'HH:MM:SS' local. 0 or missing -> '-'."""
    if not epoch:
        return "-"
    return datetime.fromtimestamp(epoch, LOCAL).strftime("%H:%M:%S")


def fmt_delay(seconds):
    if seconds is None:
        return "-"
    sign = "+" if seconds >= 0 else "-"
    m, s = divmod(abs(seconds), 60)
    return "%s%d:%02d" % (sign, m, s)


def stop_event(stu, which):
    """Return (time, delay) for stu.arrival or stu.departure, None where unset."""
    if not stu.HasField(which):
        return None, None
    ev = getattr(stu, which)
    t = ev.time if ev.HasField("time") else None
    d = ev.delay if ev.HasField("delay") else None
    return t, d


def find_files(path):
    if os.path.isfile(path):
        return [path]
    files = glob.glob(os.path.join(path, "**", "*.pb.gz"), recursive=True)
    files += glob.glob(os.path.join(path, "**", "*.pb"), recursive=True)
    # Filenames start with feed name + UTC receive time, so sorting by name
    # within one feed sorts by time.
    return sorted(f for f in files if "_invalid" not in f)


# ---------------------------------------------------------------- commands

def cmd_summary(args):
    msg = load(args.file)
    h = msg.header
    print("gtfs_realtime_version: %s" % h.gtfs_realtime_version)
    print("header timestamp:      %s (%d)" % (fmt_time(h.timestamp), h.timestamp))
    print("entities:              %d" % len(msg.entity))
    print()
    for e in msg.entity:
        if e.HasField("trip_update"):
            tu = e.trip_update
            t = tu.trip
            stops = tu.stop_time_update
            first = stops[0].stop_id if stops else "-"
            last = stops[-1].stop_id if stops else "-"
            print("TU  trip=%-14s route=%-6s start=%s %s  stops=%2d  %s..%s  vehicle=%s" % (
                t.trip_id, t.route_id or "-", t.start_date or "-", t.start_time or "",
                len(stops), first, last, tu.vehicle.id or "-"))
        elif e.HasField("vehicle"):
            v = e.vehicle
            print("VP  vehicle=%-8s trip=%-14s stop=%-8s status=%-14s pos=%.5f,%.5f  at %s" % (
                v.vehicle.id, v.trip.trip_id or "-", v.stop_id or "-",
                gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus.Name(v.current_status),
                v.position.latitude, v.position.longitude, fmt_time(v.timestamp)))
        elif e.HasField("alert"):
            print("ALERT id=%s" % e.id)


def cmd_dump(args):
    print(text_format.MessageToString(load(args.file)))


def cmd_trips(args):
    files = find_files(args.dir)
    seen = Counter()
    routes = {}
    for path in files:
        for e in load(path).entity:
            if e.HasField("trip_update"):
                tid = e.trip_update.trip.trip_id
                seen[tid] += 1
                routes[tid] = e.trip_update.trip.route_id or "-"
    print("%d snapshots, %d distinct trips\n" % (len(files), len(seen)))
    print("%-16s %-8s %s" % ("trip_id", "route", "snapshots"))
    for tid, n in seen.most_common(args.limit):
        print("%-16s %-8s %d" % (tid, routes[tid], n))


def cmd_trip(args):
    files = find_files(args.dir)
    found = 0
    for path in files:
        msg = load(path)
        snap = fmt_time(msg.header.timestamp)
        recv = os.path.basename(path)
        for e in msg.entity:
            if not e.HasField("trip_update") or e.trip_update.trip.trip_id != args.trip_id:
                continue
            found += 1
            stops = list(e.trip_update.stop_time_update)
            if args.stop:
                stops = [s for s in stops if s.stop_id == args.stop]
                if not stops:
                    print("feed %s  (stop %s no longer in this trip update)" % (snap, args.stop))
                    continue
                s = stops[0]
                t, d = stop_event(s, "arrival")
                dt, dd = stop_event(s, "departure")
                print("feed %s  arr %s (%s)  dep %s (%s)" % (
                    snap, fmt_time(t), fmt_delay(d), fmt_time(dt), fmt_delay(dd)))
            else:
                print("--- feed %s  [%s]  %d stops" % (snap, recv, len(stops)))
                for s in stops[: args.max_stops]:
                    t, d = stop_event(s, "arrival")
                    dt, dd = stop_event(s, "departure")
                    rel = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship.Name(
                        s.schedule_relationship)
                    print("    seq=%-3s stop=%-8s arr %s (%s)  dep %s (%s)  %s" % (
                        s.stop_sequence if s.HasField("stop_sequence") else "-",
                        s.stop_id, fmt_time(t), fmt_delay(d), fmt_time(dt), fmt_delay(dd),
                        "" if rel == "SCHEDULED" else rel))
    if not found:
        print("trip %s not found in %d snapshots (try the 'trips' command)" % (args.trip_id, len(files)))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("summary"); s.add_argument("file"); s.set_defaults(fn=cmd_summary)
    s = sub.add_parser("dump"); s.add_argument("file"); s.set_defaults(fn=cmd_dump)
    s = sub.add_parser("trips"); s.add_argument("dir"); s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_trips)
    s = sub.add_parser("trip"); s.add_argument("trip_id"); s.add_argument("dir")
    s.add_argument("--stop", help="show only this stop_id, one line per snapshot")
    s.add_argument("--max-stops", type=int, default=6, help="stops to print per snapshot (default 6)")
    s.set_defaults(fn=cmd_trip)

    args = p.parse_args()
    try:
        args.fn(args)
    except BrokenPipeError:  # e.g. piping into `head`
        sys.exit(0)


if __name__ == "__main__":
    main()
