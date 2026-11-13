"""`nginx -t` accepts deploy/nginx/syltharae.conf as shipped.

Needs an nginx binary (on PATH, or NGINX_BIN) and openssl; skipped otherwise.
Only the certificate paths are replaced, with a throw-away self-signed pair.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NGINX = os.environ.get("NGINX_BIN") or shutil.which("nginx")
OPENSSL = shutil.which("openssl")

pytestmark = pytest.mark.skipif(not (NGINX and OPENSSL), reason="nginx or openssl not installed")


def _free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_nginx_accepts_the_shipped_site(tmp_path):
    key, cert = tmp_path / "test.key", tmp_path / "test.pem"
    subprocess.run([OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=syltharae.example.org", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True)
    site = (ROOT / "deploy" / "nginx" / "syltharae.conf").read_text(encoding="utf-8")
    site = site.replace("/etc/ssl/certs/syltharae.pem", str(cert)) \
               .replace("/etc/ssl/private/syltharae.key", str(key))
    # `nginx -t` binds the listen sockets; unprivileged runs use free ports.
    http_port, https_port = _free_port(), _free_port()
    for old, new in (("listen 80;", f"listen {http_port};"),
                     ("listen [::]:80;", f"listen [::]:{http_port};"),
                     ("listen 443 ssl http2;", f"listen {https_port} ssl http2;"),
                     ("listen [::]:443 ssl http2;", f"listen [::]:{https_port} ssl http2;")):
        assert site.count(old) == 1, old
        site = site.replace(old, new)
    (tmp_path / "site.conf").write_text(site, encoding="utf-8")
    (tmp_path / "nginx.conf").write_text(
        f"pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/error.log;\n"
        "events {}\n"
        f"http {{\n  access_log off;\n  client_body_temp_path {tmp_path}/body;\n"
        f"  proxy_temp_path {tmp_path}/proxy;\n  include {tmp_path}/site.conf;\n}}\n",
        encoding="utf-8")
    result = subprocess.run([NGINX, "-t", "-p", str(tmp_path), "-c", str(tmp_path / "nginx.conf")],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "syntax is ok" in result.stderr
    # Only the deliberate "listen ... http2" form may warn (nginx >= 1.25.1
    # prefers "http2 on;", which nginx 1.24 - Ubuntu 24.04 - rejects).
    warnings = [line for line in result.stderr.splitlines() if "[warn]" in line]
    assert all('"listen ... http2" directive is deprecated' in w for w in warnings), warnings
