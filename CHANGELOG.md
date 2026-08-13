# Changelog

All notable changes to this project are documented here.
Versioning follows [SemVer](https://semver.org/): patch for fixes, minor for
backward-compatible additions, major for breaking changes to the wire
protocol or CLI args.

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
