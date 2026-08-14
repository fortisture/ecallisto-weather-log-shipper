#!/usr/bin/env python3
"""Generate SIMULATED power-rail telemetry for the DORM station.

Unlike fetch_demo_weather.py, which downloads real measured weather, there
is no external source for this station's own power rails -- these numbers
are invented. They are physically plausible, not measured, and exist so
the power page can be built and demonstrated before the real monitoring
hardware is reporting.

Once the Pi is logging real rail data, delete this and let sender.py ship
the real thing: any CSV whose name contains "power" is routed to the power
store automatically.

What is modelled:

  Dew heater  12 V, duty-cycled. Driven by the REAL weather store: the
              heater runs when the air temperature closes on the dew
              point, which is exactly when optics fog up. This is why the
              heater trace lines up with the weather page.
  LNA          12 V, ~90 mA, steady -- it is on whenever the station is.
  CALLISTO     12 V, ~420 mA, steady, with a small periodic bump as each
               15-minute file is written out.
  BME sensor   3.3 V, ~3 mA. Tiny, and it barely varies.

Dependency-free: standard library only.
"""
import argparse
import csv
import glob
import math
import os
import random
from datetime import datetime, timedelta, timezone

HEADER = [
    "timestamp",
    "heater_v", "heater_ma",
    "lna_v", "lna_ma",
    "callisto_v", "callisto_ma",
    "bme_v", "bme_ma",
]


def load_weather(weather_dir):
    """Read the real weather store so the heater can respond to it."""
    points = []
    pattern = os.path.join(weather_dir, "**", "*.csv")

    for path in sorted(glob.glob(pattern, recursive=True)):
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as f:
                for row in csv.DictReader(f):
                    stamp = (row.get("timestamp") or "").strip()
                    if not stamp:
                        continue
                    try:
                        when = datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S")
                        temp = float(row["temp_c"])
                        humidity = float(row["humidity_pct"])
                    except (ValueError, KeyError, TypeError):
                        continue

                    # Magnus dew point, same formula the dashboard uses.
                    if not 0 < humidity <= 100:
                        continue
                    a, b = 17.27, 237.7
                    alpha = ((a * temp) / (b + temp)) + math.log(humidity / 100.0)
                    dew = (b * alpha) / (a - alpha)

                    points.append((when.replace(tzinfo=timezone.utc), temp, dew))
        except OSError:
            continue

    points.sort()
    return points


def nearest_weather(points, when):
    """Closest weather sample to a moment, or None if the store is empty."""
    if not points:
        return None
    best = min(points, key=lambda p: abs((p[0] - when).total_seconds()))
    return best if abs((best[0] - when).total_seconds()) < 7200 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--interval-minutes", type=float, default=15.0)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "power"))
    ap.add_argument("--weather-dir",
                    default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "weather"))
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args()

    random.seed(args.seed)

    weather = load_weather(os.path.abspath(args.weather_dir))
    print(f"weather samples available for heater modelling: {len(weather)}")

    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    step = timedelta(minutes=args.interval_minutes)
    count = int(args.days * 24 * 60 / args.interval_minutes)
    start = end - step * (count - 1)

    rows = []
    when = start

    for i in range(count):
        sample = nearest_weather(weather, when)

        # ---- dew heater -------------------------------------------------
        # Runs harder the closer the air is to its dew point. Below ~1 C of
        # margin it is essentially full on; above ~6 C it idles.
        if sample:
            _, temp, dew = sample
            margin = temp - dew
        else:
            margin = 5.0

        duty = max(0.0, min(1.0, (6.0 - margin) / 5.0))
        heater_ma = 60 + duty * 1750 + random.gauss(0, 25)
        heater_ma = max(0.0, heater_ma)
        # Supply sags slightly under load -- a real 12 V rail does this.
        heater_v = 12.15 - duty * 0.35 + random.gauss(0, 0.02)

        # ---- LNA --------------------------------------------------------
        lna_ma = 92 + random.gauss(0, 1.2)
        lna_v = 12.05 + random.gauss(0, 0.015)

        # ---- CALLISTO ---------------------------------------------------
        # Small periodic bump as each 15-minute FITS file is written.
        writing = 18 if (i % max(1, int(15 / args.interval_minutes)) == 0) else 0
        callisto_ma = 418 + writing + random.gauss(0, 4)
        callisto_v = 12.02 + random.gauss(0, 0.015)

        # ---- BME sensor -------------------------------------------------
        bme_ma = 3.1 + random.gauss(0, 0.15)
        bme_v = 3.30 + random.gauss(0, 0.005)

        rows.append([
            when.strftime("%Y-%m-%d %H:%M:%S"),
            round(heater_v, 2), round(heater_ma, 1),
            round(lna_v, 2), round(lna_ma, 1),
            round(callisto_v, 2), round(callisto_ma, 1),
            round(bme_v, 3), round(bme_ma, 2),
        ])
        when += step

    store = os.path.abspath(args.out)

    by_day = {}
    for row in rows:
        by_day.setdefault(row[0][:10], []).append(row)

    for day, day_rows in sorted(by_day.items()):
        year, month, dd = day.split("-")
        out_dir = os.path.join(store, year, month, dd)
        os.makedirs(out_dir, exist_ok=True)

        with open(os.path.join(out_dir, f"power_{year}{month}{dd}.csv"),
                  "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(HEADER)
            writer.writerows(day_rows)

    print(f"wrote {len(rows)} SIMULATED readings across {len(by_day)} day-files under {store}")
    print(f"first: {rows[0]}")
    print(f"last:  {rows[-1]}")


if __name__ == "__main__":
    main()
