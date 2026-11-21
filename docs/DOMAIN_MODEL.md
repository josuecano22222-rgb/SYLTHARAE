# Domain model

What SYLTHARAE stores, in the terms an analyst uses, mapped to the tables that
hold it. This document is checked: `tests/unit/test_architecture_claims.py`
re-derives every concept-to-table mapping and every column claim from
`database/migrations/m0001_baseline.py`, and
`tests/unit/test_ocr_provenance_docs.py` checks the OCR field list against
`StoragePipeline` in both directions. A sentence that drifts fails there.

## Concepts

| Concept | Table | What it is |
|---|---|---|
| file | `paths` | One occurrence of a file at one path. The unit an analyst sees. |
| logical file | `hashs` | The content itself, identified by its SHA-256. Two paths pointing at identical bytes share one logical file. |
| occurrence | `paths` | Where a logical file was found. The same content at two paths is two occurrences of one logical file. |
| source / side | `sources` | Mandatory provenance. Which collection a file came from, and which party within it. |
| keyword | `keywords` | A classified phrase extracted from content. |
| category | `categorys` | A node in the classification tree a keyword or word is assigned to. |

A file is identified by its **path and hash** together, not by either alone.
`paths.file_path` is `NOT NULL`, `paths.context_id` is `NOT NULL` and
references `hash_contexts(id)`, and `hashs.hash` is a `CHAR(64) NOT NULL
UNIQUE`. That is what makes deduplication exact rather than approximate: same
bytes, one logical file; new path, one more occurrence.

## Containers are lineage, not a table

There is no `archive` table. An archive, a mailbox and an email with
attachments are all read as a tree, and the tree is recorded on the occurrence
itself: `paths.parent_path_id` points at the container, and
`paths.hierarchy_path` records the position within it.

This matters for scale. A one-million-message mailbox does not become a million
rows in a container table; it becomes a million occurrences whose parent is the
mailbox. Lineage is walked through `parent_path_id`, and depth is not limited by
a schema.

## Provenance

`paths.extraction_provenance` is a nullable JSONB column.
**Every item the pipeline stores carries `extraction_provenance`**, which is a
statement about what the pipeline wrote — not a claim about rows written by
other paths. The
column is nullable because a row can exist without the pipeline having written
it, and a document that claimed otherwise would be promising something it
cannot deliver.

For OCR, `extraction_provenance.ocr` holds what the pipeline recorded about the
attempt, not an interpretation of it. A single-value read records `attempted`,
`successful`, `derived`, `engine`, `engine_version`, `language`, `confidence`,
`confidence_error`, `input_variant`, `rotation` and `missing_languages`. A
paginated read instead records `total_pages`, `ocr_pages`, `rotated_pages` and
`engines`, with the per-page `derived`, `engine`, `engine_version`, `language`,
`confidence`, `input_variant` and `missing_languages` values alongside.

Two conventions make the field readable. A field that is absent means *not
measured*, never *measured as zero*: a confidence that could not be computed is
`null`, not zero. And an attempt that failed is stored as a failed attempt
rather than dropped, so `attempted` is `true` while `successful` records the
outcome — the absence of text is never evidence that OCR was not tried.

`input_variant` records whether the engine read the `original` image or a
`preprocessed` one, and `missing_languages` records languages that were asked
for and not installed. Both are recorded as failures to read, never silently
retried in another language.

## Media

A media read stores what the container and streams actually declare: duration,
geometry, codec, stream counts and tags are all stored. Speech is different,
because it is not extracted: a media read records that speech was **not**
extracted, in `speech_extracted`, so a reader of the record can tell "no speech
in this file" from "speech was never attempted". The same honesty rule applies
to every unsupported dependency — a missing codec is a recorded failure, not an
empty field.

## Text storage

`contents` holds the text search reads; `contents_raw` holds the verbatim
extraction. They are written together and are not interchangeable: a search
index may normalise, and the raw text is what an analyst quotes.

---

Reviewed by: the repository owner. Corrections to a checkable sentence belong
in `tests/unit/test_architecture_claims.py` first, then here.
