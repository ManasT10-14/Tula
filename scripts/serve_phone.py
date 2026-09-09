"""Serve the console over HTTPS on the local network, for a phone on the same Wi-Fi.

The application refuses plain HTTP from anything but loopback: every page except
`/healthz` answers 426 with "Use HTTPS to sign in or access inspection data".
That check is in `tula.security.web.SecurityMiddleware` and this script does not
touch it. A phone browsing to `http://<laptop-ip>:8000` therefore sees a JSON
error, not the console, which is the intended behaviour and not a fault.

Two separate things need the same fix. The 426 above is one. The other is the
camera: `navigator.mediaDevices.getUserMedia` is only defined in a secure
context, so the capture page's "Use camera" button cannot work over LAN HTTP
either. HTTPS satisfies both at once, so this script generates a self-signed
certificate for the machine's LAN address and starts Uvicorn with TLS.

    python scripts/serve_phone.py

The certificate is self-signed, so the phone shows a warning once and you tap
through it. That is the cost of not having a real certificate for a private
address, and it means the connection is encrypted but unauthenticated: a device
on the same network could impersonate it. Use this to try the console from a
handset on a network you trust. It is not a deployment: see docs/SECURITY.md for
HTTPS and account requirements outside loopback.

The demonstration accounts from `scripts/seed_demo.py` are the ones to sign in
with, and the README's warning applies with more force here than on loopback --
this port is reachable by everything on the network, so delete those accounts or
change their passwords when you are finished.

This serves the console to a phone; it does not install one. Browsers refuse to
register a service worker on an origin with certificate errors, so the pages and
the camera work here but "Add to home screen" will not produce the standalone app.
That needs a certificate the phone actually trusts -- see `deploy/` in the README.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import subprocess
import sys
from pathlib import Path


def lan_address() -> str:
    """The address this machine has on the local network.

    Opening a UDP socket to an off-link address does not send anything; it just
    makes the routing table pick the interface a phone would reach us on, which
    is more reliable than resolving the hostname.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1: reserved, never routed
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def ensure_certificate(directory: Path, address: str) -> tuple[Path, Path]:
    """Create a self-signed certificate naming `address`, unless one already is.

    The address is in subjectAltName because browsers have ignored commonName
    for years. A certificate made for a previous address is regenerated rather
    than reused: DHCP moves laptops between addresses, and a stale SAN produces
    a warning the user cannot tell apart from a real one.
    """
    directory.mkdir(parents=True, exist_ok=True)
    certificate = directory / "phone-cert.pem"
    key = directory / "phone-key.pem"

    if certificate.exists() and key.exists():
        current = subprocess.run(
            ["openssl", "x509", "-in", str(certificate), "-noout", "-text"],
            capture_output=True, text=True, check=False,
        )
        if current.returncode == 0 and f"IP Address:{address}" in current.stdout:
            print(f"reusing certificate  {certificate}")
            return certificate, key
        print("address changed since the last run; regenerating the certificate")

    print(f"generating a self-signed certificate for {address}")
    result = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(certificate), "-days", "365",
         "-subj", f"/CN={address}",
         "-addext", f"subjectAltName=IP:{address},IP:127.0.0.1,DNS:localhost"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            "openssl could not generate the certificate. Install OpenSSL and put it "
            f"on PATH, or pass --certfile/--keyfile.\n{result.stderr.strip()}"
        )
    key.chmod(0o600)
    return certificate, key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0",
                        help="interface to bind (default: every interface)")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--address", default=None,
                        help="LAN address to name in the certificate (default: detected)")
    parser.add_argument("--certfile", type=Path, default=None,
                        help="use an existing certificate instead of generating one")
    parser.add_argument("--keyfile", type=Path, default=None)
    parser.add_argument("--cert-dir", type=Path, default=Path("out/phone-tls"),
                        help="where generated certificates are kept")
    arguments = parser.parse_args()

    if bool(arguments.certfile) != bool(arguments.keyfile):
        parser.error("--certfile and --keyfile go together")

    address = arguments.address or lan_address()
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        parser.error(f"--address must be an IP address, not {address!r}")
    if parsed.is_loopback:
        print("warning: no local network address was detected, so this will only be "
              "reachable from this machine. Check the Wi-Fi connection, or pass "
              "--address explicitly.", file=sys.stderr)

    if arguments.certfile:
        certificate, key = arguments.certfile, arguments.keyfile
    else:
        certificate, key = ensure_certificate(arguments.cert_dir, address)

    url = f"https://{address}:{arguments.port}"
    print()
    print("  On the phone, join the same Wi-Fi and open:")
    print(f"      {url}")
    print()
    print("  The certificate is self-signed, so the browser warns once. Accept it to")
    print("  continue; the camera needs this HTTPS connection to work at all.")
    print()

    import uvicorn

    uvicorn.run(
        "tula.web.app:app",
        host=arguments.host,
        port=arguments.port,
        ssl_certfile=str(certificate),
        ssl_keyfile=str(key),
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
