#!/usr/bin/env python3
"""DORM station server -- one process that runs the whole ground station.

Replaces the old three-terminal setup (receiver + JSON generator + a
throwaway http.server). Running this one command gives you:

  * the TLS receiver, accepting rows shipped from the Pi by pi/sender.py
    and appending them to the local store (data/weather/)
  * a watcher that regenerates the dashboard's JSON API whenever that
    store changes
  * an index of locally stored CALLISTO FITS spectrograms
  * the web UI itself, served over HTTP

So the box running this is self-contained: it pulls from the Pi, keeps
its own copy of everything, and serves the site from that local copy. It
does not depend on the Pi being reachable to display history.

Dependency-free: standard library only.

    python server.py                      # everything, default ports
    python server.py --no-receiver        # serve only (no Pi ingest)
    python server.py --http-port 8080
"""

import argparse
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import api as generator
import receiver
import status as station_status

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB_DIR = os.path.join(ROOT, "web")
DEFAULT_WEATHER_DIR = os.path.join(ROOT, "data", "weather")
DEFAULT_FITS_DIR = os.path.join(ROOT, "data", "fits")
DEFAULT_POWER_DIR = os.path.join(ROOT, "data", "power")

# Generated JSON. It lives with the other data rather than inside web/, so
# the web directory holds only files a person actually wrote, and the URL
# /api/... is mapped onto it the same way /fits/... is.
DEFAULT_API_DIR = os.path.join(ROOT, "data", "api")

FITS_NAME = re.compile(r"^[A-Za-z0-9-]+_(\d{8})_(\d{6})_\d{2}\.fit\.gz$")


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------
# FITS index
# --------------------------------------------------------------------


def iter_store_days(root):
    """Yield (date_key, "YYYY/MM/DD", absolute path) for a year/month/day store."""
    if not os.path.isdir(root):
        return

    for year in sorted(os.listdir(root)):
        year_path = os.path.join(root, year)
        if not (os.path.isdir(year_path) and year.isdigit() and len(year) == 4):
            continue

        for month in sorted(os.listdir(year_path)):
            month_path = os.path.join(year_path, month)
            if not (os.path.isdir(month_path) and month.isdigit()):
                continue

            for day in sorted(os.listdir(month_path)):
                day_path = os.path.join(month_path, day)
                if not (os.path.isdir(day_path) and day.isdigit()):
                    continue
                try:
                    datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
                except ValueError:
                    continue
                yield f"{year}-{month}-{day}", f"{year}/{month}/{day}", day_path


def build_fits_index(fits_dir, api_dir):
    """Index locally stored spectrograms, marking every day in the span
    as recording or not -- the gaps are as informative as the data."""
    days = {}

    for date_key, url_path, day_path in iter_store_days(fits_dir):
        files = []
        for name in sorted(os.listdir(day_path)):
            match = FITS_NAME.match(name)
            if not match:
                continue
            hhmmss = match.group(2)
            files.append(
                {
                    "name": name,
                    "time": f"{hhmmss[0:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}",
                    "url": f"/fits/{url_path}/{name}",
                    "size": os.path.getsize(os.path.join(day_path, name)),
                }
            )

        if files:
            days[date_key] = files

    available = sorted(days)
    calendar = []

    if available:
        first = datetime.strptime(available[0], "%Y-%m-%d").date()
        last = datetime.strptime(available[-1], "%Y-%m-%d").date()
        cursor = first
        while cursor <= last:
            key = cursor.isoformat()
            calendar.append(
                {
                    "date": key,
                    "available": key in days,
                    "count": len(days.get(key, [])),
                }
            )
            cursor += timedelta(days=1)

    index = {
        "station": "Croatia-Visnjan",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "available_dates": available,
        "calendar": calendar,
        "days": days,
        "totals": {
            "days_with_data": len(available),
            "files": sum(len(v) for v in days.values()),
            "days_in_span": len(calendar),
        },
    }

    out = os.path.join(api_dir, "fits", "index.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp, out)

    return index


# --------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------


class StationHandler(SimpleHTTPRequestHandler):
    """Serves the web UI, and maps /fits/... onto the local FITS store."""

    def __init__(self, *args, fits_dir=None, api_dir=None, **kwargs):
        self.fits_dir = fits_dir
        self.api_dir = api_dir
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    # URL prefixes served from outside the web directory. Both hold
    # generated or received data, which has no business sitting in the
    # source tree.
    def _mapped_root(self, clean):
        if clean.startswith("/fits/"):
            return self.fits_dir, clean[len("/fits/") :]
        if clean.startswith("/api/"):
            return self.api_dir, clean[len("/api/") :]
        return None, None

    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        root, relative = self._mapped_root(clean)

        if root is None:
            return super().translate_path(path)

        # Reject anything that could climb out of the store.
        safe = os.path.normpath(relative).replace("\\", "/")
        if safe.startswith("..") or os.path.isabs(safe):
            return os.path.join(root, "__denied__")
        return os.path.join(root, *safe.split("/"))

    def end_headers(self):
        # The dashboard polls these; stale copies would mask fresh data.
        self.send_header("Cache-Control", "no-store, max-age=0")

        # Hardening headers. Harmless on a LAN, and necessary the moment
        # this sits behind a public reverse proxy: the page loads no
        # third-party code, so a strict policy costs nothing here and
        # blocks an injected <script> from calling home.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "  # the pages carry inline <script>
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'; "
            "form-action 'none'",
        )
        super().end_headers()

    def list_directory(self, path):
        """Directory listings are disabled.

        SimpleHTTPRequestHandler happily indexes any folder without an
        index.html. On a LAN that's a convenience; exposed publicly it
        enumerates the whole store for anyone who asks.
        """
        self.send_error(404, "Not found")

    # A request that is not fully sent within this many seconds is dropped.
    # Without it, a client can open a connection, dribble a partial request
    # and never finish (a "slowloris"), holding a worker thread open
    # indefinitely. BaseHTTPRequestHandler enforces this attribute itself.
    # Kept short because every legitimate request here is a small GET that
    # arrives in one packet -- there are no uploads to accommodate.
    timeout = 10

    def log_message(self, fmt, *args):
        pass  # too chatty; the watcher already reports what matters


class BoundedHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with two ceilings on concurrent connections.

    ThreadingHTTPServer spawns one unbounded thread per connection, so a
    flood of slow connections ("slowloris": open many, send bytes one at a
    time, never finish) can exhaust threads and memory. Two limits contain
    that:

    - a global cap on total in-flight requests, bounding memory; and
    - a per-IP cap, which is what actually defeats slowloris: a single
      source can occupy only a handful of slots no matter how many
      connections it opens, so it can never starve everyone else. Doing
      that from many addresses at once requires a botnet -- a different
      threat, and the one the reverse proxy in DEPLOYMENT.md exists for.

    Combined with the handler's own request-read timeout, a stalled
    connection both holds at most one of its source's few slots and is
    dropped after a few seconds.
    """

    daemon_threads = True

    def __init__(self, *args, max_workers=64, per_ip=8, **kwargs):
        self._slots = threading.BoundedSemaphore(max_workers)
        self._per_ip_limit = per_ip
        self._per_ip = {}
        self._per_ip_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def _ip_acquire(self, ip):
        with self._per_ip_lock:
            if self._per_ip.get(ip, 0) >= self._per_ip_limit:
                return False
            self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
            return True

    def _ip_release(self, ip):
        with self._per_ip_lock:
            n = self._per_ip.get(ip, 0) - 1
            if n <= 0:
                self._per_ip.pop(ip, None)
            else:
                self._per_ip[ip] = n

    def process_request(self, request, client_address):
        ip = client_address[0]

        if not self._ip_acquire(ip):
            try:
                request.close()
            except OSError:
                pass
            return

        if not self._slots.acquire(timeout=5):
            self._ip_release(ip)
            try:
                request.close()
            except OSError:
                pass
            return

        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()
            self._ip_release(client_address[0])


def serve_http(host, port, fits_dir, api_dir, max_workers=64, per_ip=8):
    handler = partial(StationHandler, fits_dir=fits_dir, api_dir=api_dir)
    httpd = BoundedHTTPServer(
        (host, port), handler, max_workers=max_workers, per_ip=per_ip
    )
    log(f"web UI on http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/")
    httpd.serve_forever()


# --------------------------------------------------------------------
# Watcher
# --------------------------------------------------------------------


def watch_and_generate(
    generator, weather_dir, power_dir, api_dir, fits_dir, interval, failsafe_hours
):
    """Rebuild derived files when the stores change.

    Normally this is change-driven: nothing changed, nothing is rebuilt.
    But "nothing changed" is also what a silently-stuck pipeline looks
    like, so a failsafe pass runs on a timer regardless, rebuilding
    everything from the stores on disk. That repairs the case where a
    derived file was deleted, truncated or left half-written by a crash --
    situations the change-detector cannot see, because the *source* did
    not change.
    """
    last_weather = None
    last_power = None
    last_fits = None
    next_failsafe = time.time() + failsafe_hours * 3600 if failsafe_hours > 0 else None

    while True:
        try:
            forced = next_failsafe is not None and time.time() >= next_failsafe
            if forced:
                log(
                    f"failsafe sweep ({failsafe_hours:g}h): rebuilding everything from disk"
                )
                next_failsafe = time.time() + failsafe_hours * 3600

            missing = not os.path.exists(
                os.path.join(api_dir, "weather", "history.json")
            )

            digest = generator.hash_dir(weather_dir)
            if forced or missing or digest != last_weather:
                total, emitted = generator.generate(
                    weather_dir, os.path.join(api_dir, "weather"), 0
                )
                log(f"weather API rebuilt: {total} readings")
                last_weather = digest

            power_digest = generator.hash_dir(power_dir)
            if forced or power_digest != last_power:
                count = generator.generate_power(
                    power_dir, os.path.join(api_dir, "power")
                )
                log(f"power API rebuilt: {count} readings")
                last_power = power_digest

            fits_state = fits_fingerprint(fits_dir)
            if forced or fits_state != last_fits:
                index = build_fits_index(fits_dir, api_dir)
                log(
                    f"FITS index rebuilt: {index['totals']['files']} files across "
                    f"{index['totals']['days_with_data']} day(s)"
                )
                last_fits = fits_state

            # Status and sun are cheap and time-dependent -- "how long
            # since the last file" changes even when nothing on disk does,
            # so these rebuild every pass rather than on change.
            station_status.build_status(
                api_dir,
                generator.load_all_readings(weather_dir),
                generator.load_power_readings(power_dir),
                read_json(os.path.join(api_dir, "fits", "index.json")) or {},
            )
            station_status.build_sun(api_dir)
            station_status.build_blog(
                api_dir,
                os.path.join(ROOT, "CHANGELOG.md"),
                os.path.join(WEB_DIR, "blog", "posts.json"),
            )

            clean_stale_temp_files(weather_dir, power_dir, fits_dir, api_dir)

        except Exception as e:
            # A watcher crash must never take the server down, and must
            # never leave the site silently frozen either -- so it logs
            # loudly, forgets its cached state (forcing a full rebuild on
            # the next pass) and keeps going.
            log(f"watcher error: {e!r} -- forcing a rebuild next pass")
            last_weather = None
            last_power = None
            last_fits = None

        time.sleep(interval)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def clean_stale_temp_files(*dirs, max_age=3600):
    """Remove .tmp/.part leftovers from an interrupted write.

    A crash between "write temp" and "rename into place" leaves debris
    that nothing will ever complete. Anything older than an hour is
    certainly abandoned -- a real write takes milliseconds.
    """
    cutoff = time.time() - max_age
    for root_dir in dirs:
        if not os.path.isdir(root_dir):
            continue
        for root, _, files in os.walk(root_dir):
            for name in files:
                if not name.endswith((".tmp", ".part")):
                    continue
                path = os.path.join(root, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        log(f"removed stale temp file {path}")
                except OSError:
                    pass


def fits_fingerprint(fits_dir):
    return tuple(
        (date_key, len(os.listdir(day_path)))
        for date_key, _, day_path in iter_store_days(fits_dir)
    )


# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--http-host",
        default="0.0.0.0",
        help="0.0.0.0 exposes the UI on the LAN. Set 127.0.0.1 when a "
        "reverse proxy (Caddy/nginx/cloudflared) terminates HTTPS in "
        "front of it, so the plain-HTTP port is not reachable directly.",
    )
    ap.add_argument("--http-port", type=int, default=8090)
    ap.add_argument("--weather-dir", default=DEFAULT_WEATHER_DIR)
    ap.add_argument("--fits-dir", default=DEFAULT_FITS_DIR)
    ap.add_argument("--power-dir", default=DEFAULT_POWER_DIR)
    ap.add_argument(
        "--api-dir",
        default=DEFAULT_API_DIR,
        help="where generated JSON is written. Served at /api/, so it does "
        "not need to sit inside the web directory.",
    )
    ap.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="seconds between change checks. Weather arrives every few "
        "minutes and spectrograms every fifteen, so polling faster "
        "than this only costs CPU -- the scan is proportional to the "
        "number of files in the store, which grows forever.",
    )
    ap.add_argument(
        "--failsafe-hours",
        type=float,
        default=6.0,
        help="rebuild everything from disk on this interval regardless of "
        "change detection, so a crash or deleted file self-heals (0 disables)",
    )

    ap.add_argument(
        "--no-receiver", action="store_true", help="serve only; don't accept Pi uploads"
    )
    ap.add_argument("--receiver-host", default="0.0.0.0")
    ap.add_argument("--receiver-port", type=int, default=9443)
    ap.add_argument("--cert", default=os.path.join(ROOT, "secrets", "cert.pem"))
    ap.add_argument("--key", default=os.path.join(ROOT, "secrets", "key.pem"))
    ap.add_argument("--token-file", default=os.path.join(ROOT, "secrets", "token.txt"))
    ap.add_argument(
        "--max-connections",
        type=int,
        default=16,
        help="concurrent Pi-receiver connection cap (one sender needs one)",
    )
    ap.add_argument(
        "--max-workers",
        type=int,
        default=64,
        help="concurrent HTTP worker cap, to bound a slow-client flood",
    )
    args = ap.parse_args()

    weather_dir = os.path.abspath(args.weather_dir)
    power_dir = os.path.abspath(args.power_dir)
    fits_dir = os.path.abspath(args.fits_dir)
    api_dir = os.path.abspath(args.api_dir)

    os.makedirs(weather_dir, exist_ok=True)
    os.makedirs(power_dir, exist_ok=True)
    os.makedirs(fits_dir, exist_ok=True)
    os.makedirs(api_dir, exist_ok=True)

    log(f"weather store: {weather_dir}")
    log(f"power store:   {power_dir}")
    log(f"FITS store:    {fits_dir}")
    log(f"generated API: {api_dir}")

    if not args.no_receiver:
        missing = [
            p for p in (args.cert, args.key, args.token_file) if not os.path.exists(p)
        ]
        if missing:
            log(
                "receiver disabled -- missing "
                + ", ".join(os.path.basename(m) for m in missing)
            )
            log("run  python install.py secrets  to create them, or pass --no-receiver")
        else:
            thread = threading.Thread(
                target=run_receiver,
                args=(receiver, args, weather_dir, power_dir),
                daemon=True,
            )
            thread.start()

    watcher = threading.Thread(
        target=watch_and_generate,
        args=(
            generator,
            weather_dir,
            power_dir,
            api_dir,
            fits_dir,
            args.poll_interval,
            args.failsafe_hours,
        ),
        daemon=True,
    )
    watcher.start()

    if args.failsafe_hours > 0:
        log(f"failsafe sweep every {args.failsafe_hours:g}h")

    try:
        serve_http(args.http_host, args.http_port, fits_dir, api_dir, args.max_workers)
    except KeyboardInterrupt:
        log("shutting down")


def run_receiver(receiver, args, weather_dir, power_dir):
    """Run the Pi receiver via its own hardened serve loop.

    receiver.serve owns the connection cap, the per-connection timeouts and
    the accept loop, so this does not re-implement any of it -- it only
    supplies the paths and secret. Keeping one accept loop means the
    denial-of-service protections cannot drift apart between the two entry
    points.
    """
    with open(args.token_file) as f:
        token = f.read().strip()

    cfg = {
        "token": token,
        "outdir": weather_dir,
        "blobdir": args.fits_dir,
        "powerdir": power_dir,
    }

    log(f"Pi receiver listening on {args.receiver_host}:{args.receiver_port} (TLS)")
    receiver.serve(
        args.receiver_host,
        args.receiver_port,
        args.cert,
        args.key,
        cfg,
        args.max_connections,
    )


if __name__ == "__main__":
    main()
