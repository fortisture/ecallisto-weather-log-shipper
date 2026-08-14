# DORM — Višnjan e-Callisto Station

Monitoring for the **Croatia-Visnjan** solar radio spectrometer at Višnjan
Observatory, Istria. Collects weather, power-rail telemetry and CALLISTO
spectrograms from a Raspberry Pi at the telescope, stores them on a server,
and serves a website that shows the lot.

Everything here is **dependency-free Python 3 + static HTML**. No pip, no
npm, no database.

```
Raspberry Pi  ──SSH tunnel──►  Ubuntu server  ──HTTPS──►  the web
(instrument)                   (store + site)
```

---

## Quick start (local, no install)

To run it on one machine and look at it:

```bash
git clone https://github.com/fortisture/ecallisto-weather-log-shipper.git
cd ecallisto-weather-log-shipper

python3 install.py secrets            # TLS keypair + shared token
python3 tools/fetch_weather.py        # real weather history for Višnjan
python3 tools/simulate_power.py       # simulated rail telemetry
python3 tools/fetch_fits.py           # real spectrograms from the archive

python3 station/server.py
```

Open <http://127.0.0.1:8090/>.

---

## Installing for real

Two machines, two commands. The installer prints every privileged command
before it runs it, and never installs packages behind your back.

### 1 · Server (Ubuntu)

```bash
sudo python3 install.py server
```

It will:

1. generate the TLS keypair and shared token in `secrets/`,
2. create `data/{weather,power,fits}`,
3. write and start `dorm-station.service`,
4. print the **token** and **fingerprint** you need for the Pi.

Both the receiver and the web server bind to **127.0.0.1 only**. Nothing
is publicly reachable except `sshd`.

```bash
systemctl status dorm-station
journalctl -u dorm-station -f
```

### 2 · Pi (Raspberry Pi OS)

```bash
sudo python3 install.py pi
```

It asks for the server's SSH details and the token/fingerprint from step 1,
then:

1. generates an SSH key for the tunnel,
2. installs `sender.py` and its config to `/opt/dorm`,
3. writes `dorm-tunnel.service` and `dorm-sender.service`,
4. prints the public key to authorise on the server.

Add that key on the **server**, then start both services on the Pi:

```bash
sudo systemctl enable --now dorm-tunnel dorm-sender
journalctl -u dorm-sender -f
```

### 3 · Public HTTPS

The site listens on localhost, so put a reverse proxy in front of it. See
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

### Checking an install

```bash
python3 install.py check
```

---

## Why an SSH tunnel

The Pi and the server are on different networks, and only port 22 is open
on the server. Rather than exposing a data port to the internet, the Pi
opens an SSH tunnel and speaks to the receiver through it:

```
Pi                                        Ubuntu server
sender.py ─► 127.0.0.1:19443
                  │
                  └── ssh -L ──────────► 127.0.0.1:9443  (receiver)
                      (port 22)
```

The TLS + certificate-pinning + shared-token layer stays in place *inside*
the tunnel. SSH authenticates the machines; the inner layer means even a
compromised tunnel cannot inject data. The Pi's key can be restricted in
`authorized_keys` so it can open that one forward and nothing else — no
shell, even if the Pi is stolen.

---

## Layout

```
install.py              one installer for both roles
station/                the server
  server.py               ingest + watcher + web server, one process
  receiver.py             TLS ingest from the Pi
  api.py                  CSV -> the JSON the site reads
  status.py               uptime, solar ephemeris, station log
pi/                     what runs on the Pi
  sender.py               watches files, ships changes
  setup_pi.sh             manual alternative to install.py
web/                    the site (static; no build step)
  index.html              overview + sun visual
  weather.html  power.html  status.html  sun.html  fits.html  about.html
  common.js               shared front-end helpers
tools/                  operational scripts
  fetch_weather.py        real observations from Open-Meteo
  fetch_fits.py           real spectrograms from the e-Callisto archive
  simulate_power.py       SIMULATED rail telemetry (no real source exists)
  migrate_store_layout.py moves an old flat store into year/month/day
deploy/                 service units and proxy examples
docs/
  TECHNICAL-GUIDE.md      how all of it works, in plain language
  DEPLOYMENT.md           public HTTPS hosting
data/                   the store (gitignored)
secrets/                TLS material and token (gitignored)
```

### The store

Both data stores are laid out **year/month/day**, mirroring the e-Callisto
archive:

```
data/weather/2026/08/14/visnjan_weather_20260814.csv
data/power/2026/08/14/power_20260814.csv
data/fits/2025/09/09/Croatia-Visnjan_20250909_075911_03.fit.gz
```

**The repository holds code, never data.** Everything collected lives in
`data/`, and all secrets in `secrets/` — both gitignored. Clone it anywhere
and it rebuilds its own data.

---

## The pages

| Page | What it shows |
|---|---|
| **Overview** | Everything current, plus where the sun is today |
| **Weather** | Temperature, dew point, humidity, pressure |
| **Power** | Volts/amps/watts for heater, LNA, CALLISTO, BME |
| **Status** | Whether each stream is actually delivering, and daily coverage |
| **Sun** | Today's sunrise/sunset, and the year's daylight curve |
| **Spectrograms** | The CALLISTO FITS files, decoded in the browser |
| **About** | The station's story, the people, and the station log |

---

## Data sources

- **Weather** — real observations for Višnjan from [Open-Meteo](https://open-meteo.com/).
- **Spectrograms** — real files from the [e-Callisto archive](https://www.e-callisto.org/).
- **Power** — **simulated.** There is no external source for one station's
  own rails. `tools/simulate_power.py` says so, and so does its output. The
  ingest path is real and waiting for the hardware.

---

## Documentation

- **[docs/TECHNICAL-GUIDE.md](docs/TECHNICAL-GUIDE.md)** — how every part
  works and why, written to be read without knowing the codebase.
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — getting it onto the public
  internet over HTTPS.
- **[CHANGELOG.md](CHANGELOG.md)** — every release. Also rendered on the
  About page's station log.
