#!/usr/bin/env python3
"""Fetch real observed weather for Višnjan and write it as a station CSV.

This is a DEMO/PREVIEW utility, not part of the live pipeline. In
production the CSVs in the incoming directory are shipped from the Pi by
pi/sender.py. This script fills that directory with real measured data so
the dashboard can be developed and demonstrated without a live Pi.

Source: Open-Meteo's historical reanalysis archive (ERA5-based), which is
free and needs no API key. Values are real observations/reanalysis for the
Višnjan coordinates, not simulated.

Dependency-free: standard library only.
"""
import argparse
import csv
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

# Višnjan Observatory, Istria, Croatia.
LATITUDE = 45.2775
LONGITUDE = 13.7222

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "dew_point_2m",
    "wind_speed_10m",
    "wind_direction_10m",
]

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def degrees_to_compass(degrees):
    if degrees is None:
        return ""
    return COMPASS[int((float(degrees) / 22.5) + 0.5) % 16]


def fetch(url, params):
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{query}", timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_range(start, end):
    """Pull a date range, falling back to the forecast API's past_days.

    The reanalysis archive lags real time by several days, so the most
    recent days only exist on the forecast endpoint. Merge both and let
    the archive win on overlap (it's the better-quality series).
    """
    common = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": "UTC",
    }

    merged = {}

    try:
        archive = fetch(ARCHIVE_URL, dict(
            common, start_date=start.isoformat(), end_date=end.isoformat()
        ))
        merged.update(rows_from(archive))
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        print(f"archive fetch failed ({e}); relying on the forecast endpoint")

    days_back = min(92, (date.today() - start).days + 1)
    try:
        recent = fetch(FORECAST_URL, dict(common, past_days=days_back, forecast_days=1))
        for ts, row in rows_from(recent).items():
            merged.setdefault(ts, row)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        print(f"forecast fetch failed ({e})")

    return [merged[key] for key in sorted(merged)]


def rows_from(payload):
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    out = {}

    for i, stamp in enumerate(times):
        def value(field):
            series = hourly.get(field) or []
            return series[i] if i < len(series) else None

        temp = value("temperature_2m")
        if temp is None:
            continue  # a row without a temperature isn't a usable reading

        out[stamp] = [
            stamp.replace("T", " ") + ":00" if len(stamp) == 16 else stamp.replace("T", " "),
            temp,
            value("relative_humidity_2m"),
            value("surface_pressure"),
            value("wind_speed_10m"),
            degrees_to_compass(value("wind_direction_10m")),
        ]

    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=35, help="how many days back to fetch")
    ap.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(__file__), "..", "data", "weather"),
        help="root of the weather store; files are written to <root>/YYYY/MM/DD/",
    )
    ap.add_argument(
        "--simulate-outage",
        nargs=2,
        metavar=("START_DATE", "DAYS"),
        help="drop readings for DAYS days from START_DATE, to exercise the "
             "dashboard's data-gap rendering (e.g. --simulate-outage 2026-07-25 2)",
    )
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)

    print(f"fetching real observations for Višnjan ({LATITUDE}, {LONGITUDE})")
    print(f"range: {start} .. {end}")

    rows = fetch_range(start, end)
    if not rows:
        raise SystemExit("no data returned -- check network access to open-meteo.com")

    if args.simulate_outage:
        outage_start = datetime.strptime(args.simulate_outage[0], "%Y-%m-%d")
        outage_end = outage_start + timedelta(days=float(args.simulate_outage[1]))
        before = len(rows)
        rows = [
            row for row in rows
            if not (outage_start <= datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S") < outage_end)
        ]
        print(
            f"simulated outage {outage_start:%Y-%m-%d} for {args.simulate_outage[1]} day(s): "
            f"dropped {before - len(rows)} readings"
        )

    store = os.path.abspath(args.out)
    header = [
        "timestamp", "temp_c", "humidity_pct",
        "pressure_hpa", "wind_speed_kmh", "wind_dir",
    ]

    # One file per day, in a year/month/day tree -- the same shape the real
    # station produces (it rotates its log at UTC midnight) and the same
    # layout the receiver writes into.
    by_day = {}
    for row in rows:
        day = row[0][:10]                       # "2026-08-13"
        by_day.setdefault(day, []).append(row)

    for day, day_rows in sorted(by_day.items()):
        year, month, dd = day.split("-")
        out_dir = os.path.join(store, year, month, dd)
        os.makedirs(out_dir, exist_ok=True)

        out_path = os.path.join(out_dir, f"visnjan_weather_{year}{month}{dd}.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(day_rows)

    print(f"wrote {len(rows)} hourly readings across {len(by_day)} day-files under {store}")
    print(f"first: {rows[0]}")
    print(f"last:  {rows[-1]}")


if __name__ == "__main__":
    main()
