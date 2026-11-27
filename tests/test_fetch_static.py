import io
import os
import sys
import zipfile
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "logger"))
import fetch_static  # noqa: E402

NOW = datetime(2026, 11, 27, 11, 0, 0, tzinfo=timezone.utc)


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in files:
            z.writestr(name, "x")
    return buf.getvalue()


GOOD = make_zip(["stops.txt", "trips.txt", "stop_times.txt", "routes.txt", "shapes.txt"])


def test_validate_accepts_gtfs_zip():
    fetch_static.validate(GOOD, "zip")


def test_validate_rejects_error_page():
    with pytest.raises(ValueError, match="not a zip"):
        fetch_static.validate(b"<html>Invalid token</html>", "zip")


def test_validate_rejects_incomplete_zip():
    with pytest.raises(ValueError, match="stop_times.txt"):
        fetch_static.validate(make_zip(["stops.txt", "trips.txt", "routes.txt"]), "zip")


def test_validate_json():
    fetch_static.validate(b'[{"BookingId": "2608FA"}]', "json")
    with pytest.raises(ValueError):
        fetch_static.validate(b"not json", "json")


def test_archive_saves_once_then_skips_unchanged(tmp_path):
    state = {}
    first = fetch_static.archive_one(str(tmp_path), "act", "zip", GOOD, state, NOW)
    assert first and first.endswith(".zip")
    assert "raw" in first and "static" in first and "2026-11-27" in first
    again = fetch_static.archive_one(str(tmp_path), "act", "zip", GOOD, state, NOW)
    assert again is None


def test_archive_saves_new_version_when_content_changes(tmp_path):
    state = {}
    fetch_static.archive_one(str(tmp_path), "act", "zip", GOOD, state, NOW)
    changed = make_zip(["stops.txt", "trips.txt", "stop_times.txt", "routes.txt"])
    path = fetch_static.archive_one(str(tmp_path), "act", "zip", changed, state, NOW)
    assert path is not None
    saved = list((tmp_path / "raw" / "static" / "act" / "2026-11-27").iterdir())
    assert len(saved) == 2


def test_state_round_trip(tmp_path):
    p = str(tmp_path / "state.json")
    assert fetch_static.load_state(p) == {}
    fetch_static.save_state(p, {"act": {"sha256": "abc"}})
    assert fetch_static.load_state(p) == {"act": {"sha256": "abc"}}


def test_sources_depend_on_keys(monkeypatch):
    monkeypatch.delenv("API_ACT_TOKEN", raising=False)
    monkeypatch.delenv("API_511_KEY", raising=False)
    assert [s[0] for s in fetch_static.build_sources()] == ["bart"]
    monkeypatch.setenv("API_ACT_TOKEN", "T")
    monkeypatch.setenv("API_511_KEY", "K")
    assert [s[0] for s in fetch_static.build_sources()] == ["bart", "act", "act_schedules", "511_ac"]
