# Gazetteer seed — what ships, where it came from, how it is rebuilt

`data/gazetteer/` is a **build input**, not generated output. The loader reads
`places_seed.json`; the other files exist so the seed can be rebuilt and
checked from the repository alone, without a network call to Wikidata.

## Shipped files

| file | rows / size | what it is |
| --- | --- | --- |
| `places_seed.json` | 223 places, 1 155 names | the seed the loader reads (format 1, `seed_version` `2026-09-28.2`) |
| `raw/wdqs_batch0.txt`, `wdqs_batch1.txt`, `wdqs_batch2.txt` | 75 + 75 + 73 rows | Wikidata Query Service output, one tab/`@@`-separated row per place: label, QID, WKT point, country code, and the multilingual names |
| `curation.json` | reviewed corrections | hand-reviewed fixes and additions applied on top of the raw rows; every entry carries its reason |
| `SOURCES.md` | this file | provenance and rebuild instructions |

The query that produced the raw rows is committed as
`tools/gazetteer/wikidata_query.rq`; the enwiki titles used as item keys and
their curated feature types are in `tools/gazetteer/titles.tsv`.

## Provenance

* Source: **Wikidata** (<https://www.wikidata.org/>), queried through the
  Wikidata Query Service. The seed header records this itself:
  `"source": "Wikidata (https://www.wikidata.org/), CC0 1.0; enwiki titles as
  item keys"`, `"retrieved": "2026-09-28"`.
* Licence of the derived data: **CC0 1.0**, as declared in the seed header
  (`"licence": "CC0-1.0"`). Wikidata publishes its structured data under CC0
  1.0; the licence text is not reproduced here (see the open items below).
* The seed's `content_sha256` is the SHA-256 of the canonical `places`
  payload, so a database can prove which seed it holds.

## Rebuild and check

```
python tools/gazetteer/build_seed.py            # rewrite places_seed.json
python tools/gazetteer/build_seed.py --check    # rebuild in memory, fail on drift
```

The build is deterministic (sorted, canonical JSON). `--check` is the guard
used by the test suite: it fails if the committed seed differs from what the
committed inputs produce.

## Why this file exists

`data/` used to be excluded by `.gitignore`, which silently kept
`data/gazetteer/` out of the repository, so the gazetteer migration failed on
every clone. `tests/unit/test_gazetteer_packaging.py` now asserts that these
files exist and are not git-ignored, and `curation.json` refers to this file
for the reason column.

## Open items (not decided here)

* The full attribution/licence notice to ship with the seed has not been
  agreed; the seed records the licence identifier only.
* Geo keys remain a documented stopgap: `geo_places` has no `place_key`
  column, so read paths derive `place:<id>` from the id. The replacement
  design (category types, single-word terms, multi-word keywords) is deferred
  and belongs to the schema owner.
