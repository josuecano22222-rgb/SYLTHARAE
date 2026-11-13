"""Production deployment: start-up validation and the shipped configs.

* apps/web/deployment.py refuses invalid or dangerous settings and warns
  about unsafe production ones (each rule below).
* run_web.py exits with status 2 on a refusal - the systemd unit's
  RestartPreventExitStatus - instead of a traceback and a restart loop.
* deploy/nginx/syltharae.conf and deploy/systemd/syltharae.service keep the
  properties the documentation promises. tests/integration/test_deploy_nginx.py
  additionally runs `nginx -t` on the real file when nginx is installed.
"""
import configparser
import re
from pathlib import Path

import pytest

from apps.web.deployment import (
    DeploymentError, check_deployment, enforce, parse_trusted_proxy_count,
)

ROOT = Path(__file__).resolve().parents[2]
NGINX = (ROOT / "deploy" / "nginx" / "syltharae.conf").read_text(encoding="utf-8")
UNIT_TEXT = (ROOT / "deploy" / "systemd" / "syltharae.service").read_text(encoding="utf-8")

#: A correct production deployment behind one local TLS proxy.
GOOD = {
    "FLASK_ENV": "production", "FLASK_DEBUG": "false", "WSGI_SERVER": "waitress",
    "FLASK_HOST": "127.0.0.1", "FLASK_PORT": "5000", "TRUSTED_PROXY_COUNT": "1",
    "FLASK_SECRET_KEY": "x" * 64,
}


def codes(env, waitress=True):
    return {f.code: f.level for f in check_deployment(env, waitress_available=waitress)}


def test_a_correct_production_deployment_has_no_findings():
    assert codes(GOOD) == {}


@pytest.mark.parametrize("override, code", [
    ({"FLASK_DEBUG": "true"}, "debug_in_production"),
    ({"TRUSTED_PROXY_COUNT": "one"}, "invalid_trusted_proxy_count"),
    ({"TRUSTED_PROXY_COUNT": "-1"}, "invalid_trusted_proxy_count"),
    ({"FLASK_PORT": "http"}, "invalid_port"),
    ({"FLASK_PORT": "70000"}, "invalid_port"),
])
def test_invalid_or_dangerous_settings_refuse_to_start(override, code):
    env = {**GOOD, **override}
    assert codes(env)[code] == "error"
    with pytest.raises(DeploymentError, match="Refusing to start"):
        enforce(env, printer=lambda _line: None, waitress_available=True)


@pytest.mark.parametrize("override, code", [
    ({"WSGI_SERVER": "flask"}, "development_server"),
    ({"FLASK_HOST": "0.0.0.0"}, "proxy_bypassable"),
    ({"TRUSTED_PROXY_COUNT": "0", "FLASK_HOST": "0.0.0.0"}, "no_tls_proxy"),
    ({"FLASK_SECRET_KEY": "short"}, "short_secret_key"),
    ({"WAITRESS_THREADS": "many"}, "invalid_waitress_threads"),
])
def test_unsafe_production_settings_warn_but_start(override, code):
    env = {**GOOD, **override}
    assert codes(env)[code] == "warning"
    printed = []
    enforce(env, printer=printed.append, waitress_available=True)
    assert any(line.startswith("[WARNING]") for line in printed)
    assert any(line.startswith("[ACTION]") for line in printed)


def test_http_override_warning_explains_plaintext_exposure():
    env = {**GOOD, "TRUSTED_PROXY_COUNT": "0", "FLASK_HOST": "0.0.0.0",
           "SESSION_COOKIE_SECURE": "false", "SESSION_COOKIE_SAMESITE": "Lax"}
    finding = next(f for f in check_deployment(env) if f.code == "no_tls_proxy")
    assert "HTTP sign-in is enabled" in finding.message
    assert "unencrypted" in finding.message


def test_selected_but_missing_waitress_is_reported():
    assert codes(GOOD, waitress=False) == {"waitress_missing": "warning"}


def test_the_default_installation_still_starts():
    # FLASK_ENV defaults to production and WSGI_SERVER to flask: warn, not refuse.
    found = codes({}, waitress=False)
    assert "error" not in found.values()
    assert found["development_server"] == "warning"


def test_development_is_not_held_to_production_rules():
    assert codes({"FLASK_ENV": "development", "FLASK_DEBUG": "true"}) == {}


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost", "127.0.0.2"])
def test_loopback_hosts_are_recognised(host):
    assert codes({**GOOD, "FLASK_HOST": host}) == {}


def test_trusted_proxy_count_parsing():
    assert parse_trusted_proxy_count(None) == 0
    assert parse_trusted_proxy_count(" 2 ") == 2
    for bad in ("one", "1.5", "-1"):
        with pytest.raises(ValueError):
            parse_trusted_proxy_count(bad)


def test_run_web_exits_2_on_a_refusal(monkeypatch, capsys):
    import run_web

    def refuse():
        raise DeploymentError("Refusing to start: test")
    monkeypatch.setitem(run_web.__dict__, "_run_main", refuse)
    with pytest.raises(SystemExit) as exit_info:
        run_web.main()
    assert exit_info.value.code == run_web.EXIT_REFUSED == 2
    assert "Refusing to start" in capsys.readouterr().err


def test_run_web_no_longer_recommends_multi_process_servers():
    source = (ROOT / "run_web.py").read_text(encoding="utf-8")
    assert "use a WSGI server like Gunicorn" not in source
    assert "enforce(printer=print)" in source


# ---------------------------------------------------------------------------
# systemd unit
# ---------------------------------------------------------------------------
def _unit():
    parser = configparser.ConfigParser(strict=False, interpolation=None,
                                       comment_prefixes=("#",), inline_comment_prefixes=None)
    parser.optionxform = str
    parser.read_string(UNIT_TEXT)
    return parser


def test_systemd_unit_names_every_runtime_file_kept_in_the_install_dir():
    """ReadWritePaths grants /opt/syltharae because the app writes there.

    The unit's comment is the operator's list of what gets written; each path
    is derived from the code's own constants so the list cannot drift.
    """
    from Api.services import search_history
    from core.path_utils import setup_path

    root = Path(__file__).resolve().parents[2]
    written = [
        search_history.HISTORY_FILE,
        # search_history.SAVED_SEARCHES_FILE is no longer written: saved
        # searches live in PostgreSQL (m0016); the legacy file is read once.
        Path(setup_path()) / ".app_instance.lock",
        root / "data" / "settings.json",      # settings_manager: first store location
        root / "data" / "settings_backups",   # settings_manager: backups beside it
    ]
    comment = " ".join(line.lstrip("# ") for line in UNIT_TEXT.splitlines() if line.startswith("#"))
    for path in written:
        rel = Path(path).resolve().relative_to(root).as_posix()
        assert rel in comment, f"{rel} is written at runtime but not listed in the unit"
    assert "/opt/syltharae" in _unit()["Service"]["ReadWritePaths"]


def test_systemd_unit_runs_one_hardened_process():
    service = _unit()["Service"]
    assert service["ExecStart"].endswith("python run_web.py")
    assert service["RestartPreventExitStatus"] == "2"
    assert service["User"] == "syltharae"
    assert "EnvironmentFile" not in service, "the app reads .env itself (one parser)"
    for key, value in {"NoNewPrivileges": "true", "ProtectSystem": "strict",
                       "ProtectHome": "true", "PrivateTmp": "true",
                       "CapabilityBoundingSet": "", "AUTO_INSTALL=0": None}.items():
        if value is None:
            assert key in UNIT_TEXT
        else:
            assert service[key] == value, key
    assert service["ReadWritePaths"].split() == ["/var/lib/syltharae", "/opt/syltharae"]
    assert "APP_DATA_DIR=/var/lib/syltharae" in UNIT_TEXT


# ---------------------------------------------------------------------------
# nginx site
# ---------------------------------------------------------------------------
def _directives(name):
    return [m.group(1).strip() for m in re.finditer(rf"^\s*{name}\s+([^;]*);", NGINX, re.M)]


def test_nginx_overwrites_forwarded_headers():
    headers = dict(v.split(None, 1) for v in _directives("proxy_set_header"))
    assert headers["X-Forwarded-For"] == "$remote_addr", \
        "appending ($proxy_add_x_forwarded_for) would forward a client-supplied address"
    assert headers["X-Forwarded-Proto"] == "$scheme"
    # Without Host/X-Forwarded-Host the app sees 127.0.0.1:5000 and the
    # browser's Referer (the public name) fails the HTTPS CSRF check: every
    # sign-in answers 400 (a failure met in practice; see OPERATIONS.md).
    assert headers["Host"] == "$host" and headers["X-Forwarded-Host"] == "$host"


def test_nginx_serves_tls_only_and_hides_its_version():
    assert _directives("ssl_protocols") == ["TLSv1.2 TLSv1.3"]
    assert "return 301 https://$host$request_uri" in NGINX
    assert _directives("server_tokens") == ["off", "off"]
    assert "listen 443 ssl http2" in NGINX
    assert "Strict-Transport-Security" not in NGINX.split("# them here too")[1], \
        "HSTS comes from the application; a second copy would be combined"


def test_nginx_limits_match_the_application():
    import os
    default_mb = 2048
    assert os.environ.get("OPERATIONS_MAX_UPLOAD_MB") in (None, str(default_mb))
    operations = (ROOT / "Api" / "routes" / "operations_api.py").read_text(encoding="utf-8")
    assert f'"OPERATIONS_MAX_UPLOAD_MB", "{default_mb}"' in operations
    assert _directives("client_max_body_size") == ["2g"]
    assert _directives("proxy_read_timeout") == ["600s"]
    assert "server 127.0.0.1:5000;" in NGINX


def test_event_streams_opt_out_of_proxy_buffering():
    for path in ("Api/routes/operations_api.py", "Api/routes/cursor_api.py"):
        assert "X-Accel-Buffering" in (ROOT / path).read_text(encoding="utf-8"), path


def test_readiness_json_report_is_pure_json():
    """`verify_readiness.py --json` is for CI: nothing else may reach stdout."""
    import json
    import subprocess
    import sys
    env = {"PATH": "/usr/bin:/bin", "FLASK_ENV": "production", "WSGI_SERVER": "flask",
           "AUTO_INSTALL": "0", "HOME": str(ROOT)}
    result = subprocess.run([sys.executable, "verify_readiness.py", "--json"], cwd=ROOT,
                            capture_output=True, text=True, env=env, timeout=300)
    report = json.loads(result.stdout)
    deployment = {r["name"]: r for r in report["results"] if r["section"] == "deployment"}
    assert deployment["Start-up settings are valid (apps/web/deployment.py)"]["passed"]
    warning = deployment["Production settings follow the deployment guide"]
    assert not warning["passed"] and not warning["critical"]
    assert "development server" in warning["error"]


def test_readiness_json_survives_checks_that_print(monkeypatch, capsys):
    import json
    import verify_readiness as vr

    def noisy():
        print("warning: some library notice on stdout")
        return vr.CheckResult("environment", "noisy", True, False, evidence="ok")
    monkeypatch.setattr(vr, "CHECKS", [noisy])
    monkeypatch.setattr(vr, "RESULTS", [])
    vr.RESULTS.append(vr.CheckResult("environment", "noisy", True, False, evidence="ok"))
    vr.main(["--json"])
    out, err = capsys.readouterr()
    assert json.loads(out)["results"][0]["name"] == "noisy"
    assert "library notice" in err


def test_nginx_keeps_http2_connections_below_the_goaway_race():
    """DEPLOY-H2-GOAWAY: at the default keepalive_requests (1000) nginx closed
    the browser's HTTP/2 connection every ~20 page views, and the scripts a page
    had just requested on it failed with net::ERR_FAILED - the live browser
    smoke found pages whose handlers were never defined. The limit must be far
    above a session's request count, with keepalive_time bounding the lifetime.
    """
    [requests] = _directives("keepalive_requests")
    assert int(requests) >= 100000
    assert _directives("keepalive_time") == ["1h"]
