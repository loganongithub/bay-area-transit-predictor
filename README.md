# Bay Area transit arrival predictor

Goal: predict AC Transit (later BART) arrival times more accurately than the
official real-time predictions, scored with Caltrans' GTFS-RT
prediction-accuracy definition, and publish a live public scoreboard of both.

Status: **Phase 0 complete** (archiving 24/7 on EC2 → S3). **Phase 1**
(inferring actual arrival times) in progress.

## What's archived

| Feed | Source | Every |
|---|---|---|
| `act_tripupdates`, `act_vehicles` | AC Transit's own API (primary) | 30 s |
| `511_ac_tripupdates`, `511_ac_vehiclepositions` | 511.org (backup, 60 req/h cap) | 150 s |
| `bart_tripupdates` | BART | 30 s |
| `static/act`, `static/511_ac`, `static/bart` | Static GTFS schedules, saved when changed | daily check |

Each realtime feed joins only to its own source's static schedule (AC
Transit's API and 511 use different stop IDs).

## Layout

- `logger/logger.py`: polls the realtime feeds, writes gzip snapshots (stdlib only)
- `logger/fetch_static.py`: archives static GTFS when it changes (stdlib only)
- `deploy/`: EC2 provisioning, systemd units, S3 uploader, key loading. See `docs/DEPLOY.md`
- `tools/`: laptop-side analysis helpers (below)
- `tests/`: unit tests (`.venv\Scripts\python -m pytest -q tests`)
- `DECISIONS.md`: every design decision and why

## Working with the data (laptop)

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 1. Pull a day (UTC date) from S3
powershell -ExecutionPolicy Bypass -File .\tools\sync.ps1 -Day 2026-11-27
powershell -ExecutionPolicy Bypass -File .\tools\sync.ps1 -Day 2026-11-27 -Feeds static

# 2. Look at it
.venv\Scripts\python tools/inspect_feed.py trips data/raw/act_tripupdates
.venv\Scripts\python tools/inspect_feed.py trip <trip_id> data/raw/act_tripupdates --stop <stop_id>

# 3. Flatten to Parquet for pandas
.venv\Scripts\python tools/extract.py data/raw/act_vehicles/2026-11-27 data/raw/act_tripupdates/2026-11-27

# 4. Check the archive has no gaps
.venv\Scripts\python tools/coverage.py 2026-11-27
```

Server health: `powershell -ExecutionPolicy Bypass -File .\deploy\status.ps1 -Bucket logan-transit-raw-2026`
