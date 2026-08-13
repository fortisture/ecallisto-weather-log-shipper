#!/usr/bin/env python3
"""Download Croatia-Visnjan CALLISTO FITS files from the e-Callisto archive.

The station's own spectrograms are produced on the Pi; this utility pulls
the published copies from the central FHNW archive so the viewer has real
data to display (and so past days can be browsed without the Pi online).

Files land in <store>/<YYYY-MM-DD>/ and are left gzipped exactly as the
archive serves them -- the viewer decompresses in the browser.

Dependency-free: standard library only.
"""
import argparse
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

ARCHIVE = "https://soleil.i4ds.ch/solarradio/data/2002-20yy_Callisto"
STATION = "Croatia-Visnjan"
TIMEOUT = 60


def day_url(day):
    return f"{ARCHIVE}/{day:%Y}/{day:%m}/{day:%d}/"


def list_day(day):
    """Return the station's filenames for a given day (empty if none)."""
    try:
        with urllib.request.urlopen(day_url(day), timeout=TIMEOUT) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        print(f"  {day}: listing failed ({e})")
        return []

    pattern = re.compile(rf"{re.escape(STATION)}_\d{{8}}_\d{{6}}_\d{{2}}\.fit\.gz")
    return sorted(set(pattern.findall(html)))


def download(day, names, store):
    # year/month/day, matching the layout the receiver writes into and the
    # archive's own structure.
    out_dir = os.path.join(store, f"{day:%Y}", f"{day:%m}", f"{day:%d}")
    os.makedirs(out_dir, exist_ok=True)

    fetched = 0
    for name in names:
        dest = os.path.join(out_dir, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            continue  # already have it -- the archive is immutable

        url = day_url(day) + name
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                payload = response.read()
        except (urllib.error.URLError, OSError) as e:
            print(f"  {name}: download failed ({e})")
            continue

        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(payload)
        os.replace(tmp, dest)
        fetched += 1

    return fetched


def find_recent_days(wanted, search_back, end_day):
    """Walk backwards from end_day collecting days that actually have data.

    The station has long outages, so "the last N days with data" is not
    the same as "the last N calendar days" -- this searches until it finds
    enough real ones or runs out of patience.
    """
    found = []
    for offset in range(search_back):
        day = end_day - timedelta(days=offset)
        names = list_day(day)
        if names:
            print(f"  {day}: {len(names)} files")
            found.append((day, names))
            if len(found) >= wanted:
                break
        elif offset % 30 == 0 and offset:
            print(f"  ...searched back to {day}, nothing yet")
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=3, help="how many days WITH DATA to fetch")
    ap.add_argument("--end", default=None, help="search backwards from this YYYY-MM-DD (default: today)")
    ap.add_argument("--search-back", type=int, default=420, help="max calendar days to search")
    ap.add_argument(
        "--store",
        default=os.path.join(os.path.dirname(__file__), "..", "data", "fits"),
    )
    ap.add_argument("--date", action="append", help="fetch a specific YYYY-MM-DD (repeatable)")
    args = ap.parse_args()

    store = os.path.abspath(args.store)
    os.makedirs(store, exist_ok=True)
    print(f"store: {store}")

    if args.date:
        targets = []
        for raw in args.date:
            day = datetime.strptime(raw, "%Y-%m-%d").date()
            names = list_day(day)
            print(f"  {day}: {len(names)} files")
            if names:
                targets.append((day, names))
    else:
        end_day = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
        print(f"searching backwards from {end_day} for {args.days} day(s) with data...")
        targets = find_recent_days(args.days, args.search_back, end_day)

    if not targets:
        sys.exit("no Visnjan data found in the searched range")

    total = 0
    for day, names in targets:
        got = download(day, names, store)
        total += got
        print(f"{day}: {got} new file(s), {len(names)} total on the archive")

    print(f"done -- {total} file(s) downloaded")


if __name__ == "__main__":
    main()
