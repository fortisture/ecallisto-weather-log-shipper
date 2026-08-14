# DORM — Višnjan e-Callisto Station

Monitoring and archival software for the **Croatia-Visnjan** solar radio
spectrometer at Višnjan Observatory, Istria. The system acquires weather
data, power-rail telemetry, and CALLISTO spectrograms from a Raspberry Pi
co-located with the instrument, replicates them to a server for durable
storage, and presents them through a web interface.

The implementation uses only the Python 3 standard library and static
HTML. It requires no third-party packages, no package manager, and no
database.

```
Raspberry Pi  ──SSH tunnel──►  server  ──HTTPS──►  client
(instrument)                   (store + web)
```

---

## Quick start (single host, no installation)

To evaluate the system on one machine:

```bash
git clone https://github.com/fortisture/ecallisto-weather-log-shipper.git
cd ecallisto-weather-log-shipper

python3 install.py secrets            # TLS keypair and shared token
python3 tools/fetch_weather.py        # observed weather history for Višnjan
python3 tools/simulate_power.py       # simulated rail telemetry
python3 tools/fetch_fits.py           # spectrograms from the e-Callisto archive

python3 station/server.py
```

The interface is then available at <http://127.0.0.1:8090/>.

---

## Deployment

The system runs on two hosts. The installer prints each privileged command
prior to execution and installs no packages.

### 1. Server (Ubuntu)

```bash
sudo python3 install.py server
```

This generates the TLS keypair and shared token in `secrets/`, creates the
`data/{weather,power,fits}` stores, installs and starts
`dorm-station.service`, and prints the token and certificate fingerprint
required by the Pi.

The receiver and the web server both bind to `127.0.0.1`. No service is
reachable from outside the host except `sshd`.

```bash
systemctl status dorm-station
journalctl -u dorm-station -f
```

### 2. Raspberry Pi (Raspberry Pi OS)

```bash
sudo python3 install.py pi
```

This prompts for the server's SSH parameters and the token and fingerprint
from step 1, generates an SSH key for the tunnel, installs `sender.py` and
its configuration to `/opt/dorm`, writes `dorm-tunnel.service` and
`dorm-sender.service`, and prints the public key to be authorised on the
server.

After authorising that key on the server:

```bash
sudo systemctl enable --now dorm-tunnel dorm-sender
journalctl -u dorm-sender -f
```

### 3. Public HTTPS

The web server listens on the loopback interface only. Public access is
provided by a reverse proxy that terminates TLS. See
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

### Verifying an installation

```bash
python3 install.py check
```

---

## Transport

The Pi and the server reside on separate networks; the server exposes only
port 22. Rather than exposing a data port to the network, the Pi
establishes an SSH tunnel and connects to the receiver through it:

```
Pi                                        server
sender.py ─► 127.0.0.1:19443
                  │
                  └── ssh -L ──────────► 127.0.0.1:9443  (receiver)
                      (port 22)
```

The TLS, certificate-pinning, and shared-token layer operates inside the
tunnel. SSH authenticates the hosts; the inner layer authenticates the
application and pins the receiver's certificate, so a substituted tunnel
endpoint cannot inject data. The Pi's key is constrained in
`authorized_keys` to open that single forward and nothing else, precluding
shell access if the device is compromised.

---

## Security model

Confidentiality and integrity of ingest are enforced by three independent
mechanisms: TLS encryption, pinning of the receiver's exact certificate
fingerprint, and a shared-secret token compared in constant time. These
operate within the SSH tunnel described above.

Availability is protected by bounded resource limits. The receiver caps
concurrent connections globally and per source address, and applies
timeouts to both the pre-authentication handshake and the idle post-
authentication state. The HTTP server caps concurrent workers globally and
per source address and enforces a request-read timeout. These bounds
prevent a single host from exhausting threads, memory, or sockets; defence
against distributed attack is delegated to the reverse proxy.

The web server sets `Content-Security-Policy`, `X-Frame-Options`,
`X-Content-Type-Options`, and `Referrer-Policy` on every response, refuses
directory listings, and rejects path traversal on both file-serving
prefixes.

Deployment guidance for co-hosting additional services — a second website
and a database — alongside the station is given in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): per-service system accounts,
localhost-only database binding, least-privilege database roles, and
per-service systemd sandboxing, such that compromise of one service does
not extend to another.

---

## Repository layout

```
install.py              installer for both roles
station/                server components
  server.py               ingest, watcher, and web server in one process
  receiver.py             TLS ingest from the Pi
  api.py                  conversion of stored CSV to the JSON the site reads
  status.py               liveness, solar ephemeris, station log
pi/                     Pi components
  sender.py               change detection and shipping
  setup_pi.sh             manual alternative to install.py
web/                    web interface (static; no build step)
  index.html              overview and solar-position visual
  weather.html  power.html  status.html  sun.html  fits.html  about.html  contact.html
  common.js               shared front-end functions
tools/                  operational scripts
  fetch_weather.py        observed data from Open-Meteo
  fetch_fits.py           spectrograms from the e-Callisto archive
  simulate_power.py       simulated rail telemetry (no external source exists)
  migrate_store_layout.py migration to the year/month/day layout
  stress_test.py          adversarial regression tests
deploy/                 service units and proxy examples
docs/
  TECHNICAL-GUIDE.md      system description and rationale
  DEPLOYMENT.md           public HTTPS hosting and co-hosting
data/                   local store (excluded from version control)
secrets/                TLS material and token (excluded from version control)
```

### Storage layout

Both data stores use a year/month/day directory hierarchy, consistent with
the e-Callisto archive:

```
data/weather/2026/08/14/visnjan_weather_20260814.csv
data/power/2026/08/14/power_20260814.csv
data/fits/2025/09/09/Croatia-Visnjan_20250909_075911_03.fit.gz
```

The repository contains source code only. Acquired data resides in `data/`
and credentials in `secrets/`; both are excluded from version control. A
fresh clone reconstructs its own data.

---

## Interface

| Page | Content |
|---|---|
| Overview | Current values for all streams and the solar position |
| Weather | Temperature, dew point, humidity, pressure |
| Power | Voltage, current, and power for the heater, LNA, CALLISTO, and BME sensor |
| Status | Per-stream delivery state and daily coverage against expected counts |
| Sun | Daily sunrise and sunset, and the annual daylight curve |
| Spectrograms | CALLISTO FITS files decoded client-side |
| About | Station history, personnel, and the station log |
| Contact | Enquiry and station-identification details |

---

## Data provenance

- **Weather** — observed values for Višnjan from
  [Open-Meteo](https://open-meteo.com/).
- **Spectrograms** — files from the
  [e-Callisto archive](https://www.e-callisto.org/).
- **Power** — simulated. No external source exists for a single station's
  power rails; `tools/simulate_power.py` and its output are labelled as
  such. The ingest path is functional and awaits the monitoring hardware.

---

## Documentation

- [docs/TECHNICAL-GUIDE.md](docs/TECHNICAL-GUIDE.md) — component-level
  description of the system and its design rationale.
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — public HTTPS deployment and
  co-hosting of additional services.
- [CHANGELOG.md](CHANGELOG.md) — release history, also rendered in the
  About page's station log.
