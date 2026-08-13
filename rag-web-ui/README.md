# RAG Web UI — ARRAY-7 Dashboard

Reads live weather data shipped by [`pi/sender.py`](../pi/sender.py) /
received by [`windows/receiver.py`](../windows/receiver.py) and displays it
in the browser. Two independent pieces, both dependency-free:

- `generate_dashboard_api.py` — watches `../windows/incoming_logs/*.csv`
  (the receiver's own output; this script never touches the receiver or
  sender code) and regenerates `web/api/weather/latest.json` and
  `history.json` whenever that data changes.
- `web/` — the static dashboard itself (`index.html` + `styles.css`),
  which reads those two JSON files via `fetch()`.

## Running it

Two things need to be running at once:

```bash
# 1. Keep the dashboard's JSON data in sync with incoming_logs
python generate_dashboard_api.py

# 2. Serve the page itself (fetch() can't read local files directly,
#    it needs an actual http:// origin)
cd web
python -m http.server 8090
```

Then open `http://127.0.0.1:8090/index.html`.

## Notes

- The chart (`Temperature · 24H Trend`) uses Chart.js from a CDN
  (`cdnjs.cloudflare.com`). If the machine this runs on has restricted or
  no internet access, that chart won't load — the page is built to
  degrade gracefully in that case (shows "CHART LIBRARY UNAVAILABLE"
  instead of breaking), but if you want the chart to actually work
  offline, Chart.js would need to be vendored locally instead of loaded
  from the CDN. Not done here since it wasn't a problem in testing, but
  worth knowing before this runs somewhere without internet.
- Only CSV files with a `timestamp` column are picked up; a `temp_c`
  column is used for temperature if present. Files without a recognizable
  `timestamp` column are skipped silently, which is why test/garbage
  files ever written into `incoming_logs/` don't break the dashboard.
