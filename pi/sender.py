#!/usr/bin/env python3
"""eCallisto weather log sender.

Watches a directory of *.csv files on the Raspberry Pi and ships each newly
appended row to the receiver over TLS, as soon as the row is written. Pinned
to the receiver's exact certificate fingerprint and authenticated with a
shared-secret token. Tracks a per-file byte offset in a state file so
restarts never re-send or skip rows.

Dependency-free: standard library only.
"""
import argparse
import glob
import hashlib
import json
import os
import socket
import ssl
import sys
import time


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_state(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def connect(cfg):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # We do our own pinning against the exact cert fingerprint below, so we
    # disable the normal CA-chain checks (the receiver's cert is self-signed).
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    raw = socket.create_connection((cfg["host"], cfg["port"]), timeout=10)
    sock = ctx.wrap_socket(raw, server_hostname=cfg["host"])

    der = sock.getpeercert(binary_form=True)
    fp = hashlib.sha256(der).hexdigest()
    expected = cfg["fingerprint"].replace(":", "").lower()
    if fp != expected:
        sock.close()
        raise ssl.SSLCertVerificationError(
            f"certificate fingerprint mismatch: got {fp}, expected {expected}"
        )

    sock.settimeout(30)
    sock.sendall(f"AUTH {cfg['token']}\n".encode("utf-8"))
    reply = sock.makefile("rb").readline().strip()
    if reply != b"OK":
        sock.close()
        raise PermissionError(f"receiver rejected auth: {reply!r}")

    log(f"connected to {cfg['host']}:{cfg['port']}, fingerprint verified, authenticated")
    return sock


def scan_and_send(sock, cfg, state):
    pattern = os.path.join(cfg["watch_dir"], "*.csv")
    for path in sorted(glob.glob(pattern)):
        name = os.path.basename(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue

        offset = state.get(name, 0)
        if size <= offset:
            continue

        with open(path, "rb") as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if not line.endswith(b"\n"):
                    # Partial line (still being written) -- wait for the rest.
                    break
                text = line[:-1].decode("utf-8", errors="replace")
                if text:
                    sock.sendall(f"{name}\t{text}\n".encode("utf-8"))
                # Only advance (and persist) the offset once the row has been
                # sent, so a mid-stream disconnect re-sends it rather than
                # silently dropping it -- and never re-sends what's already
                # been acknowledged by a successful send.
                offset = f.tell()
                state[name] = offset
                save_state(cfg["state_file"], state)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", help="path to a JSON config file (see pi/config.example.json)")
    ap.add_argument("--poll-interval", type=float, default=1.0, help="seconds between directory scans")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)
    cfg.setdefault(
        "state_file",
        os.path.join(os.path.dirname(os.path.abspath(args.config)), ".sender_state.json"),
    )

    state = load_state(cfg["state_file"])
    backoff = 1
    sock = None
    while True:
        try:
            sock = connect(cfg)
            backoff = 1
            while True:
                scan_and_send(sock, cfg, state)
                time.sleep(args.poll_interval)
        except (OSError, ssl.SSLError, PermissionError) as e:
            log(f"connection error: {e}; retrying in {backoff}s")
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except KeyboardInterrupt:
            log("stopping")
            sys.exit(0)


if __name__ == "__main__":
    main()
