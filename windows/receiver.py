#!/usr/bin/env python3
"""eCallisto weather log receiver.

TLS server that authenticates each incoming connection with a shared-secret
token, then handles two message types from the sender:

- ROW <filename> <row content>  -- append one row to the matching local file.
- FILE <filename> <byte length> -- replace the matching local file's entire
  content wholesale (used when the sender detects an in-place edit or a
  same-name truncation/rotation, not just an append).

Filenames are sanitized to a bare basename ending in .csv, so a connection
can never write outside --outdir.

Dependency-free: standard library only.
"""
import argparse
import hmac
import os
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
                out_path = os.path.join(cfg["outdir"], name)
                with lock:
                    with open(out_path, "a", encoding="utf-8", newline="") as out:
                        out.write(text + "\n")
                row_count += 1

            elif verb == b"FILE":
                try:
                    length = int(rest)
                    if length < 0:
                        raise ValueError
                except ValueError:
                    log(f"{peer}: bad FILE length {rest!r}, closing")
                    break
                remaining = length
                chunks = []
                while remaining > 0:
                    chunk = f.read(remaining)
                    if not chunk:
                        raise ConnectionError("connection closed mid-file-transfer")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                content = b"".join(chunks)

                name = safe_filename(raw_name.decode("utf-8", errors="replace"))
                if not name:
                    log(f"{peer}: rejected unsafe filename in FILE message, discarded {length} bytes")
                    continue
                out_path = os.path.join(cfg["outdir"], name)
                tmp_path = out_path + ".tmp"
                with lock:
                    with open(tmp_path, "wb") as out:
                        out.write(content)
                    os.replace(tmp_path, out_path)
                file_count += 1

            else:
                log(f"{peer}: unknown verb {verb!r}, closing")
                break

    except (OSError, ssl.SSLError, ConnectionError) as e:
        log(f"{peer}: connection error: {e}")
    finally:
        conn.close()
        log(f"{peer}: disconnected ({row_count} rows, {file_count} full-file replacements)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9443)
    ap.add_argument("--cert", default="cert.pem")
    ap.add_argument("--key", default="key.pem")
    ap.add_argument("--token-file", default="token.txt")
    ap.add_argument("--outdir", default="incoming_logs")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    with open(args.token_file) as f:
        token = f.read().strip()
    cfg = {"token": token, "outdir": args.outdir}

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
