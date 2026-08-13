# Changelog

All notable changes to this project are documented here.
Versioning follows [SemVer](https://semver.org/): patch for fixes, minor for
backward-compatible additions, major for breaking changes to the wire
protocol or CLI args.

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
