import gzip
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "logger"))
import logger  # noqa: E402

RECEIVED = datetime(2026, 11, 27, 8, 5, 9, 123456, tzinfo=timezone.utc)


def test_save_writes_gzip_with_utc_name(tmp_path):
    n = logger.save(str(tmp_path), "bart_tripupdates", b"\x0aabc", RECEIVED)
    assert n == 4
    path = tmp_path / "raw" / "bart_tripupdates" / "2026-11-27" / "bart_tripupdates_20261127T080509_123Z.pb.gz"
    assert path.exists()
    assert gzip.open(path).read() == b"\x0aabc"


def test_save_leaves_no_temp_files(tmp_path):
    logger.save(str(tmp_path), "f", b"\x0a", RECEIVED)
    assert os.listdir(tmp_path / "tmp") == []


def test_build_feeds_bart_only_without_keys(monkeypatch):
    monkeypatch.delenv("API_511_KEY", raising=False)
    monkeypatch.delenv("API_ACT_TOKEN", raising=False)
    assert [f["name"] for f in logger.build_feeds()] == ["bart_tripupdates"]


def test_build_feeds_all_sources(monkeypatch):
    monkeypatch.setenv("API_511_KEY", "k511")
    monkeypatch.setenv("API_ACT_TOKEN", "TOKEN")
    monkeypatch.setenv("INTERVAL_ACT_S", "30")
    feeds = {f["name"]: f for f in logger.build_feeds()}
    assert set(feeds) == {"511_ac_tripupdates", "511_ac_vehiclepositions",
                          "act_tripupdates", "act_vehicles", "bart_tripupdates"}
    # The two AC Transit API feeds must never fire together.
    assert feeds["act_vehicles"]["offset"] - feeds["act_tripupdates"]["offset"] == 15
    assert "token=TOKEN" in feeds["act_tripupdates"]["url"]


def test_redact_hides_both_keys(monkeypatch):
    monkeypatch.setenv("API_511_KEY", "secret511")
    monkeypatch.setenv("API_ACT_TOKEN", "SECRETACT")
    text = logger.redact("url?api_key=secret511&x  token=SECRETACT")
    assert "secret511" not in text and "SECRETACT" not in text


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nFOO_TEST=from_file\nBAR_TEST='quoted'\n")
    monkeypatch.setenv("FOO_TEST", "from_env")
    monkeypatch.delenv("BAR_TEST", raising=False)
    logger.load_dotenv(str(env))
    assert os.environ["FOO_TEST"] == "from_env"
    assert os.environ["BAR_TEST"] == "quoted"
