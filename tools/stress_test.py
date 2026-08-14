#!/usr/bin/env python3
"""Adversarial tests for the station pipeline.

Not a unit-test suite -- this deliberately feeds the code the things that
break file-processing systems in the field: empty files, wrong columns,
mixed line endings, non-UTF-8 bytes, absurd values, duplicate and
out-of-order timestamps, a leap day, a DST boundary, and a file that is
still being written.

Run it after any change to station/. Every failure printed is a real
defect, not a style opinion.

    python tools/stress_test.py
"""

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from station import api, receiver, status

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    mark = "PASS" if condition else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not condition:
        line += f"\n         {detail}"
    print(line)


def write(path, text, encoding="utf-8", newline=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding, newline=newline) as f:
        f.write(text)


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# ====================================================================
# 1. Malformed and hostile CSV
# ====================================================================


def test_csv_robustness(tmp):
    print("\n1. Malformed CSV input")
    store = os.path.join(tmp, "weather")

    # Nothing at all.
    write(os.path.join(store, "2026", "01", "01", "empty_20260101.csv"), "")

    # Header only, no rows.
    write(
        os.path.join(store, "2026", "01", "02", "header_20260102.csv"),
        "timestamp,temp_c\n",
    )

    # No timestamp column at all -- must be skipped, not crash.
    write(
        os.path.join(store, "2026", "01", "03", "nots_20260103.csv"), "foo,bar\n1,2\n"
    )

    # Ragged rows: too few and too many fields.
    write(
        os.path.join(store, "2026", "01", "04", "ragged_20260104.csv"),
        "timestamp,temp_c,humidity_pct\n"
        "2026-01-04 00:00:00\n"
        "2026-01-04 01:00:00,5.0,50,extra,more\n"
        "2026-01-04 02:00:00,6.0,55\n",
    )

    # Values that are not numbers.
    write(
        os.path.join(store, "2026", "01", "05", "junk_20260105.csv"),
        "timestamp,temp_c,humidity_pct\n"
        "2026-01-05 00:00:00,not-a-number,fifty\n"
        "2026-01-05 01:00:00,,\n"
        "2026-01-05 02:00:00,7.5,60\n",
    )

    # Unparseable timestamps.
    write(
        os.path.join(store, "2026", "01", "06", "badts_20260106.csv"),
        "timestamp,temp_c\n"
        "yesterday,5\n"
        "2026-13-45 99:99:99,6\n"
        "2026-01-06 03:00:00,8.0\n",
    )

    # Windows line endings and a UTF-8 BOM.
    write_bytes(
        os.path.join(store, "2026", "01", "07", "bom_20260107.csv"),
        b"\xef\xbb\xbftimestamp,temp_c\r\n2026-01-07 00:00:00,9.5\r\n",
    )

    # Latin-1 bytes that are not valid UTF-8.
    write_bytes(
        os.path.join(store, "2026", "01", "08", "latin_20260108.csv"),
        b"timestamp,temp_c,note\n2026-01-08 00:00:00,10.0,caf\xe9\n",
    )

    # Physically absurd values -- should pass through, not be silently
    # "corrected", but must not crash anything downstream.
    write(
        os.path.join(store, "2026", "01", "09", "wild_20260109.csv"),
        "timestamp,temp_c,humidity_pct,pressure_hpa\n"
        "2026-01-09 00:00:00,-999,150,0\n"
        "2026-01-09 01:00:00,1e308,-5,99999\n",
    )

    # Duplicate and out-of-order timestamps.
    write(
        os.path.join(store, "2026", "01", "10", "order_20260110.csv"),
        "timestamp,temp_c\n"
        "2026-01-10 05:00:00,5\n"
        "2026-01-10 01:00:00,1\n"
        "2026-01-10 05:00:00,5\n"
        "2026-01-10 03:00:00,3\n",
    )

    try:
        readings = api.load_all_readings(store)
        check("survives every malformed file", True)
    except Exception as e:
        check("survives every malformed file", False, f"{type(e).__name__}: {e}")
        return

    check(
        "skips files without a timestamp column", all("foo" not in r for r in readings)
    )

    stamps = [r["timestamp"] for r in readings]
    check("output is sorted chronologically", stamps == sorted(stamps))

    check(
        "BOM did not corrupt the first column",
        any(r["timestamp"].startswith("2026-01-07") for r in readings),
        "a BOM on the header makes the first column name '\\ufefftimestamp'",
    )

    check(
        "non-UTF-8 bytes did not stop the file",
        any(r["timestamp"].startswith("2026-01-08") for r in readings),
    )

    check(
        "unparseable timestamps dropped, good rows kept",
        any(r["timestamp"].startswith("2026-01-06T03") for r in readings),
    )

    check(
        "non-numeric values become null, not zero",
        all(
            r["temp_c"] != 0.0 or r["temp_c"] is None
            for r in readings
            if r["timestamp"].startswith("2026-01-05T00")
        ),
    )

    return readings


# ====================================================================
# 2. Filename safety
# ====================================================================


def test_filename_safety():
    print("\n2. Filename handling")

    hostile = [
        "../../../../etc/passwd",
        "..\\..\\windows\\system32\\config.csv",
        "/etc/shadow.csv",
        "C:\\Windows\\evil.csv",
        "....//....//x.csv",
        "normal.csv\x00.txt",
        "",
        ".",
        "..",
        "a" * 300 + ".csv",
    ]

    escaped = []
    for name in hostile:
        safe = receiver.safe_filename(name)
        if safe is None:
            continue
        target = os.path.abspath(receiver.store_path("/tmp/store", safe))
        if not target.startswith(os.path.abspath("/tmp/store")):
            escaped.append((name, target))

    check("no filename escapes the store", not escaped, str(escaped[:2]))

    check(
        "rejects a null byte in the name",
        receiver.safe_filename("normal.csv\x00.txt") is None
        or "\x00" not in (receiver.safe_filename("normal.csv\x00.txt") or ""),
        "a NUL can truncate the path in some filesystem layers",
    )

    check(
        "blob names must carry a known extension",
        receiver.safe_blob_name("evil.sh") is None
        and receiver.safe_blob_name("x.fit.gz") == "x.fit.gz",
    )

    check(
        "dates are read out of filenames",
        receiver.date_subdir("Croatia-Visnjan_20250909_075911_03.fit.gz")
        == ("2025", "09", "09"),
    )

    check(
        "an impossible date is not accepted",
        receiver.date_subdir("x_20259999_000000_00.fit.gz") is None,
    )

    check(
        "undated files are filed as undated, not guessed",
        receiver.date_subdir("rolling.csv") is None,
    )


# ====================================================================
# 3. Gaps, downsampling, ranges
# ====================================================================


def test_processing():
    print("\n3. Gap detection and downsampling")

    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    readings = []
    for i in range(200):
        # A deliberate 10-hour hole after the 100th sample.
        offset = i if i < 100 else i + 600
        readings.append(
            {
                "timestamp": (base + timedelta(minutes=offset)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "temp_c": float(i),
                "humidity_pct": 50.0,
                "pressure_hpa": 1013.0,
                "dew_point_c": 5.0,
            }
        )

    marked = api.insert_gap_markers(readings)
    gaps = [r for r in marked if r.get("gap")]
    check(
        "an outage produces exactly one gap marker", len(gaps) == 1, f"got {len(gaps)}"
    )
    check(
        "the gap marker carries no values",
        all(gaps[0].get(k) is None for k in ("temp_c", "humidity_pct"))
        if gaps
        else False,
    )

    fields = ("temp_c", "humidity_pct", "pressure_hpa", "dew_point_c")
    reduced = api.downsample(marked, 50, fields)
    check(
        "downsampling respects the point budget",
        len(reduced) <= 55,
        f"asked for 50, got {len(reduced)}",
    )
    check("downsampling preserves gap markers", any(r.get("gap") for r in reduced))

    values = [r["temp_c"] for r in reduced if r.get("temp_c") is not None]
    check(
        "averaging keeps the data's range",
        values and min(values) < 10 and max(values) > 180,
        f"min {min(values) if values else None} max {max(values) if values else None}",
    )

    # Downsampling must never be destructive when it isn't needed.
    same = api.downsample(marked, 10000, fields)
    check("no downsampling below the budget", len(same) == len(marked))

    # Single reading, and empty input.
    check(
        "one reading survives downsampling",
        len(api.downsample(readings[:1], 50, fields)) == 1,
    )
    check("empty input does not crash", api.downsample([], 50, fields) == [])
    check("empty input has no gap markers", api.insert_gap_markers([]) == [])

    # Slicing.
    sliced = api.slice_hours(readings, 1)
    check(
        "slice_hours is anchored on the newest reading",
        sliced and sliced[-1] == readings[-1],
    )
    check(
        "slice_hours(0) means everything",
        len(api.slice_hours(readings, 0)) == len(readings),
    )


# ====================================================================
# 4. Solar ephemeris
# ====================================================================


def test_sun():
    print("\n4. Solar ephemeris")

    from datetime import date

    def ev(d):
        return status._solar_events(
            date.fromisoformat(d), status.STATION_LAT, status.STATION_LON
        )

    summer = ev("2026-06-21")
    winter = ev("2026-12-21")
    equinox = ev("2026-03-20")

    check(
        "solar noon is near 11:00 UTC for 13.7E",
        "10:5" in summer["solar_noon"]
        or "11:0" in summer["solar_noon"]
        or "11:1" in summer["solar_noon"],
        f"got {summer['solar_noon']}",
    )

    check(
        "longest day is about 15.6 h at 45N",
        15.4 < summer["day_length_hours"] < 15.9,
        f"got {summer['day_length_hours']}",
    )

    check(
        "shortest day is about 8.7 h at 45N",
        8.5 < winter["day_length_hours"] < 9.0,
        f"got {winter['day_length_hours']}",
    )

    check(
        "equinox day is close to 12 h",
        11.8 < equinox["day_length_hours"] < 12.4,
        f"got {equinox['day_length_hours']}",
    )

    check(
        "sunrise precedes solar noon precedes sunset",
        summer["sunrise"] < summer["solar_noon"] < summer["sunset"],
    )

    check("civil dawn precedes sunrise", summer["civil_dawn"] < summer["sunrise"])

    # Awkward dates.
    for d in ("2024-02-29", "2026-01-01", "2026-12-31", "2027-03-28"):
        e = ev(d)
        if not (e and e["sunrise"] and e["sunset"]):
            check(f"handles {d}", False, "missing an event")
            break
    else:
        check("handles leap day, year boundaries and the DST switch", True)

    # Polar latitudes must return None rather than a nonsense number.
    polar = status._solar_events(date(2026, 6, 21), 78.9, 11.9)  # Svalbard
    check(
        "polar day returns None rather than inventing a sunrise",
        polar["sunrise"] is None and polar["solar_noon"] is not None,
    )


# ====================================================================
# 5. Status / coverage
# ====================================================================


def test_status():
    print("\n5. Liveness and coverage")

    now = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)

    fresh = status.stream_state("fits", "2026-08-14T11:55:00Z", now)
    stale = status.stream_state("fits", "2026-08-14T10:00:00Z", now)
    dead = status.stream_state("fits", "2026-08-01T00:00:00Z", now)
    never = status.stream_state("fits", None, now)

    check("recent data reads as up", fresh["state"] == "up")
    check(
        "an hour late reads as late", stale["state"] == "late", f"got {stale['state']}"
    )
    check("two weeks silent reads as down", dead["state"] == "down")
    check("no data ever is distinct from down", never["state"] == "no-data")

    # A future timestamp -- a Pi with a wrong clock, which does happen.
    future = status.stream_state("fits", "2027-01-01T00:00:00Z", now)
    check(
        "a clock ahead of the server does not read as down",
        future["state"] == "up",
        f"got {future['state']}, age {future['age_seconds']}",
    )

    cov = status.daily_coverage({"2026-08-14": 48}, 96, ["2026-08-14", "2026-08-15"])
    check("half the expected files is 50% coverage", cov[0]["coverage_pct"] == 50.0)
    check("a day with nothing is 0%, not absent", cov[1]["coverage_pct"] == 0.0)

    over = status.daily_coverage({"2026-08-14": 200}, 96, ["2026-08-14"])
    check("coverage is capped at 100%", over[0]["coverage_pct"] == 100.0)

    check("empty stores produce no span", status.span_days({}, {}) == [])


# ====================================================================
# 6. End-to-end generation
# ====================================================================


def test_generation(tmp):
    print("\n6. End-to-end API generation")

    # Its own store: section 1 deliberately fills tmp/weather with broken
    # files, and mixing them in here would make the counts meaningless.
    weather = os.path.join(tmp, "gen", "weather")
    power = os.path.join(tmp, "gen", "power")
    out = os.path.join(tmp, "gen", "api")

    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    rows = ["timestamp,temp_c,humidity_pct,pressure_hpa"]
    for i in range(500):
        t = base + timedelta(minutes=15 * i)
        rows.append(
            f"{t:%Y-%m-%d %H:%M:%S},{20 + i % 10}.0,{50 + i % 20},{1010 + i % 5}.0"
        )
    write(
        os.path.join(weather, "2026", "08", "01", "w_20260801.csv"),
        "\n".join(rows) + "\n",
    )

    prows = [
        "timestamp,heater_v,heater_ma,lna_v,lna_ma,callisto_v,callisto_ma,bme_v,bme_ma"
    ]
    for i in range(300):
        t = base + timedelta(minutes=15 * i)
        prows.append(
            f"{t:%Y-%m-%d %H:%M:%S},12.1,{100 + i % 50},12.0,90,12.0,420,3.3,3.1"
        )
    write(
        os.path.join(power, "2026", "08", "01", "power_20260801.csv"),
        "\n".join(prows) + "\n",
    )

    total, emitted = api.generate(weather, os.path.join(out, "weather"), 0)
    check("weather generation returns a count", total == 500, f"got {total}")

    n = api.generate_power(power, os.path.join(out, "power"))
    check("power generation returns a count", n >= 300, f"got {n}")

    import json

    for slug, _ in api.WEATHER_RANGES:
        p = os.path.join(out, "weather", f"history-{slug}.json")
        if not os.path.exists(p):
            check(f"weather range file {slug} written", False)
            break
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if len(d["readings"]) > api.MAX_POINTS_PER_RANGE + 10:
            check(
                f"weather range {slug} respects the budget",
                False,
                f"{len(d['readings'])} points",
            )
            break
    else:
        check("every weather range file is written and bounded", True)

    with open(os.path.join(out, "weather", "latest.json"), encoding="utf-8") as f:
        latest = json.load(f)
    check(
        "latest is a real reading, not a gap marker", latest.get("temp_c") is not None
    )

    with open(os.path.join(out, "power", "latest.json"), encoding="utf-8") as f:
        plat = json.load(f)
    check(
        "power totals are derived, not stored",
        plat["total_watts"] and plat["total_watts"] > 0,
    )

    rails = plat["rails"]
    watts = rails["callisto"]["watts"]
    expected = rails["callisto"]["volts"] * rails["callisto"]["milliamps"] / 1000
    check(
        "watts equal volts x amps exactly",
        abs(watts - expected) < 0.01,
        f"{watts} vs {expected}",
    )


# ====================================================================
# 7. Idempotence
# ====================================================================


def test_idempotence(tmp):
    print("\n7. Repeat runs")

    weather = os.path.join(tmp, "gen", "weather")
    out1 = os.path.join(tmp, "api_a")
    out2 = os.path.join(tmp, "api_b")

    api.generate(weather, os.path.join(out1, "weather"), 0)
    api.generate(weather, os.path.join(out2, "weather"), 0)

    import filecmp

    same = filecmp.cmp(
        os.path.join(out1, "weather", "history-24h.json"),
        os.path.join(out2, "weather", "history-24h.json"),
        shallow=False,
    )
    check(
        "the same input always produces the same output",
        same,
        "non-determinism here would make the change-detector rebuild forever",
    )

    h1 = api.hash_dir(weather)
    h2 = api.hash_dir(weather)
    check("the store hash is stable across calls", h1 == h2)


# ====================================================================


def main():
    tmp = tempfile.mkdtemp(prefix="dorm_stress_")
    print(f"scratch: {tmp}")
    try:
        test_csv_robustness(tmp)
        test_filename_safety()
        test_processing()
        test_sun()
        test_status()
        test_generation(tmp)
        test_idempotence(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("\n  Failures:")
        for name in FAIL:
            print(f"    - {name}")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
