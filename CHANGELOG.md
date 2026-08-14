# Changelog

All notable changes to this project are documented here.
Versioning follows [SemVer](https://semver.org/): patch for fixes, minor for
backward-compatible additions, major for breaking changes to the wire
protocol or CLI args.

## [4.8.0] - 2026-08-15

### Security — denial-of-service hardening
An authorised penetration test was run against a local instance. The
application layer (path traversal, directory listing, authentication,
certificate pinning, header injection, malformed input) held on the first
pass. The one weak class was the absence of resource limits, which is now
closed.

- **Receiver:** a pre-handshake socket timeout, a post-authentication idle
  timeout, a global concurrent-connection cap, and a per-source-address
  cap. Previously 200 connections that completed the TLS handshake and then
  sent nothing were all accepted and held indefinitely; now a single source
  obtains only a few slots and a stalled connection is dropped.
- **HTTP server:** a request-read timeout (defeats slowloris), a global
  worker cap, and a per-source cap. The per-source cap is the decisive
  control — verified that during a 100-connection flood from one address, a
  request from a different address is still served. Defeating the service
  now requires many source addresses, which is the reverse proxy's domain.
- Both accept loops are now the single hardened `receiver.serve`, so the
  server and the standalone receiver cannot drift apart.

### Changed
- **README and technical guide rewritten in an objective register.** Both
  described the system in a conversational, second-person style; they now
  read as technical documentation. The guide gains a security-model
  subsection covering the availability limits above and the controls for
  co-hosting a database and a second site. DEPLOYMENT.md gains a full
  co-hosting section: per-service accounts, loopback-only database binding,
  least-privilege roles, parameterised queries, and per-service systemd
  sandboxing.
- **Python formatted to the standard style** (ruff format) across
  `station/`, `pi/`, `tools/`, and `install.py`. Formatting only; the 49
  stress tests and 9 functional-attack checks pass unchanged.
- A closure-over-loop-variable pattern in `tools/fetch_weather.py` was
  bound explicitly. It was safe (the helper was only ever called within the
  same iteration) but fragile.

### Fixed
- **Power-chart tooltips showed the median value twice.** The reference
  line's dataset label already contains its value (e.g. "Median 66 mA"),
  and the tooltip appended the value again, producing "Median 66 mA 66 mA".
  The tooltip now shows a reference line's label alone. This is fixed in
  the shared chart helper (`common.js`) and the two custom tooltips
  (`power.html`, `sun.html`), so it covers the power charts and the sun
  daylight chart. The weather page used a different callback and was never
  affected.

## [4.7.0] - 2026-08-14

### Fixed
- **The moon overlapped the sunrise time on the Overview.** After dark the
  arc shows a dim circle for the sun below the horizon. It was positioned
  at an angle past sunrise, which put it at x=220 — directly on top of the
  sunrise label at x=210. Centred now, clear of both end labels, matching
  the sun page which had always done it correctly.
- The Power page heading said "Power Rails"; it is just Power.

### Changed
- **`weather.html` and `power.html` now use the shared helpers** instead of
  carrying their own copies. About 7,000 characters of duplicate code
  removed (weather 1138 → 1030 lines, power 777 → 671).

  Two of the ten helpers were *not* equivalent, which is why they were
  compared before being replaced rather than renamed blindly:
  `fmtAgo` returned upper case ("24S AGO") where `DORM.ago` returns lower,
  so the one call site now upper-cases explicitly and the display is
  unchanged; and `fmt` defaulted to 0 decimals against `DORM.fmt`'s 1, but
  every call site passes decimals explicitly so the default is unreachable.

  The refactor broke the Power page mid-way: a regex removing one function
  matched past its closing brace and swallowed the adjacent `fetchJSON`,
  whose call site was then undefined. Caught by testing the page rather
  than by reading the diff. Replaced with `DORM.getJSON`, which also
  retires an error message still pointing at the pre-restructure
  `rag-web-ui/simulate_demo_power.py`.

## [4.6.0] - 2026-08-14

Review and stress-test pass over the whole codebase. `tools/stress_test.py`
is new and runs 49 adversarial checks — malformed CSV, hostile filenames,
gap detection, downsampling, the solar algorithm, liveness classification
and end-to-end generation. All pass.

### Fixed
- **A UTF-8 BOM made an entire day's data vanish.** Excel writes a BOM when
  saving as "CSV UTF-8", which turned the first column name into
  `\ufefftimestamp`. The file then failed the "is this a weather log?"
  check and was skipped in silence — no error, no warning, the day simply
  absent from the site. Files are now read as `utf-8-sig`, and `find_field`
  strips a stray BOM as well.
- **The systemd unit granted ReadWritePaths to a directory that no longer
  exists** (`web/api`). Under `ProtectSystem=strict` a missing entry there
  makes systemd refuse to start the service, so a fresh deploy would have
  failed.
- The server pointed at `deploy/install_receiver.ps1` in an error message.
  That file was from the Windows-server era and has been removed;
  `install.py` replaces it.
- Reading counts reported in the log included synthetic gap markers, so
  "840 readings" could mean 839 measurements and one marker.

### Changed
- **Change detection no longer reads every byte of every file.** It hashed
  the full contents of the store on every poll: fine for a demo archive,
  untenable for a real one. At minute-resolution logging, five years of
  power telemetry is ~167 MB, and re-hashing it every two seconds took
  ~14 s — longer than the poll interval, so the watcher could never keep
  up. It now fingerprints path, size and modification time, which is
  proportional to the number of files rather than their size. All five
  change cases (append, in-place edit, new file, deleted file, no change)
  verified.
- Poll interval 2 s → 5 s. Weather arrives every few minutes and
  spectrograms every fifteen, so polling faster only cost CPU.
- **Generated JSON moved out of the source tree**: `web/api/` → `data/api/`,
  served through a URL mapping exactly as `data/fits/` already was. `web/`
  now contains only files a person wrote, and `.gitignore` no longer needs
  a rule carving generated output out of a source directory.
- `station/` is an explicit package with an `__init__.py`.

### Known and deliberately not changed
- `weather.html` and `power.html` still carry their own copies of helpers
  that also exist in `common.js`. They are functionally identical, so this
  is duplication rather than a defect — but a fix applied to one would not
  reach the other. Recorded here rather than refactored in the same pass as
  a set of behaviour fixes.

## [4.5.0] - 2026-08-14

### Fixed
- **Contact page had unstyled markup.** `.contact-intro` and `.ci-note`
  were written into the page without any matching CSS, so the explanatory
  text and the per-item notes rendered as unformatted default text.
- Placeholder values still awaiting real details now render as visibly
  placeholder (italic, muted) rather than looking like content.

### Changed
- **Reloading any page returns to the Overview.** Deliberately narrow: only
  a genuine reload triggers it, detected through the Navigation Timing API.
  Following a link, using back/forward, or opening a page fresh all behave
  normally — otherwise the other seven pages would be unreachable.

## [4.4.0] - 2026-08-14

### Changed
- **Contact is its own page.** It was a section at the foot of About, which
  buried the one thing a visitor is most likely to be looking for. It now
  carries enquiries, the station's formal details (code, coordinates,
  instrument, first light) and where to find both the data and the code.
- **The Station Log moved into About**, where it reads as that page's
  history rather than a separate destination.
- **Log posts open and close.** The list shows title, date and a one-line
  summary; clicking opens the article. Built on `<details>`, so it works
  with the keyboard and needs no JavaScript to track state, and opening a
  post writes its id into the URL so a single post can be linked to and
  arrives already open.
- "Sweeps" is now "spectrograms" throughout the interface. A *spectrograph*
  is the instrument; a *spectrogram* is the image it produces, which is
  what these files are — and what the navigation has always called them.
- Matej Marković's field is now given in English rather than Croatian.

### Fixed
- Three pages did not highlight their own navigation tab. An earlier bulk
  path rename had left `renderNav("web\fits.html")`, where `\f` is a
  form-feed escape in JavaScript, so the filename never matched and the
  active state silently never applied.

## [4.3.0] - 2026-08-14

### Changed
- **Technical guide brought up to date with v4.** It still described the
  original one-LAN, Windows-PC arrangement and the pre-restructure file
  layout. Rewrites the summary, the system overview and the rationale;
  updates every path; documents the four pages added since; and explains
  why the API is split into pre-computed ranges.
- New section **"How the data actually crosses, over SSH"** — a
  step-by-step account of the tunnel: what each field of `-L` means and
  which machine resolves it, the five hops a single row takes, why the
  data is encrypted twice and what the inner layer protects against, how
  the key is restricted in `authorized_keys`, and what happens when the
  link drops.
- Records six further defects, including the twelve-hour solar-time error,
  the over-broad `header` selector, and the three accessibility failures.

## [4.2.0] - 2026-08-14

### Changed
- **The Station Log is now written prose, not parsed release notes.** Ten
  posts, each with a summary and body: seven about the instrument, sourced
  from the e-Callisto network journal and status report #101; two about the
  software, compressing every version into one article on the backend and
  one on the frontend; and one on why students build the instruments.
  `CHANGELOG.md` remains the machine-readable version record but is no
  longer rendered as blog content.
- **About is now about the station**, not the observatory's wider history —
  what the instrument is, what it records, and why a network of them
  exists. The three people cards were levelled to a similar length.

### Fixed
- A bare `header { display: flex }` selector matched every `<header>` in
  the document, including the ones inside log articles, which laid post
  titles, dates and summaries out as overlapping flex columns. Scoped to
  `.wrap > header`.

## [4.1.0] - 2026-08-14

### Added
- **Station Log is its own page** (`blog.html`), sitting left of About in
  the nav rather than buried at the bottom of the About page.
- `docs/DESIGN-BASELINE.md` records the current appearance and the exact
  one-line command to restore it, tagged `design-v4-baseline`.

### Fixed — accessibility pass
Audited against the `ui-ux-pro-max` checklist. Four measured defects, all
fixed without changing the visual design:
- **Contrast.** `--text-faint` measured 2.12:1 against the panel surface —
  below even the 3:1 non-text minimum — and `--text-dim`, which carries
  body copy, measured 3.87:1 against a 4.5:1 requirement. Both lightened
  to exactly clear their threshold, keeping hue and saturation, so the
  palette looks the same and now passes.
- **Keyboard focus.** The focus ring computed to `outline-style: none` on
  buttons. Rebuilt on `:focus-visible` for every interactive element, with
  a backup box-shadow and a `forced-colors` variant.
- **Touch targets.** Controls were 28px tall against a 44px minimum. Under
  `@media (pointer: coarse)` they now grow to 44px; the dense desktop
  layout is untouched.
- **Horizontal scroll on mobile.** The seven-item nav was 560px wide at a
  375px viewport, forcing the whole page to scroll sideways. It now wraps.
- Reduced-motion coverage extended from the status dot to all animation
  and transitions.

## [4.0.0] - 2026-08-14

### Changed
- **Project restructured** into `station/` (server), `web/` (site),
  `tools/` (operational scripts), `deploy/`, `docs/` and `secrets/`. The
  old `rag-web-ui/` folder held both Python tooling and the web app, and
  `windows/` had stopped being Windows-specific. Dynamic module loading in
  the server is gone — the pieces are now plain imports in one package.
- **Ingest moved to an SSH tunnel.** The Pi and server sit on different
  networks with only port 22 open, so the Pi no longer connects to a data
  port across the internet: it opens an SSH tunnel and reaches a
  receiver bound to `127.0.0.1`. The TLS, certificate pinning and shared
  token all stay in place inside the tunnel.
- The landing page is now a station **overview**; the weather dashboard
  moved to `weather.html`.

### Added
- `install.py` — one installer for both machines (`server`, `pi`, `check`,
  `secrets`). Prints every privileged command before running it.
- **Pre-computed range files with downsampling.** The API writes one file
  per range and averages long ranges to ~1200 points, so a page load is
  bounded regardless of archive age. Weather 24H went from 141 KB (the
  whole archive) to 4.3 KB.
- **Overview page** with a live sun-position arc.
- **Status page**: evidence-based liveness (a stream is up if its data
  actually arrived) plus per-day coverage against expected sample counts.
- **Sun page**: NOAA solar ephemeris computed locally for the observatory's
  coordinates — no network call — with today's detail and the year curve.
- **About page**: the station's story, sourced biographies, live
  observation counters, a gallery hook, and a station log that merges the
  e-Callisto network journal with this project's changelog.
- `web/common.js` — shared front-end helpers, replacing five copies of the
  same time/format/chart code.
- Power page range set is now 1H/2H/6H/24H/7D/ALL, finer than weather's,
  because rail behaviour changes on the scale of minutes.

### Fixed
- Solar-time calculation used the Julian Day Number as if it were defined
  at midnight; it is defined at noon, which put every computed sunrise and
  sunset 12 hours out.

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
