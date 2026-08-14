# DORM Station — Technical Guide

**Višnjan Observatory · e-Callisto solar radio spectrometer**

A complete description of the data pipeline: what each component does, how
the pieces fit together, and the reasoning behind the design decisions.

Written to be readable without prior knowledge of the codebase. Familiarity
with files and networking is assumed; nothing beyond that.

| | |
|---|---|
| **Station** | Croatia-Višnjan (DORM) |
| **Instruments** | CALLISTO spectrometer, BME environmental sensor, weather station |
| **Repository** | <https://github.com/fortisture/ecallisto-weather-log-shipper> |
| **Deployment** | See [DEPLOYMENT.md](DEPLOYMENT.md) |
| **Design baseline** | See [DESIGN-BASELINE.md](DESIGN-BASELINE.md) |

---

## Table of contents

1. [Summary](#1-summary)
2. [Rationale](#2-rationale)
3. [System overview](#3-system-overview)
3b. [How the data crosses, over SSH](#3b-how-the-data-actually-crosses-over-ssh)
4. [The sender (Raspberry Pi)](#4-the-sender-raspberry-pi)
5. [The receiver (server)](#5-the-receiver-server)
6. [Security model](#6-security-model)
7. [The station server](#7-the-station-server)
8. [Data preparation: CSV to JSON](#8-data-preparation-csv-to-json)
9. [The weather dashboard](#9-the-weather-dashboard)
9c. [The other pages](#9c-the-other-pages)
10. [The power dashboard](#10-the-power-dashboard)
11. [The spectrogram viewer](#11-the-spectrogram-viewer)
12. [Supporting scripts](#12-supporting-scripts)
13. [Operation](#13-operation)
14. [Design decisions](#14-design-decisions)
15. [Defects identified and resolved](#15-defects-identified-and-resolved)
16. [Known limitations](#16-known-limitations)

---

## 1. Summary

A Raspberry Pi at the Višnjan observatory records three things: weather
readings, the electrical draw of each subsystem, and solar radio
spectrograms from the CALLISTO receiver. A program on the Pi
(`pi/sender.py`) watches those files and, the moment anything changes,
sends the change to a server — through an SSH tunnel, because the two
machines are on different networks and the server has only port 22 open.

On the server, `station/server.py` receives everything, files it into its
own archive, converts it into JSON a browser can read, and serves the
website. That server holds a complete independent copy, so the site keeps
working when the Pi is switched off, and the Pi's SD card stops being the
only place any of this exists.

The website is eight pages: an overview with the sun's position, the
weather, the power rails, whether the station is actually recording, the
solar ephemeris, the spectrograms themselves (decoded in the browser), a
log of what has happened to the station, and an about page.

Everything is dependency-free Python and static HTML. There is nothing to
install, no database, and no build step.

---

## 2. Rationale

The Raspberry Pi at the observatory records continuously. Leaving the data
there alone presents three problems:

1. **Storage reliability.** The Pi writes to an SD card, which has a finite
   write life. Years of observations on a single card is an unmitigated
   single point of failure.
2. **Accessibility.** Inspecting the data requires logging into the Pi.
3. **Capacity.** Serving a charting web application competes with the
   instrument duties the Pi already performs.

The system therefore replicates data off the Pi continuously to a server
with durable storage — which need not be anywhere near the observatory —
and performs all presentation work there.

**Continuously** is the operative constraint: every row is transmitted
within seconds of being written, not batched daily or on demand.

---

## 3. System overview

The Raspberry Pi sits at the telescope. The server is somewhere else
entirely — a different building, a different network, a different city if
you like. They are connected by nothing more than SSH.

```
   RASPBERRY PI (at the telescope)          UBUNTU SERVER (elsewhere)
   ───────────────────────────────          ─────────────────────────

   CALLISTO writes spectrograms
   sensors write weather + power
              │
              ▼
   ┌──────────────────────┐                 ┌────────────────────────┐
   │  pi/sender.py        │                 │  station/server.py     │
   │                      │                 │                        │
   │  • watches the files │                 │  • receives everything │
   │  • notices changes   │                 │  • saves to data/      │
   │  • sends what's new  │                 │  • rebuilds the JSON   │
   │  • remembers where   │                 │  • serves the website  │
   │    it got to         │                 │                        │
   └──────────┬───────────┘                 └───────────┬────────────┘
              │                                         │
              │ connects to its OWN machine             │ listens on its
              ▼                                         ▼ OWN loopback
        127.0.0.1:19443                           127.0.0.1:9443
              │                                         ▲
              │        ┌──────────────────────┐         │
              └───────►│ SSH tunnel, port 22  ├─────────┘
                       └──────────────────────┘
                         the only open port
                                                        │
                                                        ▼
                                            ┌────────────────────────┐
                                            │  reverse proxy :443    │
                                            │  the public website    │
                                            └────────────────────────┘
```

Five principles govern the design:

- **The Pi pushes; it is never polled.** A row is transmitted as soon as it
  appears. The server never has to reach back to the Pi, which means the Pi
  needs no inbound firewall rule, no static address, and no port forwarding
  on its own network.
- **The server holds an independent copy.** Received data lives in `data/`
  and does not depend on the Pi remaining reachable; the site continues to
  serve history if the Pi is switched off.
- **Nothing listens on a public interface except `sshd`.** The receiver and
  the web server both bind to `127.0.0.1`, so they are unreachable from
  outside even if the firewall is misconfigured.
- **The web layer reads files, not a database.** Nothing to install,
  configure, back up, or corrupt.
- **No third-party dependencies.** Both programs use only the Python
  standard library, so an OS or package upgrade cannot break them.

---

## 3b. How the data actually crosses, over SSH

This is the part that surprises people, so it is worth going slowly.

**No data port is open on the server.** Port 9443, where the receiver
listens, is bound to the server's own loopback address — the network
equivalent of a room with no external door. Yet the Pi, on a completely
different network, delivers into it.

### The one command that does it

On the Pi, a service runs this and nothing else:

```bash
ssh -N -L 19443:127.0.0.1:9443 dorm@server.example.org
```

Read `-L 19443:127.0.0.1:9443` as three separate things:

| Part | Meaning |
|---|---|
| `19443` | Open a listening port **on the Pi**, numbered 19443 |
| `127.0.0.1` | Anything arriving there should be delivered to this address… |
| `9443` | …on this port — **as resolved from the server's point of view** |

That middle field is the whole trick. `127.0.0.1` is not evaluated on the
Pi. It is sent across and evaluated *on the server*, where it means the
server's own loopback. The Pi has effectively borrowed a door into a room
that has no outside entrance.

`-N` means "do not run a command" — this SSH session exists purely to carry
the forward, and never gets a shell.

### Following one row of weather data

Suppose the weather logger appends a line. Every step it takes:

1. **`sender.py` opens an ordinary TCP connection to `127.0.0.1:19443`.**
   As far as the program is concerned, the receiver is running on the same
   machine. It has no idea a network is involved. This is why the sender
   contains no code about tunnels at all — the complexity lives entirely in
   the SSH configuration, not in the application.

2. **The SSH client on the Pi accepts that connection** and opens a
   *channel* inside the SSH session it already holds open.

3. **The bytes are encrypted and multiplexed** into the single TCP stream
   SSH maintains to the server on **port 22**. To anyone watching the
   network — the observatory's ISP, a hotel Wi-Fi, anyone in between — it is
   indistinguishable from somebody typing in a terminal.

4. **`sshd` on the server receives the stream,** recognises the channel, and
   opens its own TCP connection to `127.0.0.1:9443`.

5. **`receiver.py` accepts it.** From its point of view something on the
   local machine connected. It does not know, and does not need to know,
   that the other end is in another country.

The connection is now a plain pipe between the two programs. Every byte
either one writes travels steps 1–5, and replies travel them in reverse.

### Then a second lock, inside the first

Once that pipe exists, the two programs run their own handshake through it,
exactly as they did before any tunnel existed:

```
sender.py                                        receiver.py
    │                                                 │
    │ ── TLS handshake ─────────────────────────────► │
    │ ◄──────────────── server certificate ────────── │
    │                                                 │
    │  hash the certificate, compare against the      │
    │  fingerprint in config.json                     │
    │  MISMATCH -> hang up, send nothing              │
    │                                                 │
    │ ── AUTH <shared token> ──────────────────────►  │
    │ ◄──────────────── OK   (or DENY, and close) ─── │
    │                                                 │
    │ ── ROW  weather_20260814.csv <the row> ──────►  │
    │ ── BLOB Croatia-Visnjan_….fit.gz 190482 ─────►  │
    │    <190482 raw bytes>                           │
```

The data is therefore encrypted twice. That is deliberate rather than
paranoid, because the two layers answer different questions:

| Layer | Answers |
|---|---|
| **SSH** | *Which machine is this?* — proven by the Pi's private key |
| **TLS + pinning + token** | *Which program is this, and am I talking to the right receiver?* |

Consider what the inner layer protects against. Suppose someone changes the
tunnel to point at a machine of their own. SSH would be perfectly content —
it authenticated correctly, to the wrong destination. The sender then
compares that machine's certificate against the pinned fingerprint, finds it
does not match, and refuses to send a single byte. Without the inner layer
the archive would quietly upload itself to a stranger and nothing would look
wrong.

### Locking the key down

The Pi's public key goes in the server's `authorized_keys`, and it should be
restricted:

```
command="",no-agent-forwarding,no-pty,permitopen="127.0.0.1:9443" ssh-ed25519 AAAA...
```

- `command=""` — run nothing on login
- `no-pty` — never allocate a terminal
- `permitopen="127.0.0.1:9443"` — this key may open **that one forward** and
  nothing else

If the Pi is stolen off the hillside, whoever takes it holds a key that can
deliver weather data into one port. Not a shell, not the archive, not the
rest of the network.

### When the link drops

It will — rural internet is rural internet. Three mechanisms overlap:

- **The tunnel notices.** `ServerAliveInterval=30` with
  `ServerAliveCountMax=3` detects a silently dead link within about ninety
  seconds instead of hanging indefinitely. `ExitOnForwardFailure=yes` makes
  SSH quit rather than sit there pretending to be connected with a forward
  that never opened: a service that has failed should look failed.
- **systemd restarts it** — `Restart=always`, `RestartSec=10`.
- **The sender keeps its place.** Its connection attempts fail while the
  tunnel is down, so it retries with a widening backoff; and because it
  records its position after every single row, it resumes exactly where it
  stopped once the tunnel returns.

For the failure none of that catches — the server being rebuilt or restored
from a backup and quietly missing files — the Pi discards its delivery
record every six hours and sends everything again. Re-sending is safe by
construction: rows are matched by content hash and blobs replace whole
files, so anything already present is simply overwritten with itself.

### What this means practically

- The server firewall needs **one rule**: allow SSH. (Plus 443 later for the
  public website, which is a separate concern.)
- The Pi's network needs **no rules at all** — it only makes outbound
  connections.
- Moving the Pi to a different network needs **no configuration change**.
- Neither machine needs a static IP, provided the Pi can resolve the
  server's name.

---

## 4. The sender (Raspberry Pi)

**File: `pi/sender.py`**

**Responsibility:** detect changes to the weather CSV files and transmit them.

### How it notices changes

Every second it looks at each `*.csv` file in the watched folder and asks:
"is this the same as when I last looked?"

Comparing file size is insufficient. It detects appended data, but an
in-place edit to an existing row may not change the size at all, and a size
change alone does not indicate *what* changed. A size check would miss such
an edit silently, leaving the server's copy stale with no indication.

The sender therefore uses a **cryptographic hash** — a fixed-length
fingerprint derived from the file's contents, where any single-character
change produces an entirely different result. The sender records the hash of
exactly what it has transmitted and recomputes it each poll.

The comparison, from `pi/sender.py`:

```python
prev = state.get(name, {"length": 0, "hash": EMPTY_HASH})
prev_len  = prev["length"]
prev_hash = prev["hash"]

# 1. Nothing changed at all.
if len(data) == prev_len and hashlib.sha256(data).hexdigest() == prev_hash:
    continue

# 2. The file still STARTS with what we already sent -> pure append.
if len(data) >= prev_len and hashlib.sha256(data[:prev_len]).hexdigest() == prev_hash:
    ...send only the new rows...
else:
    # 3. Something we already sent has changed -> resend the whole file.
    ...
```

The significant expression is `data[:prev_len]` — the first N bytes, where N
is the amount already transmitted. If the hash of that slice still matches,
nothing previously sent has changed and only the tail is new. A mismatch
indicates the transmitted region was modified.

The comparison yields three outcomes:

| What it finds | What it means | What it does |
|---|---|---|
| Fingerprint unchanged | Nothing happened | Nothing |
| The file *starts with* what was already sent, and has extra on the end | Normal case: new rows appended | Send only the new rows |
| Anything else | A row was edited, or the file got shorter | Send the **whole file** |

The second case is the common one and is inexpensive, a row being a few
dozen bytes. The third is the correctness fallback: if any transmitted
region has changed, the sender abandons incremental transfer and resends the
file in full, and the receiver replaces its copy wholesale.

### How it never loses or duplicates a row

The sender keeps a small file (`state.json`) recording how far it has got
in each file. It updates that record **after every single row it sends**,
not at the end of a batch.

This matters because: suppose the network drops halfway through sending 50
rows. If the record were only written at the end, the sender would restart
and re-send all 50, and you'd get duplicates. If it were written at the
start, the interrupted rows would be skipped forever. Writing after each
row means the worst case is re-sending the single row that was in flight.

The record is written using a trick called an **atomic write**: write to a
temporary file first, then rename it over the real one. Renaming is
instantaneous at the operating-system level — there is no moment where the
file is half-written. Without this, losing power mid-write would leave a
corrupted record and the sender wouldn't know where it was.

```python
def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)     # <- the atomic step
```

And the save-after-every-row loop it protects:

```python
if line:
    sock.sendall(f"ROW\t{name}\t{text}\n".encode("utf-8"))
offset = f.tell()
state[name] = {"length": pos, "hash": ...}
save_state(cfg["state_file"], state)   # every row, not every batch
```

### When the network breaks

It reconnects, with **exponential backoff**: wait 1 second, then 2, 4, 8,
16, capped at 30. If the PC is off for an hour, the Pi isn't hammering the
network thousands of times — it's checking calmly every 30 seconds. When
the PC comes back, everything buffered up since the outage is sent.

---

## 5. The receiver (server)

**File: `station/receiver.py`**

It listens on port 9443 and understands exactly two kinds of message:

- **`ROW`** — one new line for one file. Append it.
- **`FILE`** — a whole file's contents. Replace the local copy entirely.

Each incoming connection is handled on its own **thread** (an independent
line of execution), so a slow or stuck connection can't block others.

There is a third: **`BLOB`** — a binary file (a CALLISTO spectrogram),
stored under the FITS folder.

This is what makes the PC a real database for the station: **both** the
weather rows and the spectrograms arrive over the same authenticated
connection, so the Pi's SD card stops being the only copy of anything.

### How the store is organised

Both stores are laid out **year / month / day**:

```
data/
  weather/
    2026/07/08/visnjan_weather_20260708.csv
    2026/07/09/visnjan_weather_20260709.csv
  fits/
    2025/09/09/Croatia-Visnjan_20250909_075911_03.fit.gz
```

This mirrors how the e-Callisto archive itself is organised, and it
matters for a practical reason: a single flat folder becomes unusable
after a few thousand files (slow to list, impossible to browse), while a
nested tree can be browsed, backed up, or pruned one period at a time.

The date comes from the **filename**, not from the clock or the file
contents:

```python
DATE_PATTERNS = (
    re.compile(r"[_-](\d{4})(\d{2})(\d{2})[_.-]"),   # ..._20250909_...
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),          # ...2026-08-13...
    re.compile(r"(\d{4})(\d{2})(\d{2})"),
)

def date_subdir(name):
    for pattern in DATE_PATTERNS:
        match = pattern.search(name)
        if not match:
            continue
        year, month, day = match.group(1), match.group(2), match.group(3)
        if 1970 <= int(year) <= 2999 and 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return year, month, day
    return None
```

A file whose name carries **no** date goes into an `undated/` folder
rather than being guessed at. Filing real measurements under a day they
didn't come from would be worse than admitting we don't know.

Because appends are matched by filename, a daily-rotated log keeps landing
in the same dated folder for its whole day, then naturally moves to the
next one when the station rotates at UTC midnight.

**Migrating an existing store:** `migrate_store_layout.py` moves files
from the old flat layout into the new tree. It's a dry run by default:

```bash
python migrate_store_layout.py            # show what would move
python migrate_store_layout.py --apply    # actually move
```

It never overwrites or deletes: a name collision is reported and skipped.

### Two things it does carefully

**It doesn't trust the filename it's given.** A filename is data arriving
over a network, and data can be malicious. Something could send
`../../Windows/System32/something.csv` and try to make the receiver write
outside its folder. So the receiver strips the filename down to its bare
last component and requires it to end in an expected extension. Anything
else is rejected and logged.

```python
def safe_filename(name):
    name = os.path.basename(name.strip())        # kills any ../ path parts
    if not name or name in (".", "..") or not name.endswith(".csv"):
        return None
    return name
```

One subtlety worth knowing: when a name is rejected, the payload bytes must
**still be read off the connection**. Skipping them would leave the unread
bytes sitting in the stream, and every message after that one would be
parsed from the wrong place:

```python
# Drain the payload even if the name turns out to be unusable, otherwise
# the stream desynchronises and every message after this one is garbage.
content = read_exactly(f, length)
```

**It replaces files atomically.** A `FILE` message writes to a temporary
file, then renames it into place — so the website can never catch a file
half-written and read garbage.

On Windows that rename can occasionally fail because something else has the
file open (antivirus, search indexing, or you looking at it in an editor).
When that happens the receiver retries five times over about a second. This
is not theoretical — it happened during testing, because the file was open
in an editor at the time.

---

## 6. Security model

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

```python
der = sock.getpeercert(binary_form=True)     # the server's actual certificate
fp  = hashlib.sha256(der).hexdigest()        # its fingerprint
expected = cfg["fingerprint"].replace(":", "").lower()

if fp != expected:
    sock.close()
    raise ssl.SSLCertVerificationError(
        f"certificate fingerprint mismatch: got {fp}, expected {expected}"
    )
```

Note this happens *before* the token is sent — so a fake server never even
gets to see the shared secret.

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

```python
token = line[len(b"AUTH "):].strip().decode("utf-8", errors="replace")

if not hmac.compare_digest(token, cfg["token"]):   # NOT  token == cfg["token"]
    conn.sendall(b"DENY\n")
    log(f"{peer}: bad token, closing")
    return
```

### What it deliberately does not do

No internet exposure. No port forwarding. No cloud. The firewall rule is
scoped to the local network only. The system is unreachable from outside
your house.

**The secrets never go in the git repository.** `.gitignore` excludes
`*.pem`, `token.txt`, `fingerprint.txt`, and `config.json`. The published
code is useless to anyone without your keys.

---

## 7. The station server

**File: `station/server.py`** — the single command that runs the whole ground station.

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
the worst kind of failure, because everything *looks* fine. It also throws
away its cached fingerprints so the next pass rebuilds from scratch:

```python
except Exception as e:
    log(f"watcher error: {e!r} -- forcing a rebuild next pass")
    last_weather = None
    last_fits = None
```

The web server also maps URLs starting `/fits/` onto the local FITS folder,
with the same path-traversal protection as the receiver.

### The 6-hour failsafe

Change-detection has a blind spot. It only rebuilds when the *source*
changes — so if a derived file gets deleted, truncated, or left half-written
by a crash, nothing changed upstream and nothing gets fixed. Worse, "nothing
changed" is exactly what a silently-stuck pipeline looks like.

So both sides run a timed sweep that ignores change detection entirely.

**On the server** (`station/server.py`), every 6 hours everything is rebuilt from
whatever is actually on disk:

```python
forced = next_failsafe is not None and time.time() >= next_failsafe
if forced:
    log(f"failsafe sweep ({failsafe_hours:g}h): rebuilding everything from disk")
    next_failsafe = time.time() + failsafe_hours * 3600

missing = not os.path.exists(os.path.join(api_dir, "weather", "history.json"))

if forced or missing or digest != last_weather:
    ...rebuild...
```

Note `missing` — if the output file has vanished, it rebuilds immediately
rather than waiting for the timer.

It also sweeps up debris from interrupted writes:

```python
def clean_stale_temp_files(*dirs, max_age=3600):
    """A crash between "write temp" and "rename into place" leaves debris
    that nothing will ever complete."""
    cutoff = time.time() - max_age
    ...remove any *.tmp / *.part older than an hour...
```

**On the Pi** (`pi/sender.py`), every 6 hours it forgets what it thinks it
already delivered, which forces a full re-send:

```python
def resync_all(cfg, state):
    state.clear()
    save_state(cfg["state_file"], state)
    log("failsafe resync: delivery state cleared, resending everything")
```

That sounds drastic, but it is safe *by construction*: rows are matched by
content hash and offset, and blobs replace whole files — so re-sending
something that already arrived changes nothing. This is what fixes the
backlog if the server was rebuilt, restored from a backup, or lost files.
The Pi has no way to *detect* that happened; periodically assuming it might
have is cheaper than trying to find out.

---

## 8. Data preparation: CSV to JSON

**File: `station/api.py`**

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

```python
deltas = sorted(times[i + 1] - times[i] for i in range(len(times) - 1))
median = deltas[len(deltas) // 2]          # the series' own normal cadence
threshold = median * gap_factor            # gap_factor = 3.0

for i, reading in enumerate(readings):
    out.append(reading)
    if i + 1 < len(readings) and (times[i + 1] - times[i]) > threshold:
        marker = {"timestamp": ..., "gap": True}
        marker.update({key: None for key in value_keys})   # all values null
        out.append(marker)
```

The threshold is relative to the file's own rhythm (that `median` line), so
it works whether the station logs every 10 seconds or every hour. On the
browser side, `spanGaps: false` is what turns those nulls into an actual
visual break rather than a line drawn straight through them.

---

### Why the API is split by range

Originally the browser downloaded the entire archive on every page load
and filtered it in JavaScript. That is fine at a few hundred kilobytes and
untenable later: at one-minute logging, a year of weather is about 84 MB,
and five years is 421 MB — per page load.

So the server pre-computes one file per range and averages long ranges
down to about 1200 points, which is as many as a chart a thousand pixels
wide can distinguish anyway:

```
web/api/weather/history-24h.json      4.3 KB
web/api/weather/history-7d.json      28   KB
web/api/weather/history-30d.json    113   KB
web/api/weather/history-all.json    141   KB
```

Switching range now fetches a different small file instead of re-slicing a
large one. Averaging rather than sampling matters: dropping every Nth point
can drop the one that mattered, silently removing a real extreme, whereas
averaging keeps every measurement represented in the result.

Gap markers survive downsampling untouched, so an outage stays a break in
the line rather than being averaged into a smooth curve across it.

---

## 9. The weather dashboard

**Files: `web/index.html`, `styles.css`**

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

### The median line

Each chart carries a dashed reference line at the **median** of whatever
period is selected — "half the time it was above this."

Median, not mean, deliberately: a few extreme readings (one very hot
afternoon, a pressure crash during a front) drag a mean away from what the
period was actually like. The median ignores them.

```python
function medianOf(values) {
  const clean = values.filter(v => v !== null && v !== undefined)
                      .sort((a, b) => a - b);
  if (!clean.length) return null;
  const mid = Math.floor(clean.length / 2);
  return clean.length % 2 ? clean[mid]
                          : (clean[mid - 1] + clean[mid]) / 2;
}
```

It is drawn **grey and dashed**, not in a colour of its own. That is a
deliberate call: it is an annotation, not a fifth data series. Any hue
close enough to look tasteful beside the data line turned out to be too
close to distinguish under colour-blind simulation — violet against the
blue humidity line measured ΔE 9.8, well under the 15 minimum. Grey plus a
dash pattern is unambiguous for everyone.

It is also **labelled in the legend with its value** (`Median 28.0 °C`),
because an unexplained dashed line across a chart is just a question.

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

## 9c. The other pages

The site grew from one dashboard into eight. Each answers a different
question, and they deliberately do not overlap.

| Page | The question it answers |
|---|---|
| **Overview** (`index.html`) | Is everything all right, and where is the sun? |
| **Weather** | What has the weather been doing? |
| **Power** | What is each subsystem drawing? |
| **Status** | Is the station actually recording, and how much has it missed? |
| **Sun** | When is the sun up, today and across the year? |
| **Spectrograms** | What did the instrument see? |
| **Log** | What has happened to this station? |
| **About** | What is this, and who built it? |

### Overview — the landing page

Opens with the sun drawn as an arc from sunrise to sunset, with a marker
where the sun currently is. That is a picture rather than three
timestamps because "where are we in the day" is read far faster from a
shape, and because at 03:00 a row of numbers tells you nothing while an
empty arc tells you immediately that it is night.

Below it: the latest weather, the latest power draw, the instrument's
totals, and a strip showing whether each data stream is delivering.

It does **not** auto-refresh the data. The demo archive is static, so a
countdown that never changes would be theatre. The refresh function is
written to be re-runnable, so switching it on later is one `setInterval`
call — the code says as much where it would go.

### Status — uptime measured, not asserted

"Uptime" for a logging station is not whether a machine answers a ping. A
Pi can be perfectly reachable while the receiver has been silently dead
for a week. What matters is whether the data that was supposed to exist
exists.

So every figure on this page is derived from files on disk:

- **Live state** comes from how long ago each stream last delivered
  anything, compared against a limit that is a generous multiple of that
  stream's own cadence. One missed sample is not an outage; three hours of
  silence from a fifteen-minute instrument is.
- **Coverage** is a per-day count of what arrived against what was
  expected. CALLISTO writes 96 files a day, so 48 files is 50% coverage
  for that day, plainly.

The instrument stream decides the overall verdict. Weather still flowing
while CALLISTO is silent is exactly the failure this page exists to catch,
and averaging the three together would hide it.

### Sun — computed, never fetched

Sunrise and sunset come from the NOAA solar-position algorithm evaluated
for the observatory's coordinates. No weather API, no network call. The
station therefore knows when the sun rises with no internet at all, which
matters because this drives observation scheduling.

Accuracy is a few minutes. The algorithm ignores terrain, so a hill on the
horizon will always delay real sunrise past the computed one — worth
knowing before treating it as gospel.

### Log — two kinds of history

Posts are prose, written by hand in `web/blog/posts.json`. Instrument
posts quote the e-Callisto network journal and link the original entry, so
a reader can check the claim. Software posts explain how the monitoring
works. `CHANGELOG.md` is still the machine-readable record of every
version, but it is not rendered here: a reader wants two articles, not
eleven version bumps.

---

## 10. The power dashboard

**File: `web/power.html`**

The station's own health telemetry — four powered subsystems:

| Rail | What it is | Typical |
|---|---|---|
| **Dew heater** | Keeps condensation off the optics/antenna | 12 V, 0–1800 mA, duty-cycled |
| **LNA** | Low-noise amplifier — the first thing the signal hits | 12 V, ~90 mA, steady |
| **CALLISTO** | The spectrometer itself | 12 V, ~420 mA, steady |
| **BME sensor** | The environmental sensor (temp/humidity/pressure) | 3.3 V, ~3 mA |

### Watts are calculated, not logged

Only volts and milliamps are recorded. Power is derived:

```python
def rail_watts(reading, key):
    volts = reading.get(key + "_v")
    milliamps = reading.get(key + "_ma")
    if volts is None or milliamps is None:
        return None
    return round(volts * milliamps / 1000.0, 3)
```

If watts were logged as a third column, it could drift out of agreement
with the two numbers it's supposed to come from — and then you'd have no
way to know which one to believe. Deriving it makes that impossible.

Same reason `None` is returned rather than 0 when a measurement is
missing: zero watts is a *claim* ("nothing is drawing power"), which is a
very different statement from "we didn't measure it."

### Why four separate current charts

The heater peaks near 1800 mA; the BME sensor draws about 3 mA. That's a
600:1 ratio. On one shared axis the BME, LNA and CALLISTO traces would all
be flattened into the baseline and you'd effectively have a heater chart
with three invisible lines on it.

So each rail gets its own small chart with its own scale. The BME chart
also uses two decimal places where the others use none — 0.1 mA is noise
on a heater and meaningful signal on a 3 mA sensor.

Voltages *do* share one chart, because they're all in volts and within a
similar range (3.3 and 12), so a shared axis is honest there.

### How the data gets there

The Pi ships power CSVs over the same authenticated connection as
everything else. The receiver routes them by filename:

```python
POWER_HINTS = ("power", "psu", "rail", "volt", "current")

def csv_store_root(cfg, name):
    lowered = name.lower()
    if cfg.get("powerdir") and any(hint in lowered for hint in POWER_HINTS):
        return cfg["powerdir"]
    return cfg["outdir"]      # anything else is weather
```

Filename routing is used because a `ROW` message carries only a filename
and a row — there is no "stream" field in the protocol. Naming the log
`power_20260813.csv` is enough.

### The demo data is simulated — and says so

`simulate_demo_power.py` **invents** these numbers. There is no external
source for one specific station's power rails, unlike the weather (which
comes from real Open-Meteo observations). The file says so at the top, and
so does its output.

It does one thing worth knowing: the heater duty cycle is driven off the
**real** weather store. The heater runs harder as air temperature closes
on the dew point, which is physically when condensation forms:

```python
duty = max(0.0, min(1.0, (6.0 - margin) / 5.0))   # margin = temp - dewpoint
heater_ma = 60 + duty * 1750 + random.gauss(0, 25)
heater_v  = 12.15 - duty * 0.35 + random.gauss(0, 0.02)   # rail sags under load
```

So the heater trace lines up with the dew-point chart on the weather page,
and the supply voltage dips slightly when the heater pulls hard — the way
a real 12 V rail behaves. Delete this script once the real hardware
reports.

## 11. The spectrogram viewer

**File: `web/fits.html`**

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

The published e-Callisto method is to subtract **a constant background per
frequency channel, computed as the mean over time**, then clip. That is the
default here, so what you see matches the standard product. Four modes are
offered:

```javascript
function computeBaseline(data, width, height, mode) {
  const baseline = new Float32Array(height);

  if (mode === "none") return baseline;                 // raw digits

  if (mode === "global") {                              // one number for all
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i];
    baseline.fill(sum / data.length);
    return baseline;
  }

  if (mode === "channel-median") { ...per-row median... }

  // Default: per-channel MEAN -- the e-Callisto standard.
  for (let y = 0; y < height; y++) {
    let sum = 0;
    for (let x = 0; x < width; x++) sum += data[y * width + x];
    baseline[y] = sum / width;
  }
  return baseline;
}
```

- **channel-mean** — the standard. Matches published quicklooks.
- **channel-median** — robust variant. A long burst drags a *mean* upward
  and so partially erases itself; a median ignores it. Better on a busy
  file, but not the standard.
- **global** — one number for the whole image. Preserves the real
  differences *between* channels, so you can see which parts of the band
  are noisy. Useless for spotting faint bursts.
- **none** — raw receiver digits, stripes and all.

### Contrast scaling

The colour range is set from **percentiles** (1st to 99.5th) rather than
minimum and maximum. A single interference spike would otherwise stretch
the scale so far that everything real collapsed into one flat colour —
which is exactly what happened on the first attempt.

The contrast slider applies a **gamma curve**, which brightens faint
detail without blowing out bright features (a simple multiplier saturates
everything instead).

### The axes — what makes it a plot rather than a picture

A coloured rectangle tells you nothing without labels. The canvas therefore
reserves margins around the image and draws the chrome into them:

```javascript
const PLOT = {
  left:   74,   // frequency axis + its title
  right:  96,   // colorbar + its ticks
  top:    40,   // plot title
  bottom: 58,   // time axis + its title
  barWidth: 16
};
```

What ends up on screen:

- **Vertical axis** — frequency in MHz, high at the top (the convention).
- **Horizontal axis** — time as a UT clock, derived from `CRVAL1` (seconds
  into the day) and `CDELT1` (0.25 s per column).
- **Title** — instrument, date, start time.
- **Colorbar** — the actual value scale, labelled `DIGITS ABOVE BACKGROUND`
  (or just `DIGITS` when subtraction is off, because then the numbers mean
  something different and mislabelling them would be worse than not
  labelling them).
- **A processing note** under the plot saying which background mode
  produced the image, so an exported screenshot is self-describing.

Tick values are rounded to human numbers rather than whatever the range
happens to divide into:

```javascript
const candidates = [1, 2, 2.5, 5, 10].map(m => m * magnitude);
const step = candidates.find(c => c >= rough) || ...;
```

"100, 200, 300" is readable. "94.6, 189.2, 283.8" is not.

The whole thing is drawn at `devicePixelRatio` so the text is crisp on
high-DPI screens, and redrawn on window resize — otherwise the labels
stretch with the canvas and go blurry.

### Palettes

**CALLISTO** is the default: the dark-blue → cyan → green → yellow → red
look these spectrograms are conventionally published in, because that is
what this community reads fluently.

It is worth knowing that this palette is *not* monotonic in brightness —
mid-range greens can read as "brighter" than stronger signals further up
the scale. The other three (**Inferno**, **Viridis**, **Grayscale**) are
perceptually ordered: brightness rises consistently with signal, so a
brighter pixel always means a stronger signal. If you are judging relative
intensity rather than pattern-matching against published plots, use one of
those.

I could not retrieve the official quicklook PNGs to colour-match exactly,
so the CALLISTO ramp is modelled on published CALLISTO figures rather than
sampled from the official renderer.

### Browsing years

The archive spans years, so a flat list of days would be thousands of
buttons — and "09-07" alone doesn't tell you which year. Navigation is
therefore **year → month → day**, and each level shows how many recording
days it contains, so empty stretches are visible before you click into
them.

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

## 12. Supporting scripts

- **`tools/fetch_visnjan_fits.py`** — downloads real spectrograms from
  the central e-Callisto archive. Skips files it already has (the archive
  never changes, so re-downloading is pure waste) and searches *backwards*
  for days that actually contain data, because the station has long gaps.

- **`tools/fetch_demo_weather.py`** — downloads real measured weather
  for Višnjan's coordinates from Open-Meteo, and writes it in the station's
  CSV format. This is **demo data for developing the dashboard** when the
  Pi isn't connected. It's real measured weather, not simulated. It can also
  deliberately delete a couple of days (`--simulate-outage`) to test that
  gap rendering works.

- **`pi/setup_pi.sh`** — installs the sender on the Pi as a *service*, so it
  starts automatically at boot and restarts if it crashes.

- **`deploy/install_receiver.ps1`** — generates the certificate and token,
  opens the firewall port, registers the receiver to start at login.

---

## 13. Operation

**On the PC (once):**

```powershell
cd windows
.\install_and_run.ps1
```

Generates the certificate and token and prints the fingerprint. Copy the
token and fingerprint — the Pi needs them.

**On the PC (every time):**

```bash
python station/server.py
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
python tools/fetch_demo_weather.py --days 35
python tools/fetch_visnjan_fits.py --days 3
```

---

## 14. Design decisions

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
| Spectrograms shipped over the same connection | One authenticated channel, one store; the Pi's SD card stops being the only copy |
| Blobs sent whole, never incrementally | A FITS file is written once and never appended to |
| Skip blobs modified in the last 5 seconds | Avoids shipping a file the instrument is still writing |
| Failsafe resync every 6 hours | Change-detection can't see a loss on the *other* side |
| Re-sending is safe rather than tracked | Cheaper to re-send blindly than to build an acknowledgement protocol |
| Median (not mean) reference line | A few extremes drag a mean away from the typical value |
| Grey dashed median line | It's an annotation, not a series; and every tasteful hue failed CVD separation |
| Per-channel **mean** default subtraction | It's the published e-Callisto standard, so output matches the reference product |
| Axes drawn into the canvas | An exported image stays self-describing |
| Year → month → day navigation | A flat day list across years is thousands of buttons |

---

## 15. Defects identified and resolved

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

9. **Title and processing label overlapped** on the spectrogram at normal
   widths. The note moved below the plot; a title must never be the thing
   that gets overwritten.

10. **A rejected filename would have desynchronised the stream.** When a
    `FILE`/`BLOB` name failed validation, the payload bytes still had to be
    read off the connection — otherwise every following message would be
    parsed starting from the middle of the discarded file.

11. **Spectrograms were rendered upside down.** I had assumed CALLISTO
    writes row 0 at the low-frequency end and flipped the image to
    compensate. It writes row 0 at the **high**-frequency end (the
    frequency table runs 404 → 45 MHz), so the flip *created* the problem
    instead of fixing it. What made it hard to spot: the axis labels were
    drawn independently and were correct, so the plot looked entirely
    plausible — 400 MHz printed at the top, with 45 MHz data underneath
    it. The fix reads the ordering from the frequency table rather than
    assuming it:

    ```javascript
    const ascending = freqs && freqs.length > 1
      ? freqs[0] < freqs[freqs.length - 1]
      : (currentImage.header.CDELT2 || -1) > 0;

    const sourceRow = ascending ? height - 1 - y : y;
    ```

---


12. **Solar times were twelve hours out.** The Julian Day Number is
    defined at *noon*, and the code treated it as midnight. Caught because
    solar noon came out at 23:07 UTC for a site at 13.7° east, where it
    should be about 11:05. The day *lengths* were correct throughout,
    which is why it was not obvious — the shape of the year was right and
    only the clock was wrong.

13. **A CSS selector matched more than intended.** A bare
    `header { display: flex }` rule for the page banner also matched every
    `<header>` inside a log article, laying titles, dates and summaries out
    as overlapping columns. Scoped to `.wrap > header`.

14. **Two text colours failed contrast.** Measured, not noticed:
    `--text-faint` came out at 2.12:1 against the panel surface — below
    even the 3:1 floor for non-text — and `--text-dim`, which carries body
    copy, at 3.87:1 against a 4.5:1 requirement. Both were invisible
    problems on a good monitor in a dark room and very visible on a laptop
    outdoors.

15. **The keyboard focus ring did not exist.** It computed to
    `outline-style: none` on buttons, so tabbing through the site gave no
    indication of position at all.

16. **The navigation forced horizontal scrolling on phones.** Seven tabs
    is 560px of nav at a 375px viewport, which pushed the entire page
    sideways.

---

## 16. Known limitations

- **The Višnjan CALLISTO station appears to have been offline since
  September 2025.** The archive has data for 2024-08-11, mid-2025, and
  2025-08-01 → 2025-09-09, then nothing on any 2026 date checked. The three
  days stored locally are the most recent that exist.

- **The power figures are entirely simulated.** Unlike the weather, there
  is no external source for this station's own rails, so
  `simulate_demo_power.py` invents them. They are physically plausible and
  the heater responds to the real weather, but they are not measurements.

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

- **The 6-hour resync re-sends everything.** That is the point, and it is
  safe — but on a slow link with a large FITS archive it is real traffic.
  Turn it down with `--resync-hours` on the Pi or `--failsafe-hours` on the
  server; `0` disables it.

- **The Pi does not yet ship FITS by default.** The sender only looks for
  spectrograms if `fits_dir` is set in its `config.json`. Without it, the
  BLOB path is simply unused and only weather is shipped.

- **The CALLISTO palette is not brightness-ordered.** It matches the
  published convention, which is why it is the default — but for judging
  relative intensity, switch to Inferno or Viridis.

- **The station's frequency coverage is 45–404 MHz**, not the 45–870 MHz
  the network as a whole spans. That is what this receiver's own frequency
  table reports.
