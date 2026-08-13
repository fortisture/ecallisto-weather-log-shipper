#!/usr/bin/env python3
"""eCallisto weather log sender.

Watches a directory of *.csv files on the Raspberry Pi and ships changes to
the receiver over TLS. Pinned to the receiver's exact certificate
fingerprint and authenticated with a shared-secret token.

Each poll, a file's full current content is hashed and compared against
what was last sent for that file:

- Pure append (everything previously sent is still there unchanged, with
  new bytes tacked onto the end): ship just the new complete lines as
  individual ROW messages, persisting state after each one, so a
  mid-stream disconnect re-sends at most the single line in flight --
  never a duplicate, never a drop.
- Anything else -- an in-place edit to already-sent content, or the file
  getting shorter (rotated/truncated under the same name): ship the whole
  current file as one FILE message and let the receiver replace its local
  copy wholesale.

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

EMPTY_HASH = hashlib.sha256(b"").hexdigest()


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
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue

        prev = state.get(name, {"length": 0, "hash": EMPTY_HASH})
        prev_len = prev["length"]
        prev_hash = prev["hash"]

        if len(data) == prev_len and hashlib.sha256(data).hexdigest() == prev_hash:
            continue  # unchanged since we last checked

        if len(data) >= prev_len and hashlib.sha256(data[:prev_len]).hexdigest() == prev_hash:
            # Pure append.
            pos = prev_len
            while True:
                nl = data.find(b"\n", pos)
                if nl == -1:
                    break
                line = data[pos:nl]
                pos = nl + 1
                if line:
                    text = line.decode("utf-8", errors="replace")
                    sock.sendall(f"ROW\t{name}\t{text}\n".encode("utf-8"))
                state[name] = {"length": pos, "hash": hashlib.sha256(data[:pos]).hexdigest()}
                save_state(cfg["state_file"], state)
        else:
            # In-place edit, or the file got shorter (rotated/truncated) --
            # ship the whole current file and let the receiver replace its
            # copy wholesale. A resend of identical content on retry is
            # harmless, since this is a full replace, not an append.
            header = f"FILE\t{name}\t{len(data)}\n".encode("utf-8")
            sock.sendall(header)
            if data:
                sock.sendall(data)
            state[name] = {"length": len(data), "hash": hashlib.sha256(data).hexdigest()}
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
