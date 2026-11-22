# Bay Area transit arrival predictor

Goal: predict AC Transit (later BART) arrival times more accurately than the
official real-time predictions, scored with Caltrans' GTFS-RT
prediction-accuracy definition, and publish a live public scoreboard of both.

Status: **Phase 0**, archiving raw GTFS-Realtime snapshots 24/7.

- `logger/logger.py` polls the feeds and writes gzip snapshots to disk
- `deploy/` holds the EC2 setup: systemd units and the S3 uploader
- `docs/DEPLOY.md` has the step-by-step server setup
- `DECISIONS.md` records design decisions and the reasons for them

Run locally (BART only without a key):

```
cp .env.example .env   # add API_511_KEY for AC Transit
python logger/logger.py
```

Inspect snapshots (needs the dev dependencies):

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python tools/inspect_feed.py trips data/raw
.venv\Scripts\python tools/inspect_feed.py trip <trip_id> data/raw --stop <stop_id>
```
