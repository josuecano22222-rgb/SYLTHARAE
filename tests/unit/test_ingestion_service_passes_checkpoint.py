"""A job-driven folder ingestion must actually get a checkpoint file.

Reproduces the reported resume bug: *"clicking Retry causes the process to
start from the beginning rather than resuming from the last completed
checkpoint"* for **folder** ingestions.

Root cause
----------
``IngestionService._make_reader`` decided whether to pass ``checkpoint_file``
to the reader factory by asking::

    sig = inspect.signature(factory)
    if "checkpoint_file" in sig.parameters:
        kwargs["checkpoint_file"] = self._checkpoint_file(request)

The service's own default factory is declared as
``def _default_reader_factory(**kwargs)`` — it forwards every keyword straight
into ``IntegratedFileReader``, which *does* accept ``checkpoint_file``. But
``inspect.signature`` reports that parameter as a single ``VAR_KEYWORD`` entry
named ``kwargs``, so the name ``"checkpoint_file"`` is not in
``sig.parameters`` and the branch never ran.

Consequence: **no ingestion started through the job manager ever received a
checkpoint file.** ``IntegratedFileReader.checkpoint_file`` was ``None``, no
``CheckpointManager`` was constructed, and nothing was written to the
checkpoints directory. An interrupted folder run therefore had no checkpoint
to resume from:

* ``_inventory_tree`` reported ``skipped=0`` — every file, including files
  already stored, was queued again;
* containers (archives, mailboxes) are explicitly exempt from the database
  fallback in ``_skip_committed``, so they were re-extracted and every member
  re-processed;
* the operator saw progress restart at 0% with no "Resuming: N files already
  processed" line — indistinguishable from starting over.

The run still *appeared* to resume for plain files only because of an
unrelated durability guard (``_skip_committed``), which hashes each file and
looks it up in the database. That guard re-reads and re-hashes every file
before skipping it, and it cannot cover containers at all.

These tests pin the corrected behaviour: a factory that forwards ``**kwargs``
is recognised as accepting ``checkpoint_file``.
"""

from __future__ import annotations

import inspect

import pytest


def _request(tmp_path):
    from services.ingesting.options import IngestionOptions
    from services.ingesting.service import IngestionRequest

    folder = tmp_path / "corpus"
    folder.mkdir()
    (folder / "a.txt").write_text("alpha" * 32)
    return IngestionRequest(
        path=str(folder),
        file_paths=[],
        source="unit-source",
        side="unit-side",
        recursive=True,
        options=IngestionOptions(checkpoint="auto"),
    )


def test_kwargs_forwarding_factory_receives_the_checkpoint_file(tmp_path):
    """The service's own default factory shape must still get a checkpoint."""
    from services.ingesting.service import IngestionService

    captured = {}

    def factory(**kwargs):  # exactly how _default_reader_factory is declared
        captured.update(kwargs)
        return object()

    IngestionService()._make_reader(factory, _request(tmp_path))

    assert captured.get("checkpoint_file"), (
        "a factory declared as **kwargs forwards checkpoint_file to the reader, "
        "so it must be given one; without it no checkpoint is written and an "
        "interrupted folder ingestion restarts from the beginning"
    )


def test_expected_checkpoint_path_is_derived_from_the_folder(tmp_path):
    """The path is the stable one: digest of the folder plus source and side."""
    from services.ingesting.service import IngestionService

    captured = {}
    IngestionService()._make_reader(lambda **kw: captured.update(kw) or object(),
                                    _request(tmp_path))

    checkpoint_file = captured.get("checkpoint_file") or ""
    assert checkpoint_file.endswith("_unit-source_unit-side.json"), (
        f"checkpoint identity must key on the folder, source and side: "
        f"{checkpoint_file!r}"
    )
    assert "checkpoints" in checkpoint_file


def test_checkpoint_policy_off_still_yields_no_checkpoint(tmp_path):
    """'off' must stay off: no file is passed, so nothing is written."""
    from services.ingesting.options import IngestionOptions
    from services.ingesting.service import IngestionRequest, IngestionService

    request = _request(tmp_path)
    request.options = IngestionOptions(checkpoint="off")

    captured = {}
    IngestionService()._make_reader(lambda **kw: captured.update(kw) or object(), request)

    assert captured.get("checkpoint_file") is None


def test_a_factory_that_cannot_accept_it_is_not_broken(tmp_path):
    """A narrow factory (e.g. a test double) must not receive an unknown kwarg.

    The signature probe exists precisely so such a factory is not handed
    ``checkpoint_file`` and blown up with a TypeError. Widening the probe to
    ``**kwargs`` must not loosen this case.
    """
    from services.ingesting.service import IngestionService

    captured = {}

    def factory(enable_storage=True, storage_source="", storage_side="",
                max_workers=0, enable_monitoring=True, issue_registry=None):
        captured.update(enable_storage=enable_storage, storage_source=storage_source,
                        storage_side=storage_side)
        return object()

    # Must not raise TypeError: the narrow signature is respected.
    assert IngestionService()._make_reader(factory, _request(tmp_path)) is not None
    assert captured, "the narrow factory should still receive the normal options"


def test_the_real_reader_gets_a_checkpoint_manager(tmp_path, monkeypatch):
    """End to end through the real factory: the reader must be checkpointed."""
    from services.ingesting.service import IngestionService

    monkeypatch.setenv("INGESTION_ROOTS", str(tmp_path))

    class _NoOpReader:
        checkpoint_file = None
        checkpoint_manager = None

        def __getattr__(self, name):
            return lambda *a, **k: None

    seen = {}
    from pipeline import integrated_reader

    monkeypatch.setattr(
        integrated_reader, "IntegratedFileReader",
        lambda **kwargs: seen.update(kwargs) or _NoOpReader(),
    )

    IngestionService(reader_factory=None)._make_reader(
        IngestionService._default_reader_factory, _request(tmp_path))

    assert seen.get("checkpoint_file"), (
        "IntegratedFileReader accepts checkpoint_file, so the real default "
        "factory must pass it through"
    )
