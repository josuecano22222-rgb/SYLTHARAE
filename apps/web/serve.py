"""How the web application is served.

``python run_web.py`` has always used Flask's built-in server. That server is
fine for one analyst on one workstation, but Werkzeug documents it as a
development server: it is not hardened against slow or hostile clients and
does not manage a worker pool.

``WSGI_SERVER=waitress`` switches to Waitress, a pure-Python production WSGI
server that runs on Windows and Linux alike (``pip install waitress``, or the
``server`` extra). It is opt-in, so an existing installation - including an
offline one whose wheel bundle has no Waitress - behaves exactly as before.

``WAITRESS_THREADS`` defaults to 32 rather than Waitress's 4: the Jobs page
keeps a Server-Sent Events stream open per browser tab, and each stream holds
a thread for as long as the page is open.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_WAITRESS_THREADS = 32


def selected_server() -> str:
    value = os.environ.get("WSGI_SERVER", "flask").strip().lower()
    return value if value in ("flask", "waitress") else "flask"


def _threads() -> int:
    try:
        return max(4, int(os.environ.get("WAITRESS_THREADS", DEFAULT_WAITRESS_THREADS)))
    except ValueError:
        return DEFAULT_WAITRESS_THREADS


def serve(app, host: str, port: int, debug: bool = False) -> str:
    """Serve *app*; return the name of the server that was used."""
    if selected_server() == "waitress" and not debug:
        try:
            from waitress import serve as waitress_serve
        except ImportError:
            print("[WARNING] WSGI_SERVER=waitress but Waitress is not installed "
                  "(pip install waitress); falling back to the built-in server.")
        else:
            threads = _threads()
            print(f"[OK] Serving with Waitress ({threads} threads)")
            # Waitress drops X-Forwarded-* headers from peers it does not
            # trust, which would silently defeat TRUSTED_PROXY_COUNT. Trust
            # is decided in one place - the app's ProxyFix, enabled only by
            # TRUSTED_PROXY_COUNT - so Waitress passes the headers through.
            # Without ProxyFix, Flask ignores them anyway.
            waitress_serve(app, host=host, port=port, threads=threads,
                           channel_timeout=600, ident="SYLTHARAE",
                           clear_untrusted_proxy_headers=False)
            return "waitress"
    elif selected_server() == "waitress" and debug:
        print("[WARNING] Debug mode uses the built-in server; WSGI_SERVER ignored.")
    app.run(debug=debug, host=host, port=port, threaded=True)
    return "flask"
