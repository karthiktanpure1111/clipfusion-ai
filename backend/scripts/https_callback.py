"""Local HTTPS forwarder for OAuth redirects that must use HTTPS (Meta's Instagram Unified Login).

Runs only when ``INSTAGRAM_REDIRECT_URI`` is an ``https://localhost:<port>/...`` URL. It serves HTTPS on
127.0.0.1:<port> with a self-signed certificate (created once in ``backend/data/certs``) and forwards every
``/api/v1/accounts/callback/...`` request, query string included, to the ClipFusion API
(``BACKEND_PUBLIC_URL``). Nothing else is served. The browser shows a certificate warning the first time:
choose "Advanced" -> "Proceed to localhost".

Started by start.ps1, stopped by stop.ps1. Usage: python scripts/https_callback.py
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402

log = logging.getLogger("https_callback")
CERT_DIR = Path(__file__).resolve().parents[1] / "data" / "certs"
CALLBACK_PREFIX = "/api/v1/accounts/callback/"


def ensure_certificate() -> tuple[Path, Path]:
    """A self-signed certificate for localhost / 127.0.0.1 (valid ~2 years)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_path, key_path = CERT_DIR / "localhost.crt", CERT_DIR / "localhost.key"
    if cert_path.is_file() and key_path.is_file():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if cert.not_valid_after_utc > dt.datetime.now(dt.UTC) + dt.timedelta(days=7):
            return cert_path, key_path
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ClipFusion (local)")])
    now = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=800))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def make_handler(target_base: str) -> type[BaseHTTPRequestHandler]:
    class Forward(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if not self.path.startswith(CALLBACK_PREFIX):
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"ClipFusion: only sign-in redirects are handled here.")
                return
            self.send_response(302)
            self.send_header("Location", target_base + self.path)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            log.info("%s %s", self.command, self.path.split("?", 1)[0])  # never log codes/state

    return Forward


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    redirect = (settings.instagram_redirect_uri or "").strip()
    parts = urlsplit(redirect)
    if parts.scheme != "https" or parts.hostname not in ("localhost", "127.0.0.1") or not parts.port:
        log.info("INSTAGRAM_REDIRECT_URI is not an https://localhost:<port> URL; nothing to serve.")
        return 0
    cert, key = ensure_certificate()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server = ThreadingHTTPServer(("127.0.0.1", parts.port), make_handler(settings.backend_public_url.rstrip("/")))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    log.info("Forwarding https://localhost:%d%s* to %s", parts.port, CALLBACK_PREFIX, settings.backend_public_url)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
