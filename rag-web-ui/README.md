# RAG Web UI — DORM station dashboard

The browser front-end for the Višnjan station: live weather charts and a
CALLISTO spectrogram viewer.

It is served by [`server.py`](../server.py) in the project root — you do
not run anything in this folder to view the site. See
[M&M EXPLANATION FOR DUMMIES.md](../M&M%20EXPLANATION%20FOR%20DUMMIES.md)
for a full walkthrough of how it all works.

```
generate_dashboard_api.py   CSV -> dashboard JSON (imported by server.py)
fetch_demo_weather.py       demo data: real observed weather for Višnjan
fetch_visnjan_fits.py       downloads real CALLISTO spectrograms
web/index.html              weather dashboard
web/fits.html               spectrogram viewer
web/styles.css              shared styling
web/vendor/chart.js         Chart.js, vendored so it works offline
```

## Viewing it

```bash
python ../server.py
```

Then open <http://127.0.0.1:8090/>.

## Loading data without a Pi

```bash
python fetch_demo_weather.py --days 35
python fetch_visnjan_fits.py --days 3
```

`fetch_demo_weather.py` pulls **real measured** weather for Višnjan's
coordinates from Open-Meteo (free, no API key) and writes it in the
station's own CSV format, so the dashboard can be developed without a live
Pi. Add `--simulate-outage 2026-07-25 2` to delete a couple of days and
confirm the charts render the gap as a break rather than interpolating
across it.

`fetch_visnjan_fits.py` downloads real spectrograms from the central
e-Callisto archive, searching backwards for days that actually contain
data. **Note:** the Višnjan station appears to have been offline since
September 2025, so "latest" currently means 2025-09-07 → 2025-09-09.

## The two pages

**`index.html`** — temperature, dew point, humidity and pressure over
24H / 7D / 30D / all time. Times are shown in both UTC and local. Dew point
is computed (Magnus formula) rather than measured. Data gaps render as
breaks in the line.

**`fits.html`** — decodes CALLISTO `.fit.gz` files entirely in the browser
(gunzip → FITS parse → canvas). Per-channel median background subtraction
is what makes bursts visible; percentile-based contrast stops a single RFI
spike from flattening the image. The frequency axis is read from the file's
frequency table, not the header, because the header value is wrong.

## Notes

- Chart.js is vendored in `web/vendor/` — no internet needed to render.
- Chart colours were validated for colour-blind separation and contrast
  rather than picked by eye.
- Everything under `web/api/` is generated at runtime and gitignored.
