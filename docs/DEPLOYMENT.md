# Deployment

Getting the station from "runs on a laptop" to "an Ubuntu server holding
the archive and serving a public HTTPS site, fed by a Pi on a different
network".

**Constraint this is built around:** the Pi and the server are on separate
networks, and the only port open on the server is SSH. Nothing here asks
you to open a data port to the internet.

---

## The shape of it

```
  Raspberry Pi (network A)              Ubuntu server (network B)
  ────────────────────────              ─────────────────────────

  CALLISTO + weather + rails
            │
            ▼
      pi/sender.py  ──►  127.0.0.1:19443
                              │
                              │  ssh -L  (port 22 only)
                              ▼
                                        127.0.0.1:9443   receiver
                                                │
                                                ▼
                                        data/  (the archive)
                                                │
                                                ▼
                                        127.0.0.1:8090   web UI
                                                │
                                                ▼
                                        reverse proxy ──HTTPS 443──► public
```

Two rules hold the whole design together:

1. **Nothing binds to a public interface except `sshd`** (and later, the
   proxy on 443). The receiver and the web server both listen on
   `127.0.0.1`, so they are unreachable from outside even if the firewall
   is wrong.
2. **The Pi connects outward.** It needs no inbound rule, no static IP and
   no port forwarding on its own network.

### Why still use TLS inside an SSH tunnel?

SSH already encrypts and authenticates the machines. The inner TLS +
certificate pinning + shared token layer is kept anyway because it answers
a different question: SSH proves *which machine* connected, the inner layer
proves *which program* is talking and that it is talking to the right
receiver. If the tunnel were ever misconfigured to point somewhere else,
the sender would refuse on the fingerprint check rather than cheerfully
uploading the archive to a stranger.

---

## Part 1 — the server

```bash
sudo apt update && sudo apt install -y python3 openssl git
sudo useradd -m -s /bin/bash dorm          # if it doesn't exist

sudo -u dorm git clone https://github.com/fortisture/ecallisto-weather-log-shipper.git /opt/dorm
cd /opt/dorm

sudo python3 install.py server --user dorm
```

The installer generates the TLS keypair and token, creates the data store,
writes `dorm-station.service`, starts it, and prints the **token** and
**fingerprint**. Keep that output — the Pi needs both.

```bash
systemctl status dorm-station
journalctl -u dorm-station -f
```

Firewall: only SSH.

```bash
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status
```

---

## Part 2 — the Pi

```bash
git clone https://github.com/fortisture/ecallisto-weather-log-shipper.git ~/dorm
cd ~/dorm
sudo python3 install.py pi
```

It asks for:

| Prompt | Value |
|---|---|
| Server SSH hostname | the server's public name or IP |
| Server SSH username | `dorm` |
| Weather CSV directory | wherever the station writes them |
| FITS directory | wherever CALLISTO writes them |
| Token / fingerprint | from Part 1 |

It generates an SSH key, installs the sender, writes both services, and
prints the **public key**.

### Authorise the key (on the server)

Paste the printed key, but restrict what it can do:

```bash
sudo -u dorm mkdir -p /home/dorm/.ssh
sudo -u dorm nano /home/dorm/.ssh/authorized_keys
```

Prefix the key line with:

```
command="",no-agent-forwarding,no-pty,no-X11-forwarding,permitopen="127.0.0.1:9443" ssh-ed25519 AAAA... dorm-tunnel@pi
```

That key can now open exactly one forward and nothing else — **no shell**,
even if the Pi is stolen. This is the single most valuable line in this
document.

```bash
sudo chmod 600 /home/dorm/.ssh/authorized_keys
sudo chown -R dorm:dorm /home/dorm/.ssh
```

### Start it

```bash
sudo systemctl enable --now dorm-tunnel dorm-sender
systemctl status dorm-tunnel dorm-sender
journalctl -u dorm-sender -f
```

You should see `connected to 127.0.0.1:19443, fingerprint verified,
authenticated`, then rows and spectrograms shipping.

### If the tunnel will not come up

```bash
# Test the SSH leg by hand, as the service user:
sudo -u pi ssh -i /home/pi/.ssh/dorm_tunnel -N -v \
    -L 19443:127.0.0.1:9443 dorm@SERVER
```

- `Permission denied` → the key is not in `authorized_keys`, or its
  permissions are wrong (`700` on `.ssh`, `600` on the file).
- `administratively prohibited: open failed` → `permitopen` does not match
  `127.0.0.1:9443`.
- Connects but the sender still fails → the receiver is not running; check
  `journalctl -u dorm-station`.

---

## Part 3 — public HTTPS

The site listens on `127.0.0.1:8090`. Put a proxy in front of it.

### Caddy (recommended — certificates are automatic)

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```
dorm.example.org {
    reverse_proxy 127.0.0.1:8090
    encode gzip

    # Optional: password-protect the whole site.
    # Generate the hash with:  caddy hash-password
    # basic_auth {
    #     visnjan $2a$14$...
    # }
}
```

```bash
sudo ufw allow 80,443/tcp
sudo systemctl reload caddy
```

Requirements: a DNS `A`/`AAAA` record pointing at the server, and ports 80
and 443 reachable (80 is used for the certificate challenge).

**If you cannot open 443 either**, the only remaining option is an outbound
tunnel such as Cloudflare Tunnel, which makes an outbound connection
instead of accepting an inbound one. That means running a third-party agent
on the server — a trade-off worth making deliberately, not by default.

### nginx

Equivalent, but you manage certbot yourself:

```nginx
server {
    listen 443 ssl http2;
    server_name dorm.example.org;

    ssl_certificate     /etc/letsencrypt/live/dorm.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dorm.example.org/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
    }
}
```

---

## What is exposed

Everything under `web/` and the whole FITS archive becomes world-readable.
For a science station that is usually the intent — but decide it
deliberately.

| Path | Contents |
|---|---|
| `/`, `/weather.html`, … | the pages |
| `/api/**` | all weather, power, status, sun and index JSON |
| `/fits/**` | every stored `.fit.gz`, downloadable |

Secrets live in `secrets/`, outside the web root, and are gitignored. Never
move them under `web/`.

There is **no login**. If any of this should be private, put access control
in the proxy — all the options above support it.

### Already hardened

`station/server.py` sets `Content-Security-Policy`,
`X-Content-Type-Options`, `X-Frame-Options` and `Referrer-Policy` on every
response, refuses directory listings, and rejects path traversal under
`/fits/`:

```
GET /api/                  -> 404
GET /fits/                 -> 404
GET /fits/../../server.py  -> 404
```

### Not hardened

- **No rate limiting.** The FITS archive is the exposure — someone could
  pull every file in a loop. Add a limit in the proxy if the archive grows.
- **`ThreadingHTTPServer`** is fine behind a proxy that terminates TLS and
  buffers slow clients; do not expose it directly.

---

## Moving the archive

The repo carries no data, so moving machines is just:

```bash
rsync -avz --progress /opt/dorm/data/ dorm@newserver:/opt/dorm/data/
```

If you skip it, the Pi's six-hour failsafe resync refills the server on its
own — it periodically forgets what it believes was delivered and re-sends,
precisely so a rebuilt server heals itself.

---

## Routine operation

```bash
# Server
systemctl status dorm-station
journalctl -u dorm-station -f
python3 install.py check

# Pi
systemctl status dorm-tunnel dorm-sender
journalctl -u dorm-sender -f

# Restart everything after a config change
sudo systemctl restart dorm-station          # server
sudo systemctl restart dorm-tunnel dorm-sender   # Pi
```

The **Status** page is the fastest check that it is all working: if the
instrument stream shows anything other than ONLINE, data has stopped
arriving regardless of what the services claim.
