from __future__ import annotations

import base64
from types import SimpleNamespace

from Api.services import file_preview


def test_pdf_preview_renders_page_one_as_a_bounded_image(monkeypatch):
    calls = {}

    class Page:
        rect = SimpleNamespace(width=612, height=792)

        def get_pixmap(self, *, matrix, alpha):
            calls["scale"] = matrix.scale
            calls["alpha"] = alpha
            return SimpleNamespace(
                width=612,
                height=792,
                tobytes=lambda _format: b"png-preview-bytes",
            )

    class Document:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __len__(self):
            return 4

        def load_page(self, page_number):
            calls["page"] = page_number
            return Page()

    class Matrix:
        def __init__(self, scale_x, scale_y):
            assert scale_x == scale_y
            self.scale = scale_x

    fake_fitz = SimpleNamespace(
        open=lambda _path: Document(),
        Matrix=Matrix,
    )
    monkeypatch.setattr(file_preview, "fitz", fake_fitz)
    monkeypatch.setattr(file_preview, "FITZ_AVAILABLE", True)
    monkeypatch.setattr(file_preview, "PDF_FALLBACK_AVAILABLE", False)

    preview = file_preview.FilePreviewService._preview_pdf(
        "/trusted/document.pdf", max_width=300, max_height=500)

    assert preview["preview_type"] == "image"
    assert preview["preview_kind"] == "pdf_first_page"
    assert preview["page_count"] == 4
    assert preview["data"] == "data:image/png;base64," + base64.b64encode(
        b"png-preview-bytes").decode("ascii")
    assert calls["page"] == 0
    assert calls["alpha"] is False
    assert calls["scale"] < 1


def test_pdf_preview_does_not_exceed_requested_bounds_for_oversized_pages(monkeypatch):
    calls = {}

    class Page:
        rect = SimpleNamespace(width=50000, height=40000)

        def get_pixmap(self, *, matrix, alpha):
            calls["scale"] = matrix.scale
            return SimpleNamespace(
                width=round(self.rect.width * matrix.scale),
                height=round(self.rect.height * matrix.scale),
                tobytes=lambda _format: b"bounded-png",
            )

    class Document:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __len__(self):
            return 1

        def load_page(self, _page_number):
            return Page()

    class Matrix:
        def __init__(self, scale_x, scale_y):
            assert scale_x == scale_y
            self.scale = scale_x

    monkeypatch.setattr(
        file_preview,
        "fitz",
        SimpleNamespace(open=lambda _path: Document(), Matrix=Matrix),
    )
    monkeypatch.setattr(file_preview, "FITZ_AVAILABLE", True)
    monkeypatch.setattr(file_preview, "PDF_FALLBACK_AVAILABLE", False)

    preview = file_preview.FilePreviewService._preview_pdf(
        "/trusted/very-large-page.pdf", max_width=1000, max_height=700)

    assert calls["scale"] < 0.1
    assert preview["width"] <= 1000
    assert preview["height"] <= 700
