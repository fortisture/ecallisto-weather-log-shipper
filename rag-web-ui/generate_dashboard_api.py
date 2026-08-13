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


def parse_temp(raw):
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def load_all_readings(incoming_dir):
    readings = []
    for path in sorted(glob.glob(os.path.join(incoming_dir, "*.csv"))):
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    continue
                ts_field = find_field(reader.fieldnames, "timestamp", "time", "datetime")
                if not ts_field:
                    continue  # not one of our weather logs -- skip quietly
                temp_field = find_field(reader.fieldnames, "temp_c", "temperature", "temp")

                for row in reader:
                    ts = normalize_timestamp(row.get(ts_field, "") or "")
                    if ts is None:
                        continue
                    readings.append({
                        "timestamp": ts,
                        "temp_c": parse_temp(row.get(temp_field)) if temp_field else None,
                    })
        except (OSError, csv.Error):
            continue

    readings.sort(key=lambda r: r["timestamp"])
    return readings


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def generate(incoming_dir, api_dir, history_hours):
    readings = load_all_readings(incoming_dir)

    if readings:
        last = readings[-1]
        latest_out = {"timestamp": last["timestamp"], "temp_c": last["temp_c"]}
        cutoff_dt = (
            datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00"))
            - timedelta(hours=history_hours)
        )
        history_readings = [
            r for r in readings
            if datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) >= cutoff_dt
        ]
    else:
        latest_out = {"timestamp": None, "temp_c": None}
        history_readings = []

    write_json_atomic(os.path.join(api_dir, "latest.json"), latest_out)
    write_json_atomic(os.path.join(api_dir, "history.json"), {"readings": history_readings})
    return len(readings), len(history_readings)


def hash_dir(incoming_dir):
    h = hashlib.sha256()
    for path in sorted(glob.glob(os.path.join(incoming_dir, "*.csv"))):
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
    ap.add_argument("--history-hours", type=float, default=24.0)
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
            log(f"regenerated dashboard API: {total} total readings, {windowed} in the {args.history_hours:g}h window")
            last_hash = digest
        if args.once:
            break
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
