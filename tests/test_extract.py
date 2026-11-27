import gzip
import os
import sys

import pyarrow.parquet as pq
import pytest
from google.transit import gtfs_realtime_pb2 as rt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import extract  # noqa: E402


def write_snapshot(folder, name, msg):
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(folder / name, "wb") as f:
        f.write(msg.SerializeToString())


def header(msg, ts=1795765320):
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = ts


def test_received_time_from_filename():
    t = extract.received_time("act_vehicles_20261127T074222_174Z.pb.gz")
    assert t == pytest.approx(1795765342.174)
    with pytest.raises(ValueError):
        extract.received_time("random.pb.gz")


def test_vehicle_rows_keep_unset_fields_null(tmp_path):
    msg = rt.FeedMessage(); header(msg)
    e = msg.entity.add(); e.id = "1"
    v = e.vehicle
    v.vehicle.id = "7043"
    v.trip.trip_id = "1776010"
    v.position.latitude = 37.8
    v.position.longitude = -122.2
    v.current_status = rt.VehiclePosition.STOPPED_AT
    v.stop_id = "3308"
    # deliberately unset: bearing, speed, timestamp, current_stop_sequence
    day = tmp_path / "raw" / "act_vehicles" / "2026-11-27"
    write_snapshot(day, "act_vehicles_20261127T074222_174Z.pb.gz", msg)

    out = extract.extract_folder(str(day), str(tmp_path / "tables"))
    rows = pq.read_table(out).to_pylist()
    assert len(rows) == 1
    r = rows[0]
    assert r["vehicle_id"] == "7043" and r["trip_id"] == "1776010"
    assert r["current_status"] == "STOPPED_AT" and r["stop_id"] == "3308"
    assert r["latitude"] == pytest.approx(37.8)
    assert r["bearing"] is None and r["speed_mps"] is None
    assert r["position_timestamp"] is None and r["current_stop_sequence"] is None
    assert r["feed_timestamp"] == 1795765320


def test_trip_update_rows_one_per_stop_and_empty_trips_kept(tmp_path):
    msg = rt.FeedMessage(); header(msg)
    e = msg.entity.add(); e.id = "a"
    tu = e.trip_update
    tu.trip.trip_id = "1333010"
    tu.vehicle.id = "1521"
    for seq, stop, t in ((76, "2494", 1795765324), (77, "2495", 1795765400)):
        s = tu.stop_time_update.add()
        s.stop_sequence = seq
        s.stop_id = stop
        s.arrival.time = t
    empty = msg.entity.add(); empty.id = "b"
    empty.trip_update.trip.trip_id = "99010"
    empty.trip_update.trip.schedule_relationship = rt.TripDescriptor.CANCELED

    day = tmp_path / "raw" / "act_tripupdates" / "2026-11-27"
    write_snapshot(day, "act_tripupdates_20261127T074207_858Z.pb.gz", msg)
    rows = pq.read_table(extract.extract_folder(str(day), str(tmp_path / "t"))).to_pylist()

    assert len(rows) == 3
    stops = [r for r in rows if r["trip_id"] == "1333010"]
    assert [r["stop_id"] for r in stops] == ["2494", "2495"]
    assert stops[0]["arrival_time"] == 1795765324
    assert stops[0]["arrival_delay"] is None and stops[0]["departure_time"] is None
    (cancel,) = [r for r in rows if r["trip_id"] == "99010"]
    assert cancel["n_stop_time_updates"] == 0 and cancel["stop_id"] is None
    assert cancel["trip_schedule_relationship"] == "CANCELED"


def test_unreadable_snapshot_is_skipped(tmp_path, capsys):
    msg = rt.FeedMessage(); header(msg)
    day = tmp_path / "raw" / "act_vehicles" / "2026-11-27"
    write_snapshot(day, "act_vehicles_20261127T000000_000Z.pb.gz", msg)
    (day / "act_vehicles_20261127T000030_000Z.pb.gz").write_bytes(b"not gzip")
    out = extract.extract_folder(str(day), str(tmp_path / "t"))
    assert pq.read_table(out).num_rows == 0
    assert "skipping unreadable" in capsys.readouterr().err
