# Security

What the application does to protect itself, what it relies on the deployment
to do, and what it explicitly does not claim.

This document describes controls that exist in the code and are exercised by
`tests/security` and `tests/unit`. It is evidence of a security posture, not a
penetration test, a certification, or a threat model.

## Reporting a vulnerability

Open an issue on the project's source repository with the smallest
reproduction you can share. Do not attach customer data. If the issue is
sensitive, ask the maintainer for a private channel first rather than posting
exploitation detail publicly.

## Content-Security-Policy

Every response carries this header, set by
`add_security_headers` in `apps/web/app.py`:

`default-src 'self'; script-src 'self'; worker-src 'self' blob:; child-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'`

There is no inline script. Handlers are `data-on-<event>` attributes
interpreted by `static/js/modules/core/declarative-events.js`, and page data
travels in `<script type="application/json">` blocks, which are not executed.
This is enforced rather than intended:
`tests/unit/test_csp_no_inline_script.py` fails if a template acquires an
inline handler attribute or if the served policy stops matching the one
documented here.

Styles are the one deliberate exception: `style-src` permits inline styles
because the UI sets computed geometry and theming inline, and there is no
path from a style to script execution.

Each header is applied with `setdefault`, so a view can relax one
deliberately — `frame-ancestors` in particular is relaxed for the
original-file viewer, which frames a PDF this application serves.

## Trust boundaries

There are three, and untrusted data crosses all of them.

- **Browser and reverse proxy → application.** Requests are untrusted in full.
  Client IP attribution depends on `TRUSTED_PROXY_COUNT` matching the real
  proxy chain; a wrong value misattributes addresses and therefore breaks rate
  limits and lockout.
- **Application → PostgreSQL.** All SQL is parameterised. This is policy and is
  checked; string-formatted SQL is a finding, not a style question.
- **Application → filesystem.** Paths are confined by `core/path_safety.py`.
  Ingestion from a server path is allowlisted through `INGESTION_ROOTS`, and a
  path that resolves outside the allowlist is refused rather than sanitised.

Uploaded documents, archives and emails are attacker-controlled input. Archive
extraction is guarded against path traversal, decompression bombs and
excessive nesting, and optional decoders are treated as untrusted parsers.

## Authentication and authorisation

- Password hashing, per-account lockout after repeated failures, and rate
  limiting on authentication endpoints.
- CSRF protection via Flask-WTF on every state-changing request. Behind a
  proxy, `Host` and `X-Forwarded-Host` must both be forwarded: without them the
  application sees `127.0.0.1:5000`, the browser's `Referer` fails the HTTPS
  check, and every sign-in answers `400`. `deploy/nginx/syltharae.conf` sets
  both.
- Authorisation is enforced per endpoint and per interface. Interface
  visibility is a registry property, not a template convention.
- Sessions use secure, HttpOnly cookies with a configured `SameSite`.
  `SESSION_COOKIE_SECURE` must stay enabled in production.
- Changing a password requires the current password, so a session alone is not
  enough to take over an account, and the change endpoint is rate limited. The
  attempt is recorded whether or not it succeeds: `password.change`,
  `password.change_failed` and `password.change_locked`.

## Audit and integrity

Security-relevant actions are written to an append-only audit log. Selected
history is protected by database triggers so it cannot be updated or deleted
through the application. Report artifacts carry checksums so a stored result
can be shown to be the one that was produced.

## Data handling and retention

Extracted text, signals and derived data are stored in PostgreSQL; uploaded
files and job artifacts live under `APP_DATA_DIR`. Retention policies are
configurable and enforced by a scheduled job, and deletion covers the derived
rows and the artifacts, not only the primary record.

## Secrets

Never commit `.env`. Provision `FLASK_SECRET_KEY` and the database
credentials from a secret store. The installer's auto-generated key is a
fallback so a first run works; it is not secret lifecycle management, and
rotating it invalidates sessions by design.

## Deployment hardening

The shipped unit hardens the service (`NoNewPrivileges`, `ProtectSystem=strict`,
`ProtectHome`, `PrivateTmp`, an empty `CapabilityBoundingSet`) and the
application refuses to start on dangerous settings — see
`apps/web/deployment.py` and [OPERATIONS](OPERATIONS.md). The reverse proxy
terminates TLS; the application binds to loopback and will warn if it is asked
to do otherwise.

## Residual risk, stated plainly

- Repository controls and tests are evidence of care, not proof of absence.
  They are not an independent assessment.
- Optional decoders (archive formats, OCR, ebook and email parsers) enlarge the
  attack surface. They are optional for that reason; install only the ones the
  deployment needs.
- OCR invokes an external engine over untrusted pixels. `tesseract` is a native
  binary; the pure-Python `rapidocr` fallback runs in-process.
- Dependency advisories are the operator's to track. `THIRD_PARTY_NOTICES.md`
  lists what is installed and under which licence; it does not attest that
  every version is free of known vulnerabilities.
