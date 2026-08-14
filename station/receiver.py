#!/usr/bin/env python3
"""eCallisto station receiver.

TLS server that authenticates each incoming connection with a shared-secret
token, then handles three message types from the sender:

- ROW <filename> <row content>  -- append one row to the matching local file.
- FILE <filename> <byte length> -- replace the matching local file's entire
  content wholesale (used when the sender detects an in-place edit or a
  same-name truncation/rotation, not just an append).
- BLOB <filename> <byte length> -- store a binary file (a CALLISTO
  spectrogram) under --blobdir, filed into a per-day folder taken from the
  filename's embedded date.

Every filename is reduced to a bare basename and checked against an
allowed extension before use, so a connection can never write outside the
configured directories.

Dependency-free: standard library only.
"""
import argparse
import hmac
import os
import re
import socket
import ssl
import threading
import time


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def safe_filename(name):
    name = os.path.basename(name.strip())
    if not name or name in (".", "..") or not name.endswith(".csv"):
        return None
    return name


BLOB_SUFFIXES = (".fit.gz", ".fits.gz", ".fit", ".fits")

# Dates as they appear in station filenames, most specific first:
#   Croatia-Visnjan_20250909_075911_03.fit.gz   -> 20250909
#   weather_2026-08-13.csv                      -> 2026-08-13
DATE_PATTERNS = (
    re.compile(r"[_-](\d{4})(\d{2})(\d{2})[_.-]"),
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    re.compile(r"(\d{4})(\d{2})(\d{2})"),
)

UNDATED_DIR = "undated"


def safe_blob_name(name):
    """Sanitize a spectrogram filename the same way as a CSV name.

    Same reasoning as safe_filename: the name arrives over the network, so
    it is reduced to a bare basename and must carry a known extension
    before anything touches the filesystem.
    """
    name = os.path.basename(name.strip())
    if not name or name in (".", ".."):
        return None
    if not name.lower().endswith(BLOB_SUFFIXES):
        return None
    return name


def date_subdir(name):
    """Return ("YYYY", "MM", "DD") parsed out of a station filename.

    Both stores are laid out year/month/day, mirroring how the e-Callisto
    archive itself is organised: a single flat folder becomes unusable
    after a few thousand files, and a nested tree can be browsed, backed
    up or pruned one period at a time.

    Files whose name carries no date (a log that isn't rotated daily, say)
    go to an "undated" folder rather than being guessed at -- inventing a
    date would file real data under a day it didn't come from.
    """
    for pattern in DATE_PATTERNS:
        match = pattern.search(name)
        if not match:
            continue
        year, month, day = match.group(1), match.group(2), match.group(3)
        if 1970 <= int(year) <= 2999 and 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return year, month, day
    return None


def store_path(root, name):
    """Full path for a file inside a year/month/day store."""
    parts = date_subdir(name)
    return os.path.join(root, *parts, name) if parts else os.path.join(root, UNDATED_DIR, name)


# Which CSVs are power-rail telemetry rather than weather. Routed on the
# filename because that is all a ROW message carries -- the station names
# these logs e.g. power_20260813.csv.
POWER_HINTS = ("power", "psu", "rail", "volt", "current")


def csv_store_root(cfg, name):
    """Pick the store a CSV belongs in.

    Weather and power telemetry arrive over the same connection and both
    look like CSV rows, so they are separated by filename. Anything that
    doesn't look like power telemetry falls through to the weather store,
    which keeps the original behaviour for existing deployments.
    """
    lowered = name.lower()
    if cfg.get("powerdir") and any(hint in lowered for hint in POWER_HINTS):
        return cfg["powerdir"]
    return cfg["outdir"]


def read_exactly(stream, length):
    """Read exactly `length` bytes, or fail. Anything less means the
    connection died mid-payload and the stream can't be trusted."""
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise ConnectionError("connection closed mid-transfer")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def write_atomic(out_path, content):
    """Write to a temp file, then rename over the target.

    The rename is what makes it atomic -- a reader either sees the old
    file or the new one, never a half-written mixture. On Windows the
    rename can transiently fail if something else (antivirus, indexing, a
    file open in an editor) holds the target, so it is retried; there is
    no application-level ack for a sender to fall back on.
    """
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "wb") as out:
        out.write(content)

    for attempt in range(5):
        try:
            os.replace(tmp_path, out_path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2)


def handle_client(raw_conn, addr, cfg, ctx, lock):
    peer = f"{addr[0]}:{addr[1]}"
    try:
        conn = ctx.wrap_socket(raw_conn, server_side=True)
    except (ssl.SSLError, OSError) as e:
        log(f"{peer}: TLS handshake failed: {e}")
        raw_conn.close()
        return

    f = conn.makefile("rwb")
    row_count = 0
    blob_count = 0
    file_count = 0
    try:
        line = f.readline()
        if not line.startswith(b"AUTH "):
            conn.sendall(b"DENY\n")
            log(f"{peer}: missing AUTH line, closing")
            return

        token = line[len(b"AUTH "):].strip().decode("utf-8", errors="replace")
        if not hmac.compare_digest(token, cfg["token"]):
            conn.sendall(b"DENY\n")
            log(f"{peer}: bad token, closing")
            return

        conn.sendall(b"OK\n")
        log(f"{peer}: authenticated")

        while True:
            line = f.readline()
            if not line:
                break
            line = line.rstrip(b"\n")
            parts = line.split(b"\t", 2)
            if len(parts) != 3:
                log(f"{peer}: malformed message {line[:80]!r}, closing")
                break
            verb, raw_name, rest = parts

            if verb == b"ROW":
                name = safe_filename(raw_name.decode("utf-8", errors="replace"))
                text = rest.decode("utf-8", errors="replace")
                if not name:
                    log(f"{peer}: rejected unsafe filename {raw_name!r}")
                    continue
                out_path = store_path(csv_store_root(cfg, name), name)
                with lock:
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with open(out_path, "a", encoding="utf-8", newline="") as out:
                        out.write(text + "\n")
                row_count += 1

            elif verb in (b"FILE", b"BLOB"):
                try:
                    length = int(rest)
                    if length < 0:
                        raise ValueError
                except ValueError:
                    log(f"{peer}: bad {verb.decode()} length {rest!r}, closing")
                    break

                # The payload must be drained even if the name turns out to
                # be unusable, otherwise the stream desynchronises and every
                # message after this one is garbage.
                content = read_exactly(f, length)
                raw = raw_name.decode("utf-8", errors="replace")

                if verb == b"FILE":
                    name = safe_filename(raw)
                    if not name:
                        log(f"{peer}: rejected unsafe filename in FILE, discarded {length} bytes")
                        continue
                    out_path = store_path(csv_store_root(cfg, name), name)
                    file_count += 1
                else:
                    name = safe_blob_name(raw)
                    if not name or not cfg.get("blobdir"):
                        log(f"{peer}: rejected BLOB {raw!r}, discarded {length} bytes")
                        continue
                    out_path = store_path(cfg["blobdir"], name)
                    blob_count += 1

                with lock:
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    write_atomic(out_path, content)

            else:
                log(f"{peer}: unknown verb {verb!r}, closing")
                break

    except (OSError, ssl.SSLError, ConnectionError) as e:
        log(f"{peer}: connection error: {e}")
    finally:
        conn.close()
        log(f"{peer}: disconnected ({row_count} rows, {file_count} file replacements, {blob_count} spectrograms)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9443)
    ap.add_argument("--cert", default="cert.pem")
    ap.add_argument("--key", default="key.pem")
    ap.add_argument("--token-file", default="token.txt")
    ap.add_argument("--outdir", default="incoming_logs")
    ap.add_argument("--blobdir", default=None, help="where to store incoming spectrograms")
    ap.add_argument("--powerdir", default=None, help="where to store incoming power telemetry")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    for extra in (args.blobdir, args.powerdir):
        if extra:
            os.makedirs(extra, exist_ok=True)
    with open(args.token_file) as f:
        token = f.read().strip()
    cfg = {"token": token, "outdir": args.outdir,
           "blobdir": args.blobdir, "powerdir": args.powerdir}

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=args.cert, keyfile=args.key)

    lock = threading.Lock()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    sock.listen(5)
    log(f"listening on {args.host}:{args.port}, writing rows to {os.path.abspath(args.outdir)}")

    try:
        while True:
            raw_conn, addr = sock.accept()
            t = threading.Thread(
                target=handle_client, args=(raw_conn, addr, cfg, ctx, lock), daemon=True
            )
            t.start()
    except KeyboardInterrupt:
        log("stopping")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
