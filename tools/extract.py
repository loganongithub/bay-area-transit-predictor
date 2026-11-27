"""Flatten archived GTFS-Realtime snapshots into Parquet tables for analysis.

This only reshapes data; it makes no inferences. Every value comes straight
from the feed. Fields the feed leaves unset become nulls rather than 0, so you
can tell "missing" apart from "zero".

Usage (repo root, venv active):
  python tools/extract.py data/raw/act_vehicles/2026-11-27
  python tools/extract.py data/raw/act_tripupdates/2026-11-27 -o data/tables

Writes one file per input folder: <out>/<feed>/<day>.parquet
  vehicle feeds   -> one row per vehicle per snapshot
  trip-update feeds -> one row per stop prediction per snapshot

Load it with pandas:
  pd.read_parquet("data/tables/act_vehicles/2026-11-27.parquet", dtype_backend="pyarrow")
dtype_backend="pyarrow" keeps integer columns with nulls as integers (plain
pandas turns them into floats, e.g. stop_sequence 76.0).

Times: received_utc is when OUR logger got the snapshot (from the filename);
feed_timestamp is when the feed says it was generated; position_timestamp /
arrival_time etc. are the feed's own values. All are Unix seconds, UTC.
"""

import argparse
import glob
import gzip
import os
import re
import sys
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
from google.transit import gtfs_realtime_pb2 as rt

# <feed>_<YYYYMMDDTHHMMSS>_<ms>Z.pb.gz  (written by logger/logger.py)
NAME_RE = re.compile(r"^(?P<feed>.+)_(?P<stamp>\d{8}T\d{6})_(?P<ms>\d{3})Z\.pb\.gz$")

VEHICLE_STATUS = rt.VehiclePosition.VehicleStopStatus.Name
TRIP_REL = rt.TripDescriptor.ScheduleRelationship.Name
STOP_REL = rt.TripUpdate.StopTimeUpdate.ScheduleRelationship.Name


def received_time(filename):
    """UTC time our logger received the snapshot, from its filename."""
    m = NAME_RE.match(os.path.basename(filename))
    if not m:
        raise ValueError("unexpected snapshot filename: %s" % filename)
    t = datetime.strptime(m["stamp"], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    return t.timestamp() + int(m["ms"]) / 1000.0


def opt(msg, field):
    """Field value if set, else None (protobuf returns 0/'' for unset fields)."""
    return getattr(msg, field) if msg.HasField(field) else None


def trip_fields(trip):
    return {
        "trip_id": opt(trip, "trip_id"),
        "route_id": opt(trip, "route_id"),
        "direction_id": opt(trip, "direction_id"),
        "start_date": opt(trip, "start_date"),
        "start_time": opt(trip, "start_time"),
        "trip_schedule_relationship": TRIP_REL(trip.schedule_relationship)
        if trip.HasField("schedule_relationship") else None,
    }


def vehicle_rows(feed, received, source_file):
    for e in feed.entity:
        if not e.HasField("vehicle"):
            continue
        v = e.vehicle
        row = {"received_utc": received, "feed_timestamp": opt(feed.header, "timestamp"),
               "source_file": source_file, "entity_id": e.id,
               "vehicle_id": opt(v.vehicle, "id") if v.HasField("vehicle") else None}
        row.update(trip_fields(v.trip) if v.HasField("trip") else trip_fields(rt.TripDescriptor()))
        p = v.position if v.HasField("position") else None
        row.update({
            "latitude": p.latitude if p is not None else None,
            "longitude": p.longitude if p is not None else None,
            "bearing": opt(p, "bearing") if p is not None else None,
            "speed_mps": opt(p, "speed") if p is not None else None,
            "odometer_m": opt(p, "odometer") if p is not None else None,
            "position_timestamp": opt(v, "timestamp"),
            "current_status": VEHICLE_STATUS(v.current_status) if v.HasField("current_status") else None,
            "stop_id": opt(v, "stop_id"),
            "current_stop_sequence": opt(v, "current_stop_sequence"),
        })
        yield row


def stop_time_rows(feed, received, source_file):
    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        base = {"received_utc": received, "feed_timestamp": opt(feed.header, "timestamp"),
                "source_file": source_file, "entity_id": e.id,
                "vehicle_id": opt(tu.vehicle, "id") if tu.HasField("vehicle") else None,
                "trip_update_timestamp": opt(tu, "timestamp"),
                "trip_delay": opt(tu, "delay"),
                "n_stop_time_updates": len(tu.stop_time_update)}
        base.update(trip_fields(tu.trip))
        if not tu.stop_time_update:
            # Keep trips with no stop predictions visible (one row, stop fields null).
            yield dict(base, stop_sequence=None, stop_id=None, stop_schedule_relationship=None,
                       **{k: None for k in EVENT_COLS})
            continue
        for s in tu.stop_time_update:
            row = dict(base)
            row["stop_sequence"] = opt(s, "stop_sequence")
            row["stop_id"] = opt(s, "stop_id")
            row["stop_schedule_relationship"] = STOP_REL(s.schedule_relationship) \
                if s.HasField("schedule_relationship") else None
            for which in ("arrival", "departure"):
                ev = getattr(s, which) if s.HasField(which) else None
                row[which + "_time"] = opt(ev, "time") if ev is not None else None
                row[which + "_delay"] = opt(ev, "delay") if ev is not None else None
                row[which + "_uncertainty"] = opt(ev, "uncertainty") if ev is not None else None
            yield row


EVENT_COLS = [w + f for w in ("arrival", "departure") for f in ("_time", "_delay", "_uncertainty")]

# Explicit schemas so every day's file has identical columns and types,
# even on a day where some field is always null.
TRIP_COLS = [("trip_id", pa.string()), ("route_id", pa.string()), ("direction_id", pa.int64()),
             ("start_date", pa.string()), ("start_time", pa.string()),
             ("trip_schedule_relationship", pa.string())]
COMMON = [("received_utc", pa.float64()), ("feed_timestamp", pa.int64()),
          ("source_file", pa.string()), ("entity_id", pa.string()), ("vehicle_id", pa.string())]
VEHICLE_SCHEMA = pa.schema(COMMON + TRIP_COLS + [
    ("latitude", pa.float64()), ("longitude", pa.float64()), ("bearing", pa.float64()),
    ("speed_mps", pa.float64()), ("odometer_m", pa.float64()), ("position_timestamp", pa.int64()),
    ("current_status", pa.string()), ("stop_id", pa.string()),
    ("current_stop_sequence", pa.int64())])
STOP_TIME_SCHEMA = pa.schema(COMMON + [
    ("trip_update_timestamp", pa.int64()), ("trip_delay", pa.int64()),
    ("n_stop_time_updates", pa.int64())] + TRIP_COLS + [
    ("stop_sequence", pa.int64()), ("stop_id", pa.string()), ("stop_schedule_relationship", pa.string())]
    + [(c, pa.int64()) for c in EVENT_COLS])


def extract_folder(folder, out_root):
    files = sorted(glob.glob(os.path.join(folder, "*.pb.gz")))
    if not files:
        raise SystemExit("no *.pb.gz files in %s" % folder)
    feed_name = NAME_RE.match(os.path.basename(files[0]))["feed"]
    is_vehicle = "vehicle" in feed_name
    rows, bad = [], 0
    for path in files:
        try:
            msg = rt.FeedMessage()
            with gzip.open(path, "rb") as f:
                msg.ParseFromString(f.read())
        except Exception as e:
            bad += 1
            print("skipping unreadable %s: %s" % (os.path.basename(path), e), file=sys.stderr)
            continue
        gen = vehicle_rows if is_vehicle else stop_time_rows
        rows.extend(gen(msg, received_time(path), os.path.basename(path)))

    schema = VEHICLE_SCHEMA if is_vehicle else STOP_TIME_SCHEMA
    table = pa.Table.from_pylist(rows, schema=schema)
    day = os.path.basename(os.path.normpath(folder))
    out_dir = os.path.join(out_root, feed_name)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, day + ".parquet")
    pq.write_table(table, out_path, compression="zstd")
    print("%s: %d snapshots (%d unreadable) -> %d rows -> %s" % (
        feed_name, len(files) - bad, bad, table.num_rows, out_path))
    return out_path


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folders", nargs="+", help="raw/<feed>/<YYYY-MM-DD> folders")
    p.add_argument("-o", "--out", default=os.path.join("data", "tables"))
    args = p.parse_args()
    for folder in args.folders:
        extract_folder(folder, args.out)


if __name__ == "__main__":
    main()
