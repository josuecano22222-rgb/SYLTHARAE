"""Deferred item 3d: a container must be skipped on retry, not re-extracted.

A container (zip, and the other archive kinds) expands into member files that
are checkpointed under their own ids. If the container itself is not marked,
a retry re-enters it and every member is extracted again - the most visible
form of "it started over" for an operator watching one big archive.
"""

from __future__ import annotations

import os
import zipfile

import pytest


class _Anything:
    """A permissive stand-in: callable, and every attribute is one too.

    The router reaches for attributes the stub does not define and then calls
    methods on them (``something.close()``), so a plain lambda is not enough.
    """

    def __init__(self, name="stub"):
        self._name = name

    def __call__(self, *args, **kwargs):
        return _Anything(self._name + "()")

    def __getattr__(self, item):
        if item.startswith("__") and item.endswith("__"):
            raise AttributeError(item)
        return _Anything(f"{self._name}.{item}")

    def __bool__(self):
        return True


class _StubPipeline:
    """Stands in for StoragePipeline (no database in unit tests)."""

    #: Everything stored in the process, across every reader instance. Nested
    #: readers are created inside the router, so counting on one instance
    #: would miss the members extracted from an archive.
    stored = []

    def _store_file_sync(self, file_info, result, source_name=None, side_name=None):
        _StubPipeline.stored.append(file_info.get("path"))
        return len(_StubPipeline.stored)

    def get_statistics(self):
        return {"files_stored": len(_StubPipeline.stored)}

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Anything(f"StubPipeline.{name}")


@pytest.fixture
def no_database(monkeypatch):
    """Make every StoragePipeline construction return the stub.

    Containers are handled by a nested IntegratedFileReader created inside the
    router, which initialises its own storage - and therefore tries to open a
    real PostgreSQL connection. This replaces the name where it is imported,
    so neither the outer nor the nested reader touches a database.
    """
    _StubPipeline.stored = []
    import pipeline.integrated_reader as integrated_reader

    monkeypatch.setattr(
        integrated_reader, "StoragePipeline", lambda *a, **k: _StubPipeline()
    )
    return _StubPipeline


def _build_reader(folder, checkpoint, workers=1):
    from pipeline.integrated_reader import IntegratedFileReader

    reader = IntegratedFileReader(
        enable_storage=True,
        storage_source="SRC",
        storage_side="SIDE",
        checkpoint_file=checkpoint,
        max_workers=workers,
    )
    return reader


@pytest.fixture
def corpus(tmp_path):
    """A folder with one zip of three members plus two loose files."""
    folder = tmp_path / "corpus"
    folder.mkdir()

    archive = folder / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for i in range(3):
            zf.writestr(f"member{i}.txt", f"member content {i}\n")

    for i in range(2):
        (folder / f"loose{i}.txt").write_text(f"loose content {i}\n")

    return folder, archive


def test_a_container_is_skipped_on_retry(corpus, tmp_path, capsys, no_database):
    """Second run over the same checkpoint must not re-extract the archive."""
    folder, archive = corpus
    checkpoint = tmp_path / "ck" / "test.json"

    first = _build_reader(folder, checkpoint)
    first.process_folder(str(folder))
    capsys.readouterr()
    first_stored = list(_StubPipeline.stored)

    assert first_stored, "the first run must have stored something"

    # A retry: a brand new reader over the same checkpoint, as a resumed job
    # would be. The first run is not finalized beyond its marks.
    second = _build_reader(folder, checkpoint)
    second_results = second.process_folder(str(folder)) or []
    capsys.readouterr()
    second_stored = list(_StubPipeline.stored)[len(first_stored):]

    assert second_stored == [], (
        f"no file may be stored twice; the retry stored {second_stored}"
    )
    assert second_results == [], (
        f"the retry must skip everything already checkpointed, but it "
        f"produced {len(second_results)} results"
    )
