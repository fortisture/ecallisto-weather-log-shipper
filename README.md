# eCallisto Weather Log Shipper

Ships weather-station CSV logs from the Raspberry Pi running the eCallisto
radio-spectrometer software to a Windows PC on the same LAN, continuously
and securely, as each row is written.

## How it works

```
Raspberry Pi                              Windows PC
------------                              ----------
pi/sender.py                              windows/receiver.py
  watches *.csv in a directory   --TLS-->   listens on :9443
  hashes each file every poll,              authenticates the connection
  compares against what was last            (shared-secret token)
  sent: pure append -> ship new             verifies the sender is talking
  rows; anything else changed ->            to it via cert-fingerprint pin
  ship the whole file, replace              appends new rows, or replaces
  reconnects automatically                  a file wholesale, under
  (no dupes/drops on restart)               incoming_logs/
```

eCallisto rotates to a new CSV file each day (UTC); within a day, rows are
appended to that day's file. Both are handled without any special-casing:
new files are picked up the moment they appear (the watch directory is
re-scanned every poll).

Appends and edits are told apart by comparing hashes, not just size: each
poll, `sender.py` hashes a file's current content and checks whether it
still starts with exactly what was last sent. If so, it's a pure append —
only the new complete lines ship, one `ROW` message each, with state
persisted after every single line so a mid-stream disconnect can't
duplicate or drop one. If the content before that boundary has changed (an
in-place edit to an already-shipped row) or the file got shorter (rotated
or truncated under the same name), the whole current file ships as one
`FILE` message and the receiver replaces its local copy wholesale.

**First run against a directory that already has old files in it ships
their full existing contents**, not just rows appended from that point on
— there's no "only watch what's new" mode. If you're pointing `sender.py`
at a directory with weeks of prior daily logs, expect that history to be
shipped once on the very first start.

**Security model:** TLS encryption, the sender is pinned to the receiver's
exact certificate fingerprint (refuses to talk to anything else), and every
connection must present a shared-secret token or gets a `DENY`. No cloud,
no port-forwarding — LAN only.

Both scripts are dependency-free (Python 3 standard library only).

## Setup

### 1. Windows (receiver)

```powershell
cd windows
.\install_and_run.ps1
```

This generates `cert.pem`/`key.pem`/`token.txt` in `windows/` (all
gitignored), prints the certificate's SHA-256 fingerprint, opens a
Private-profile firewall rule on TCP 9443, and registers a scheduled task
that keeps the receiver running. Run it yourself in PowerShell — it
touches firewall and scheduled-task state.

### 2. Raspberry Pi (sender)

Copy `pi/sender.py`, `pi/setup_pi.sh`, and a filled-in copy of
`pi/config.example.json` to the Pi (fill in `host`/`port`/`token`/
`fingerprint` from step 1), then:

```bash
chmod +x setup_pi.sh
./setup_pi.sh /path/to/weather_logs config.json
sudo systemctl status weather-shipper
journalctl -u weather-shipper -f
```

### Manual run (no systemd), useful for testing

```bash
python3 sender.py config.json
```

## Wire protocol

1. Client opens a TLS connection, verifies the server certificate's
   SHA-256 fingerprint matches the pinned value (aborts otherwise).
2. Client sends `AUTH <token>\n`. Server replies `OK\n` or `DENY\n`
   (constant-time comparison) and closes on mismatch.
3. Client sends one message per change, one of:
   - `ROW\t<filename>\t<csv row content>\n` — append one row.
   - `FILE\t<filename>\t<byte length>\n` followed by exactly
     `<byte length>` raw bytes — replace the file's entire content.

   In both cases `<filename>` is sanitized to a bare basename ending in
   `.csv` (path traversal is rejected) before being applied under
   `incoming_logs/`.

## The station server

The receiving PC runs one self-contained process that does everything:
accepts data from the Pi, stores it locally, and serves the web UI from
that local copy — so the site keeps working when the Pi is offline.

```bash
python server.py
```

Then open <http://127.0.0.1:8090/>. Add `--no-receiver` to serve the site
without accepting Pi uploads.

## Repository layout

```
server.py                      the station server: ingest + store + serve
pi/sender.py                   sender, runs on the Pi
pi/setup_pi.sh                 installs sender.py as a systemd service
pi/config.example.json         config template (fill in and copy to the Pi)
windows/receiver.py            TLS ingest, used by server.py
windows/install_and_run.ps1    generates secrets + firewall + scheduled task
rag-web-ui/                    the web UI and its data tooling
data/                          local store (gitignored -- see below)
M&M EXPLANATION FOR DUMMIES.md full plain-English explanation of everything
CHANGELOG.md
```

**The repository holds code, never data.** Everything the station collects
or downloads lives in `data/` and is gitignored, along with all secrets
(`*.pem`, `token.txt`, `fingerprint.txt`, `config.json`) and the generated
dashboard JSON. Clone the repo anywhere and it rebuilds its own data.
