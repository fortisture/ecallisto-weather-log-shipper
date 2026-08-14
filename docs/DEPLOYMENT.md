# Public HTTPS deployment

How to take this from "works on my PC over the LAN" to "a real HTTPS site
anyone can reach", running on a dedicated machine.

**Short version:** don't put `server.py` on the internet directly. Bind it
to localhost and let a reverse proxy handle HTTPS. Of the options below,
**Cloudflare Tunnel is the one to pick** unless you have a specific reason
not to.

---

## First: decide what is public

Everything under `rag-web-ui/web/` and the whole FITS store becomes
world-readable. For a science station that is usually the point — but
decide it deliberately rather than discovering it later.

| Exposed | What it is |
|---|---|
| `/` `/power.html` `/fits.html` | The three pages |
| `/api/**` | All weather, power and FITS index JSON |
| `/fits/**` | Every stored `.fit.gz`, downloadable |

Secrets (`token.txt`, `*.pem`, `fingerprint.txt`, `config.json`) live
**outside** the web root and are not served. Keep it that way — never move
them under `rag-web-ui/web/`.

There is **no login**. If any of this should be private, put access control
in the proxy (all three options below support it) rather than in
`server.py`.

---

## The shape of it

```
  Raspberry Pi                Server device               The internet
  ─────────────               ─────────────               ────────────

  pi/sender.py  ──TLS 9443──► server.py                        │
  (weather,                   ├─ receiver  :9443  ◄── keep PRIVATE
   power, FITS)               └─ web UI  127.0.0.1:8090
                                        │
                                        ▼
                                 reverse proxy  ──HTTPS 443──► visitors
                                 (TLS + public name)
```

Two rules:

1. **The web port binds to `127.0.0.1`.** Only the proxy can reach it, so
   nobody can bypass TLS by hitting the plain HTTP port.
2. **Port 9443 (the Pi receiver) never goes public.** It is protected by
   cert pinning and a shared token, but it has no business being
   internet-facing. If the Pi is off-site, join it to the server with
   Tailscale/WireGuard instead of forwarding the port.

---

## Option A — Cloudflare Tunnel (recommended)

Best for a machine on a home/observatory connection. No port forwarding,
no static IP, works behind CGNAT, free automatic HTTPS, and your home IP
address is never exposed.

You need a domain on Cloudflare (you can move an existing one, or buy one
through them).

```bash
# on the server device
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

cloudflared tunnel login                 # opens a browser, pick your domain
cloudflared tunnel create dorm
cloudflared tunnel route dns dorm dorm.example.org
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: dorm
credentials-file: /root/.cloudflared/<TUNNEL-ID>.json

ingress:
  - hostname: dorm.example.org
    service: http://127.0.0.1:8090
  - service: http_status:404
```

```bash
cloudflared service install
systemctl enable --now cloudflared
```

Then run the station server bound to localhost only:

```bash
python3 server.py --http-host 127.0.0.1 --http-port 8090
```

That's it — `https://dorm.example.org` is live with a valid certificate.

To require a login, add Cloudflare Access (free tier) in front of the
hostname; it handles auth before traffic ever reaches you.

---

## Option B — Caddy + Let's Encrypt

Use when you control a public IP and can forward ports 80 and 443 (a VPS,
or a connection with a static address). Caddy gets and renews certificates
automatically.

`/etc/caddy/Caddyfile`:

```
dorm.example.org {
    reverse_proxy 127.0.0.1:8090

    encode gzip

    # Optional: password-protect the whole site.
    # Generate the hash with:  caddy hash-password
    # basic_auth {
    #     roko $2a$14$...
    # }
}
```

```bash
sudo systemctl reload caddy
```

Requirements:
- DNS `A`/`AAAA` record for `dorm.example.org` pointing at your public IP.
- Router forwards **443 and 80** to the server device (80 is used for the
  certificate challenge and the HTTP→HTTPS redirect).
- Dynamic IP? Add a DDNS updater, or use Option A instead.

nginx works equally well if you prefer it — the difference is you manage
certbot yourself instead of Caddy doing it silently.

---

## Option C — VPS, with the station pushing to it

Cleanest separation if the observatory's connection is unreliable: rent a
small VPS (a €4/month box is plenty), run `server.py` + Caddy there, and
have the Pi ship to it.

The only change is the Pi's `config.json`:

```json
{
  "host": "vps.example.org",
  "port": 9443,
  "token": "…",
  "fingerprint": "…"
}
```

Since the data now crosses the public internet rather than a LAN:

- The existing TLS + cert pinning + token is doing real work here. Keep the
  token long and random.
- Firewall 9443 to the Pi's IP if it is static:
  `ufw allow from <PI-IP> to any port 9443 proto tcp`
- If the Pi's IP changes, put both machines on Tailscale and point the Pi
  at the VPS's Tailscale address instead — then 9443 needs no public
  exposure at all.

---

## Running it as a service (Linux server device)

`/etc/systemd/system/dorm-station.service`:

```ini
[Unit]
Description=DORM station server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=dorm
WorkingDirectory=/opt/dorm
ExecStart=/usr/bin/python3 /opt/dorm/server.py \
    --http-host 127.0.0.1 \
    --http-port 8090 \
    --data-dir /var/lib/dorm
Restart=always
RestartSec=5

# The server only ever needs to read its code and write its data.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/dorm

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dorm-station
journalctl -u dorm-station -f
```

---

## Moving from this PC to the real server

1. `git clone` the repo onto the new machine. **The repo has no data and no
   secrets in it** — that is deliberate, and it makes this step trivial.
2. Generate fresh TLS material there (`windows/install_and_run.ps1` on
   Windows, or `openssl req -x509 -newkey rsa:2048 -sha256 -days 3650
   -nodes -keyout key.pem -out cert.pem -subj "/CN=dorm-receiver"` on
   Linux). **Generate a new token too** — don't copy this proof-of-concept
   one across.
3. Put the new fingerprint and token into the Pi's `config.json` and
   restart `weather-shipper` on the Pi.
4. Copy `data/` over if you want the history; otherwise the Pi's 6-hour
   failsafe resync will refill it on its own.
5. Point the proxy at it and you're done.

---

## What is already hardened

`server.py` sets `Content-Security-Policy`, `X-Content-Type-Options`,
`X-Frame-Options` and `Referrer-Policy` on every response, refuses
directory listings, and rejects path traversal under `/fits/`. Verified:

```
GET /api/                  -> 404   (no listing)
GET /fits/                 -> 404   (no listing)
GET /fits/../../server.py  -> 404   (traversal blocked)
```

## What is not

- **No rate limiting.** The FITS store is the exposure — someone could pull
  every `.fit.gz` in a loop. All three proxy options can rate-limit; use it
  if the archive gets large.
- **No authentication**, by design (see the top of this file).
- **The HTTP server is Python's `ThreadingHTTPServer`.** Fine behind a
  proxy handling TLS and buffering slow clients; not something to expose
  raw.
