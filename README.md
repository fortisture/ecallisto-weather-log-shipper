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
  ships each new row the instant            authenticates the connection
  it's appended                             (shared-secret token)
  reconnects automatically                  verifies the sender is talking
  tracks send-offset in a state             to it via cert-fingerprint pin
  file (no dupes/drops on restart)          appends rows to matching files
                                             under incoming_logs/
```

eCallisto rotates to a new CSV file each day (UTC); within a day, rows are
appended to that day's file. Both are handled without any special-casing:
new files are picked up the moment they appear (the watch directory is
re-scanned every poll), and appends are detected by comparing each file's
current size against the last-seen offset.

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
3. Client sends one line per row: `<filename>\t<csv row content>\n`.
   Server appends `<csv row content>\n` to `incoming_logs/<filename>`
   (basename only, must end in `.csv` — path traversal is rejected).

## Repository layout

```
pi/sender.py               sender, runs on the Pi
pi/setup_pi.sh              installs sender.py as a systemd service
pi/config.example.json      config template (fill in and copy to the Pi)
windows/receiver.py         receiver, runs on the Windows PC
windows/install_and_run.ps1 generates secrets + registers the receiver
CHANGELOG.md
```
