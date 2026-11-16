# Licensing

## This project

SYLTHARAE is released under **`AGPL-3.0-or-later`**. The authoritative
statement is the `license` field in `pyproject.toml`; the authoritative text is
[`LICENSE`](../LICENSE) at the repository root. This document explains the
obligations that follow, and nothing in it overrides those two.

The AGPL is a *network* copyleft licence. Its distinguishing clause — section
13, "Remote Network Interaction; Use with the GNU General Public License" — is
what makes it different from the ordinary GPL:

> if you modify the program and let users interact with it **over a network**,
> you must offer those users the corresponding source of your modified version.

Deploying an unmodified copy carries no source-offering obligation. Deploying a
*modified* copy over a network does. For a self-hosted platform like this one,
"deploying over a network" is the normal case, so **any modified deployment is
in scope**.

## `SOURCE_CODE_URL`

Because section 13 requires an offer of source, the application exposes a
configurable disclosure URL via the **`SOURCE_CODE_URL`** setting.

- Set it in `.env` (it is documented in `.env.example`) or through the
  administration settings surface.
- Point it at a location where the corresponding source of the *deployed*
  version — including every modification — can be obtained.
- When it is set, the UI states the licence and links to that URL, so the offer
  is visible to the people interacting with the deployment.
- When it is unset, the UI states the licence and links to the upstream
  project. **That is honest for an unmodified deployment and misleading for a
  modified one.** If the deployment is modified, set it.

`SOURCE_CODE_URL` accepts `http:` and `https:` only. Other schemes are refused
rather than silently dropped, because a disclosure URL that cannot be opened is
not a disclosure.

## Third-party components

[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) lists the Python
distributions and vendored front-end assets this repository ships, with the
licence each one declares.

It is **generated**, not hand-written:

```bash
python tools/licenses/generate_notices.py           # rewrite it
python tools/licenses/generate_notices.py --check   # fail if it is stale
```

It is generated from facts only: the installed distributions' own metadata and
`git ls-files static`. It reproduces no licence text, and it resolves no
unclear licence by guess. An unclear package is listed as unclear; it is never
assumed to be fine.

Compatibility verdicts come from **`tools/licenses/check_licenses.py`**. A
package can be compatible with this project's licence and still appear in the
notices — the notices record what ships, not only what is permitted.

Adding a dependency therefore has two steps: add it, and regenerate the
notices. A pull request that adds a dependency without regenerating them will
fail the licence tests, which is deliberate: the notices must never drift from
what is actually shipped.

## Optional components with their own terms

Some capabilities are optional and carry licence terms that deserve explicit
attention before they are enabled.

Machine translation is off by default and supports several backends. The
per-request opt-in **`NLLB-200`** adapter is released under **CC-BY-NC-4.0**,
which **excludes commercial use**. It is disabled by default, requires an
explicit per-request opt-in, and is not a substitute for the MIT-licensed
`Argos Translate` or CC-BY-4.0 `Opus-MT` backends. Do not enable it in a
commercial deployment without taking separate advice.

Nothing in this project grants a licence to any document, dataset or media file
ingested into it. Ingesting a corpus into SYLTHARAE does not transfer, licence
or waive any right in that corpus.

## Practical summary

| You are doing | What is required |
|---|---|
| Running an unmodified copy for your own team | Nothing beyond keeping the notices with the copy. |
| Running a modified copy internally, no external users | Nothing under section 13, but you must not remove the licence or notices. |
| Running a modified copy that others interact with over a network | Offer the corresponding source; set **`SOURCE_CODE_URL`** to where it can be obtained. |
| Distributing binaries or an offline bundle | Include `LICENSE` and `THIRD_PARTY_NOTICES.md`; both are listed in `license-files` so they are packaged automatically. |
| Enabling `NLLB-200` translation | CC-BY-NC-4.0 — commercial use excluded. Confirm it is appropriate first. |

This document is a practical guide, not legal advice.
