# Changelog

All notable changes to this project are documented here.
Versioning follows [SemVer](https://semver.org/): patch for fixes, minor for
backward-compatible additions, major for breaking changes to the wire
protocol or CLI args.

## [2.1.0] - 2026-08-13

### Added
- `rag-web-ui/` — a browser dashboard showing live weather conditions.
  `generate_dashboard_api.py` watches `windows/incoming_logs/*.csv` (read
  only -- it never touches `sender.py` or `receiver.py`) and regenerates
  the static `web/api/weather/latest.json` / `history.json` the dashboard
  reads via `fetch()`. Only CSVs with a `timestamp` column are picked up;
  files without one are skipped rather than raising, and naive
  `YYYY-MM-DD HH:MM:SS` timestamps are normalized to UTC ISO-8601 so the
  browser doesn't misread them as local time.
- Verified end-to-end against real shipped data (including stray
  non-weather CSVs already sitting in `incoming_logs/`, confirmed skipped
  without error).

## [2.0.2] - 2026-08-13

### Fixed
- `windows/receiver.py`: the atomic rename that finalizes a `FILE`
  message's write (`<file>.csv.tmp` -> `<file>.csv`) could transiently
  fail on Windows with `PermissionError: [WinError 5] Access is denied`
  (e.g. antivirus/indexing briefly holding the target open without
  `FILE_SHARE_DELETE`), stranding the `.tmp` file and silently dropping
  that update -- there's no application-level ack, so the sender had
  already considered it delivered. Now retries the rename up to 5 times
  with a short delay before giving up.

## [2.0.1] - 2026-08-13

### Fixed
- `pi/setup_pi.sh`: `config.json` and the state directory were created via
  `sudo` (root-owned), then `config.json` was `chmod 600`'d -- but the
  systemd service runs as the installing user, not root, so it could
  never open its own config file (`PermissionError: [Errno 13] Permission
  denied`, crash-looping on every restart). Both are now `chown`'d to that
  user before being locked down.

## [2.0.0] - 2026-08-13

### Changed
- **Breaking wire-protocol change.** The old design only ever compared a
  file's current size against a last-seen byte offset, so it could detect
  appends but was blind to any change to already-shipped content (e.g. a
  weather-log writer correcting an already-written row in place) — the
  edited bytes sat before the tracked offset and were never re-examined.
- `sender.py` now hashes each watched file's full content every poll and
  compares it against what was last sent. A pure append (previously-sent
  content unchanged, new bytes only at the end) still ships as individual
  `ROW\t<filename>\t<row>` messages, with state persisted after each one
  so a mid-stream disconnect can't duplicate or drop a row. Anything else
  — an in-place edit, or the file getting shorter (same-name rotation or
  truncation) — now ships the entire current file as one
  `FILE\t<filename>\t<byte length>` message, and `receiver.py` replaces
  its local copy of that file wholesale (atomic write + rename).
- Old `sender.py` and `receiver.py` builds are not wire-compatible with
  this version — the line format changed from `<filename>\t<row>` to
  `ROW\t<filename>\t<row>`, plus the new `FILE` message type.

### Verified
- Local rehearsal covering: fresh backfill of a pre-existing file, pure
  append, in-place edit of an already-shipped row, same-name file
  truncation/rotation, and a sender restart mid-append — all produced a
  byte-identical result on the receiver with no duplicate or dropped rows.

## [1.1.0] - 2026-08-13

### Confirmed
- End-to-end test against a real Raspberry Pi test device on the LAN:
  `sender.py` connected, verified the pinned certificate fingerprint,
  authenticated, and shipped both pre-existing and live-appended CSV rows
  to `receiver.py` within seconds. Received file matched the source
  exactly.

### Fixed
- `windows/receiver.py`: a connection aborted during the TLS handshake
  (`OSError`/`ConnectionAbortedError`) was not caught — only `ssl.SSLError`
  was — crashing that connection's thread with an unhandled traceback
  instead of logging it and continuing to serve other connections.

## [1.0.0] - 2026-08-13

### Added
- `pi/sender.py` — dependency-free TLS client. Watches a directory of
  `*.csv` files and ships each newly appended row to the receiver as soon
  as it's written. Pinned to the receiver's exact certificate fingerprint,
  authenticated with a shared-secret token. Tracks per-file byte offsets in
  a state file so restarts never duplicate or drop rows. Reconnects with
  exponential backoff.
- `windows/receiver.py` — dependency-free TLS server. Authenticates each
  connection against the shared token and appends incoming rows to matching
  local files under `incoming_logs/`. Rejects unsafe/path-traversal
  filenames.
- `windows/install_and_run.ps1` — generates the self-signed cert/key and
  shared token, prints the fingerprint to hand to the Pi side, adds a
  Private-profile firewall rule for TCP 9443, and registers a scheduled
  task that runs the receiver at logon and restarts it on failure.
- `pi/setup_pi.sh` — installs `sender.py` and a filled-in `config.json` as
  a systemd service (`weather-shipper.service`) on the Pi.
