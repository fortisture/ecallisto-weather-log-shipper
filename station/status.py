#!/usr/bin/env python3
"""Station liveness and solar ephemeris for the DORM dashboards.

Two independent things the web UI needs that aren't measurements:

1. **Is the station actually up?** Not "did someone say it was" -- derived
   from evidence on disk: how recently each stream delivered data, and how
   much of each day's expected data actually arrived.

2. **When is the sun up?** Computed from the NOAA solar-position
   algorithm for the observatory's coordinates. No network call, no
   dependency, correct to well under a minute.

Dependency-free: standard library only.
"""
import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone

# Višnjan Observatory, Tićan, Istria.
# 45°16'39"N 13°43'34"E, ~250 m elevation.
STATION_LAT = 45.2775
STATION_LON = 13.7261

# CALLISTO writes one file per 15 minutes, continuously.
FITS_SLOT_MINUTES = 15
FITS_SLOTS_PER_DAY = 24 * 60 // FITS_SLOT_MINUTES     # 96

# How stale a stream may get before it counts as down. Generous multiples
# of each stream's own cadence, so a single missed sample isn't an outage.
STALE_LIMITS = {
    "weather": 3 * 3600,
    "power": 3 * 3600,
    "fits": 3 * FITS_SLOT_MINUTES * 60,
}


# --------------------------------------------------------------------
# Liveness
# --------------------------------------------------------------------

def stream_state(name, last_iso, now):
    """Classify a stream from how long ago it last delivered anything."""
    limit = STALE_LIMITS.get(name, 3600)

    if not last_iso:
        return {
            "stream": name,
            "last_seen": None,
            "age_seconds": None,
            "state": "no-data",
            "stale_after_seconds": limit,
        }

    last = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
    age = (now - last).total_seconds()

    if age <= limit:
        state = "up"
    elif age <= limit * 8:
        state = "late"
    else:
        state = "down"

    return {
        "stream": name,
        "last_seen": last_iso,
        "age_seconds": round(age),
        "state": state,
        "stale_after_seconds": limit,
    }


def daily_coverage(day_counts, expected_per_day, days):
    """Per-day delivered-vs-expected, as a percentage.

    Coverage is what "uptime" actually means for a logging station: not
    whether a machine answered a ping, but whether the data it was
    supposed to record exists.
    """
    out = []
    for day in days:
        got = day_counts.get(day, 0)
        pct = min(100.0, 100.0 * got / expected_per_day) if expected_per_day else 0.0
        out.append({
            "date": day,
            "count": got,
            "expected": expected_per_day,
            "coverage_pct": round(pct, 1),
        })
    return out


def count_by_day(timestamps):
    counts = {}
    for ts in timestamps:
        if ts:
            counts[ts[:10]] = counts.get(ts[:10], 0) + 1
    return counts


def span_days(*count_maps):
    """Every calendar day between the first and last data, inclusive.

    Days with nothing recorded have to appear explicitly -- a gap that is
    simply absent from the list reads as "fine" instead of "outage".
    """
    keys = [k for m in count_maps for k in m]
    if not keys:
        return []

    first = datetime.strptime(min(keys), "%Y-%m-%d").date()
    last = datetime.strptime(max(keys), "%Y-%m-%d").date()

    out = []
    cursor = first
    while cursor <= last:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def build_status(api_dir, weather, power, fits_index, now=None):
    """Write api/status/{latest,history}.json."""
    now = now or datetime.now(timezone.utc)

    weather_days = count_by_day([r.get("timestamp") for r in weather if not r.get("gap")])
    power_days = count_by_day([r.get("timestamp") for r in power if not r.get("gap")])

    fits_days = {
        day: len(files)
        for day, files in (fits_index.get("days") or {}).items()
    }

    real_weather = [r for r in weather if not r.get("gap")]
    real_power = [r for r in power if not r.get("gap")]

    streams = [
        stream_state("weather", real_weather[-1]["timestamp"] if real_weather else None, now),
        stream_state("power", real_power[-1]["timestamp"] if real_power else None, now),
        stream_state("fits", fits_last_seen(fits_index), now),
    ]

    # The station is only "up" if the instrument itself is producing --
    # weather still flowing while CALLISTO is silent is exactly the
    # failure this page exists to make visible.
    instrument = next(s for s in streams if s["stream"] == "fits")
    overall = instrument["state"]

    latest = {
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overall": overall,
        "streams": streams,
    }

    days = span_days(weather_days, power_days, fits_days)

    # Expected sample counts come from each stream's own observed cadence
    # rather than a hard-coded assumption, except FITS which is fixed by
    # the instrument at one file per 15 minutes.
    weather_expected = median_daily(weather_days) or 24
    power_expected = median_daily(power_days) or 96

    history = {
        "generated": latest["generated"],
        "days": days,
        "series": {
            "fits": daily_coverage(fits_days, FITS_SLOTS_PER_DAY, days),
            "weather": daily_coverage(weather_days, weather_expected, days),
            "power": daily_coverage(power_days, power_expected, days),
        },
        "expected": {
            "fits": FITS_SLOTS_PER_DAY,
            "weather": weather_expected,
            "power": power_expected,
        },
    }

    summary = {}
    for key, entries in history["series"].items():
        if entries:
            recorded = sum(1 for e in entries if e["count"] > 0)
            summary[key] = {
                "days_total": len(entries),
                "days_with_data": recorded,
                "mean_coverage_pct": round(
                    sum(e["coverage_pct"] for e in entries) / len(entries), 1
                ),
            }
    history["summary"] = summary
    latest["summary"] = summary

    write_json(os.path.join(api_dir, "status", "latest.json"), latest)
    write_json(os.path.join(api_dir, "status", "history.json"), history)
    return latest


def median_daily(day_counts):
    """Typical samples per day, ignoring partial first/last days."""
    counts = sorted(day_counts.values())
    if len(counts) < 3:
        return counts[len(counts) // 2] if counts else 0
    trimmed = counts[1:-1]
    return trimmed[len(trimmed) // 2]


def fits_last_seen(fits_index):
    """Timestamp of the most recent spectrogram, from the index."""
    days = fits_index.get("days") or {}
    if not days:
        return None
    day = max(days)
    files = days[day]
    if not files:
        return None
    return f"{day}T{files[-1]['time']}Z"


# --------------------------------------------------------------------
# Sun
# --------------------------------------------------------------------

def _solar_events(day, lat, lon):
    """Sunrise/solar noon/sunset for one date, in UTC hours.

    NOAA general solar position algorithm. Returns None for an event that
    does not occur (polar day/night) -- which never happens at this
    latitude, but returning None is honest rather than clamping.
    """
    # Julian Day Number. This is defined at NOON UT, which matters: the
    # algorithm below counts days from the J2000 epoch (2000-01-01 12:00),
    # so converting to midnight here would shift every result by 12 hours.
    a = (14 - day.month) // 12
    y = day.year + 4800 - a
    m = day.month + 12 * a - 3
    jdn = (day.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045)

    n = jdn - 2451545

    # Longitude east is positive here; the sun crosses an eastern meridian
    # earlier in UTC, hence the subtraction.
    mean_solar_noon = n + 0.0008 - lon / 360.0

    solar_mean_anomaly = (357.5291 + 0.98560028 * mean_solar_noon) % 360
    sma = math.radians(solar_mean_anomaly)

    center = (1.9148 * math.sin(sma)
              + 0.0200 * math.sin(2 * sma)
              + 0.0003 * math.sin(3 * sma))

    ecliptic_lon = (solar_mean_anomaly + center + 180 + 102.9372) % 360
    el = math.radians(ecliptic_lon)

    solar_transit = (2451545.0 + mean_solar_noon
                     + 0.0053 * math.sin(sma)      # equation of time,
                     - 0.0069 * math.sin(2 * el))  # eccentricity + obliquity

    declination = math.asin(math.sin(el) * math.sin(math.radians(23.4397)))

    lat_r = math.radians(lat)

    def hour_angle(elevation_deg):
        cos_omega = (
            (math.sin(math.radians(elevation_deg)) - math.sin(lat_r) * math.sin(declination))
            / (math.cos(lat_r) * math.cos(declination))
        )
        if cos_omega > 1 or cos_omega < -1:
            return None
        return math.degrees(math.acos(cos_omega))

    def to_iso(julian):
        unix = (julian - 2440587.5) * 86400.0
        return datetime.fromtimestamp(unix, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # -0.833 deg accounts for refraction and the sun's apparent radius.
    omega = hour_angle(-0.833)
    civil = hour_angle(-6.0)

    result = {
        "date": day.isoformat(),
        "solar_noon": to_iso(solar_transit),
        "sunrise": None,
        "sunset": None,
        "civil_dawn": None,
        "civil_dusk": None,
        "day_length_hours": None,
    }

    if omega is not None:
        rise = solar_transit - omega / 360.0
        set_ = solar_transit + omega / 360.0
        result["sunrise"] = to_iso(rise)
        result["sunset"] = to_iso(set_)
        result["day_length_hours"] = round((set_ - rise) * 24, 3)

    if civil is not None:
        result["civil_dawn"] = to_iso(solar_transit - civil / 360.0)
        result["civil_dusk"] = to_iso(solar_transit + civil / 360.0)

    return result


def build_sun(api_dir, lat=STATION_LAT, lon=STATION_LON, now=None):
    """Write api/sun/{today,year}.json for the observatory's location."""
    now = now or datetime.now(timezone.utc)
    today = now.date()

    today_events = _solar_events(today, lat, lon)
    tomorrow_events = _solar_events(today + timedelta(days=1), lat, lon)

    # A whole year at daily resolution is 365 rows -- small enough to send
    # in full, and it makes the seasonal curve the point of the chart.
    year_start = date(today.year, 1, 1)
    year = []
    cursor = year_start
    while cursor.year == today.year:
        year.append(_solar_events(cursor, lat, lon))
        cursor += timedelta(days=1)

    lengths = [e["day_length_hours"] for e in year if e["day_length_hours"] is not None]

    payload = {
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station": {"latitude": lat, "longitude": lon, "name": "Višnjan Observatory"},
        "today": today_events,
        "tomorrow": tomorrow_events,
        "extremes": {
            "longest_day_hours": max(lengths) if lengths else None,
            "shortest_day_hours": min(lengths) if lengths else None,
        },
    }

    write_json(os.path.join(api_dir, "sun", "today.json"), payload)
    write_json(os.path.join(api_dir, "sun", "year.json"), {
        "generated": payload["generated"],
        "year": today.year,
        "days": year,
    })
    return payload


# --------------------------------------------------------------------
# Station log / blog
# --------------------------------------------------------------------

VERSION_HEADING = re.compile(r"^##\s*\[?([0-9]+\.[0-9]+\.[0-9]+)\]?\s*-\s*(\d{4}-\d{2}-\d{2})")
BULLET = re.compile(r"^[-*]\s+(.*)")


def parse_changelog(path, limit=12):
    """Turn CHANGELOG.md into blog entries.

    The changelog is already written and maintained; re-typing it into a
    second file would guarantee the two drift apart. Parsing it means a
    release note appears on the site the moment it is committed.
    """
    if not os.path.exists(path):
        return []

    posts = []
    current = None

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()

            heading = VERSION_HEADING.match(line)
            if heading:
                if current:
                    posts.append(current)
                current = {
                    "date": heading.group(2),
                    "kind": "site",
                    "title": "Website v" + heading.group(1),
                    "version": heading.group(1),
                    "points": [],
                }
                continue

            if current is None:
                continue

            if line.startswith("### "):
                current.setdefault("sections", []).append(line[4:].strip())
                continue

            bullet = BULLET.match(line.strip())
            if bullet and len(current["points"]) < 6:
                text = bullet.group(1)
                # Strip markdown emphasis and inline code for plain display.
                text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
                text = re.sub(r"`([^`]+)`", r"\1", text)
                current["points"].append(text.strip())

    if current:
        posts.append(current)

    return posts[:limit]


def build_blog(api_dir, changelog_path, station_posts_path):
    """Publish the station log.

    Posts are written by hand in web/blog/posts.json -- they are prose,
    not release notes. CHANGELOG.md is still the machine-readable record
    of every version, but it is not rendered here: a reader wants two
    articles about how the system works, not eleven version bumps.
    """
    posts = []

    if os.path.exists(station_posts_path):
        try:
            with open(station_posts_path, encoding="utf-8") as f:
                posts.extend(json.load(f).get("posts", []))
        except (OSError, ValueError):
            pass

    # Newest first; a date is all these have in common.
    posts.sort(key=lambda p: p.get("date", ""), reverse=True)

    write_json(os.path.join(api_dir, "blog.json"), {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "posts": posts,
    })
    return len(posts)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
