# Changelog

All notable changes to this project are documented here.
Versioning follows [SemVer](https://semver.org/): patch for fixes, minor for
backward-compatible additions, major for breaking changes to the wire
protocol or CLI args.

## [3.3.0] - 2026-08-13

### Added
- **Power page** (`rag-web-ui/web/power.html`) and a third nav tab,
  monitoring the station's four powered subsystems: dew heater, LNA, the
  CALLISTO receiver, and the BME environmental sensor. Shows total draw,
  per-rail volts/milliamps/watts, a per-rail current chart and a shared
  voltage chart, over the same 24H/7D/30D/all range selector.
- Power telemetry store at `data/power/YYYY/MM/DD/`. The receiver routes
  any incoming CSV whose filename suggests power telemetry (`power`,
  `psu`, `rail`, `volt`, `current`) to it, so the Pi can ship rails and
  weather over the same authenticated connection.
- `generate_power()` in the dashboard generator, reusing the existing
  timestamp normalisation and data-gap detection.
- `rag-web-ui/simulate_demo_power.py` — SIMULATED rail telemetry for
  development. Unlike the weather fetcher there is no real external source
  for this station's own rails, so these numbers are invented; the heater
  duty cycle is driven off the real weather store (it runs as air
  temperature closes on the dew point) so the trace stays physically
  coherent with the weather page.

### Notes
- Watts are derived from volts x milliamps rather than logged, so the
  figure can never contradict the two measurements behind it.
- Each rail gets its own current chart rather than four series sharing one
  axis: the heater peaks near 1800 mA and the BME sits around 3 mA, so a
  shared scale would flatten three of the four into the baseline.

## [3.2.0] - 2026-08-13

### Changed
- **Both stores are now laid out year/month/day** (`data/weather/2026/07/08/…`,
  `data/fits/2025/09/09/…`), mirroring the e-Callisto archive's own
  structure. A flat folder becomes unusable after a few thousand files;
  a nested tree can be browsed, backed up and pruned per period. The date
  is parsed from the filename; files without one go to `undated/` rather
  than being filed under a guessed date.
- `migrate_store_layout.py` moves an existing store into the new layout.
  Dry run by default, never overwrites or deletes.
- The demo weather fetcher now writes one file per day, matching the real
  station's daily rotation instead of one monolithic CSV.
- Contrast slider is finer: 0.02 steps over 0.30–3.00, shown to two
  decimals (was 0.1 steps).

### Fixed
- **Spectrograms rendered upside down.** The renderer assumed CALLISTO
  writes row 0 at the low-frequency end and flipped to compensate; it
  writes row 0 at the *high*-frequency end, so the flip introduced the
  error. The axis labels were drawn independently and were correct, which
  made the wrong image look plausible — 400 MHz printed at the top with
  45 MHz data beneath it. Orientation is now read from the file's
  frequency table rather than assumed.

## [3.1.0] - 2026-08-13

### Added
- **Spectrograms now ship from the Pi.** New `BLOB` wire message carries
  binary files over the same authenticated TLS connection as the weather
  rows; the receiver files them into per-day folders taken from the
  filename's embedded date. The server is now the single store for
  everything the station produces.
- **6-hour failsafe on both sides.** The server rebuilds all derived files
  from disk on a timer regardless of change detection (and immediately if
  an output file has gone missing), and sweeps up `.tmp`/`.part` debris
  from interrupted writes. The Pi periodically clears its delivery state so
  a server-side loss re-sends automatically. Both intervals are
  configurable; `0` disables.
- **Fully annotated spectrogram plots**: frequency axis (MHz), time axis
  (UT clock), title, and a labelled colorbar with units — drawn into the
  canvas at device pixel ratio so exported images are self-describing.
- **Year → month → day navigation** for the FITS archive, each level
  showing how many recording days it contains.
- **Four background-subtraction modes**: per-channel mean (the published
  e-Callisto standard, now the default), per-channel median (robust),
  global constant, and none.
- **CALLISTO palette** as the default, alongside the perceptually-ordered
  Inferno/Viridis/Grayscale ramps.
- **Median reference line** on every weather chart — dashed and neutral
  (an annotation, not a fifth series), labelled in the legend with its
  value.

### Fixed
- A rejected `FILE`/`BLOB` filename left its payload unread, desynchronising
  the connection so every subsequent message parsed as garbage.
- Spectrogram title overlapped the processing label at normal widths.

## [3.0.0] - 2026-08-13

### Added
- `server.py` — a single self-contained station server replacing the old
  three-process setup. Runs the TLS ingest, the JSON generator and the web
  server in one command, storing everything under `data/` so the site works
  with the Pi offline.
- Spectrogram viewer (`rag-web-ui/web/fits.html`): decodes real CALLISTO
  `.fit.gz` files entirely in the browser (gunzip → FITS parse → canvas),
  with day/sweep browsing, three perceptually-ordered palettes, per-channel
  median background subtraction and percentile contrast scaling. Days with
  no observations are shown explicitly as "not recording" rather than
  hidden.
- `fetch_visnjan_fits.py` — downloads real spectrograms from the
  e-Callisto archive, searching backwards for days that actually have data.
- `fetch_demo_weather.py` — pulls real observed weather for Višnjan from
  Open-Meteo, replacing the previous synthetic series. `--simulate-outage`
  deliberately drops days to exercise gap rendering.
- Dew point (Magnus formula), humidity and pressure charts.
- Range selector (24H / 7D / 30D / all) filtering client-side, with stat
  labels that relabel themselves so figures can't be misread as another
  period.
- Data-gap detection: a gap over 3× the series' own cadence inserts a null
  marker so charts break instead of interpolating across downtime.
- Both UTC and local time everywhere, plus a live "updated Ns ago".
- Site navigation between the weather and spectrogram pages.

### Changed
- **Renamed to DORM** throughout (was ARRAY-7).
- `generate_dashboard_api.py` now emits full history by default
  (`--history-hours 0`); range filtering moved to the browser.
- Local store moved to `data/weather/` and `data/fits/`.
- `.gitignore` now excludes all collected data — the repo carries the code
  that pulls and displays data, never the data itself.

### Fixed
- FITS header parser split values at `/` to strip comments, truncating
  quoted dates like `'2025/09/09'` to `2025`.
- Frequency axis was read from `CRVAL2`/`CDELT2`, reporting ~1–200 MHz for
  an instrument that actually covers 45–404 MHz. Now read from the file's
  BINTABLE frequency column, with the source labelled in the UI.
- Spectrogram contrast scaled from min/max, so one RFI spike flattened the
  whole image to a single colour. Now percentile-based with a gamma slider.

## [2.1.1] - 2026-08-13

### Fixed
- `rag-web-ui`: Chart.js was loaded from a CDN (`cdnjs.cloudflare.com`),
  so the temperature chart silently fell back to "unavailable" wherever
  that CDN wasn't reachable. Vendored Chart.js v4.4.4 locally
  (`rag-web-ui/web/vendor/chart.js`) instead -- the dashboard no longer
  depends on internet access to render.
- Chart mark specs brought in line with dataviz best practice: line
  weight 1.5px -> 2px, area-fill wash reduced from a fairly saturated 28%
  opacity to a proper ~10% wash, and the hover-point ring width made
  explicit (2px) instead of relying on Chart.js's default.

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
