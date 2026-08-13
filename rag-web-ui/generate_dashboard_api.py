#!/usr/bin/env python3
"""ARRAY-7 dashboard API generator.

Watches windows/incoming_logs/*.csv (the shipper's own output -- this
script never touches pi/sender.py or windows/receiver.py, so it can't
affect delivery correctness) and regenerates the two static JSON files the
dashboard reads:

  web interface/api/weather/latest.json
  web interface/api/weather/history.json

Any CSV in the watched directory that has a "timestamp" column (matched
case-insensitively) is included; files without one (stray test data,
whatever) are skipped rather than raising. A "temp_c" column is used if
present; readings without one still count for the timeline but show a
null temperature.

CSV timestamps in this project are naive "YYYY-MM-DD HH:MM:SS" (space
separated, no timezone) but are always UTC (eCallisto rotates log files on
UTC midnight). They're normalized here to ISO-8601 with a trailing "Z" so
the browser's `new Date(...)` parses them as UTC instead of silently
treating them as local time.

Dependency-free: standard library only.
"""
import argparse
import csv
import glob
import hashlib
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def normalize_timestamp(raw):
    raw = raw.strip()
    if not raw:
        return None
    iso = raw.replace(" ", "T", 1) if ("T" not in raw and " " in raw) else raw
    try:
        # Accept a trailing Z or an explicit offset if the source ever adds one.
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_field(fieldnames, *candidates):
    lowered = {name.strip().lower(): name for name in fieldnames if name}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def parse_number(raw):
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def dew_point_c(temp_c, humidity_pct):
    """Magnus-Tetens approximation, the standard meteorological formula.

    Valid roughly 0-60 C / 1-100% RH, which covers anything this station
    will see. Returns None rather than guessing when either input is
    missing or the humidity is outside a physically meaningful range.
    """
    if temp_c is None or humidity_pct is None:
        return None
    if not (0 < humidity_pct <= 100):
        return None
    a, b = 17.27, 237.7
    alpha = ((a * temp_c) / (b + temp_c)) + math.log(humidity_pct / 100.0)
    return round((b * alpha) / (a - alpha), 1)


def find_csv_files(incoming_dir):
    """Every CSV in the store, at any depth.

    The store is laid out year/month/day, so a flat glob would find
    nothing. Sorted by path, which for this layout is also chronological
    order -- zero-padded date components sort correctly as text.
    """
    return sorted(
        glob.glob(os.path.join(incoming_dir, "**", "*.csv"), recursive=True)
    )


def load_all_readings(incoming_dir):
    readings = []
    for path in find_csv_files(incoming_dir):
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    continue
                ts_field = find_field(reader.fieldnames, "timestamp", "time", "datetime")
                if not ts_field:
                    continue  # not one of our weather logs -- skip quietly
                temp_field = find_field(reader.fieldnames, "temp_c", "temperature", "temp")
                humidity_field = find_field(
                    reader.fieldnames, "humidity_pct", "humidity", "rh"
                )
                pressure_field = find_field(
                    reader.fieldnames, "pressure_hpa", "pressure", "baro"
                )

                for row in reader:
                    ts = normalize_timestamp(row.get(ts_field, "") or "")
                    if ts is None:
                        continue
                    temp = parse_number(row.get(temp_field)) if temp_field else None
                    humidity = parse_number(row.get(humidity_field)) if humidity_field else None
                    readings.append({
                        "timestamp": ts,
                        "temp_c": temp,
                        "humidity_pct": humidity,
                        "pressure_hpa": (
                            parse_number(row.get(pressure_field)) if pressure_field else None
                        ),
                        "dew_point_c": dew_point_c(temp, humidity),
                    })
        except (OSError, csv.Error):
            continue

    readings.sort(key=lambda r: r["timestamp"])
    return readings


def to_epoch(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def insert_gap_markers(readings, gap_factor=3.0):
    """Insert an all-null reading inside any unexpectedly long gap.

    Without this the chart would draw a straight line across an outage,
    silently inventing data that was never measured. A null-valued point
    makes the line break instead, so downtime looks like downtime. The
    threshold is relative to the series' own median cadence, so it works
    for 10-second or hourly logging alike.
    """
    if len(readings) < 3:
        return readings

    times = [to_epoch(r["timestamp"]) for r in readings]
    deltas = sorted(times[i + 1] - times[i] for i in range(len(times) - 1))
    median = deltas[len(deltas) // 2]
    if median <= 0:
        return readings

    threshold = median * gap_factor
    value_keys = [k for k in readings[0] if k != "timestamp"]

    out = []
    for i, reading in enumerate(readings):
        out.append(reading)
        if i + 1 < len(readings) and (times[i + 1] - times[i]) > threshold:
            marker_time = datetime.fromtimestamp(times[i] + median, tz=timezone.utc)
            marker = {"timestamp": marker_time.strftime("%Y-%m-%dT%H:%M:%SZ"), "gap": True}
            marker.update({key: None for key in value_keys})
            out.append(marker)

    return out


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def generate(incoming_dir, api_dir, history_hours):
    readings = load_all_readings(incoming_dir)

    fields = ("temp_c", "humidity_pct", "pressure_hpa", "dew_point_c")

    if readings:
        # "latest" must be a real measurement, so read it before gap
        # markers (which are deliberately all-null) are mixed in.
        last = readings[-1]
        readings = insert_gap_markers(readings)
        latest_out = {"timestamp": last["timestamp"]}
        latest_out.update({key: last[key] for key in fields})
        # history_hours <= 0 means "emit everything" -- the page filters to
        # the selected range (1D/7D/30D/all) client-side, so the API has to
        # carry more than the shortest range.
        if history_hours > 0:
            cutoff_dt = (
                datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00"))
                - timedelta(hours=history_hours)
            )
            history_readings = [
                r for r in readings
                if datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) >= cutoff_dt
            ]
        else:
            history_readings = readings
    else:
        latest_out = {"timestamp": None}
        latest_out.update({key: None for key in fields})
        history_readings = []

    write_json_atomic(os.path.join(api_dir, "latest.json"), latest_out)
    write_json_atomic(os.path.join(api_dir, "history.json"), {"readings": history_readings})
    return len(readings), len(history_readings)


def hash_dir(incoming_dir):
    h = hashlib.sha256()
    for path in find_csv_files(incoming_dir):
        try:
            with open(path, "rb") as f:
                h.update(path.encode("utf-8", "replace"))
                h.update(f.read())
        except OSError:
            continue
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--incoming-dir",
        default=os.path.join(os.path.dirname(__file__), "..", "windows", "incoming_logs"),
    )
    ap.add_argument(
        "--api-dir",
        default=os.path.join(os.path.dirname(__file__), "web", "api", "weather"),
    )
    ap.add_argument(
        "--history-hours",
        type=float,
        default=0.0,
        help="cap history to the last N hours; 0 (default) emits everything",
    )
    ap.add_argument("--poll-interval", type=float, default=2.0)
    ap.add_argument("--once", action="store_true", help="generate once and exit")
    args = ap.parse_args()

    incoming_dir = os.path.abspath(args.incoming_dir)
    api_dir = os.path.abspath(args.api_dir)
    log(f"watching {incoming_dir}")
    log(f"writing {api_dir}")

    last_hash = None
    while True:
        digest = hash_dir(incoming_dir)
        if digest != last_hash:
            total, windowed = generate(incoming_dir, api_dir, args.history_hours)
            window = f"{args.history_hours:g}h window" if args.history_hours > 0 else "no cap"
            log(f"regenerated dashboard API: {total} total readings, {windowed} emitted ({window})")
            last_hash = digest
        if args.once:
            break
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
