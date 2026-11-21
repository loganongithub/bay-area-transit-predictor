# Decisions

One line per decision: what was chosen, over what, and why.

## Phase 0: logger

- 2026-11-21 Archive raw protobuf bytes, not parsed rows: a parser bug can be fixed and re-run later; lost bytes can't be recovered.
- 2026-11-21 Log BART from day one even though analysis starts with AC Transit: it costs ~1 GB/month and no API quota, and missed history is gone for good.
- 2026-11-21 Logger is stdlib-only Python under systemd, not Docker/k8s yet: fewest moving parts for the one component that must never stop; it moves to a container in phase 3.
- 2026-11-21 Logger runs on its own small EC2 instance, separate from the future k3s cluster: experimenting with Kubernetes must never be able to interrupt data collection.
- 2026-11-21 Write to local disk, then `aws s3 mv` every 10 min, over uploading inside the logger: an S3 outage can't block or crash polling, and files only leave disk after a successful upload.
- 2026-11-21 Atomic write (tmp file + rename): the uploader can never ship a half-written snapshot.
- 2026-11-21 Poll each 511 feed every 150 s (48 req/h) under the 60 req/h default: leaves headroom so we never hit 429s; drop to 30 s once the limit is raised.
- 2026-11-21 Target 30 s polling long-term: the Caltrans metric bins predictions per minute and counts a minute "complete" only with ≥2 predictions, so 60 s polling can't measure completeness.
- 2026-11-21 Timestamp files by our receive time (UTC), keep feed header timestamp inside the bytes: lets us detect stale/duplicate feed snapshots later.
- 2026-11-21 EC2 instance role instead of access keys on the server: no long-lived secret to leak; permissions limited to PutObject on one bucket prefix.
- 2026-11-21 Session Manager instead of SSH, no inbound ports: nothing on the server is reachable from the internet, and no key files to manage on Windows.
- 2026-11-21 t4g.micro (ARM) over t3.micro: cheaper for the same job; the logger has no native dependencies, so ARM doesn't matter.
