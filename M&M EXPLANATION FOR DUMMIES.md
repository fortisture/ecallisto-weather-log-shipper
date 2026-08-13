# M&M Explanation For Dummies

**A complete, plain-English explanation of how this whole thing works and why.**

No prior knowledge assumed. If you know what a file and a network are, you
can read this.

---

## Table of contents

1. [The one-paragraph version](#1-the-one-paragraph-version)
2. [The problem this solves](#2-the-problem-this-solves)
3. [The big picture](#3-the-big-picture)
4. [Part one: the sender (on the Pi)](#4-part-one-the-sender-on-the-pi)
5. [Part two: the receiver (on the PC)](#5-part-two-the-receiver-on-the-pc)
6. [Part three: the security model](#6-part-three-the-security-model)
7. [Part four: the server that ties it together](#7-part-four-the-server-that-ties-it-together)
8. [Part five: turning CSV into something a webpage can use](#8-part-five-turning-csv-into-something-a-webpage-can-use)
9. [Part six: the weather dashboard](#9-part-six-the-weather-dashboard)
10. [Part seven: the spectrogram viewer](#10-part-seven-the-spectrogram-viewer)
11. [Part eight: the helper scripts](#11-part-eight-the-helper-scripts)
12. [How to run the whole thing](#12-how-to-run-the-whole-thing)
13. [Every design decision, and why](#13-every-design-decision-and-why)
14. [Bugs found and fixed along the way](#14-bugs-found-and-fixed-along-the-way)
15. [Things you should know that might bite you](#15-things-you-should-know-that-might-bite-you)

---

## 1. The one-paragraph version

A Raspberry Pi at the Višnjan observatory records weather readings into CSV
files and solar radio spectrograms into FITS files. A program on the Pi
(`sender.py`) watches those weather files and, the moment a new line is
written, sends that line over an encrypted connection to your Windows PC. A
program on the PC (`server.py`) receives those lines, saves them to its own
local copy, converts them into a format a webpage can read, and serves a
website that displays it all as charts. A second page on that website reads
the FITS spectrogram files and draws them as images. The PC keeps its own
copy of everything, so the website works even when the Pi is switched off.

---

## 2. The problem this solves

The Pi is a small computer sitting at the observatory. It is recording data
constantly. Three things could go wrong if you just left the data there:

1. **SD cards die.** Raspberry Pis store data on SD cards, which wear out.
   Years of observations on one card is a single point of failure.
2. **You can't see it.** To look at your data you'd have to log into the Pi
   every time.
3. **The Pi is slow.** Asking it to also serve a website with charts is
   asking a lot from a small machine that has a real job already.

So: copy the data off the Pi continuously, onto a machine with a real disk
and a real screen, and do all the presentation work there.

The key word is **continuously**. Not "once a day," not "when I remember."
Every row, seconds after it's recorded.

---

## 3. The big picture

```
   RASPBERRY PI (at the observatory)         WINDOWS PC (your machine)
   ─────────────────────────────────         ─────────────────────────

   weather logger writes CSV rows
              │
              ▼
   ┌──────────────────────┐                 ┌────────────────────────┐
   │  pi/sender.py        │                 │  server.py             │
   │                      │   encrypted     │                        │
   │  • watches the files │───────────────► │  • receives rows       │
   │  • notices changes   │   TLS over      │  • saves to data/      │
   │  • sends what's new  │   your LAN      │  • rebuilds the JSON   │
   │  • remembers where   │   port 9443     │  • serves the website  │
   │    it got to         │                 │                        │
   └──────────────────────┘                 └───────────┬────────────┘
                                                        │
                                                        ▼
                                            ┌────────────────────────┐
                                            │  your web browser      │
                                            │  localhost:8090        │
                                            │                        │
                                            │  • weather charts      │
                                            │  • spectrogram viewer  │
                                            └────────────────────────┘
```

Four ideas hold this together:

- **The Pi only pushes.** It never waits to be asked. The instant a row
  appears, it goes out.
- **The PC keeps its own copy.** Once data is on the PC it lives in
  `data/`, independent of the Pi. Unplug the Pi and the website still works.
- **The website reads files, not a database.** No database to install,
  configure, back up, or corrupt.
- **Nothing needs installing.** Both programs use only what comes with
  Python. No `pip install`. Nothing to break on an OS upgrade.

---

## 4. Part one: the sender (on the Pi)

**File: `pi/sender.py`**

Its whole job: notice when a weather CSV changes, and send the change.

### How it notices changes

Every second it looks at each `*.csv` file in the watched folder and asks:
"is this the same as when I last looked?"

The naive way to answer is to check the file size. Bigger = new data. That
works right up until someone *edits* a row that's already there — the size
doesn't change, or changes in a way that doesn't tell you what happened. A
size check would miss the edit entirely and you'd never know your copy had
gone stale.

So instead it uses a **hash**: a short fingerprint calculated from the
file's contents. Change one character anywhere and the fingerprint changes
completely. The sender remembers the fingerprint of exactly what it has
already sent, and each second it recalculates and compares.

That comparison has three possible outcomes:

| What it finds | What it means | What it does |
|---|---|---|
| Fingerprint unchanged | Nothing happened | Nothing |
| The file *starts with* what was already sent, and has extra on the end | Normal case: new rows appended | Send only the new rows |
| Anything else | A row was edited, or the file got shorter | Send the **whole file** |

That second case is the common one and it's cheap — a new row is a few
dozen bytes. The third case is the safety net: if anything about the
already-sent portion changed, the sender stops trying to be clever and
re-sends everything, and the receiver replaces its copy wholesale.

### How it never loses or duplicates a row

The sender keeps a small file (`state.json`) recording how far it has got
in each file. It updates that record **after every single row it sends**,
not at the end of a batch.

Why that matters: suppose the network drops halfway through sending 50
rows. If the record were only written at the end, the sender would restart
and re-send all 50, and you'd get duplicates. If it were written at the
start, the interrupted rows would be skipped forever. Writing after each
row means the worst case is re-sending the single row that was in flight.

The record is written using a trick called an **atomic write**: write to a
temporary file first, then rename it over the real one. Renaming is
instantaneous at the operating-system level — there is no moment where the
file is half-written. Without this, losing power mid-write would leave a
corrupted record and the sender wouldn't know where it was.

### When the network breaks

It reconnects, with **exponential backoff**: wait 1 second, then 2, 4, 8,
16, capped at 30. If the PC is off for an hour, the Pi isn't hammering the
network thousands of times — it's checking calmly every 30 seconds. When
the PC comes back, everything buffered up since the outage is sent.

---

## 5. Part two: the receiver (on the PC)

**File: `windows/receiver.py`**

It listens on port 9443 and understands exactly two kinds of message:

- **`ROW`** — one new line for one file. Append it.
- **`FILE`** — a whole file's contents. Replace the local copy entirely.

Each incoming connection is handled on its own **thread** (an independent
line of execution), so a slow or stuck connection can't block others.

### Two things it does carefully

**It doesn't trust the filename it's given.** A filename is data arriving
over a network, and data can be malicious. Something could send
`../../Windows/System32/something.csv` and try to make the receiver write
outside its folder. So the receiver strips the filename down to its bare
last component and requires it to end in `.csv`. Anything else is rejected
and logged.

**It replaces files atomically.** A `FILE` message writes to a temporary
file, then renames it into place — so the website can never catch a file
half-written and read garbage.

On Windows that rename can occasionally fail because something else has the
file open (antivirus, search indexing, or you looking at it in an editor).
When that happens the receiver retries five times over about a second. This
is not theoretical — it happened during testing, because the file was open
in an editor at the time.

---

## 6. Part three: the security model

The data crosses your local network. Three independent protections, each
covering a different failure:

### 1. TLS encryption — nobody can read it

The same technology as the padlock in your browser. Anyone capturing the
traffic sees scrambled bytes.

### 2. Certificate pinning — the Pi can't be tricked

Encryption alone doesn't prove *who* you're talking to. Normally your
browser checks a certificate against a list of trusted authorities. On a
private network there's no such authority, so this project does something
stricter and simpler.

The PC generates a certificate. Its **fingerprint** (a hash of that
certificate) is written into the Pi's config. Every time the Pi connects,
it recomputes the fingerprint of the certificate it was handed and compares
it to the expected value. Not "is this signed by someone reputable" — but
"is this the *exact* certificate I was told to expect." One byte different
and the Pi refuses to send anything.

This defeats an attacker who redirects traffic to their own machine: they
can present a valid-looking certificate, but not *your* certificate.

### 3. A shared secret token — only your Pi can connect

A long random password, generated once, known to both sides. Every
connection must present it before sending data. Wrong token → `DENY` and
disconnect.

The comparison uses `hmac.compare_digest` rather than `==`. A normal string
comparison stops at the first wrong character, so a wrong guess starting
with the right letter takes microscopically longer to reject. Measure
enough attempts and you can extract the token one character at a time. This
is a **timing attack**, and `compare_digest` defeats it by always taking
the same amount of time.

### What it deliberately does not do

No internet exposure. No port forwarding. No cloud. The firewall rule is
scoped to the local network only. The system is unreachable from outside
your house.

**The secrets never go in the git repository.** `.gitignore` excludes
`*.pem`, `token.txt`, `fingerprint.txt`, and `config.json`. The published
code is useless to anyone without your keys.

---

## 7. Part four: the server that ties it together

**File: `server.py`** — the single command that runs the whole ground station.

Originally this was three separate programs in three terminal windows. Now
one process runs three threads:

1. **The receiver thread** — accepts connections from the Pi, writes into
   `data/weather/`.
2. **The watcher thread** — every 2 seconds, checks whether `data/` has
   changed. If so, rebuilds the JSON files the website reads and re-indexes
   the FITS store.
3. **The web server** (the main thread) — serves the website on port 8090.

The watcher only does work when something actually changed, by comparing a
fingerprint of the folder contents. Otherwise it would rewrite the same
files hundreds of times an hour for no reason.

The watcher is wrapped in a catch-all error handler. If something
unexpected breaks in it, it logs the error and keeps going, rather than
silently dying and leaving you with a website that quietly stops updating —
the worst kind of failure, because everything *looks* fine.

The web server also maps URLs starting `/fits/` onto the local FITS folder,
with the same path-traversal protection as the receiver.

---

## 8. Part five: turning CSV into something a webpage can use

**File: `rag-web-ui/generate_dashboard_api.py`**

Browsers can't easily read CSV. This converts it into JSON, which they can.
It writes two files:

- **`latest.json`** — the single most recent reading.
- **`history.json`** — every reading it has.

### It's deliberately forgiving about column names

It looks for a `timestamp` column, accepting `time` or `datetime` too, and
matching case-insensitively. A file without any recognisable timestamp
column is **skipped silently** rather than crashing.

This matters more than it sounds. During testing there were junk files in
the data folder from earlier experiments. A stricter program would have
crashed on them and taken the whole dashboard down. Instead they were
ignored and everything else worked.

### It fixes the timezone problem

The CSV timestamps look like `2026-08-13 06:00:00` — no timezone marker.
The station records in UTC, but a browser reading that string assumes
**local time**. In Croatia that's a 2-hour error: every reading silently
shifted, every chart subtly wrong, no error message anywhere.

So every timestamp is converted to the unambiguous form
`2026-08-13T06:00:00Z` — the `Z` means UTC, and every browser on earth
agrees on what that means.

### It calculates dew point

Dew point isn't recorded by the station; it's calculated from temperature
and humidity using the **Magnus formula**, the standard meteorological
approximation. It's the temperature at which air becomes saturated — a
better comfort indicator than humidity alone, and it tells you when dew or
fog will form on the equipment.

If either input is missing, or humidity is outside a physically sensible
range, it returns nothing rather than a fabricated number.

### It marks gaps in the data

If the station is offline for two days, the readings either side are still
consecutive *in the file*. A chart would draw a straight line between them —
inventing two days of data that was never measured.

So the program measures the normal gap between readings, and wherever it
finds a gap more than 3× that, it inserts a marker row with no values. The
chart draws a **break** there instead of a line. Downtime looks like
downtime.

The threshold is relative to the file's own rhythm, so it works whether the
station logs every 10 seconds or every hour.

---

## 9. Part six: the weather dashboard

**Files: `rag-web-ui/web/index.html`, `styles.css`**

Four charts (temperature, dew point, humidity, pressure), current
conditions, and a range selector.

### The range selector

`24H / 7D / 30D / ALL`. The server sends *all* the history and the browser
filters it. One download, instant switching, no server round-trip per click.

Everything recalculates on switch — the min/avg/max tiles even relabel
themselves ("24H MIN" becomes "30D MIN"), so the numbers can never be
mistaken for a different period than they describe.

### Downsampling

30 days of hourly data is manageable, but at the Pi's real 10-second
logging rate it's hundreds of thousands of points — more points than the
chart has pixels. Drawing them all is slow and looks identical.

So when there are more than 600 points, it takes every Nth. Crucially it
**always keeps the last point**, because that's the current reading and
dropping it would misreport the station's present state.

### Both UTC and local time

Everything is shown twice: UTC (what science uses, unambiguous) and your
local time with the zone name (CEST), so "07:50" is never ambiguous. Plus
"UPDATED 11S AGO", ticking every second — so a frozen page is obvious.
Without it, a dashboard that stopped updating looks exactly like a
dashboard where nothing is happening.

### The colours were verified, not chosen by eye

The four chart colours were checked with a validator against five criteria,
including how they look to people with colour-blindness. **The first
palette I picked failed**: violet and blue were nearly identical to someone
with protanopia (a numerical difference of 1.9, where 8 is the minimum).
The current colours pass every check. This is measurable, so it was
measured rather than guessed.

Chart.js is stored **locally** in `web/vendor/`. Originally it loaded from
the internet, which meant no charts if the machine was offline — a bad
property for equipment at a remote observatory.

---

## 10. Part seven: the spectrogram viewer

**File: `rag-web-ui/web/fits.html`**

This displays actual solar radio observations from the Višnjan CALLISTO
instrument.

### What a spectrogram is

The instrument sweeps across radio frequencies (45–404 MHz here), measuring
signal strength, four times a second. Stack those sweeps side by side and
you get an image: **time across, frequency up, brightness = signal
strength**. Solar flares appear as bright streaks.

Each file covers 15 minutes: 3600 time samples × 200 frequency channels.

### The browser decodes it directly

No server-side image conversion. The browser:

1. Downloads the `.fit.gz` file
2. Decompresses it (`DecompressionStream`, built into modern browsers)
3. Parses the FITS format
4. Draws it onto a canvas

FITS is an astronomy file format from the 1970s. The header is a series of
80-character lines — a fixed width chosen because that's how many columns a
punched card had. The format is still used because it's simple and
self-describing.

### Background subtraction — why the image would otherwise be useless

Each frequency channel has its own baseline: different antenna gain,
different local interference. Raw, the image is horizontal stripes and you
cannot see anything real.

So for each frequency row, the viewer calculates the **median** value and
subtracts it. Median, not average, deliberately — a bright burst would drag
an average upward and partly erase itself. A median ignores outliers, which
is exactly what you want when the outliers are the signal you're looking
for.

### Contrast scaling

The colour range is set from **percentiles** (1st to 99.5th) rather than
minimum and maximum. A single interference spike would otherwise stretch
the scale so far that everything real collapsed into one flat colour —
which is exactly what happened on the first attempt.

The contrast slider applies a **gamma curve**, which brightens faint
detail without blowing out bright features (a simple multiplier saturates
everything instead).

Colour palettes are **perceptually ordered**: brightness increases
consistently with signal strength, so a brighter pixel always means a
stronger signal. Rainbow palettes fail this and create features that aren't
there.

### The frequency axis — a real correctness trap

The header says the frequencies run 200 MHz down in 1 MHz steps, which
gives 1–200 MHz. That is **wrong** — the instrument covers 45–404 MHz. The
header even carries a warning that this value "may be rounded" and that the
axis "may not be regular."

The real frequency list is stored separately, in a table after the image
data. The viewer reads that table and shows the true range — and labels
which source it used, so you always know whether you're seeing measured
values or a header approximation.

Had I trusted the header, the page would have displayed a confident,
official-looking, completely wrong frequency range.

### Recording vs not recording

The day selector shows every day in the archive's span, with days that have
no data shown greyed out and labelled "no data — station not recording."
Absence of data is itself information: it tells you the instrument was
down, which is something you want to see rather than have hidden.

---

## 11. Part eight: the helper scripts

- **`rag-web-ui/fetch_visnjan_fits.py`** — downloads real spectrograms from
  the central e-Callisto archive. Skips files it already has (the archive
  never changes, so re-downloading is pure waste) and searches *backwards*
  for days that actually contain data, because the station has long gaps.

- **`rag-web-ui/fetch_demo_weather.py`** — downloads real measured weather
  for Višnjan's coordinates from Open-Meteo, and writes it in the station's
  CSV format. This is **demo data for developing the dashboard** when the
  Pi isn't connected. It's real measured weather, not simulated. It can also
  deliberately delete a couple of days (`--simulate-outage`) to test that
  gap rendering works.

- **`pi/setup_pi.sh`** — installs the sender on the Pi as a *service*, so it
  starts automatically at boot and restarts if it crashes.

- **`windows/install_and_run.ps1`** — generates the certificate and token,
  opens the firewall port, registers the receiver to start at login.

---

## 12. How to run the whole thing

**On the PC (once):**

```powershell
cd windows
.\install_and_run.ps1
```

Generates the certificate and token and prints the fingerprint. Copy the
token and fingerprint — the Pi needs them.

**On the PC (every time):**

```bash
python server.py
```

Then open `http://127.0.0.1:8090/`.

**On the Pi (once):**

Copy `sender.py`, `setup_pi.sh` and a filled-in `config.json`, then:

```bash
chmod +x setup_pi.sh
./setup_pi.sh /path/to/weather_logs config.json
```

**To load demo data without a Pi:**

```bash
python rag-web-ui/fetch_demo_weather.py --days 35
python rag-web-ui/fetch_visnjan_fits.py --days 3
```

---

## 13. Every design decision, and why

| Decision | Why |
|---|---|
| Standard library only | Nothing to install, nothing to break on upgrade, works on a fresh Pi |
| Push from the Pi, not pull from the PC | Data arrives in seconds instead of waiting for the next poll |
| Hash comparison, not file size | Size can't detect an edit to an existing row |
| Save progress after every row | Worst case on a crash is one repeated row, never a gap |
| Atomic writes everywhere | A crash can't leave a half-written file |
| Certificate pinning over normal TLS validation | No certificate authority exists on a home network |
| Constant-time token comparison | Ordinary comparison leaks the token through timing |
| Whole-file resend when anything changes | Simple and always correct; the fast path handles the common case |
| Files instead of a database | Nothing to install or corrupt; readable with any text editor |
| Server sends all history, browser filters | One download, instant range switching |
| Downsample above 600 points | More points than pixels is wasted work |
| Explicit gap markers | Otherwise the chart invents data that never existed |
| Chart.js stored locally | An observatory may not have reliable internet |
| Colour palette validated with a tool | Colour-blind safety is measurable, so it was measured |
| Median for background subtraction | An average would be dragged up by the very bursts you want to see |
| Percentile contrast scaling | One interference spike would otherwise flatten the whole image |
| Read frequencies from the table, not the header | The header value is wrong by a factor of two |
| Data excluded from git | Large, reproducible, and not source code |

---

## 14. Bugs found and fixed along the way

Real bugs, found by testing rather than reading:

1. **Receiver crashed on an aborted handshake.** A connection dropping
   mid-handshake raised an error type that wasn't being caught, killing that
   connection's thread with a raw stack trace. (v2.0.1)

2. **The service couldn't read its own config.** The installer created
   `config.json` as root and locked it to root-only, but the service runs as
   a normal user — so it crash-looped on startup, permanently. (v2.0.1)

3. **File replacement failed when a file was open.** On Windows the rename
   was rejected because the file was open in an editor. Now retried. (v2.0.2)

4. **Edits to existing rows were invisible.** The original design only ever
   looked at data past a recorded position, so a corrected row was never
   noticed. This is what forced the redesign to hash comparison. (v2.0.0)

5. **First colour palette failed colour-blindness checks.** Caught by
   running the validator instead of trusting my eyes.

6. **Spectrogram rendered as a flat yellow band.** Contrast scaling was
   saturating everything.

7. **Dates lost everything after the first slash.** The FITS header parser
   split values at `/` to strip comments — but `'2025/09/09'` contains
   slashes, so it became `2025`.

8. **Frequency axis was wrong by ~2×.** Trusted the header instead of the
   frequency table.

---

## 15. Things you should know that might bite you

- **The Višnjan CALLISTO station appears to have been offline since
  September 2025.** The archive has data for 2024-08-11, mid-2025, and
  2025-08-01 → 2025-09-09, then nothing on any 2026 date checked. The three
  days stored locally are the most recent that exist.

- **The weather data currently displayed is real but not from your Pi.**
  It's measured data for Višnjan's coordinates from Open-Meteo, standing in
  until the real station is connected. It includes a deliberate 2-day gap to
  demonstrate outage rendering.

- **First run against an existing log folder sends everything.** Point the
  sender at a folder with months of history and it'll ship all of it once.
  Intended, but worth knowing before you point it at a large archive.

- **Nothing is deduplicated across machines.** If two Pis send files with
  the same name, they'll write to the same local file.

- **The dashboard reloads every 60 seconds.** Fine for weather. If you move
  to fast logging, this becomes the limiting factor for what "live" means.

- **A file open in an editor may block replacement.** The retry covers a
  brief lock; a file left open indefinitely will still fail.
