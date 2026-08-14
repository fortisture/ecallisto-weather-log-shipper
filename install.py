#!/usr/bin/env python3
"""DORM station installer.

One script for both machines. It writes config, generates secrets, and
registers services -- it never installs packages behind your back, and it
prints every privileged command before running it.

    sudo python3 install.py server      # the Ubuntu box that stores and serves
    sudo python3 install.py pi          # the Raspberry Pi at the telescope
    python3 install.py check            # verify an existing install

TOPOLOGY
--------
The Pi and the server are on different networks, and only SSH is open on
the server. So the Pi does not connect to a data port across the internet;
instead it opens an SSH tunnel and speaks to the receiver through it:

    Pi                                         Ubuntu server
    ---------------                            ---------------------
    sender.py  ->  127.0.0.1:19443
                        |
                        +-- ssh -L ... -->  127.0.0.1:9443  (receiver)
                            (port 22 only)

Nothing but sshd listens on a public interface. The receiver binds to
localhost, so even if the firewall were misconfigured it would not be
reachable from outside. The TLS + certificate-pinning + token layer stays
in place inside the tunnel: SSH authenticates the machines, and the inner
layer means a compromised tunnel still cannot inject data.

Dependency-free: standard library only.
"""
import argparse
import getpass
import os
import secrets
import shutil
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "secrets")

RECEIVER_PORT = 9443          # on the server, bound to localhost
TUNNEL_PORT = 19443           # on the Pi, forwarded into the above
HTTP_PORT = 8090              # on the server, bound to localhost


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------

def say(msg):
    print(msg, flush=True)


def head(title):
    say("\n" + "=" * 66)
    say("  " + title)
    say("=" * 66)


def run(cmd, check=True, quiet=False):
    """Run a command, showing it first. Nothing happens invisibly."""
    if not quiet:
        say("  $ " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if check:
            say("    failed: " + (result.stderr or result.stdout).strip())
            raise SystemExit(1)
        return None
    return (result.stdout or "").strip()


def need_root(role):
    if os.name != "nt" and os.geteuid() != 0:
        say(f"This needs root to install a service. Re-run:\n\n"
            f"    sudo python3 install.py {role}\n")
        raise SystemExit(1)


def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    answer = input(f"  {prompt}{suffix}: ").strip()
    return answer or default or ""


def python_bin():
    return shutil.which("python3") or sys.executable


# --------------------------------------------------------------------
# secrets
# --------------------------------------------------------------------

def make_secrets(force=False):
    """Create the TLS keypair and shared token used inside the tunnel."""
    os.makedirs(SECRETS, exist_ok=True)

    cert = os.path.join(SECRETS, "cert.pem")
    key = os.path.join(SECRETS, "key.pem")
    token_file = os.path.join(SECRETS, "token.txt")
    fp_file = os.path.join(SECRETS, "fingerprint.txt")

    if os.path.exists(cert) and not force:
        say("  certificate already exists, keeping it")
    else:
        if not shutil.which("openssl"):
            say("  openssl not found -- install it:  sudo apt install openssl")
            raise SystemExit(1)
        run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
             "-days", "3650", "-nodes", "-keyout", key, "-out", cert,
             "-subj", "/CN=dorm-receiver"])

    if os.path.exists(token_file) and not force:
        say("  token already exists, keeping it")
    else:
        # 32 bytes of CSPRNG output. Long enough that guessing is
        # hopeless, short enough to paste into a config file.
        with open(token_file, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(token_file, 0o600)

    fingerprint = run([
        "openssl", "x509", "-in", cert, "-noout", "-fingerprint", "-sha256"
    ], quiet=True)
    fingerprint = fingerprint.split("=", 1)[1].replace(":", "").lower()

    with open(fp_file, "w") as f:
        f.write(fingerprint)

    for path in (key, token_file):
        if os.path.exists(path):
            os.chmod(path, 0o600)

    with open(token_file) as f:
        token = f.read().strip()

    return token, fingerprint


# --------------------------------------------------------------------
# server role
# --------------------------------------------------------------------

SERVER_UNIT = """[Unit]
Description=DORM station server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={root}
ExecStart={python} {root}/station/server.py \\
    --http-host 127.0.0.1 --http-port {http_port} \\
    --receiver-host 127.0.0.1 --receiver-port {rx_port}
Restart=always
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths={root}/data {root}/web/api

[Install]
WantedBy=multi-user.target
"""


def install_server(args):
    head("DORM server install (Ubuntu)")
    need_root("server")

    user = args.user or ask("Run the service as which user?", os.environ.get("SUDO_USER", "dorm"))

    say("\n[1/5] Generating TLS material and shared token")
    token, fingerprint = make_secrets(force=args.regenerate_secrets)
    if user:
        run(["chown", "-R", f"{user}:{user}", SECRETS], check=False)

    say("\n[2/5] Preparing data store")
    for sub in ("weather", "power", "fits"):
        os.makedirs(os.path.join(HERE, "data", sub), exist_ok=True)
    os.makedirs(os.path.join(HERE, "web", "api"), exist_ok=True)
    if user:
        run(["chown", "-R", f"{user}:{user}",
             os.path.join(HERE, "data"), os.path.join(HERE, "web", "api")], check=False)
    say("  data/weather, data/power, data/fits ready")

    say("\n[3/5] Registering systemd service")
    unit = SERVER_UNIT.format(
        user=user, root=HERE, python=python_bin(),
        http_port=HTTP_PORT, rx_port=RECEIVER_PORT,
    )
    unit_path = "/etc/systemd/system/dorm-station.service"
    with open(unit_path, "w") as f:
        f.write(unit)
    say(f"  wrote {unit_path}")

    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "dorm-station"])

    say("\n[4/5] Checking it started")
    state = run(["systemctl", "is-active", "dorm-station"], check=False)
    say(f"  dorm-station is {state or 'not running'}")
    if state != "active":
        say("  inspect with:  journalctl -u dorm-station -n 40 --no-pager")

    say("\n[5/5] Details for the Pi")
    hostname = socket.gethostname()
    head("COPY THESE TO THE PI")
    say(f"""
  When you run the Pi installer it will ask for these:

    SSH host      {args.public_host or hostname}   (this server, over SSH)
    SSH user      {user}
    token         {token}
    fingerprint   {fingerprint}

  Both values are also in:
    {SECRETS}/token.txt
    {SECRETS}/fingerprint.txt
""")

    say("""  NOTHING is listening publicly except sshd:
    receiver  127.0.0.1:%d   (reachable only through the SSH tunnel)
    web UI    127.0.0.1:%d   (put a reverse proxy in front for HTTPS)

  Next:
    1. Add the Pi's SSH public key to ~%s/.ssh/authorized_keys
    2. Run the Pi installer:  sudo python3 install.py pi
    3. For a public site, see docs/DEPLOYMENT.md
""" % (RECEIVER_PORT, HTTP_PORT, "/" + user))


# --------------------------------------------------------------------
# pi role
# --------------------------------------------------------------------

TUNNEL_UNIT = """[Unit]
Description=DORM SSH tunnel to the station server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
# -N: no remote command, this is a tunnel only.
# ExitOnForwardFailure: fail loudly instead of pretending to be connected
#   while the forward is dead -- systemd then restarts us.
# ServerAlive*: notice a silently dropped link within ~90 seconds.
ExecStart=/usr/bin/ssh -N \\
    -o ExitOnForwardFailure=yes \\
    -o ServerAliveInterval=30 \\
    -o ServerAliveCountMax=3 \\
    -o StrictHostKeyChecking=accept-new \\
    -i {keyfile} \\
    -L {local_port}:127.0.0.1:{remote_port} \\
    {ssh_user}@{ssh_host} -p {ssh_port}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""

SENDER_UNIT = """[Unit]
Description=DORM station sender
After=network-online.target dorm-tunnel.service
Wants=network-online.target
Requires=dorm-tunnel.service

[Service]
Type=simple
User={user}
ExecStart={python} {install_dir}/sender.py {install_dir}/config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def install_pi(args):
    head("DORM Pi install")
    need_root("pi")

    user = args.user or ask("Run as which user?", os.environ.get("SUDO_USER", "pi"))
    home = os.path.expanduser(f"~{user}")

    ssh_host = args.ssh_host or ask("Server SSH hostname or IP")
    ssh_user = args.ssh_user or ask("Server SSH username", "dorm")
    ssh_port = args.ssh_port or ask("Server SSH port", "22")

    weather_dir = args.weather_dir or ask("Weather CSV directory on this Pi",
                                          "/home/pi/ecallisto/weather_logs")
    fits_dir = args.fits_dir or ask("CALLISTO FITS directory (blank to skip)",
                                    "/home/pi/ecallisto/fits")

    token = args.token or getpass.getpass("  Shared token (from the server): ").strip()
    fingerprint = args.fingerprint or ask("Certificate fingerprint (from the server)")

    if not token or not fingerprint:
        say("\n  token and fingerprint are both required -- run the server installer first.")
        raise SystemExit(1)

    install_dir = args.install_dir
    keyfile = os.path.join(home, ".ssh", "dorm_tunnel")

    say("\n[1/6] SSH key for the tunnel")
    if not os.path.exists(keyfile):
        os.makedirs(os.path.dirname(keyfile), exist_ok=True)
        run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", keyfile,
             "-C", f"dorm-tunnel@{socket.gethostname()}"])
        run(["chown", "-R", f"{user}:{user}", os.path.dirname(keyfile)], check=False)
    else:
        say("  key already exists, keeping it")

    with open(keyfile + ".pub") as f:
        pubkey = f.read().strip()

    say("\n[2/6] Installing sender")
    os.makedirs(install_dir, exist_ok=True)
    shutil.copy2(os.path.join(HERE, "pi", "sender.py"), os.path.join(install_dir, "sender.py"))

    config = {
        "host": "127.0.0.1",          # the local end of the SSH tunnel
        "port": int(TUNNEL_PORT),
        "token": token,
        "fingerprint": fingerprint,
        "watch_dir": weather_dir,
        "state_file": "/var/lib/dorm/state.json",
    }
    if fits_dir:
        config["fits_dir"] = fits_dir

    import json
    config_path = os.path.join(install_dir, "config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    os.chmod(config_path, 0o600)
    run(["chown", f"{user}:{user}", config_path], check=False)

    os.makedirs("/var/lib/dorm", exist_ok=True)
    run(["chown", "-R", f"{user}:{user}", "/var/lib/dorm"], check=False)
    say(f"  wrote {config_path}")

    say("\n[3/6] Registering the SSH tunnel service")
    with open("/etc/systemd/system/dorm-tunnel.service", "w") as f:
        f.write(TUNNEL_UNIT.format(
            user=user, keyfile=keyfile, local_port=TUNNEL_PORT,
            remote_port=RECEIVER_PORT, ssh_user=ssh_user,
            ssh_host=ssh_host, ssh_port=ssh_port,
        ))

    say("\n[4/6] Registering the sender service")
    with open("/etc/systemd/system/dorm-sender.service", "w") as f:
        f.write(SENDER_UNIT.format(user=user, python=python_bin(), install_dir=install_dir))

    run(["systemctl", "daemon-reload"])

    say("\n[5/6] Authorise this Pi on the server")
    head("ADD THIS KEY TO THE SERVER")
    say(f"""
  The tunnel cannot start until the server trusts this Pi. On the SERVER,
  as {ssh_user}, run:

    mkdir -p ~/.ssh && chmod 700 ~/.ssh
    echo '{pubkey}' >> ~/.ssh/authorized_keys
    chmod 600 ~/.ssh/authorized_keys

  Optional but recommended -- restrict what this key may do by prefixing
  the line in authorized_keys with:

    command="",no-agent-forwarding,no-pty,permitopen="127.0.0.1:{RECEIVER_PORT}"

  That key can then ONLY open the one forward it needs. It cannot get a
  shell even if the Pi is stolen.
""")

    say("[6/6] Once the key is installed, start both services:\n")
    say("    sudo systemctl enable --now dorm-tunnel dorm-sender")
    say("    systemctl status dorm-tunnel dorm-sender")
    say("    journalctl -u dorm-sender -f\n")


# --------------------------------------------------------------------
# check role
# --------------------------------------------------------------------

def check(args):
    head("DORM install check")

    ok = True

    say("\nPython")
    say(f"  {sys.version.split()[0]} at {sys.executable}")
    if sys.version_info < (3, 8):
        say("  WARNING: 3.8+ recommended")
        ok = False

    say("\nProject files")
    for rel in ("station/server.py", "station/receiver.py", "station/api.py",
                "station/status.py", "pi/sender.py", "web/index.html",
                "web/common.js", "web/vendor/chart.js"):
        path = os.path.join(HERE, rel)
        mark = "ok  " if os.path.exists(path) else "MISSING"
        say(f"  [{mark}] {rel}")
        ok = ok and os.path.exists(path)

    say("\nSecrets")
    for name in ("cert.pem", "key.pem", "token.txt", "fingerprint.txt"):
        path = os.path.join(SECRETS, name)
        say(f"  [{'ok  ' if os.path.exists(path) else 'none'}] secrets/{name}")

    say("\nData store")
    for sub in ("weather", "power", "fits"):
        path = os.path.join(HERE, "data", sub)
        count = 0
        for _, _, files in os.walk(path):
            count += len(files)
        say(f"  data/{sub}: {count} file(s)")

    say("\nServices")
    if shutil.which("systemctl"):
        for unit in ("dorm-station", "dorm-tunnel", "dorm-sender"):
            state = run(["systemctl", "is-active", unit], check=False, quiet=True)
            say(f"  {unit}: {state or 'not installed'}")
    else:
        say("  systemd not present (fine on Windows/macOS)")

    say("\n" + ("All good." if ok else "Some checks failed -- see above."))
    return 0 if ok else 1


# --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("role", choices=["server", "pi", "check", "secrets"])
    ap.add_argument("--user")
    ap.add_argument("--ssh-host")
    ap.add_argument("--ssh-user")
    ap.add_argument("--ssh-port")
    ap.add_argument("--public-host")
    ap.add_argument("--weather-dir")
    ap.add_argument("--fits-dir")
    ap.add_argument("--token")
    ap.add_argument("--fingerprint")
    ap.add_argument("--install-dir", default="/opt/dorm")
    ap.add_argument("--regenerate-secrets", action="store_true")
    args = ap.parse_args()

    if args.role == "server":
        install_server(args)
    elif args.role == "pi":
        install_pi(args)
    elif args.role == "secrets":
        token, fp = make_secrets(force=args.regenerate_secrets)
        say(f"\n  token       {token}\n  fingerprint {fp}\n")
    else:
        raise SystemExit(check(args))


if __name__ == "__main__":
    main()
