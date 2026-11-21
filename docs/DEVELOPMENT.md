# Development

How to work in this codebase: the layout, the rules that are enforced, and how
to run the checks before opening a change.

## Layout

| Directory | Contents |
|---|---|
| `core/` | Domain logic with no web or service dependencies: detection, OCR, path safety, checkpointing, interfaces, criteria |
| `services/` | Application services that coordinate `core`: jobs, detection, reporting, geo, retention |
| `Api/routes/` | HTTP endpoints, grouped by capability |
| `apps/web/` | Application factory, security headers, deployment validation |
| `templates/` | Jinja templates, grouped by interface |
| `static/js/modules/` | Browser JavaScript, loaded as ES modules |
| `database/migrations/` | Numbered migrations, applied in order |
| `tools/` | Generators, audits and one-off tooling — including the documentation generators |
| `tests/` | `unit`, `integration`, `security`, plus the JS harness under `tests/js` |

The layering rule is **enforced**: `core` must not import `services` or `Api`.
`tests/unit/test_architecture_claims.py` pins the current exceptions by name,
so introducing a new one fails the build rather than being noticed later. If
`core` needs behaviour that lives in `services`, invert it — hand the
dependency in, as `core/installer.py` does with its gazetteer seeder.

## Running the tests

```
python -m pytest tests/unit -q            # fast, no external services
python -m pytest tests/security -q        # authorisation and header checks
python -m pytest tests/integration -q     # needs PostgreSQL
python -m pytest -q                       # everything
```

Integration tests create a disposable database; `pgserver` provides one, which
is why it is a declared test dependency rather than an optional extra.

Two tools are worth running directly:

- `python verify_readiness.py` — environment and configuration checks.
- `python -m pytest tests/unit/test_architecture_claims.py` — the layering rule,
  fastest way to learn you have crossed a boundary.

## Rules the tests enforce

These are not conventions someone wrote down and hopes for. Each is a test, and
it is faster to read the test than to be told about it twice.

### No inline script

The content security policy forbids inline script. Handlers are
`data-on-<event>` attributes — for example `data-on-click` — interpreted by
`static/js/modules/core/declarative-events.js`, and page data travels in
`<script type="application/json">` blocks, which are not executed.

`tests/unit/test_csp_no_inline_script.py` fails if a template acquires an
inline handler attribute, if the served policy stops matching the one
documented in [SECURITY](SECURITY.md), or if a JavaScript file would need
`unsafe-eval`. Adding a page means using `data-on-click` and friends, not
`onclick=`.

### Generated documents are generated

A number of documents under `docs/` embed a block produced by a generator, and
a test compares the block against what the generator currently says. A count
typed into those documents by hand is a count that will drift — and there is a
test that fails on exactly that, in several of them.

If a generated document is stale, regenerate it; do not edit the block:

```
python -m core.interfaces.docgen docs/INTERFACE_REGISTRY.md
python -m core.experience.docgen docs/EXPERIENCE_CONTRACT.md
python -m core.experience.action_audit docs/ACTION_SURFACE_AUDIT.md
python -m core.frontend.component_audit docs/COMPONENT_LIBRARY.md
python -m core.experience.audit docs/SCREEN_INSPECTOR_EVIDENCE.md
python tools/docs/generate_reference.py
python tools/windows/inventory_dependencies.py
```

The generators **splice** into an existing file between `BEGIN`/`END` markers.
They do not create the file, so a missing document needs its skeleton first.
`python tools/docs/generate_schema.py` needs a migrated PostgreSQL.

### The interface registry is frozen

Every user-facing route is owned by a registered interface. Adding a page
without registering it fails
`tests/integration/test_interface_coverage.py`, and the registry's lifecycle
policy is the only thing that decides whether a status is navigable.

### Parameterised SQL, and confined paths

String-built SQL is a finding. Filesystem access goes through
`core/path_safety.py`, and ingestion from a server path is allowlisted.

## Migrations

Add `database/migrations/mNNNN_<name>.py` with the next number. Migrations
apply in order and are recorded in `schema_migrations`; a migration that has
already been applied is not applied again. Never edit an applied migration —
add the next one.

## Conventions

- Python is formatted and linted with `ruff`; `bandit` covers security linting.
  Both are declared development dependencies.
- Browser JavaScript is ES modules under `static/js/modules/`, one pack per
  capability, with no build step.
- User-facing strings go through the translation catalogs; a new string needs
  its entry rather than a literal in the template.

## Before opening a change

1. `python -m pytest tests/unit tests/security -q`
2. `python verify_readiness.py`
3. If you touched `docs/`, regenerate rather than edit.
4. If you added a route, confirm it is registered and owned.
