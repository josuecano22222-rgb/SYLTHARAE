"""Unit and route tests for DocumentAnalysis + details export columns."""

from __future__ import annotations

from flask import Flask

from Api.blueprints import files as files_module
from Api.services import document_analysis as analysis_module


def _files_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(files_module.files_bp)
    return app.test_client()


def test_build_document_analysis_maps_categories_and_terms(monkeypatch):
    monkeypatch.setattr(
        analysis_module,
        "_file_metadata",
        lambda _fid: {
            "id": 7,
            "name": "report.pdf",
            "type": "pdf",
            "size": 100,
            "status": "ready",
            "date": "2026-01-01",
            "path": "/private/report.pdf",
            "source": "Inbox",
            "side": "A",
        },
    )
    monkeypatch.setattr(
        analysis_module,
        "select_classification",
        lambda _fid: [
            ("Finance", "budget", 3),
            ("Finance", "ledger", 1),
            ("Legal", "contract", 2),
        ],
    )
    monkeypatch.setattr(
        analysis_module,
        "compute_percentage",
        lambda _rows: {
            "Finance": {"count": 4, "percentage": 66.7},
            "Legal": {"count": 2, "percentage": 33.3},
        },
    )
    monkeypatch.setattr(
        analysis_module,
        "_keyword_rows_with_category",
        lambda _fid, limit=50: [
            {"phrase": "annual budget", "category": "Finance", "count": 2},
        ],
    )

    payload = analysis_module.build_document_analysis(7)
    assert payload["file_id"] == 7
    assert payload["file_name"] == "report.pdf"
    assert [c["name"] for c in payload["categories"]] == ["Finance", "Legal"]
    terms = {(t["term"], t["category"]) for t in payload["category_terms"]}
    assert ("budget", "Finance") in terms
    assert ("contract", "Legal") in terms
    assert payload["keywords"][0]["phrase"] == "annual budget"
    assert payload["metadata"]["source"] == "Inbox"


def test_build_selection_analysis_aggregates_unique_union(monkeypatch):
    def fake_build(fid, **_kwargs):
        if fid == 1:
            return {
                "file_id": 1,
                "file_name": "a.txt",
                "categories": [{"name": "Finance", "count": 2}],
                "category_terms": [{"term": "budget", "category": "Finance", "count": 2}],
                "keywords": [{"phrase": "annual budget", "category": "Finance", "count": 1}],
                "metadata": {},
            }
        return {
            "file_id": 2,
            "file_name": "b.txt",
            "categories": [{"name": "Finance", "count": 1}, {"name": "Legal", "count": 3}],
            "category_terms": [{"term": "budget", "category": "Finance", "count": 1}],
            "keywords": [{"phrase": "annual budget", "category": "Finance", "count": 4}],
            "metadata": {},
        }

    monkeypatch.setattr(analysis_module, "build_document_analysis", fake_build)
    result = analysis_module.build_selection_analysis([1, 2, 2, "x"])
    assert result["success"] is True
    assert result["aggregate"]["file_count"] == 2
    finance = next(c for c in result["aggregate"]["categories"] if c["name"] == "Finance")
    assert finance["count"] == 3
    budget = next(t for t in result["aggregate"]["category_terms"] if t["term"] == "budget")
    assert budget["count"] == 3
    phrase = next(k for k in result["aggregate"]["keywords"] if k["phrase"] == "annual budget")
    assert phrase["count"] == 5


def test_analysis_column_values_join_unique_strings(monkeypatch):
    monkeypatch.setattr(
        analysis_module,
        "build_document_analysis",
        lambda *_a, **_k: {
            "categories": [{"name": "Finance"}, {"name": "finance"}, {"name": "Legal"}],
            "category_terms": [{"term": "budget"}, {"term": "Budget"}, {"term": "ledger"}],
            "keywords": [{"phrase": "annual budget"}, {"phrase": "Annual Budget"}],
        },
    )
    values = analysis_module.analysis_column_values(9)
    assert values["category"] == "Finance; Legal"
    assert values["category_terms"] == "budget; ledger"
    assert values["keywords"] == "annual budget"


def test_file_analysis_route_returns_payload(monkeypatch):
    monkeypatch.setattr(
        analysis_module,
        "build_document_analysis",
        lambda file_id, **_k: {
            "file_id": file_id,
            "file_name": "doc.txt",
            "categories": [{"name": "Legal", "count": 1, "percentage": 100.0}],
            "category_terms": [{"term": "contract", "category": "Legal", "count": 1}],
            "keywords": [],
            "metadata": {"type": "txt"},
        },
    )
    # Route imports the helper inside the handler; patch the service module.
    import Api.services.document_analysis as svc

    monkeypatch.setattr(svc, "build_document_analysis", analysis_module.build_document_analysis)
    # Re-bind through files_module import path used by the route
    monkeypatch.setattr(
        "Api.services.document_analysis.build_document_analysis",
        lambda file_id, **_k: {
            "file_id": file_id,
            "file_name": "doc.txt",
            "categories": [{"name": "Legal", "count": 1, "percentage": 100.0}],
            "category_terms": [{"term": "contract", "category": "Legal", "count": 1}],
            "keywords": [],
            "metadata": {"type": "txt"},
        },
    )
    response = _files_client().get("/api/file/42/analysis")
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["analysis"]["file_id"] == 42
    assert body["analysis"]["categories"][0]["name"] == "Legal"


def test_batch_analysis_route_requires_selection(monkeypatch):
    response = _files_client().post("/api/files/analysis", json={"file_ids": []})
    assert response.status_code == 400


def test_details_export_columns_include_analysis_keys():
    keys = {key for key, _label, _expr in files_module._DETAILS_EXPORT_COLUMNS}
    assert {"category", "category_terms", "keywords"} <= keys


def test_details_export_enriches_analysis_columns(monkeypatch):
    # select_keys becomes id + requested columns (analysis keys use NULL in SQL).
    monkeypatch.setattr(
        files_module,
        "execute_query",
        lambda *_args, **_kwargs: [(7, "report.pdf", None, None, None)],
    )
    monkeypatch.setattr(
        "Api.services.document_analysis.analysis_column_values",
        lambda _fid: {
            "category": "Finance",
            "category_terms": "budget",
            "keywords": "annual budget",
        },
    )
    monkeypatch.setattr(files_module, "note_disclosure", lambda **_kwargs: None)
    response = _files_client().post(
        "/api/files/details/export",
        json={
            "scope": "selected",
            "file_ids": [7],
            "format": "csv",
            "columns": ["name", "category", "category_terms", "keywords"],
            "filename": "analysis_check",
        },
    )
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Category" in text
    assert "Finance" in text
    assert "budget" in text
    assert "annual budget" in text